#!/bin/sh
set -e
# Works in:
# - Production Docker image (WORKDIR /app, app package at ./app)
# - Nixpacks / Procfile from repo root (backend/app)
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

if [ -f "app/main.py" ]; then
  :
elif [ -f "backend/app/main.py" ]; then
  cd backend
  export PYTHONPATH="${PWD}${PYTHONPATH:+:$PYTHONPATH}"
elif [ -f "/app/app/main.py" ]; then
  cd /app
fi

exec uvicorn app.main:app --host "$HOST" --port "$PORT"
