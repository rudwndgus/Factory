param([switch]$SkipDeploy)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$workspaceRoot = Split-Path $projectRoot -Parent
$runtime = Join-Path $projectRoot '.runtime'
$cloudflareDir = Join-Path $workspaceRoot '.tools\cloudflared'
$cloudflared = Join-Path $cloudflareDir 'cloudflared.exe'
$gh = Join-Path $workspaceRoot '.tools\gh\bin\gh.exe'
$envFile = Join-Path $projectRoot '.env'

function Read-DotEnvValue([string]$name) {
    if (-not (Test-Path -LiteralPath $envFile)) { return $null }
    $line = Get-Content -LiteralPath $envFile -Encoding utf8 |
        Where-Object { $_ -match "^\s*$([regex]::Escape($name))\s*=" } |
        Select-Object -Last 1
    if (-not $line) { return $null }
    return (($line -split '=', 2)[1].Trim()).Trim('"').Trim("'")
}

$tunnelToken = if ($env:CLOUDFLARE_TUNNEL_TOKEN) {
    $env:CLOUDFLARE_TUNNEL_TOKEN
} else { Read-DotEnvValue 'CLOUDFLARE_TUNNEL_TOKEN' }
$publicUrl = if ($env:FIXED_API_URL) {
    $env:FIXED_API_URL
} else { Read-DotEnvValue 'FIXED_API_URL' }

if (-not $tunnelToken) {
    throw 'CLOUDFLARE_TUNNEL_TOKEN is missing from .env.'
}
if (-not $publicUrl -or $publicUrl -notmatch '^https://[^/]+/?$') {
    throw 'FIXED_API_URL must be the HTTPS hostname published for the tunnel, without a path.'
}
$publicUrl = $publicUrl.TrimEnd('/')

New-Item -ItemType Directory -Force -Path $runtime, $cloudflareDir | Out-Null
if (-not (Test-Path -LiteralPath $cloudflared)) {
    Invoke-WebRequest `
        'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' `
        -OutFile $cloudflared
}

$apiListener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
if (-not $apiListener) {
    $env:PYTHONPATH = Join-Path $projectRoot 'apps\api'
    $api = Start-Process `
        -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') `
        -ArgumentList '-m','uvicorn','factory.main:app','--host','127.0.0.1','--port','8000' `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtime 'api.out.log') `
        -RedirectStandardError (Join-Path $runtime 'api.err.log') -PassThru
    Set-Content -LiteralPath (Join-Path $runtime 'api.pid') -Value $api.Id
}

$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
        if ($health.status -eq 'online') { $healthy = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $healthy) { throw 'The Factory API did not become healthy on port 8000.' }

$pidFile = Join-Path $runtime 'cloudflared.pid'
if (Test-Path -LiteralPath $pidFile) {
    $oldPid = [int](Get-Content -LiteralPath $pidFile -Raw)
    $old = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
    if ($old -and $old.ExecutablePath -eq $cloudflared) { Stop-Process -Id $oldPid }
}

$outLog = Join-Path $runtime 'cloudflared.out.log'
$errorLog = Join-Path $runtime 'cloudflared.err.log'
Remove-Item -LiteralPath $outLog, $errorLog -Force -ErrorAction SilentlyContinue
# cloudflared accepts TUNNEL_TOKEN from its environment, keeping it out of the process command line.
$env:TUNNEL_TOKEN = $tunnelToken
$tunnel = Start-Process -FilePath $cloudflared `
    -ArgumentList 'tunnel','--no-autoupdate','run' `
    -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errorLog -PassThru
Remove-Item Env:TUNNEL_TOKEN
Set-Content -LiteralPath $pidFile -Value $tunnel.Id

$remoteHealthy = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($tunnel.HasExited) { throw "Cloudflare Tunnel stopped. See $errorLog" }
    try {
        $health = Invoke-RestMethod "$publicUrl/api/health" -TimeoutSec 8
        if ($health.status -eq 'online') { $remoteHealthy = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $remoteHealthy) { throw "Fixed tunnel did not become healthy at $publicUrl/api/health" }

Set-Content -LiteralPath (Join-Path $runtime 'public-api-url.txt') -Value $publicUrl
if (Test-Path -LiteralPath $gh) {
    & $gh variable set PUBLIC_API_URL --repo rudwndgus/Factory --body $publicUrl
    if ($LASTEXITCODE -ne 0) { throw 'Could not update the GitHub PUBLIC_API_URL variable.' }
    if (-not $SkipDeploy) {
        & $gh workflow run deploy-pages.yml --repo rudwndgus/Factory
        if ($LASTEXITCODE -ne 0) { throw 'Could not start the GitHub Pages deployment.' }
    }
} else {
    Write-Warning 'GitHub CLI was not found; the frontend deployment variable was not updated.'
}

Write-Host "Stable remote API is ready: $publicUrl"
Write-Host 'It remains reachable at this same address while this PC, API, and tunnel are running.'
