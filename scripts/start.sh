#!/bin/sh
set -e
# Railway (and most PaaS) inject PORT; default for local Docker.
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
