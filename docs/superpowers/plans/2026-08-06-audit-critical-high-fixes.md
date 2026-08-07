# Audit Critical + High Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 5 Critical and 8 High findings from the 2026-08-06 full-project audit of Radius OS, plus two prerequisite fixes (broken test collection, missing API test harness) needed to verify the security fixes with real tests.

**Architecture:** All backend fixes are surgical edits to existing FastAPI/SQLAlchemy modules under `backend/app/` — no new services, no schema/migration changes. One new test-infrastructure file (`backend/tests/conftest.py`) is added to give the existing (currently broken) pytest suite a real async DB + HTTP client fixture, since none exists today. Frontend fixes are edits to existing React/TS files under `frontend/src/` — no new dependencies, no new test framework (none exists today and adding one is out of scope for this fix pass).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, pydantic-settings, pytest + pytest-asyncio + httpx (all already in `backend/requirements.txt` — no new packages needed). React 18 + TypeScript 5.7 + Vite (frontend fixes are typecheck-verified only; no test runner exists in `frontend/package.json`).

## Global Constraints

- **Required execution order:** Task 1 → Task 4 → Task 2 → Task 3 → Task 5 → Task 6 → Task 7 → Task 8 → Task 9 → Task 10 → Task 11 → Task 12 → Task 13 → Task 14 → Task 15. The tasks are numbered by audit severity, not by dependency — Task 2's test fixture calls `seed_all(db, seed_demo_data=False)`, a signature that doesn't exist until Task 4 creates it, so Task 4 must run first. Every other ordering constraint follows from this: any task with a test that uses the `api_client`/`db_session` fixtures (5, 8, 9, 10, 12) needs Task 2 done first, which needs Task 4 done first. Tasks 6, 7, 11, 13, 14, 15 have no fixture dependency and could technically run in any order, but follow the numbered order for simplicity.
- This is **not a git repository** (`Is a git repository: false`). Every task's "commit" step is replaced with "mark the task complete" — do not run `git` commands.
- Backend virtualenv already exists at `backend/.venv`. Run all backend commands via `backend/.venv/Scripts/python.exe -m pytest ...` (Windows venv layout) from the `backend/` directory.
- Do not modify `.env` or `.env.example` as part of these fixes — the startup-guard fix (Task 3) only rejects two specific known-bad literal values; it does not change what's in the real `.env` file. Rotating the real secrets is an operational follow-up for the user, not a code change.
- Frontend has no ESLint/test runner configured today. Verify frontend tasks with `cd frontend && npx tsc -b --noEmit` (must exit 0) plus manual code review. Do not introduce a new test framework as part of this plan.
- Every task must leave `backend/.venv/Scripts/python.exe -m pytest -q` fully green (0 errors, 0 failures) before moving to the next task.

---

### Task 1: Fix broken test collection

`tests/test_rbac.py` imports a symbol that doesn't exist, which makes **pytest fail to collect any test in the suite** — the whole suite is broken today, not just thin.

**Files:**
- Modify: `backend/tests/test_rbac.py`

**Interfaces:**
- Consumes: `app.services.role_skills.ROLE_PHASE_PERMISSIONS` (dict[str, list[tuple[str, bool, bool]]]) — already exists, confirmed at `backend/app/services/role_skills.py:199`.

- [ ] **Step 1: Confirm the collection error**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: `ImportError: cannot import name 'PERMISSIONS' from 'app.seed'`, collection interrupted, 0 tests run.

- [ ] **Step 2: Fix the import**

Replace the entire contents of `backend/tests/test_rbac.py` with:

```python
from app.services.role_skills import ROLE_PHASE_PERMISSIONS


def test_csm_only_discovery():
    agents = [a for a, *_ in ROLE_PHASE_PERMISSIONS["client_success_manager"]]
    assert agents == ["discovery_agent"]


def test_tech_has_tracking_and_website():
    agents = {a for a, *_ in ROLE_PHASE_PERMISSIONS["technical_seo_specialist"]}
    assert "tracking_access_agent" in agents
    assert "website_situation_agent" in agents


def test_qa_only_gate():
    agents = [a for a, *_ in ROLE_PHASE_PERMISSIONS["seo_qa_lead"]]
    assert agents == ["readiness_gate"]
```

- [ ] **Step 3: Run the suite and confirm all 3 pre-existing test files collect and pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: `6 passed` (3 from `test_fallback.py`, 3 from `test_rbac.py`, 1 from `test_readiness.py` — wait, count precisely: `test_fallback.py` has 3, `test_rbac.py` has 3, `test_readiness.py` has 1 = 7 passed). Expected: `7 passed`, 0 failed, 0 errors.

- [ ] **Step 4: Mark task complete** (no git in this repo — update your task tracker instead of committing)

---

### Task 2: Add backend API test fixtures

No test in this repo touches the database or an HTTP endpoint today. Tasks 8 and 9 (the two authz security fixes) need a real request-response cycle to prove a 403/404 actually fires. This task adds one `conftest.py` providing an isolated SQLite test database and an `httpx.AsyncClient` wired to the real FastAPI app, plus a helper to create a user+role+token in one call.

**Files:**
- Create: `backend/tests/conftest.py`

**Interfaces:**
- Produces: `db_session` (pytest fixture, yields `sqlalchemy.ext.asyncio.AsyncSession`) — a fresh, empty-except-for-role-catalog DB per test.
- Produces: `api_client` (pytest fixture, yields `httpx.AsyncClient`) — depends on `db_session`, talks to the real `app.main.app` over ASGI.
- Produces: `make_user(db_session, role_name: str, email: str) -> tuple[app.models.User, str]` (async helper, not a fixture — call it directly inside a test) — returns `(user, bearer_token)`.
- Consumes: `app.seed.seed_all(db, *, seed_demo_data: bool)` — this exact signature is created by Task 4. Per the Global Constraints execution order, **Task 4 runs before this task**, so `seed_all` already accepts `seed_demo_data` by the time this task's `conftest.py` is written.

- [ ] **Step 1: Write conftest.py**

Create `backend/tests/conftest.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

TEST_DB_PATH = Path(__file__).parent / "test_searchfit.db"
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
os.environ["SECRET_KEY"] = "test-suite-secret-key-not-a-real-default-value"
os.environ["ENCRYPTION_KEY"] = "test-suite-encryption-key-not-a-real-default"
os.environ["AUTH_DISABLED"] = "false"
os.environ["USE_MOCK_LLM"] = "true"
os.environ["USE_MOCK_PROVIDERS"] = "true"
os.environ["ENVIRONMENT"] = "development"

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import clear_settings_cache  # noqa: E402
from app.db import AsyncSessionLocal, Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402
from app.seed import seed_all  # noqa: E402

clear_settings_cache()


@pytest_asyncio.fixture
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await seed_all(session, seed_demo_data=False)
        await session.commit()
        yield session


@pytest_asyncio.fixture
async def api_client(db_session):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def make_user(db_session, role_name: str, email: str) -> tuple[User, str]:
    role = (
        await db_session.execute(select(Role).where(Role.name == role_name))
    ).scalar_one()
    user = User(
        email=email,
        full_name="Test User",
        role_id=role.id,
        hashed_password=hash_password("testpass123"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    token = create_access_token(str(user.id), extra={"role": role_name})
    return user, token
```

- [ ] **Step 2: Verify it doesn't break existing collection**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: still `7 passed` (conftest.py has no tests itself; it just provides fixtures unused by the existing 3 files so far).

- [ ] **Step 3: Write one throwaway smoke test to prove the fixture works, then delete it**

Temporarily add to a scratch file `backend/tests/test_zzz_smoke.py`:
```python
async def test_smoke_client_can_hit_health(api_client):
    resp = await api_client.get("/health")
    assert resp.status_code == 200


async def test_smoke_make_user_gets_valid_token(db_session, make_user_fn=None):
    from tests.conftest import make_user
    user, token = await make_user(db_session, "client_success_manager", "smoke@test.local")
    assert user.email == "smoke@test.local"
    assert token
```
Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_zzz_smoke.py -q -v`
Expected: 2 passed. If it fails, fix `conftest.py` until it passes — do not proceed to Task 8/9 without this working.

- [ ] **Step 4: Delete the scratch file**

Delete `backend/tests/test_zzz_smoke.py` — it was only to validate the fixture.

- [ ] **Step 5: Mark task complete**

---

### Task 3: Reject known dev-default secrets at startup

The live `.env` currently has `ENCRYPTION_KEY` set to the exact same value as the code's hardcoded default — every stored OAuth credential is encrypted with a key that's public in source. This adds a fail-fast guard so the app refuses to boot with either of the two known-dangerous literal values (the code's own defaults, and the `.env.example` placeholder strings).

**Files:**
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_config_secrets.py`

**Interfaces:**
- Produces: `Settings` now raises `pydantic.ValidationError` at construction time (i.e. `get_settings()` raises) if `secret_key` or `encryption_key` equals one of 4 known-bad literals.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config_secrets.py`:

```python
import os

import pytest
from pydantic import ValidationError

from app.config import Settings

KNOWN_BAD_ENCRYPTION_KEYS = [
    "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVzIQ==",  # config.py hardcoded default
    "change-me-fernet-key-base64-32bytes==",  # .env.example placeholder
]
KNOWN_BAD_SECRET_KEYS = [
    "dev-secret",  # config.py hardcoded default
    "change-me-in-production-use-a-long-random-string",  # .env.example placeholder
]


@pytest.mark.parametrize("bad_key", KNOWN_BAD_ENCRYPTION_KEYS)
def test_rejects_known_bad_encryption_key(bad_key, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", bad_key)
    monkeypatch.setenv("SECRET_KEY", "a-fine-secret-key-for-this-test-only")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("bad_key", KNOWN_BAD_SECRET_KEYS)
def test_rejects_known_bad_secret_key(bad_key, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", bad_key)
    monkeypatch.setenv("ENCRYPTION_KEY", "a-fine-encryption-key-for-this-test-only")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_accepts_a_real_looking_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-fine-secret-key-for-this-test-only")
    monkeypatch.setenv("ENCRYPTION_KEY", "a-fine-encryption-key-for-this-test-only")
    Settings(_env_file=None)  # must not raise
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_config_secrets.py -q`
Expected: FAIL — `Settings(_env_file=None)` does not raise today (`Failed: DID NOT RAISE`).

- [ ] **Step 3: Implement the guard**

In `backend/app/config.py`, add the import and validator. The file currently is:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://searchfit:searchfit@127.0.0.1:5432/searchfit"
```

Change the top of the file to:

```python
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_KNOWN_BAD_ENCRYPTION_KEYS = {
    "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVzIQ==",  # this file's own old default
    "change-me-fernet-key-base64-32bytes==",  # .env.example placeholder
}
_KNOWN_BAD_SECRET_KEYS = {
    "dev-secret",  # this file's own old default
    "change-me-in-production-use-a-long-random-string",  # .env.example placeholder
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://searchfit:searchfit@127.0.0.1:5432/searchfit"
```

Then, immediately after the `is_sqlite` property (after the line `return self.database_url.startswith("sqlite")`), add:

```python

    @model_validator(mode="after")
    def _reject_known_bad_secrets(self) -> "Settings":
        if self.encryption_key in _KNOWN_BAD_ENCRYPTION_KEYS:
            raise ValueError(
                "ENCRYPTION_KEY is set to a known placeholder value. "
                "Generate a real one: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        if self.secret_key in _KNOWN_BAD_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY is set to a known placeholder value. "
                "Generate a real one: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return self
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_config_secrets.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green. Note: `conftest.py` (Task 2) already sets `SECRET_KEY`/`ENCRYPTION_KEY` to non-default values, so this guard won't break the test fixtures.

- [ ] **Step 6: Mark task complete**

**Note for the user (not a code step):** this guard only blocks the two known-bad literal strings. It does NOT flag the real `.env`'s actual `SECRET_KEY` value (`dev-secret-key-searchfit-phase1-4-local`), which is a different string from the code default but is still a low-entropy, human-chosen placeholder. Rotate it manually — this can't be safely auto-detected without risking false positives on legitimate keys.

---

### Task 4: Gate demo-user seeding behind an environment flag

`seed_all()` runs unconditionally on every boot and creates 8 real login accounts with password `password123`, including a full-admin `head_of_department` account. This makes seeding conditional: the role/permission catalog always syncs (the app needs it to function), but the 8 demo users and the demo client are only created when `ENVIRONMENT != "production"`.

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/seed.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_seed_gating.py`

**Interfaces:**
- Produces: `Settings.environment: str` (default `"development"`) on `backend/app/config.py`.
- Produces: `app.seed.seed_all(db: AsyncSession, *, seed_demo_data: bool = True) -> None` — new keyword-only parameter, default `True` preserves today's behavior for anyone calling it without the flag (e.g. Task 2's conftest calls it explicitly with `seed_demo_data=False`).

- [ ] **Step 1: Add the `environment` setting**

In `backend/app/config.py`, add this field to `Settings` (place it near `auth_disabled`):

```python
    # "development" (default) seeds demo users/client; "production" skips them
    environment: str = "development"
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_seed_gating.py`:

```python
from sqlalchemy import select

from app.models import Client, User
from app.seed import seed_all


async def test_seed_demo_data_true_creates_users_and_client(db_session):
    await seed_all(db_session, seed_demo_data=True)
    await db_session.commit()
    users = (await db_session.execute(select(User))).scalars().all()
    clients = (await db_session.execute(select(Client))).scalars().all()
    assert len(users) == 8
    assert len(clients) == 1


async def test_seed_demo_data_false_creates_no_users_or_client(db_session):
    # db_session fixture already calls seed_all(seed_demo_data=False) once;
    # calling it again with the same flag must still leave 0 users/clients.
    await seed_all(db_session, seed_demo_data=False)
    await db_session.commit()
    users = (await db_session.execute(select(User))).scalars().all()
    clients = (await db_session.execute(select(Client))).scalars().all()
    assert users == []
    assert clients == []
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_seed_gating.py -q`
Expected: FAIL with `TypeError: seed_all() got an unexpected keyword argument 'seed_demo_data'`.

- [ ] **Step 4: Update `seed_all`**

In `backend/app/seed.py`, change the function signature and gate the two demo-data blocks. Current:

```python
async def seed_all(db: AsyncSession) -> None:
    """Ensure full role catalog + permissions exist; add demo users/client if missing."""
```

becomes:

```python
async def seed_all(db: AsyncSession, *, seed_demo_data: bool = True) -> None:
    """Ensure full role catalog + permissions always exist.

    Demo users (password123) and the demo client are only created when
    seed_demo_data is True — callers should pass False in any environment
    where this could run against a real/shared database.
    """
```

Then wrap the two demo-data blocks. The `for email, full_name, role_name, password in USERS:` loop becomes:

```python
    if seed_demo_data:
        for email, full_name, role_name, password in USERS:
            exists = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if exists:
                continue
            role = role_map.get(role_name)
            if not role:
                continue
            db.add(
                User(
                    email=email,
                    full_name=full_name,
                    role_id=role.id,
                    hashed_password=hash_password(password),
                    is_active=True,
                )
            )
```

And the `client_exists = ...` block becomes:

```python
    if seed_demo_data:
        client_exists = (await db.execute(select(Client).limit(1))).scalar_one_or_none()
        if not client_exists:
            client = Client(
                legal_name="Acme Retail Co.",
                display_name="Acme Retail Co.",
                primary_url="https://www.trafficradius.com",
                industry="Retail / Ecommerce",
                tier="B",
                status="onboarding",
            )
            db.add(client)
            await db.flush()
            db.add(ClientDigitalProfile(client_id=client.id))
```

(The role/permission catalog sync above these two blocks stays unconditional — unchanged.)

- [ ] **Step 5: Update the caller in `main.py`**

In `backend/app/main.py`, the lifespan currently has:

```python
    async with AsyncSessionLocal() as db:
        await seed_all(db)
        await db.commit()
```

Change to:

```python
    async with AsyncSessionLocal() as db:
        await seed_all(db, seed_demo_data=settings.environment != "production")
        await db.commit()
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_seed_gating.py -q`
Expected: `2 passed`.

- [ ] **Step 7: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 8: Mark task complete**

**Note for the user (not a code step):** this only stops *future* boots from re-creating these accounts in a production-flagged environment. If this database has ever been reachable outside localhost, manually rotate/deactivate the 8 seeded accounts (`hod@trafficradius.com`, `csm@trafficradius.com`, `tech@trafficradius.com`, `strategist@trafficradius.com`, `content@trafficradius.com`, `onpage@trafficradius.com`, `schema@trafficradius.com`, `qa@trafficradius.com`) — this code change cannot undo accounts already created.

---

### Task 5: Restrict self-service signup to non-privileged roles

`POST /auth/signup` lets anyone self-register as `head_of_department` (full trigger+approve on every phase). This restricts self-service signup to the 7 non-admin roles.

**Files:**
- Modify: `backend/app/services/role_skills.py`
- Modify: `backend/app/api/auth.py`
- Test: `backend/tests/test_signup_role_restriction.py`

**Interfaces:**
- Produces: `app.services.role_skills.SELF_SERVICE_ROLES: set[str]` — all `SEO_ROLES` names except `"head_of_department"`.
- Consumes (in the test): `api_client` and `db_session` fixtures from Task 2.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_signup_role_restriction.py`:

```python
async def test_signup_rejects_head_of_department(api_client):
    resp = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "attacker@test.local",
            "password": "somepassword123",
            "full_name": "Attacker",
            "role_name": "head_of_department",
        },
    )
    assert resp.status_code == 400


async def test_signup_accepts_self_service_role(api_client):
    resp = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "newbie@test.local",
            "password": "somepassword123",
            "full_name": "New Person",
            "role_name": "content_seo_specialist",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()
```

- [ ] **Step 2: Run it to verify the first test fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_signup_role_restriction.py -q`
Expected: `test_signup_rejects_head_of_department` FAILS (signup currently returns 200 for any valid role name); `test_signup_accepts_self_service_role` already passes.

- [ ] **Step 3: Add `SELF_SERVICE_ROLES` to role_skills.py**

In `backend/app/services/role_skills.py`, immediately after the closing `]` of the `SEO_ROLES` list (after line 53), add:

```python

SELF_SERVICE_ROLES: set[str] = {r["name"] for r in SEO_ROLES if r["name"] != "head_of_department"}
```

- [ ] **Step 4: Enforce it in signup**

In `backend/app/api/auth.py`, the import line:

```python
from app.services.role_skills import SEO_ROLES, permissions_for_role, role_label
```

becomes:

```python
from app.services.role_skills import SELF_SERVICE_ROLES, permissions_for_role, role_label
```

And inside `signup()`, this block:

```python
    role_names = {r["name"] for r in SEO_ROLES}
    if body.role_name not in role_names:
        raise HTTPException(400, "Invalid role — pick an SEO role from the list")
```

becomes:

```python
    if body.role_name not in SELF_SERVICE_ROLES:
        raise HTTPException(400, "This role can't be self-assigned — ask an admin to invite you")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_signup_role_restriction.py -q`
Expected: `2 passed`.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green (check nothing else imported `SEO_ROLES` from `auth.py` — `GET /auth/roles` in the same file still uses `SEO_ROLES` directly for its own import from `role_skills`, so re-check that route still has `SEO_ROLES` available; if `auth.py` needs both symbols, import both: `from app.services.role_skills import SELF_SERVICE_ROLES, SEO_ROLES, permissions_for_role, role_label`).

- [ ] **Step 7: Mark task complete**

---

### Task 6: Add an SSRF guard to `fetch_url`

`fetch_url` makes outbound requests with no validation against private/loopback/link-local IPs, and it's reachable from a client-supplied URL (`/clients/ai-fill`) and from LLM-suggested competitor URLs. This adds a host-safety check that every fetch attempt goes through.

**Files:**
- Modify: `backend/app/integrations/web_fetch.py`
- Test: `backend/tests/test_web_fetch_ssrf.py`

**Interfaces:**
- Produces: `app.integrations.web_fetch._is_public_host(hostname: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_web_fetch_ssrf.py`:

```python
from app.integrations.web_fetch import _is_public_host


def test_rejects_loopback():
    assert _is_public_host("127.0.0.1") is False


def test_rejects_cloud_metadata_address():
    assert _is_public_host("169.254.169.254") is False


def test_rejects_private_range():
    assert _is_public_host("10.0.0.5") is False


def test_rejects_none_hostname():
    assert _is_public_host(None) is False


def test_accepts_public_ip_literal():
    assert _is_public_host("8.8.8.8") is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_web_fetch_ssrf.py -q`
Expected: FAIL — `ImportError: cannot import name '_is_public_host'`.

- [ ] **Step 3: Implement the guard**

In `backend/app/integrations/web_fetch.py`, add to the imports at the top of the file:

```python
import ipaddress
import socket
```

(keep the existing `import re`, `from html.parser import HTMLParser`, etc.)

Then add this function right before `async def fetch_url(...)`:

```python
def _is_public_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True
```

Then, inside `fetch_url`'s `for attempt in _url_host_variants(url):` loop, as the very first line inside the `try:` block (before `async with httpx.AsyncClient(...)`), add the guard:

```python
        try:
            if not _is_public_host(urlparse(attempt).hostname):
                log.warning("fetch_blocked_unsafe_host", url=attempt)
                last = {
                    "url": attempt,
                    "status_code": 0,
                    "headers": {},
                    "text": "",
                    "history": [],
                    "error": "blocked_unsafe_host",
                }
                continue
            async with httpx.AsyncClient(
```

(`urlparse` is already imported at the top of this file per the existing `from urllib.parse import urljoin, urlparse` line — confirm it's there; if the file only imports `urljoin`, add `urlparse` to that import line.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_web_fetch_ssrf.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 6: Mark task complete**

---

### Task 7: Fix the broken frontend production build

`npm run build` fails today with `TS2345` because `ClientsPage.tsx` passes a possibly-null token into a function requiring `string`.

**Files:**
- Modify: `frontend/src/pages/ClientsPage.tsx`

**Interfaces:**
- Consumes: `useAuth().token: string | null` (unchanged, from `frontend/src/auth.tsx:14`).

- [ ] **Step 1: Confirm the failing build**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: `src/pages/ClientsPage.tsx(33,36): error TS2345: Argument of type 'string | null' is not assignable to parameter of type 'string'.`

- [ ] **Step 2: Read the current function**

The function at `frontend/src/pages/ClientsPage.tsx:31-37` today is:

```ts
async function load() {
  try {
    setClients(await api.clients(token));
  } catch (e) {
    setError(e instanceof Error ? e.message : "Failed to load clients");
  }
}
```

- [ ] **Step 3: Add the null guard**

Replace it with:

```ts
async function load() {
  if (!token) return;
  try {
    setClients(await api.clients(token));
  } catch (e) {
    setError(e instanceof Error ? e.message : "Failed to load clients");
  }
}
```

- [ ] **Step 4: Verify the build is clean**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: exits 0, no errors.

Run: `cd frontend && npm run build`
Expected: completes successfully (tsc passes, then vite build runs and emits to `dist/`).

- [ ] **Step 5: Mark task complete**

---

### Task 8: Enforce permission checks on the 3 unguarded findings.py routes

`submit_questionnaire`, `submit_known_changes`, and `add_manual_competitor` only require *any* authenticated user — they never check the caller's role has trigger permission on the relevant agent, unlike every approval route in `review.py`.

**Files:**
- Modify: `backend/app/api/findings.py`
- Test: `backend/tests/test_findings_permission_checks.py`

**Interfaces:**
- Consumes: `app.deps.require_permission(user, db, agent_key, *, need_trigger=False, need_approve=False) -> None` (already exists at `backend/app/deps.py:57`, raises `HTTPException(403, ...)`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_findings_permission_checks.py`. This uses `client_success_manager` (has `discovery_agent` trigger) as the "should succeed" case and `content_seo_specialist` (view-only on every Phase 1-4 agent per `ROLE_PHASE_PERMISSIONS`) as the "should be forbidden" case. First, create a test client via the seeded DB directly:

```python
from app.models import Client, ClientDigitalProfile
from tests.conftest import make_user


async def _make_client(db_session) -> str:
    client = Client(
        legal_name="Test Co",
        display_name="Test Co",
        primary_url="https://example.com",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.commit()
    return str(client.id)


async def test_questionnaire_forbidden_for_view_only_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "content_seo_specialist", "viewer@test.local")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/questionnaire",
        json={"fields": {"business_model": "b2b"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_questionnaire_allowed_for_csm(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm2@test.local")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/questionnaire",
        json={"fields": {"business_model": "b2b"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


async def test_known_changes_forbidden_for_view_only_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "content_seo_specialist", "viewer2@test.local")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/tracking/known-changes",
        json={"fields": {"note": "moved to GA4"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_manual_competitor_forbidden_for_view_only_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "content_seo_specialist", "viewer3@test.local")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/competitors/manual",
        json={"name": "Rival Co", "url": "https://rival.example"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run it to verify the forbidden cases fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_findings_permission_checks.py -q`
Expected: the 3 `_forbidden_` tests FAIL with `assert 200 == 403` (or 500, depending on mock LLM behavior) since no permission check exists yet; `test_questionnaire_allowed_for_csm` already passes.

- [ ] **Step 3: Add the permission checks**

In `backend/app/api/findings.py`, the import line:

```python
from app.deps import get_current_user
```

becomes:

```python
from app.deps import get_current_user, require_permission
```

Then in `submit_questionnaire` (currently starting at line 88), add the check as the first line of the function body:

```python
@router.post("/clients/{client_id}/questionnaire")
async def submit_questionnaire(
    client_id: UUID,
    body: QuestionnaireSubmit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "discovery_agent", need_trigger=True)
    for key, value in body.fields.items():
```

In `submit_known_changes` (currently starting at line 121), add as the first line of the function body:

```python
@router.post("/clients/{client_id}/tracking/known-changes")
async def submit_known_changes(
    client_id: UUID,
    body: KnownChangesSubmit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "tracking_access_agent", need_trigger=True)
    await log_event(
```

In `add_manual_competitor` (currently starting at line 145), add as the first line of the function body:

```python
@router.post("/clients/{client_id}/competitors/manual")
async def add_manual_competitor(
    client_id: UUID,
    body: ManualCompetitor,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "competitor_market_agent", need_trigger=True)
    cp = CompetitorProfile(
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_findings_permission_checks.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 6: Mark task complete**

---

### Task 9: Fix session IDOR — scope session lookups to the owning user

Every route that takes a `session_id` directly (`list_messages`, `post_message`, `post_message_stream`, `ws_chat`) fetches the session by ID only, never checking it belongs to the caller — even though `list_sessions` already scopes by `user_id`, proving ownership is the intended boundary.

**Files:**
- Modify: `backend/app/api/sessions.py`
- Modify: `backend/app/api/chat.py`
- Test: `backend/tests/test_session_idor.py`

**Interfaces:**
- No new interfaces — tightens existing `select(ChatSession).where(...)` queries in place.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_session_idor.py`:

```python
from app.models import Client, ClientDigitalProfile
from tests.conftest import make_user


async def _make_client(db_session) -> str:
    client = Client(
        legal_name="Test Co",
        display_name="Test Co",
        primary_url="https://example.com",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.commit()
    return str(client.id)


async def test_user_cannot_read_another_users_session_messages(api_client, db_session):
    client_id = await _make_client(db_session)
    _, owner_token = await make_user(db_session, "client_success_manager", "owner@test.local")
    _, other_token = await make_user(db_session, "client_success_manager", "other@test.local")

    create_resp = await api_client.post(
        "/api/v1/sessions",
        json={"client_id": client_id},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["id"]

    other_resp = await api_client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert other_resp.status_code == 404

    owner_resp = await api_client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert owner_resp.status_code == 200


async def test_user_cannot_post_message_into_another_users_session(api_client, db_session):
    client_id = await _make_client(db_session)
    _, owner_token = await make_user(db_session, "client_success_manager", "owner2@test.local")
    _, other_token = await make_user(db_session, "client_success_manager", "other2@test.local")

    create_resp = await api_client.post(
        "/api/v1/sessions",
        json={"client_id": client_id},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    session_id = create_resp.json()["id"]

    resp = await api_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "hello"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_session_idor.py -q`
Expected: both tests FAIL — `other_resp`/`resp` currently return 200 (or downstream errors), not 404.

- [ ] **Step 3: Fix `sessions.py`**

In `backend/app/api/sessions.py`, `list_messages` currently has:

```python
    session = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    ).scalar_one_or_none()
```

Change to:

```python
    session = (
        await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id, ChatSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
```

- [ ] **Step 4: Fix `chat.py`'s `post_message`**

Currently:

```python
    session = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    events = await process_chat_turn(db, session=session, user=user, content=body.content)
```

Change the query to:

```python
    session = (
        await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id, ChatSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    events = await process_chat_turn(db, session=session, user=user, content=body.content)
```

- [ ] **Step 5: Fix `chat.py`'s `post_message_stream`**

Currently:

```python
    async with AsyncSessionLocal() as db:
        session = (
            await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        ).scalar_one_or_none()
        if not session:
            raise HTTPException(404, "Session not found")
```

Change the query to:

```python
    async with AsyncSessionLocal() as db:
        session = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.id == session_id, ChatSession.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if not session:
            raise HTTPException(404, "Session not found")
```

- [ ] **Step 6: Fix `chat.py`'s `ws_chat`**

Currently, inside the `while True` loop:

```python
                session = (
                    await db.execute(select(ChatSession).where(ChatSession.id == session_id))
                ).scalar_one_or_none()
                if not user or not session:
```

Change to (note: this must come *after* `user` is resolved above it in the same block, and only queries with the ownership filter when `user` is truthy):

```python
                session = None
                if user:
                    session = (
                        await db.execute(
                            select(ChatSession).where(
                                ChatSession.id == session_id,
                                ChatSession.user_id == user.id,
                            )
                        )
                    ).scalar_one_or_none()
                if not user or not session:
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_session_idor.py -q`
Expected: `2 passed`.

- [ ] **Step 8: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 9: Mark task complete**

---

### Task 10: Bound inline agent execution with a timeout; fix misleading Celery stub status

Agent runs (crawls, LLM calls) execute synchronously inside the chat-turn request with no upper bound — a pathological run can block a worker indefinitely. The Celery task stubs also claim false status values that could mislead anyone reading logs/return values.

**Files:**
- Modify: `backend/app/orchestration/pipeline.py`
- Modify: `backend/app/tasks/jobs.py`
- Test: `backend/tests/test_pipeline_timeout.py`

**Interfaces:**
- No new public interfaces — `process_chat_turn`'s return shape (`list[dict]`) is unchanged; on timeout it now returns a single `{"type": "error", "content": ...}` event instead of hanging.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_pipeline_timeout.py`. This monkeypatches `AGENT_RUNNERS` with a runner that never finishes, and checks the pipeline returns an error event within a short bound rather than hanging (the test itself uses a short timeout via monkeypatching a module-level constant so the test doesn't have to wait 180 real seconds):

```python
import asyncio

import app.orchestration.pipeline as pipeline_module
from app.models import Client, ClientDigitalProfile, ChatSession


async def test_pipeline_times_out_instead_of_hanging(db_session, monkeypatch):
    client = Client(
        legal_name="Slow Co",
        display_name="Slow Co",
        primary_url="https://example.com",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    profile = ClientDigitalProfile(client_id=client.id)
    db_session.add(profile)
    from app.models import Role, User
    from sqlalchemy import select

    role = (
        await db_session.execute(select(Role).where(Role.name == "client_success_manager"))
    ).scalar_one()
    from app.security import hash_password

    user = User(
        email="slow-test@test.local",
        full_name="Slow Tester",
        role_id=role.id,
        hashed_password=hash_password("testpass123"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    session = ChatSession(client_id=client.id, user_id=user.id, active_agent_key="discovery_agent")
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    async def _never_finishes(db, *, client, session_id, user_id, message):
        await asyncio.sleep(9999)
        return []

    monkeypatch.setitem(pipeline_module.AGENT_RUNNERS, "discovery_agent", _never_finishes)
    monkeypatch.setattr(pipeline_module, "AGENT_TIMEOUT_SECONDS", 0.2)

    async def _fake_route(content, statuses):
        return "discovery_agent"

    monkeypatch.setattr(pipeline_module, "route_agent", _fake_route)

    events = await pipeline_module.process_chat_turn(
        db_session, session=session, user=user, content="run discovery"
    )
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "discovery_agent" in events[0]["content"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_pipeline_timeout.py -q --timeout=5`
Expected: FAIL — either hangs (if `pytest-timeout` isn't installed, it will hang; if it is, it errors) or errors with `AttributeError: module 'app.orchestration.pipeline' has no attribute 'AGENT_TIMEOUT_SECONDS'`. If the run hangs with no `pytest-timeout` plugin available, skip this verification sub-step and proceed directly to Step 3 — the implementation is unambiguous enough to write without watching this one hang.

- [ ] **Step 3: Implement the timeout in `pipeline.py`**

In `backend/app/orchestration/pipeline.py`, add near the top of the file (after the imports, before `async def process_chat_turn`):

```python
AGENT_TIMEOUT_SECONDS = 180
```

Add `import asyncio` to the imports at the top of the file.

Then, the current block:

```python
    session.active_agent_key = agent_key
    runner = AGENT_RUNNERS[agent_key]
    events = await runner(
        db,
        client=client,
        session_id=session.id,
        user_id=user.id,
        message=content,
    )
    await _persist_agent_events(db, session, client.id, events)
    return events
```

becomes:

```python
    session.active_agent_key = agent_key
    runner = AGENT_RUNNERS[agent_key]
    try:
        events = await asyncio.wait_for(
            runner(
                db,
                client=client,
                session_id=session.id,
                user_id=user.id,
                message=content,
            ),
            timeout=AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        events = [
            {
                "type": "error",
                "content": (
                    f"{agent_key} is taking longer than expected and was stopped after "
                    f"{AGENT_TIMEOUT_SECONDS}s. Try again or narrow the request."
                ),
            }
        ]
    await _persist_agent_events(db, session, client.id, events)
    return events
```

- [ ] **Step 4: Fix the misleading status strings in `jobs.py`**

In `backend/app/tasks/jobs.py`, change:

```python
@celery_app.task(name="app.tasks.jobs.site_crawl")
def site_crawl(client_id: str, session_id: str, url: str) -> dict:
    log.info("site_crawl_task", client_id=client_id, url=url)
    return {"status": "queued_inline_preferred", "url": url}
```

to:

```python
@celery_app.task(name="app.tasks.jobs.site_crawl")
def site_crawl(client_id: str, session_id: str, url: str) -> dict:
    log.info("site_crawl_task", client_id=client_id, url=url)
    return {"status": "not_dispatched_execution_is_inline", "url": url}
```

And change:

```python
@celery_app.task(name="app.tasks.jobs.recheck_unverified_tracking")
def recheck_unverified_tracking() -> dict:
    log.info("scheduled_recheck_unverified_tracking")
    return {"status": "scheduled"}
```

to:

```python
@celery_app.task(name="app.tasks.jobs.recheck_unverified_tracking")
def recheck_unverified_tracking() -> dict:
    log.info("scheduled_recheck_unverified_tracking_noop")
    return {"status": "not_implemented"}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_pipeline_timeout.py -q`
Expected: `1 passed` (runs in ~0.2s thanks to the monkeypatched `AGENT_TIMEOUT_SECONDS`).

- [ ] **Step 6: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 7: Mark task complete**

---

### Task 11: Fix the double-counted "Future Threat" competitor score

`growth_indicators` and `digital_presence` are each weighted twice in the `future_threat` formula (effectively 0.40 and 0.25) while other signals get none — almost certainly a copy/paste bug.

**Files:**
- Modify: `backend/app/ml/tier_competitors.py`
- Test: `backend/tests/test_tier_competitors_scoring.py`

**Interfaces:**
- No signature change — `future_threat` is a local variable inside the existing scoring function; the test targets the function that computes it (locate the exact enclosing function name by reading the ~40 lines around `backend/app/ml/tier_competitors.py:180-220` before writing the test — it is the function containing the `similarity =`, `maturity =`, `future_threat =`, `client_maturity =` block).

- [ ] **Step 1: Confirm the enclosing function name and signature**

Run: `cd backend && grep -n "^def \|^async def " app/ml/tier_competitors.py`
Read the output to find which function contains line ~201 (`future_threat = (`), and note its exact parameter list — use that exact signature in Step 2's test (do not guess it).

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_tier_competitors_scoring.py`. Replace `<function_name>` and `<its arguments>` below with what you found in Step 1 (call the function the same way `score_entity`/`score_entity_live`/`build_tiered_analysis` already call it elsewhere in the same file — check an existing call site for the correct argument shapes, e.g. `comp_scores`/`client_scores` dict shape produced by `score_entity`):

```python
from app.ml.tier_competitors import score_entity, <function_name>


def test_future_threat_does_not_double_count_params():
    client = score_entity("Client Co", "https://client.example", is_client=True)
    comp = score_entity("Rival Co", "https://rival.example")
    result = <function_name>(comp["scores"], client["scores"])
    # Weights must sum to 1.0 across 6 DISTINCT parameters, not 4 with 2 doubled.
    # This is asserted indirectly: force every param to the same score and confirm
    # future_threat scales linearly with a single weight-sum of 1.0 (i.e. equals
    # that score * 10 when every input param is identical).
    assert 0 <= result["future_threat"] <= 100
```

(If `<function_name>`'s return shape isn't a dict with a `"future_threat"` key, adjust the test to match whatever shape Step 1 revealed — read enough of the surrounding function body to get the real return statement before finalizing this test.)

- [ ] **Step 3: Run it to verify it fails or passes for the wrong reason**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_tier_competitors_scoring.py -q -v`
This test alone can't distinguish "double-counted" from "correct" without a more targeted assertion. Replace it with a targeted unit test that isolates the formula instead of going through the full function — write this test against a hand-built `comp_scores` dict where every parameter is a distinct known value, so the double-counted terms are mathematically visible:

```python
def test_future_threat_uses_six_distinct_params_not_four():
    """Every PARAMS key set to a distinct value 1..16; if growth_indicators or
    digital_presence is counted twice, future_threat will differ from the
    value computed with each of the 6 intended weights applied to a DIFFERENT param."""
    from app.ml.tier_competitors import PARAMS

    param_keys = [k for k, _, _ in PARAMS]
    # distinct value per param so any duplicate use is detectable
    comp_scores = {
        key: {"label": key, "score": (i % 10) + 1} for i, key in enumerate(param_keys)
    }
    client_scores = {
        key: {"label": key, "score": 5} for key in param_keys
    }
    result = <function_name>(comp_scores, client_scores)
    # brand_authority and ad_spend must now influence the result (they didn't before the fix)
    baseline = <function_name>(comp_scores, client_scores)
    bumped_scores = dict(comp_scores)
    bumped_scores["brand_authority"] = {"label": "brand_authority", "score": 10}
    bumped = <function_name>(bumped_scores, client_scores)
    assert bumped["future_threat"] != baseline["future_threat"]
```

- [ ] **Step 4: Run it, confirm it fails on today's code**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_tier_competitors_scoring.py -q -v`
Expected: FAIL — bumping `brand_authority` has no effect on `future_threat` today since it isn't in the formula.

- [ ] **Step 5: Fix the formula**

In `backend/app/ml/tier_competitors.py`, the current block:

```python
    future_threat = (
        s(comp_scores, "growth_indicators") * 0.30
        + s(comp_scores, "ai_adoption") * 0.20
        + s(comp_scores, "innovation") * 0.15
        + s(comp_scores, "digital_presence") * 0.15
        + s(comp_scores, "growth_indicators") * 0.10
        + s(comp_scores, "digital_presence") * 0.10
    ) * 10
```

becomes:

```python
    future_threat = (
        s(comp_scores, "growth_indicators") * 0.30
        + s(comp_scores, "ai_adoption") * 0.20
        + s(comp_scores, "innovation") * 0.15
        + s(comp_scores, "digital_presence") * 0.15
        + s(comp_scores, "brand_authority") * 0.10
        + s(comp_scores, "ad_spend") * 0.10
    ) * 10
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_tier_competitors_scoring.py -q`
Expected: `1 passed`.

- [ ] **Step 7: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 8: Mark task complete**

---

### Task 12: Fix N+1 queries in batch finding approval

`approve_phase_batch` re-queries `User` and `FindingsLedger` and re-runs `require_permission` once per row inside its loop — ~3N avoidable queries per batch. This adds a shared internal helper that does the DB-write logic once the caller already has the loaded `User`/`FindingsLedger`/permission check, and has the batch loop use it instead of calling the full `review_finding` per row.

**Files:**
- Modify: `backend/app/services/review.py`
- Test: `backend/tests/test_review_batch_query_count.py`

**Interfaces:**
- Produces: `app.services.review._apply_review(db: AsyncSession, *, user: User, ledger: FindingsLedger, action: str, edits: dict | None, note: str | None) -> FindingsLedger` — internal, not exported from the API layer.
- `review_finding` and `approve_phase_batch`'s existing public signatures are unchanged.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_review_batch_query_count.py`. This creates 3 pending findings for one client, approves them as a batch, and asserts both correctness and an upper bound on SQL statement count (loose bound, not exact, to avoid brittleness):

```python
from sqlalchemy import event, select

from app.db import engine
from app.models import Client, ClientDigitalProfile, DiscoveryResponse, FindingsLedger
from app.services.review import approve_phase_batch
from tests.conftest import make_user


async def _make_client_with_pending_findings(db_session, n: int) -> str:
    client = Client(
        legal_name="Batch Co",
        display_name="Batch Co",
        primary_url="https://example.com",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    for i in range(n):
        dr = DiscoveryResponse(
            client_id=client.id,
            source="client_questionnaire",
            field_key=f"field_{i}",
            field_value={"value": f"val_{i}"},
            confidence=None,
            discrepancy_flag=False,
            status="pending",
        )
        db_session.add(dr)
        await db_session.flush()
        db_session.add(
            FindingsLedger(
                client_id=client.id,
                agent_key="discovery_agent",
                source_table="discovery_responses",
                source_id=dr.id,
                confidence=None,
                status="pending",
            )
        )
    await db_session.commit()
    return str(client.id)


async def test_batch_approve_resolves_all_and_uses_bounded_query_count(db_session):
    client_id = await _make_client_with_pending_findings(db_session, 5)
    hod_user, _ = await make_user(db_session, "head_of_department", "hod-batch@test.local")

    statement_count = 0

    def _count(*args, **kwargs):
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        result = await approve_phase_batch(
            db_session,
            user=hod_user,
            client_id=client_id,
            agent_key="discovery_agent",
            action="approve",
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert result["resolved"] == 5
    # Old N+1 code issues ~3 queries per row (15) plus overhead; fixed code should
    # be well under that for 5 rows. Bound is loose on purpose — this is a
    # regression guard, not an exact-count assertion.
    assert statement_count < 20, f"expected a bounded query count, got {statement_count}"
```

- [ ] **Step 2: Run it and record the current statement count**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_review_batch_query_count.py -q -v -s`
Expected: passes or fails depending on where you set the bound — read the actual printed/assert failure count if it fails, confirm it's meaningfully above what Step 5 will achieve. If it already passes under 20, lower the bound in the test to `< 12` so the test is meaningful, then re-run to confirm it now fails — you need to see it fail before implementing the fix.

- [ ] **Step 3: Extract `_apply_review`**

In `backend/app/services/review.py`, the current `review_finding` function body (after the permission check) contains the action dispatch (`if action == "reject": ... elif action == "flag_for_client": ... elif action in ("approve", "edit"): ... else: raise ...` plus the trailing `await db.flush(); return ledger`). Extract everything from `if action == "reject":` through `return ledger` into a new function, and have `review_finding` call it:

Replace the whole `review_finding` function with:

```python
async def _apply_review(
    db: AsyncSession,
    *,
    user: User,
    ledger: FindingsLedger,
    action: str,
    edits: dict | None = None,
    note: str | None = None,
) -> FindingsLedger:
    if action == "reject":
        ledger.status = "rejected"
        ledger.resolved_at = datetime.now(timezone.utc)
        await _update_source_status(db, ledger, "rejected", user.id, edits)
        await _set_phase_status(db, ledger.client_id, ledger.agent_key, "in_progress")
        await log_event(
            db,
            client_id=ledger.client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="finding_rejected",
            event_detail={"ledger_id": str(ledger.id), "note": note},
        )
    elif action == "flag_for_client":
        if ledger.source_table != "tracking_audits":
            raise HTTPException(400, "flag_for_client only applies to tracking audits")
        ledger.status = "rejected"
        ledger.resolved_at = datetime.now(timezone.utc)
        await db.execute(
            update(TrackingAudit)
            .where(TrackingAudit.id == ledger.source_id)
            .values(status="flagged_for_client", reviewed_by=user.id)
        )
        await log_event(
            db,
            client_id=ledger.client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="finding_flagged",
            event_detail={"ledger_id": str(ledger.id), "note": note},
        )
    elif action in ("approve", "edit"):
        status = "edited" if action == "edit" else "approved"
        ledger.status = status
        ledger.resolved_at = datetime.now(timezone.utc)
        await _update_source_status(db, ledger, status, user.id, edits)
        await log_event(
            db,
            client_id=ledger.client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="finding_edited" if action == "edit" else "finding_approved",
            event_detail={"ledger_id": str(ledger.id), "edits": edits, "note": note},
        )
    else:
        raise HTTPException(400, f"Unknown action {action}")

    await db.flush()
    return ledger


async def review_finding(
    db: AsyncSession,
    *,
    user: User,
    ledger_id: UUID,
    action: str,
    edits: dict | None = None,
    note: str | None = None,
) -> FindingsLedger:
    user = (
        await db.execute(select(User).options(selectinload(User.role)).where(User.id == user.id))
    ).scalar_one()
    ledger = (
        await db.execute(select(FindingsLedger).where(FindingsLedger.id == ledger_id))
    ).scalar_one_or_none()
    if not ledger:
        raise HTTPException(404, "Finding not found")

    await require_permission(user, db, ledger.agent_key, need_approve=True)
    return await _apply_review(db, user=user, ledger=ledger, action=action, edits=edits, note=note)
```

- [ ] **Step 4: Update `approve_phase_batch`'s loop**

Change the loop body in `approve_phase_batch` from:

```python
    for ledger in ledgers:
        await review_finding(
            db,
            user=user,
            ledger_id=ledger.id,
            action="approve" if action == "approve" else action,
            edits=edits if action == "edit" else None,
            note=note,
        )
```

to:

```python
    for ledger in ledgers:
        await _apply_review(
            db,
            user=user,
            ledger=ledger,
            action="approve" if action == "approve" else action,
            edits=edits if action == "edit" else None,
            note=note,
        )
```

(The permission check and `User` load already happen once at the top of `approve_phase_batch` via `await require_permission(user, db, agent_key, need_approve=True)` — the caller-supplied `user` there is already the one loaded by `get_current_user`, which already has `.role` selectin-loaded, so no additional `User` re-fetch is needed here.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_review_batch_query_count.py -q`
Expected: `1 passed`.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 7: Mark task complete**

---

### Task 13: Unify the two duplicate value-renderers (frontend)

`PresentableValue.tsx` and `PlaygroundBlocks.tsx` independently reimplement the same null/boolean/number/string/array/object dispatch logic, and only `PresentableValue` has the numeric "split-bars" branch for all-numeric objects — the same score-breakdown data renders differently in different views.

**Files:**
- Modify: `frontend/src/components/cards/PresentableValue.tsx`
- Modify: `frontend/src/components/PlaygroundBlocks.tsx`

**Interfaces:**
- Produces: `PresentableValue.tsx` exports an additional named export `StructuredValue({ value, classPrefix }: { value: unknown; classPrefix?: string })` alongside its existing default export (verify the existing default export name/signature by reading the file before editing — do not guess it).
- `PlaygroundBlocks.tsx`'s existing default export signature is unchanged; its internals now delegate to `StructuredValue`.

- [ ] **Step 1: Read both files in full before editing**

Read `frontend/src/components/cards/PresentableValue.tsx` and `frontend/src/components/PlaygroundBlocks.tsx` completely. Note the exact current default export name of `PresentableValue.tsx` (likely `PresentableValue`), its exact prop type, and the exact CSS class names it emits (prefixed `pv-`) versus `PlaygroundBlocks.tsx`'s `BlockValue` internal function (prefixed `pg-`). Confirm the "split-bars" percentage-object branch's exact JSX so it can be reused verbatim, just with a parameterized class prefix.

- [ ] **Step 2: Parameterize `PresentableValue.tsx` by class prefix**

Rename the internal recursive renderer function (keep the existing default-exported component name and prop shape unchanged for every existing caller) so it accepts a `classPrefix` parameter defaulting to `"pv"`, and thread that prefix through every `pv-*` class name in the file (e.g. `` `${classPrefix}-row` `` instead of `"pv-row"`). Export this renderer as a new named export `StructuredValue`:

```ts
export function StructuredValue({
  value,
  classPrefix = "pv",
}: {
  value: unknown;
  classPrefix?: string;
}) {
  // ... existing dispatch body, with every literal "pv-..." class name
  // replaced by `${classPrefix}-...` template strings
}
```

Keep the existing default export (`PresentableValue` or whatever it's actually named) as a thin wrapper that calls `StructuredValue` with `classPrefix="pv"`, so every existing caller of the default export is unaffected.

- [ ] **Step 3: Add matching `pg-*` CSS rules if they don't already exist**

Check `frontend/src/styles.css` and `frontend/src/landing.css` for existing `.pg-*` class rules (PlaygroundBlocks' current styling). If `PlaygroundBlocks.tsx` today defines its own `.pg-*` CSS that visually differs from `.pv-*` (e.g. no bar-chart styling for the numeric-object branch, since that branch didn't exist in `PlaygroundBlocks` before this change), add the missing `.pg-*` bar-chart rules mirroring the existing `.pv-*` ones so the new branch renders correctly in the Playground view too. Copy the exact `.pv-*` bar-chart rule block and duplicate it with `.pg-` prefixes.

- [ ] **Step 4: Replace `PlaygroundBlocks.tsx`'s internal `BlockValue` with `StructuredValue`**

Replace the internal recursive dispatch logic in `PlaygroundBlocks.tsx` (the `BlockValue` function and its call sites) with calls to `StructuredValue` imported from `./cards/PresentableValue`, passing `classPrefix="pg"`:

```ts
import { StructuredValue } from "./cards/PresentableValue";

export default function PlaygroundBlocks({ value }: { value: unknown }) {
  return (
    <div className="pg-blocks">
      <StructuredValue value={value} classPrefix="pg" />
    </div>
  );
}
```

Keep any `PlaygroundBlocks`-specific wrapper markup (the outer `<div className="pg-blocks">` or equivalent) that isn't part of the recursive value-rendering logic itself — only the recursive dispatch (`BlockValue`) is being replaced, not the whole component.

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: exits 0. Fix any type errors from the refactor before proceeding.

- [ ] **Step 6: Manual visual check (if a dev environment is available)**

If Postgres/Redis are running locally and `npm run dev` can be started, open a client's Playground view and a review card that shows an all-numeric score breakdown (e.g. a competitor tier score), and confirm both now render the same bar-chart style. If a local dev environment isn't available in this session, note that this step was skipped and should be done before merging.

- [ ] **Step 7: Mark task complete**

---

### Task 14: Unify the three divergent status→tone implementations (frontend)

`httpStatusLabel.ts`, `PlaygroundBlocks.tsx`, and `TrackingCard.tsx` each have their own `statusTone`/`httpStatusTone` function with different rule sets — `"pending_setup"` maps differently between them.

**Files:**
- Create: `frontend/src/lib/statusTone.ts`
- Modify: `frontend/src/lib/httpStatusLabel.ts`
- Modify: `frontend/src/components/PlaygroundBlocks.tsx`
- Modify: `frontend/src/components/cards/TrackingCard.tsx`

**Interfaces:**
- Produces: `statusTone(status: string | null | undefined): string` in the new `frontend/src/lib/statusTone.ts` — the single shared implementation, covering the union of every status string handled by the 3 existing implementations (read all 3 before writing this file, so no case is silently dropped).

- [ ] **Step 1: Read all 3 existing implementations in full**

Read `frontend/src/lib/httpStatusLabel.ts:43-52`, `frontend/src/components/PlaygroundBlocks.tsx:26-33`, and `frontend/src/components/cards/TrackingCard.tsx:286-290` in full. List every distinct status string and every tone value each one currently produces for it (including the substring-matching behavior in `PlaygroundBlocks.tsx` vs exact-match in `TrackingCard.tsx`) — you need this list to write a merged function that doesn't silently change behavior for a status string that isn't `"pending_setup"` (the one case already known to diverge).

- [ ] **Step 2: Design the merged rule set**

Using the audit's finding as the deciding case: `"pending_setup"` should map to `"warning"` (matching `TrackingCard.tsx`'s explicit mapping, which is the more specific exact-match rule — prefer exact-match rules over substring-matching heuristics when they conflict). For every other status string found in Step 1 that isn't in conflict, carry its existing tone forward. Write `frontend/src/lib/statusTone.ts`:

```ts
export type StatusTone = "positive" | "warning" | "danger" | "neutral";

export function statusTone(status: string | null | undefined): StatusTone {
  const key = (status || "").trim().toLowerCase();
  if (key === "connected" || key === "pass" || key === "approved" || key === "complete") {
    return "positive";
  }
  if (key === "pending_setup" || key === "warning" || key === "pending" || key === "in_progress") {
    return "warning";
  }
  if (key === "fail" || key === "failed" || key === "blocked" || key === "rejected") {
    return "danger";
  }
  return "neutral";
}
```

(This starting rule set must be reconciled against the full list from Step 1 — add any status string found there that isn't already covered above, placing it in whichever bucket matches its *current* majority behavior across the 3 files, except `pending_setup` which is deliberately being changed to `"warning"` everywhere per the audit finding.)

- [ ] **Step 3: Point `httpStatusLabel.ts` at the shared function**

In `frontend/src/lib/httpStatusLabel.ts`, remove the local `httpStatusTone` function body and replace it with a re-export (keep the existing exported name so every caller of `httpStatusTone` is unaffected):

```ts
export { statusTone as httpStatusTone } from "./statusTone";
```

- [ ] **Step 4: Point `PlaygroundBlocks.tsx` at the shared function**

Remove the local `statusTone` function from `frontend/src/components/PlaygroundBlocks.tsx` and add:

```ts
import { statusTone } from "../lib/statusTone";
```

(adjust the relative path to match the file's actual location relative to `lib/`).

- [ ] **Step 5: Point `TrackingCard.tsx` at the shared function**

Remove the local `statusTone` function from `frontend/src/components/cards/TrackingCard.tsx` and add:

```ts
import { statusTone } from "../../lib/statusTone";
```

(adjust the relative path to match the file's actual location relative to `lib/`).

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: exits 0.

- [ ] **Step 7: Mark task complete**

---

### Task 15: Add error handling to `onGrant` (frontend)

Every other action handler in `ChatPage.tsx` wraps its API call in try/catch and sets an error banner on failure; `onGrant` doesn't, so a failed OAuth mock-grant silently does nothing visible.

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`

**Interfaces:**
- No new interfaces — matches the existing `setError` pattern already used by every sibling handler in the same file (read one sibling handler, e.g. `onCardAction`, before editing, to match its exact error-message-extraction idiom).

- [ ] **Step 1: Read the current `onGrant` and one sibling handler**

Read `frontend/src/pages/ChatPage.tsx:490-502` (`onGrant`) and a nearby sibling handler such as `onSubmitQuestionnaire` or `onSubmitKnownChanges` in full, to copy their exact try/catch and `setError` idiom (e.g. whether they check `e instanceof Error`, what fallback message string they use).

- [ ] **Step 2: Wrap `onGrant` in the same pattern**

The current function:

```ts
async function onGrant(provider: string) {
  if (!token || !clientId || !sessionId) return;
  await api.mockGrant(token, clientId, provider);
  setMessages((m) => [...]);
  await send(`Re-check tracking now that ${provider} access is granted`);
}
```

becomes (matching whatever exact `setError`/message-fallback idiom Step 1 found in a sibling — this is the pattern seen elsewhere in the file, adjust the fallback string if the sibling uses different wording):

```ts
async function onGrant(provider: string) {
  if (!token || !clientId || !sessionId) return;
  try {
    await api.mockGrant(token, clientId, provider);
    setMessages((m) => [...]);
    await send(`Re-check tracking now that ${provider} access is granted`);
  } catch (e) {
    setError(e instanceof Error ? e.message : "Failed to grant access");
  }
}
```

(Keep the actual body of the `setMessages` call and the `send(...)` call byte-identical to what's in the file today — only the try/catch wrapper and the `setError` line are new.)

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: exits 0.

- [ ] **Step 4: Mark task complete**

---

## Final verification (after all 15 tasks)

- [ ] Run `cd backend && .venv/Scripts/python.exe -m pytest -q` — expect all tests passing, 0 errors.
- [ ] Run `cd frontend && npx tsc -b --noEmit` — expect exit 0.
- [ ] Run `cd frontend && npm run build` — expect a successful build with output in `frontend/dist/`.
- [ ] Re-read `backend/app/api/findings.py`, `backend/app/api/sessions.py`, `backend/app/api/chat.py`, `backend/app/config.py`, `backend/app/seed.py`, `backend/app/services/role_skills.py`, `backend/app/api/auth.py`, `backend/app/integrations/web_fetch.py`, `backend/app/orchestration/pipeline.py`, `backend/app/ml/tier_competitors.py`, `backend/app/services/review.py` end-to-end once more to confirm no task's edit was left half-applied.
