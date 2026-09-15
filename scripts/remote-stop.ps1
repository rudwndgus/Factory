$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pidFile = Join-Path $projectRoot '.runtime\cloudflared.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'No Factory remote tunnel PID was recorded.'
    exit 0
}

$tunnelPid = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$tunnelPid" -ErrorAction SilentlyContinue
if ($process -and $process.Name -eq 'cloudflared.exe') {
    Stop-Process -Id $tunnelPid
    Write-Host 'Factory remote tunnel stopped.'
} else {
    Write-Host 'The recorded tunnel process is no longer running.'
}
Remove-Item -LiteralPath $pidFile -Force
