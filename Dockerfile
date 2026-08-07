# syntax=docker/dockerfile:1
# Production image for Railway: builds the Vite SPA and serves it from FastAPI.
# Bind address uses Railway's PORT (see scripts/start.sh).

FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Empty VITE_API_URL → browser calls same-origin /api (served by FastAPI)
ENV VITE_API_URL=
RUN npm run build

FROM python:3.12-slim AS runtime
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pytest pytest-asyncio || true

COPY backend/ .
COPY --from=frontend /frontend/dist /app/static
# Fail the image build if the SPA did not land in the runtime image
RUN test -f /app/static/index.html \
    && test -d /app/static/assets \
    && ls -la /app/static

# Railway may honor Procfile (`sh scripts/start.sh`) over CMD — keep both paths.
RUN mkdir -p /app/scripts
COPY scripts/start.sh /start.sh
COPY scripts/start.sh /app/scripts/start.sh
RUN chmod +x /start.sh /app/scripts/start.sh \
    && sed -i 's/\r$//' /start.sh /app/scripts/start.sh

ENV PYTHONPATH=/app \
    STATIC_DIR=/app/static \
    ENVIRONMENT=production \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

# Prefer shell form so $PORT is expanded if Railway wraps the command
CMD ["sh", "/start.sh"]
