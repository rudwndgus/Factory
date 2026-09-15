param([switch]$SkipDeploy)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$workspaceRoot = Split-Path $projectRoot -Parent
$runtime = Join-Path $projectRoot '.runtime'
$cloudflareDir = Join-Path $workspaceRoot '.tools\cloudflared'
$cloudflared = Join-Path $cloudflareDir 'cloudflared.exe'
$gh = Join-Path $workspaceRoot '.tools\gh\bin\gh.exe'

New-Item -ItemType Directory -Force -Path $runtime, $cloudflareDir | Out-Null

if (-not (Test-Path -LiteralPath $cloudflared)) {
    Write-Host 'Downloading the official Cloudflare Tunnel client...'
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
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtime 'api.out.log') `
        -RedirectStandardError (Join-Path $runtime 'api.err.log') `
        -PassThru
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
    if ($old -and $old.ExecutablePath -eq $cloudflared) {
        Stop-Process -Id $oldPid
    }
}

$outLog = Join-Path $runtime 'cloudflared.out.log'
$errorLog = Join-Path $runtime 'cloudflared.err.log'
Remove-Item -LiteralPath $outLog, $errorLog -Force -ErrorAction SilentlyContinue
$tunnel = Start-Process `
    -FilePath $cloudflared `
    -ArgumentList 'tunnel','--url','http://127.0.0.1:8000','--no-autoupdate' `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errorLog `
    -PassThru
Set-Content -LiteralPath $pidFile -Value $tunnel.Id

$publicUrl = $null
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $logs = ''
    if (Test-Path -LiteralPath $outLog) { $logs += Get-Content -LiteralPath $outLog -Raw }
    if (Test-Path -LiteralPath $errorLog) { $logs += Get-Content -LiteralPath $errorLog -Raw }
    $match = [regex]::Match($logs, 'https://[-a-z0-9]+\.trycloudflare\.com')
    if ($match.Success) { $publicUrl = $match.Value; break }
    if ($tunnel.HasExited) { throw "Cloudflare Tunnel stopped. See $errorLog" }
    Start-Sleep -Milliseconds 500
}
if (-not $publicUrl) { throw "Cloudflare Tunnel URL was not issued. See $errorLog" }

Set-Content -LiteralPath (Join-Path $runtime 'public-api-url.txt') -Value $publicUrl
Write-Host "Public Factory API: $publicUrl"

if (-not (Test-Path -LiteralPath $gh)) {
    Write-Warning 'GitHub CLI was not found. Enter the public API URL in Connect server on each device.'
    exit 0
}

& $gh variable set PUBLIC_API_URL --repo rudwndgus/Factory --body $publicUrl
if ($LASTEXITCODE -ne 0) { throw 'Could not update the GitHub PUBLIC_API_URL variable.' }

if (-not $SkipDeploy) {
    & $gh workflow run deploy-pages.yml --repo rudwndgus/Factory
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the GitHub Pages deployment.' }
    Write-Host 'GitHub Pages redeployment started. Allow about two minutes before opening the web app.'
}

Write-Host 'Remote access is ready while this PC, the API, and cloudflared remain running.'
