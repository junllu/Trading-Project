@echo off
REM Trading Portal launcher for Windows. Double-click to start.
cd /d "%~dp0"
title Automated Trading Portal
echo ======================================
echo    Automated Trading Portal - launch
echo ======================================
echo.

REM 1. Ensure a virtual environment exists.
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment ^(one-time^)...
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo ERROR: could not create the virtual environment.
    echo Make sure Python 3 is installed from python.org and "Add to PATH" was checked.
    echo Test it by opening Command Prompt and running:  python --version
    echo.
    pause
    exit /b 1
  )
) else (
  echo [1/4] Virtual environment found.
)

set "PY=.venv\Scripts\python.exe"

REM 2. Upgrade pip (visible).
echo [2/4] Preparing installer...
"%PY%" -m pip install --upgrade pip

REM 3. Install ESSENTIALS (visible progress). First run: a couple of minutes.
echo.
echo [3/4] Installing required packages ^(first run can take 2-5 minutes^)...
echo       You will see download progress below - this is normal, please wait.
echo.
"%PY%" -m pip install -r requirements-core.txt
if errorlevel 1 (
  echo.
  echo ERROR: installing required packages failed. Check your internet connection
  echo and try again. If it persists, run this in Command Prompt to see the error:
  echo    "%PY%" -m pip install -r requirements-core.txt
  echo.
  pause
  exit /b 1
)

REM 3b. Broker libraries are OPTIONAL (only for live Robinhood/Webull). Never block startup.
echo.
echo [3/4] Installing optional broker libraries ^(safe to skip if this warns^)...
"%PY%" -m pip install robin-stocks webull
if errorlevel 1 echo NOTE: broker libraries did not install - the portal still runs in paper mode.

REM 4. First-run config scaffolding.
if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul
if not exist "config\config.yaml" if exist "config\config.example.yaml" copy "config\config.example.yaml" "config\config.yaml" >nul

echo.
echo ======================================
echo   Starting the portal...
echo   Your browser will open automatically to the dashboard.
echo   (If port 8000 is taken by another app, it picks a free one and
echo    opens that URL - watch the line that says "Dashboard:".)
echo   Leave this window OPEN. Press Ctrl+C to stop.
echo ======================================
echo.
"%PY%" run.py
echo.
echo (The portal has stopped.)
pause
