$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) { python -m venv .venv }
& '.\.venv\Scripts\python.exe' -m pip install -r apps/api/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency install failed' }
& '.\.venv\Scripts\python.exe' scripts/bootstrap.py
Push-Location apps/web
npm ci
Pop-Location
Write-Host 'Setup complete. Read OWNER_PASSWORD in .env, then run scripts/dev.ps1.'
