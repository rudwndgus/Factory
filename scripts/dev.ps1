$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$env:PYTHONPATH=Join-Path $projectRoot 'apps\api'
$api=Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') -ArgumentList '-m','uvicorn','factory.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
try { Set-Location (Join-Path $projectRoot 'apps\web'); npm run dev } finally { if (-not $api.HasExited) { Stop-Process -Id $api.Id } }
