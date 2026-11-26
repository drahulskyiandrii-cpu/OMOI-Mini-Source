@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo OMOI Mini: Python-Umgebung fehlt. Siehe README.
  pause
  exit /b 1
)
if not exist "web\dist\index.html" (
  echo OMOI Mini: Web-Ansicht fehlt. Zuerst npm run build --prefix web ausfuehren.
  pause
  exit /b 2
)
set "PYTHONPATH=%CD%\python"
".venv\Scripts\python.exe" -m omoi_mini
if errorlevel 1 pause
