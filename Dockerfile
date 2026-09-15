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
# Playwright's Chromium is a fallback path only (used when the plain HTTP
# crawl in live_site_scan.py comes back thin/JS-shell) but the browser binary
# and its system deps still need to be in the image either way.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pytest pytest-asyncio || true \
    && apt-get update \
    && playwright install --with-deps chromium \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ .
COPY --from=frontend /frontend/dist /app/static
# Fail the image build if the SPA did not land in the runtime image
RUN test -f /app/static/index.html \
    && test -d /app/static/assets \
    && ls -la /app/static

# Railway may honor Procfile (`sh scripts/start.sh`) over CMD — keep both paths.
RUN mkdir -p /app/scripts
COPY scripts/start.sh scripts/start-worker.sh scripts/start-beat.sh /app/scripts/
COPY scripts/start.sh /start.sh
RUN chmod +x /start.sh /app/scripts/*.sh \
    && sed -i 's/\r$//' /start.sh /app/scripts/*.sh

ENV PYTHONPATH=/app \
    STATIC_DIR=/app/static \
    ENVIRONMENT=production \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Match railway.json / railway.toml healthcheckPath
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD /bin/sh -c "curl -fsS http://127.0.0.1:$${PORT:-8000}/healthz || exit 1"

# Prefer shell form so $PORT is expanded if Railway wraps the command
CMD ["sh", "/start.sh"]
