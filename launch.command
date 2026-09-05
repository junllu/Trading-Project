#!/bin/bash
# Trading Portal launcher for macOS.
# Double-click this file in Finder to start the portal and open the dashboard.
# (First run sets up a virtual environment and installs dependencies.)

cd "$(dirname "$0")" || exit 1
clear
echo "======================================"
echo "   Automated Trading Portal — launch"
echo "======================================"

# 1. Ensure a virtual environment exists.
if [ ! -d ".venv" ]; then
  echo "First run: creating virtual environment..."
  python3 -m venv .venv || { echo "Could not create venv. Is Python 3 installed?"; read -r; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 2. Install / update dependencies (quiet; only prints on error).
echo "Checking dependencies..."
pip install -q --upgrade pip >/dev/null 2>&1
pip install -q -r requirements.txt || { echo "Dependency install failed."; read -r; exit 1; }

# 3. First-run config scaffolding.
[ -f .env ] || { [ -f .env.example ] && cp .env.example .env && echo "Created .env (add your API keys later)."; }
[ -f config/config.yaml ] || { [ -f config/config.example.yaml ] && cp config/config.example.yaml config/config.yaml; }

# 4. Open the dashboard shortly after the server starts.
( sleep 3; open "http://127.0.0.1:8000" ) &

echo ""
echo "Starting dashboard at http://127.0.0.1:8000"
echo "Leave this window open. Press Ctrl+C here to stop the portal."
echo ""
python run.py
