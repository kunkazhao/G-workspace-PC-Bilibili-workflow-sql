$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv-asr"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements-asr.txt"

py -3.11 -m venv $venvPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath -m pip install -r $requirementsPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8', cpu_threads=1, num_workers=1); print('ASR environment ready')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
