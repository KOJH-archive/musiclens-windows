@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name MusicLens launcher.py
if errorlevel 1 (
  echo EXE build failed. See errors above.
) else (
  echo EXE built: dist\MusicLens\MusicLens.exe
)
pause
