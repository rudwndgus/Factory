param(
    [switch]$SkipModelPull,
    [switch]$SkipPythonInstall
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$requirements = Join-Path $projectRoot 'apps\api\requirements.txt'
$envFile = Join-Path $projectRoot '.env'
$configuredModel = if (Test-Path -LiteralPath $envFile) {
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*OLLAMA_MODEL\s*=' } | Select-Object -Last 1
    if ($line) { ($line -split '=', 2)[1].Trim() }
}
$model = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } elseif ($configuredModel) { $configuredModel } else { 'qwen3:4b' }

Write-Host 'Pixel Shorts Factory zero-cost setup'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Run scripts/setup.ps1 first.'
}
& $python --version

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    & $python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'
}

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    $knownOllama = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:LOCALAPPDATA\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($knownOllama) { $ollama = Get-Item -LiteralPath $knownOllama }
    else {
        Write-Host 'Ollama is missing. Install it from https://ollama.com/download/windows and run this script again.'
        throw 'Ollama command not found'
    }
}
& $ollama.FullName --version

try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5 | Out-Null
} catch {
    Write-Host 'Starting the local Ollama service...'
    Start-Process -FilePath $ollama.FullName -ArgumentList 'serve' -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

if (-not $SkipModelPull) {
    Write-Host "Verifying/pulling configured Ollama model: $model"
    & $ollama.FullName pull $model
    if ($LASTEXITCODE -ne 0) { throw "Ollama model pull failed: $model" }
}

if (-not $SkipPythonInstall) {
    Write-Host 'Installing/verifying Kokoro dependencies in the project environment...'
    & $python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
}

$espeak = Get-Command espeak-ng -ErrorAction SilentlyContinue
if (-not $espeak) {
    $known = @(
        'C:\Program Files\eSpeak NG\espeak-ng.exe',
        'C:\Program Files (x86)\eSpeak NG\espeak-ng.exe'
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $known) {
        & $python -c 'import espeakng_loader; print("Embedded eSpeak-NG loader: READY")'
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Neither the eSpeak-NG executable nor Python espeakng-loader was found.'
        }
    } else {
        Write-Host "eSpeak-NG found: $known"
    }
} else {
    & $espeak.Source --version
}

$env:PYTHONPATH = Join-Path $projectRoot 'apps\api'
Write-Host 'Running local Ollama structured-output and Kokoro voice checks...'
& $python -m factory.zero_cost_check
if ($LASTEXITCODE -ne 0) { throw 'Local provider readiness checks failed' }

Write-Host 'READY: Ollama, configured model, Kokoro and local voice sample passed.'
Write-Host 'Cloudflare and YouTube credentials are validated from the Factory INSPECT screen.'
Write-Host 'OpenAI credentials are not requested or used.'
