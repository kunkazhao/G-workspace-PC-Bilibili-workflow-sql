[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $repoRoot ".venv-align"
$pythonExe = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    & py -3.10 -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python 3.10 forced-alignment environment."
    }
}

$env:PIP_CACHE_DIR = "G:\workspace\.pip-cache"
& $pythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip in the forced-alignment environment."
}
& $pythonExe -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the PyTorch CUDA runtime."
}
& $pythonExe -m pip install qwen-asr==0.0.6
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install qwen-asr."
}
& $pythonExe -m pip install modelscope==1.38.1
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install ModelScope."
}
$modelRoot = Join-Path $repoRoot "data\models\Qwen3-ForcedAligner-0.6B"
if (-not (Test-Path -LiteralPath (Join-Path $modelRoot "model.safetensors"))) {
    & (Join-Path $venvRoot "Scripts\modelscope.exe") download --model Qwen/Qwen3-ForcedAligner-0.6B --local_dir $modelRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download Qwen3 Forced Aligner from ModelScope."
    }
}

# Some Windows installations keep an old 2015 MSVC DLL in System32 even after
# the current v14 Redistributable is registered. Stage the newest WinSxS copy
# beside torch only when a clean import fails; this avoids a reboot and limits
# the workaround to the isolated aligner environment.
& $pythonExe -c "import torch" 2>$null
if ($LASTEXITCODE -ne 0) {
    $runtime = Get-ChildItem "$env:WINDIR\WinSxS" -Recurse -Filter msvcp140.dll -File -ErrorAction SilentlyContinue |
        Sort-Object { $_.VersionInfo.FileVersionRaw } -Descending |
        Where-Object {
            $dir = $_.DirectoryName
            (Test-Path (Join-Path $dir "vcruntime140.dll")) -and
            (Test-Path (Join-Path $dir "vcruntime140_1.dll")) -and
            (Test-Path (Join-Path $dir "concrt140.dll"))
        } |
        Select-Object -First 1
    if ($null -eq $runtime) {
        throw "PyTorch failed to load and no current Microsoft v14 runtime was found in WinSxS."
    }
    $torchLib = Join-Path $venvRoot "Lib\site-packages\torch\lib"
    $runtimeDir = $runtime.DirectoryName
    $runtimeDlls = @(
        (Join-Path $runtimeDir "msvcp140.dll"),
        (Join-Path $runtimeDir "vcruntime140.dll"),
        (Join-Path $runtimeDir "vcruntime140_1.dll"),
        (Join-Path $runtimeDir "concrt140.dll")
    )
    Copy-Item -LiteralPath $runtimeDlls -Destination $torchLib -Force
}
& $pythonExe -c "import qwen_asr, torch; print('qwen-asr ready; cuda=' + str(torch.cuda.is_available()))"
if ($LASTEXITCODE -ne 0) {
    throw "The forced-alignment environment failed its import check."
}
