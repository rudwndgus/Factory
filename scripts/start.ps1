$projectRoot=Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
docker compose up --build -d
