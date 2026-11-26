# Install official runtime wheels locally, without redistributing Qt binaries.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$lock = Join-Path $root "requirements-runtime-lock.txt"
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"
$stamp = Join-Path $root "runtime-ready.txt"
$hash = (Get-FileHash -LiteralPath $lock -Algorithm SHA256).Hash
if ((Test-Path $venv) -and ((Get-Item -LiteralPath $venv).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Runtime directory must not link to another environment."
}
if ((Test-Path $python) -and (Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $hash)) { exit 0 }
$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if (!$launcher) { throw "Python 3.11 x64 with launcher is required: https://www.python.org/downloads/" }
& $launcher.Source -3.11 -c "import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 x64 required." }
if (!(Test-Path $python)) {
    & $launcher.Source -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Creating isolated runtime failed." }
}
& $python -m pip --isolated --disable-pip-version-check install --index-url https://pypi.org/simple --only-binary=:all: --no-cache-dir -r $lock
if ($LASTEXITCODE -ne 0) { throw "Runtime installation failed; check Internet access." }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency compatibility check failed." }
& $python -c "from PySide6 import QtCore, QtWidgets, QtMultimedia, QtWebChannel, QtWebEngineWidgets; import httpx, numpy, sounddevice"
if ($LASTEXITCODE -ne 0) { throw "Runtime import check failed." }
Set-Content -LiteralPath $stamp -Value $hash -Encoding ASCII
Write-Output "Runtime ready. No AI models downloaded."
