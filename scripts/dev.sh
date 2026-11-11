#!/usr/bin/env bash
# Lance l'API (port 8000) et l'interface Vite (port 5173) avec rechargement à chaud.
# Sous bash (Git Bash, Linux, macOS). Utilise le Python de backend/.venv s'il existe, sinon `python` du PATH.
set -e
cd "$(dirname "$0")/.."
if [ -x backend/.venv/Scripts/python.exe ]; then PY=backend/.venv/Scripts/python.exe
elif [ -x backend/.venv/bin/python ]; then PY=backend/.venv/bin/python
else PY=python
fi
"$PY" -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend --port 8000 &
API=$!
trap 'kill $API' EXIT
cd frontend && npm run dev
