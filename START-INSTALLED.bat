@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_runtime.ps1"
if errorlevel 1 (
  echo OMOI Mini: Runtime setup failed. See error above.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0python"
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\omoi_mini_entry.py"
