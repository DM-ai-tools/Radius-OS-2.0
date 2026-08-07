# Radius OS Onboarding & Audit Agent (Phases 1–4)

Chat-based agentic application that walks Traffic Radius specialists through Business Discovery, Tracking & Access, Website Situation Analysis, and Competitor & Market Analysis until a human-approved **Client Digital Profile** is ready for Phase 5.

Documentation sources in this folder: `01_TRD`, `02_PRD`, `03_Backend_Schema`, `04_UIUX_Design`, `05_App_Flow`, plus architecture HTML references.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Celery, Redis
- **Database:** PostgreSQL 16 + pgvector (Docker); SQLite fallback for local demo
- **Frontend:** React 18, TypeScript, Vite
- **LLM:** Anthropic Claude (Haiku router / Sonnet skills) with deterministic mock mode when no API key

## Prerequisites (local)

- **PostgreSQL 16+** running on `127.0.0.1:5432`
- **Redis** running on `127.0.0.1:6379`

Create the app database once (uses admin password):

```powershell
.\scripts\setup_postgres.ps1 -PostgresPassword "your-postgres-password"
```

Default app credentials created: user/db `searchfit` / password `searchfit`.

## Quick start

```bash
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\activate          # Windows
pip install -r requirements.txt
# .env should point at Postgres + Redis (see .env.example)
uvicorn app.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — no login required (`AUTH_DISABLED=true`).

Seed client: **Acme Retail Co.** Click it to open the chat and run all four phases.

`GET /health` reports Postgres + Redis status.

## Docker Compose

```bash
docker compose up --build
```

API: http://localhost:8000 · UI: http://localhost:5173 · Docs: http://localhost:8000/docs

## End-to-end walkthrough

1. Open Acme Retail → Discovery pre-research starts → Approve.
2. Ask “run tracking check” → grant access → re-run → Approve baseline.
3. Ask “run website situation analysis” → Approve.
4. Ask “run competitor analysis” → Approve landscape.
5. Ask “readiness gate” → when score ≥ 90 and all phases complete → Ready for Phase 5.

## API surface

- `POST /api/v1/auth/login` · `GET /api/v1/auth/me`
- `GET/POST /api/v1/clients` · `GET /api/v1/clients/{id}/profile`
- `POST /api/v1/sessions` · `GET /api/v1/sessions/{id}/messages`
- `POST /api/v1/sessions/{id}/messages` (REST chat turn)
- `WS /api/v1/ws/chat/{session_id}?token=…`
- `POST /api/v1/clients/{id}/phases/{agent_key}/review`
- `POST /api/v1/oauth/mock-grant`
- `GET /api/v1/clients/{id}/readiness` · `POST .../readiness/gate`

## Tests

```bash
cd backend
pytest -q
```

## Feature flags

`FEATURE_DISCOVERY_AGENT`, `FEATURE_TRACKING_AGENT`, `FEATURE_WEBSITE_AGENT`, `FEATURE_COMPETITOR_AGENT` in `.env`.

## Assumptions

- Ahrefs primary / Moz fallback for backlinks; mock providers when keys absent.
- GTM via OAuth mock grant (manual export path can be added later).
- Readiness threshold **90** (PRD).
- Long-running jobs run inline when Celery/Redis unavailable; Celery tasks defined for production.
