@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto :setup
if not exist ".venv\installed.ok" goto :setup
start "" ".venv\Scripts\pythonw.exe" "%~dp0launcher.py"
exit /b 0
:setup
call "%~dp0start_debug.bat"
