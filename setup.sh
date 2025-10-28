#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Pick a Python interpreter
if command -v python3 >/dev/null 2>&1; then
    SYS_PY=python3
elif command -v python >/dev/null 2>&1; then
    SYS_PY=python
else
    echo "ERROR: No Python found. Install Python 3.11+" >&2
    exit 1
fi

# Create venv if missing
if [ ! -d venv ]; then
    $SYS_PY -m venv venv
fi

# Pick venv python path (Windows vs POSIX)
if [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY="venv/Scripts/python.exe"
fi

# Install dependencies
"$PY" -m pip install -U pip
"$PY" -m pip install -r requirements.txt

echo "setup.sh complete."