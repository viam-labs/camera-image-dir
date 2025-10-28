#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Pick venv python + target binary (POSIX vs Windows)
if [ -x venv/bin/python ]; then
  PY=venv/bin/python
  BIN=dist/main
  BINARY_NAME=main
else
  PY=venv/Scripts/python.exe
  BIN=dist/main.exe
  BINARY_NAME=main.exe
fi

# Ensure PyInstaller
"$PY" -m pip install -q -U pyinstaller

# Hidden imports (robust packaging)
HIDDEN=(--hidden-import=viam --hidden-import=PIL --collect-all viam)
if "$PY" -c "import importlib; importlib.import_module('googleapiclient')" >/dev/null 2>&1; then
  HIDDEN+=(--hidden-import=googleapiclient)
fi

# Build one-file binary
"$PY" -m PyInstaller --onefile --name main \
  "${HIDDEN[@]}" \
  --distpath dist --specpath build --workpath build \
  src/main.py

# Make Linux binary executable if present
chmod +x dist/main 2>/dev/null || true

# Stage package with correct meta.json entrypoint
PKG=".pkg"; rm -rf "$PKG"; mkdir -p "$PKG/dist"

# Rewrite meta.json.entrypoint to platform-correct path using Python (portable)
[ -f meta.json ] || { echo "ERROR: meta.json missing" >&2; exit 1; }
"$PY" -c "import json,sys,pathlib;
p=sys.argv[1];
d=json.loads(pathlib.Path('meta.json').read_text(encoding='utf-8'));
d['entrypoint']=p;
print(json.dumps(d, indent=2))" "$BIN" > "$PKG/meta.json"

# Copy binary (+ optional README) into package tree
cp "$BIN" "$PKG/dist/$BINARY_NAME"
[ -f README.md ] && cp README.md "$PKG/"

# Create the archive exactly as Viam expects
mkdir -p dist
tar -czf dist/archive.tar.gz -C "$PKG" meta.json $( [ -f "$PKG/README.md" ] && echo README.md ) dist

echo "Built dist/archive.tar.gz (entrypoint: $BIN)"