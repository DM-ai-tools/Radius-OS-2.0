#!/bin/sh
set -e
# Celery beat — same layout detection as start.sh (Docker /app or repo root).
if [ -f "app/main.py" ]; then
  :
elif [ -f "backend/app/main.py" ]; then
  cd backend
  export PYTHONPATH="${PWD}${PYTHONPATH:+:$PYTHONPATH}"
elif [ -f "/app/app/main.py" ]; then
  cd /app
fi

exec celery -A app.tasks.celery_app beat --loglevel="${CELERY_LOG_LEVEL:-info}"
