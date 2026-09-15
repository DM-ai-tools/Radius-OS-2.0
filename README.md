# Radius OS — SearchFit SEO Agentic Onboarding (Phases 1–12)

Chat-based agentic application that walks Traffic Radius specialists from Business Discovery through Publishing & Indexation. Human-approved **Client Digital Profile** summaries feed each later phase via shared memory.

**Shipped phases:** 01 Discovery → 02 Tracking → 03 Website → 04 Competitor → 05 Keywords → 06a Strategy / 06b Architecture → 07 Technical SEO → 08 Content Audit → 09 Page Planning → 10 Briefs & Drafts → 11 On-Page → 12 Publishing (mock CMS / IndexNow preview).

## Repository layout

```text
Radius OP/
├── frontend/                 # React + Vite SPA
├── backend/
│   ├── app/
│   │   ├── agents/           # Phase runners + their SKILL.md prompts, side by side
│   │   ├── api/              # FastAPI routers
│   │   ├── services/         # Business logic
│   │   ├── models/ schemas/ integrations/ orchestration/ tasks/ ml/
│   │   └── main.py
│   ├── alembic/              # Migrations
│   ├── scripts/              # Backend CLIs (e.g. architecture audit)
│   └── tests/
├── docs/
│   ├── product/              # TRD, PRD, schema, UI/UX, flow, agent prompts, briefs
│   ├── architecture/         # SEO architecture / skills coverage HTML
│   ├── archive/              # Legacy non-runtime artifacts
│   └── superpowers/          # Internal plans
├── scripts/                  # start.sh, setup_postgres.ps1
├── Dockerfile                # Production API + SPA (Railway)
├── docker-compose.yml
└── .env.example
```

Product docs: [`docs/product/`](docs/product/) (`01_TRD` … `06_Agent_Build`, leadership briefs).  
Architecture references: [`docs/architecture/`](docs/architecture/).  
Runtime prompts (SKILL.md) live alongside each agent's code under [`backend/app/agents/`](backend/app/agents/) — e.g. `agents/discovery-agent/SKILL.md` next to `agents/discovery.py`.

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

Production-like single container (API + built SPA):

```bash
docker compose --profile production up --build app db redis
```

App: http://localhost:8080

## Railway

1. Create a project from this repo (root `Dockerfile` + `railway.json` / `railway.toml`).
2. Add **PostgreSQL** and **Redis** plugins; link `DATABASE_URL` / `REDIS_URL` / `CELERY_BROKER_URL`.
3. Set required secrets (`SECRET_KEY`, `ENCRYPTION_KEY`) — placeholders are rejected at startup.
4. Set `ENVIRONMENT=production`, `AUTH_DISABLED=false`, and public URLs:
   - `FRONTEND_URL=https://<public-domain>`
   - `OAUTH_REDIRECT_URI=https://<public-domain>/api/v1/oauth/callback`
   - `CORS_ORIGINS=https://<public-domain>`
5. Health check: `GET /healthz` (Railway config) or `GET /health`.
6. Optional: add separate services for `worker` / `beat` (see `Procfile`).

The production image serves the React SPA from FastAPI and binds `0.0.0.0:$PORT` via `scripts/start.sh`. See `.env.example` for the full Railway checklist.

## End-to-end walkthrough

1. Open Acme Retail → Discovery pre-research starts → Approve.
2. Ask “run tracking check” → grant access → re-run → Approve baseline.
3. Ask “run website situation analysis” → Approve.
4. Ask “run competitor analysis” → Approve landscape.
5. Ask “run technical SEO” → Approve.
6. Continue through content audit → planning → briefs → on-page → publishing checklist (Phases 8–12).

Phases 13–17 (Local, Links/PR, CRO, Reporting, Continuous) are out of scope for this build.

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
