from __future__ import annotations

import os
from pathlib import Path

TEST_DB_PATH = Path(__file__).parent / "test_searchfit.db"
if TEST_DB_PATH.exists():
    try:
        TEST_DB_PATH.unlink()
    except OSError:
        # Another pytest worker or local process may still hold the SQLite file open.
        pass

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
os.environ["SECRET_KEY"] = "test-suite-secret-key-not-a-real-default-value"
os.environ["ENCRYPTION_KEY"] = "test-suite-encryption-key-not-a-real-default"
os.environ["AUTH_DISABLED"] = "false"
os.environ["USE_MOCK_LLM"] = "true"
os.environ["USE_MOCK_PROVIDERS"] = "true"
os.environ["ENVIRONMENT"] = "development"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import clear_settings_cache
from app.db import AsyncSessionLocal, Base, engine
from app.main import app
from app.models import Role, User
from app.security import create_access_token, hash_password
from app.seed import seed_all

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
