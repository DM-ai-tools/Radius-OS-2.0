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

echo "Starting Radius OS on ${HOST}:${PORT}"

# Fail fast with a clear message if required secrets are missing/placeholders
python - <<'PY'
from app.config import clear_settings_cache, get_settings
clear_settings_cache()
s = get_settings()
print("config_ok", "env=", s.environment, "static=", bool(s.static_dir))
PY

exec uvicorn app.main:app --host "$HOST" --port "$PORT" --proxy-headers --forwarded-allow-ips="*"
