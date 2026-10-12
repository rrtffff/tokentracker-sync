@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."

rem ---------------------------------------------------------------------------
rem Build TokenTrackerSync.exe (GUI, no console) and TokenTrackerSync-cli.exe (console) on Windows.
rem Requires: Python 3.8+ with tkinter, and:
rem   pip install pyinstaller tkinterdnd2
rem Output: dist\TokenTrackerSync.exe , dist\TokenTrackerSync-cli.exe
rem ---------------------------------------------------------------------------

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found on PATH.
  pause
  exit /b 1
)

python -c "import tkinter" 2>nul
if errorlevel 1 (
  echo [ERROR] This Python has no tkinter. Use the official python.org build.
  pause
  exit /b 1
)

echo [1/3] Checking PyInstaller...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo Installing pyinstaller tkinterdnd2 ...
  python -m pip install --disable-pip-version-check pyinstaller tkinterdnd2
  if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
  )
)

echo [2/4] Generating UI assets from art\mascot.jpg ...
python packaging\make_assets.py
if errorlevel 1 (
  echo [WARN] make_assets.py failed - the bundled background will be missing.
)

echo [3/4] Building GUI exe...
python -m PyInstaller --noconfirm --onefile --noconsole --name TokenTrackerSync ^
  --icon assets\icon.ico --add-data "assets;assets" --collect-all tkinterdnd2 tokentracker-sync.py
if errorlevel 1 (
  echo [ERROR] GUI build failed.
  pause
  exit /b 1
)

echo [4/4] Building CLI exe...
python -m PyInstaller --noconfirm --onefile --console --name TokenTrackerSync-cli ^
  --icon assets\icon.ico --add-data "assets;assets" --collect-all tkinterdnd2 tokentracker-sync.py
if errorlevel 1 (
  echo [ERROR] CLI build failed.
  pause
  exit /b 1
)

echo.
echo Done. Artifacts:
dir /b dist\*.exe
echo.
pause
