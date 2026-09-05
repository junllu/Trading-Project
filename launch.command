#!/bin/bash
# Trading Portal launcher for macOS.
# Double-click this file in Finder to start the portal and open the dashboard.

cd "$(dirname "$0")" || exit 1
clear
echo "======================================"
echo "   Automated Trading Portal - launch"
echo "======================================"
echo ""

# 1. Ensure a virtual environment exists.
if [ ! -x ".venv/bin/python" ]; then
  echo "[1/4] Creating virtual environment (one-time)..."
  python3 -m venv .venv || {
    echo ""
    echo "ERROR: could not create the virtual environment."
    echo "Install Python 3 from https://www.python.org/downloads/ and try again."
    read -r -p "Press Return to close."; exit 1;
  }
else
  echo "[1/4] Virtual environment found."
fi
PY=".venv/bin/python"

# 2. Upgrade pip (visible).
echo "[2/4] Preparing installer..."
"$PY" -m pip install --upgrade pip

# 3. Install ESSENTIALS (visible progress).
echo ""
echo "[3/4] Installing required packages (first run can take 2-5 minutes)..."
echo "      Download progress appears below - this is normal, please wait."
echo ""
"$PY" -m pip install -r requirements-core.txt || {
  echo ""; echo "ERROR: installing required packages failed. Check your connection and retry.";
  read -r -p "Press Return to close."; exit 1;
}

# 3b. Broker libraries are OPTIONAL - never block startup.
echo ""
echo "[3/4] Installing optional broker libraries (safe to skip if this warns)..."
"$PY" -m pip install robin-stocks webull || echo "NOTE: broker libraries did not install - the portal still runs in paper mode."

# 4. First-run config scaffolding.
[ -f .env ] || { [ -f .env.example ] && cp .env.example .env; }
[ -f config/config.yaml ] || { [ -f config/config.example.yaml ] && cp config/config.example.yaml config/config.yaml; }

echo ""
echo "======================================"
echo "  Starting the portal..."
echo "  Your browser will open automatically to the dashboard."
echo "  (If port 8000 is taken, it picks a free port and opens that URL"
echo "   - watch the line that says 'Dashboard:'.)"
echo "  Leave this window OPEN. Press Ctrl+C to stop."
echo "======================================"
echo ""
"$PY" run.py
echo ""
echo "(The portal has stopped.)"
read -r -p "Press Return to close."
