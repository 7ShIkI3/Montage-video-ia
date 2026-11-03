#!/usr/bin/env bash
# Lance l'API (port 8000) et l'interface Vite (port 5173) avec rechargement à chaud.
set -e
cd "$(dirname "$0")/.."
(cd backend && uvicorn app.main:app --reload --port 8000) &
API=$!
trap 'kill $API' EXIT
cd frontend && npm run dev
