# Backend image (API-only or Compose). Prefer root Dockerfile for Railway SPA+API.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pytest pytest-asyncio || true \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8000

RUN printf '%s\n' '#!/bin/sh' 'set -e' 'PORT="${PORT:-8000}"' \
    'exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"' > /start.sh \
    && chmod +x /start.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD /bin/sh -c "curl -fsS http://127.0.0.1:$${PORT:-8000}/healthz || exit 1"

CMD ["/start.sh"]
