@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv ".venv"
  if errorlevel 1 py -3.11 -m venv ".venv"
)
if not exist ".venv\Scripts\python.exe" goto :failed
".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 goto :failed
echo ready>".venv\installed.ok"
".venv\Scripts\python.exe" -u "launcher.py"
if errorlevel 1 goto :failed
exit /b 0
:failed
echo MusicLens failed. Check musiclens-error.log and the error above.
pause
exit /b 1
