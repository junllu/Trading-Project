@echo off
REM Trading Portal launcher for Windows. Double-click to start.
cd /d "%~dp0"
title Automated Trading Portal
echo ======================================
echo    Automated Trading Portal - launch
echo ======================================

if not exist ".venv\" (
  echo First run: creating virtual environment...
  python -m venv .venv || (echo Could not create venv. Is Python installed and on PATH? & pause & exit /b 1)
)
call .venv\Scripts\activate.bat

echo Checking dependencies...
python -m pip install -q --upgrade pip >nul 2>&1
pip install -q -r requirements.txt || (echo Dependency install failed. & pause & exit /b 1)

if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul & echo Created .env (add your API keys later).
if not exist "config\config.yaml" if exist "config\config.example.yaml" copy "config\config.example.yaml" "config\config.yaml" >nul

echo.
echo Starting dashboard at http://127.0.0.1:8000
echo Leave this window open. Press Ctrl+C to stop.
start "" http://127.0.0.1:8000
python run.py
pause
