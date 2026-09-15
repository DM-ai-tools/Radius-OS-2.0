# Phase 0 — Inventory

Baseline captured on a **clean tree** at `c56cd37` (2026-09-15).
No source file has been modified. Nothing below rests on a single grep.

---

## Baseline measurements

| metric | value |
|---|---|
| tests | **765 passed**, 0 failed (732 s, `pytest -q -p no:randomly`) |
| ruff (`app tests scripts`) | **462 errors**, 256 auto-fixable |
| tracked source LOC (all types) | **113,899** across 395 files |
| python LOC in audit scope | **78,497** across 267 files |

### LOC by directory

| directory | LOC | files |
|---|---:|---:|
| `backend/app` | 64,017 | 192 |
| `frontend/src` | 22,151 | 49 |
| `backend/tests` | 16,965 | 100 |
| `docs/*` | 5,808 | 19 |
| `backend/scripts` | 2,281 | 14 |
| `scripts/*` (repo root) | 1,703 | 4 |
| `backend/alembic` | 385 | 7 |
| infra / config | 434 | 9 |

(`backend/app` is 64,017 LOC counting `.md` skill files; the **python** subset is 59,251 LOC across 153 files.)

### ruff baseline — grouped by whether it is a real finding

| rule | count | reading |
|---|---:|---|
| `S110` try-except-pass | 11 | named explicitly in the brief as a Q3 flag — **real** |
| `B023` function-uses-loop-variable | 5 | classic late-binding bug — **real, possibly behavioural** |
| `BLE001` blind-except | 3 | swallowed errors — **real** |
| `PLW0127` self-assigning-variable | 2 | almost certainly dead — **real** |
| `F401` unused-import | 2 | mechanical — **real** |
| `RUF100` unused-noqa | 74 | suppressions for rules that no longer fire = "comments describing code that no longer exists" — **real** |
| `SIM103` needless-bool | 9 | SIMPLIFY candidates |
| `SIM102` / `SIM117` collapsible | 30 | SIMPLIFY candidates |
| `B008` function-call-in-default-arg | 94 | **FastAPI `Depends()` — idiomatic, KEEP all 94** |
| `I001` + `FURB167` | 148 | import order / regex flag style — **explicitly out of scope per the brief** |

So of 462 ruff hits, ~242 are style or framework idiom and are not audit material. ~97 are worth reading.

---

## Churn is not a usable ordering signal in this repo

The repo has **14 commits**, three of which are bulk "ship everything" commits
(`d44d9de` initial, `615c469` Phases 5–12, `c56cd37` two weeks of work).

| commits touching file | files |
|---:|---:|
| 1 | 160 |
| 2 | 78 |
| 3 | 21 |
| 4 | 4 |
| 5 | 3 |
| 6 | 1 |

60 % of files have exactly one commit and 29 % have two, so `LOC × churn` collapses to
`LOC × {1,2}` and barely reorders anything. **The queue below uses that product as specified,
but it is effectively LOC-ordered.**

This has a second, more serious consequence for Phase 1: `git log -S'<symbol>'` — the
Chesterton's-fence tool the brief mandates — will resolve almost every symbol to one of the
three bulk commits, whose messages describe a release rather than a rationale. **Expect a
higher-than-normal UNCERTAIN count**, and expect it to be a property of the history, not of
the reading.

---

## Dependencies — 27 declared, 0 removable

| package | imported in | purpose |
|---|---:|---|
| fastapi | 16 | HTTP layer |
| sqlalchemy | 84 | ORM + async session |
| alembic | 8 | migrations |
| pydantic | 10 | request/response schemas |
| pydantic-settings | 1 | `app/config.py` |
| httpx | 17 | every outbound HTTP call |
| structlog | 1 | `app/logging_config.py`, used repo-wide via `get_logger` |
| celery | 1 | `app/tasks/celery_app.py` + 6 tasks |
| redis | 1 | `app/services/cache.py` |
| numpy | 1 | `app/ml/scoring.py` |
| anthropic | 1 | `app/integrations/llm.py` |
| python-jose / bcrypt / cryptography | 1 | `app/security.py` — JWT, password hashing, token encryption |
| openpyxl | 4 | xlsx export |
| python-docx | 3 | docx export |
| pypdf | 2 | PDF read |
| reportlab | 4 | PDF write |
| playwright | 1 | `app/services/live_site_scan.py` JS rendering |
| pytest / pytest-asyncio | 27 | test suite |

**Six have no direct `import` statement. All six verified as genuine runtime dependencies:**

| package | why it is needed | evidence |
|---|---|---|
| uvicorn | ASGI server, invoked as a binary | `scripts/start.sh`: `exec uvicorn app.main:app` |
| asyncpg | SQLAlchemy async postgres driver, selected by DSN | `config.py:23` `postgresql+asyncpg://` |
| aiosqlite | SQLAlchemy async sqlite driver for tests | `tests/conftest.py:14` `sqlite+aiosqlite://` |
| python-multipart | FastAPI requires it to accept `UploadFile` | `app/api/clients.py` uses `UploadFile` |
| email-validator | pydantic requires it for `EmailStr` | `app/schemas/auth.py:3` imports `EmailStr` |
| greenlet | hard requirement of `sqlalchemy[asyncio]` | no code reference; implied by the extra |

> `greenlet` is the only arguable entry: it is already pulled in transitively by
> `sqlalchemy[asyncio]`, so the explicit pin is redundant. Pinning a transitive dependency is
> a defensible reproducibility choice, so this is a **KEEP with a note**, not a finding.

---

## Entry points — the death-proofing surface

A symbol reachable from any of these can never be marked DEAD, however few direct callers it has.

| kind | count | where | why it defeats grep |
|---|---:|---|---|
| Process entry | 3 | `Procfile` → `scripts/start.sh`, `start-worker.sh`, `start-beat.sh` | `app.main:app` and `app.tasks.celery_app` are **string** targets |
| HTTP routers | 12 | `app/main.py:130–141` | auth, clients, sessions, findings, oauth, integrations, readiness, chat, engine_room, cost_tracker, technical_seo, workbook |
| Celery tasks | 6 | `app/tasks/jobs.py` | registered by **string** `name="app.tasks.jobs.*"` |
| Celery beat schedule | — | `app/tasks/celery_app.py:20` | references task names as strings |
| **Agent runners** | **13** | `app/agents/__init__.py` | **`AGENT_RUNNERS` dict keyed by string**, dispatched at `orchestration/pipeline.py:263` as `AGENT_RUNNERS[agent_key]`; all 13 also listed in `__all__` |
| **Agent skills** | **20 + 5** | `load_skill("…")` / `load_skill_file("…")` | **string** names resolving to 26 `SKILL.md` files on disk |
| Alembic migrations | 6 | `alembic/versions/` | run by revision id, never imported |
| Test fixtures | — | `tests/conftest.py` | sets `DATABASE_URL` at import time |

The agent-runner and skill registries are the important ones: this codebase routinely reaches
code by string key, so a "no callers" grep result is weak evidence here by default.

---

## Audit queue — `backend/app` python, ordered by LOC × churn

153 python files, 59,251 LOC. Tests (100 files / 16,965 LOC) and scripts (14 / 2,281) are
inventoried but are **not** primary targets — the brief forbids deleting tests to reduce
line count.

| # | file | LOC | churn | score | last touched |
|---:|---|---:|---:|---:|---|
| 1 | `app/integrations/providers.py` | 2549 | 5 | 12745 | 2026-09-15 |
| 2 | `app/services/role_skills.py` | 1514 | 4 | 6056 | 2026-09-15 |
| 3 | `app/integrations/web_fetch.py` | 751 | 5 | 3755 | 2026-09-15 |
| 4 | `app/services/create_content.py` | 1772 | 2 | 3544 | 2026-09-15 |
| 5 | `app/agents/website.py` | 1039 | 3 | 3117 | 2026-09-15 |
| 6 | `app/agents/search_demand.py` | 1514 | 2 | 3028 | 2026-09-15 |
| 7 | `app/integrations/llm.py` | 984 | 3 | 2952 | 2026-09-15 |
| 8 | `app/services/keyword_opportunity.py` | 1446 | 2 | 2892 | 2026-09-15 |
| 9 | `app/services/url_mapping.py` | 1374 | 2 | 2748 | 2026-09-15 |
| 10 | `app/services/content_strategy.py` | 1288 | 2 | 2576 | 2026-09-15 |
| 11 | `app/services/content_brief.py` | 1252 | 2 | 2504 | 2026-09-15 |
| 12 | `app/services/create_topic.py` | 1194 | 2 | 2388 | 2026-09-15 |
| 13 | `app/services/phase_validation/deterministic.py` | 1114 | 2 | 2228 | 2026-09-15 |
| 14 | `app/services/content_planning.py` | 1087 | 2 | 2174 | 2026-09-15 |
| 15 | `app/services/site_architecture.py` | 1069 | 2 | 2138 | 2026-09-15 |
| 16 | `app/api/clients.py` | 689 | 3 | 2067 | 2026-09-15 |
| 17 | `app/services/review.py` | 635 | 3 | 1905 | 2026-09-15 |
| 18 | `app/services/keyword_seeding.py` | 942 | 2 | 1884 | 2026-09-15 |
| 19 | `app/agents/competitor.py` | 611 | 3 | 1833 | 2026-09-15 |
| 20 | `app/services/keyword_clustering.py` | 905 | 2 | 1810 | 2026-09-15 |
| 21 | `app/services/memory_packs.py` | 900 | 2 | 1800 | 2026-09-15 |
| 22 | `app/ml/tier_competitors.py` | 578 | 3 | 1734 | 2026-09-15 |
| 23 | `app/integrations/dataforseo.py` | 817 | 2 | 1634 | 2026-09-01 |
| 24 | `app/services/technical_seo.py` | 815 | 2 | 1630 | 2026-09-15 |
| 25 | `app/main.py` | 271 | 6 | 1626 | 2026-09-15 |
| 26 | `app/services/on_page_seo.py` | 742 | 2 | 1484 | 2026-09-15 |
| 27 | `app/integrations/wordpress.py` | 712 | 2 | 1424 | 2026-09-15 |
| 28 | `app/services/keyword_relevance.py` | 684 | 2 | 1368 | 2026-09-15 |
| 29 | `app/agents/discovery.py` | 423 | 3 | 1269 | 2026-09-15 |
| 30 | `app/services/chat_revisions.py` | 616 | 2 | 1232 | 2026-09-15 |
| 31 | `app/agents/tracking.py` | 408 | 3 | 1224 | 2026-09-15 |
| 32 | `app/services/content_audit.py` | 579 | 2 | 1158 | 2026-09-15 |

…and 121 further files below a score of 1150.

**Scope reality check.** A genuine line-by-line pass over 59,251 LOC is multi-session work.
Phase 1 works down this queue in order and `SUMMARY.md` states exactly how many modules were
covered and which remain, rather than implying whole-repo coverage.
