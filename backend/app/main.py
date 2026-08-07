from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat, clients, findings, oauth, readiness, sessions
from app.config import get_settings
from app.db import Base, AsyncSessionLocal, engine
from app.logging_config import get_logger, setup_logging
from app.seed import seed_all

# Ensure models are registered
import app.models  # noqa: F401

setup_logging()
log = get_logger("main")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Create tables (Alembic for Postgres; create_all works for SQLite local)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await seed_all(db, seed_demo_data=settings.environment != "production")
        await db.commit()
    log.info("startup_complete", database=settings.database_url.split("://")[0])
    yield
    await engine.dispose()


app = FastAPI(
    title="Radius OS Onboarding & Audit Agent",
    description="Phase 1–4 orchestration API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = FastAPI()  # unused — routers mounted directly
prefix = "/api/v1"
app.include_router(auth.router, prefix=prefix)
app.include_router(clients.router, prefix=prefix)
app.include_router(sessions.router, prefix=prefix)
app.include_router(findings.router, prefix=prefix)
app.include_router(oauth.router, prefix=prefix)
app.include_router(readiness.router, prefix=prefix)
app.include_router(chat.router, prefix=prefix)


@app.get("/health")
async def health():
    from sqlalchemy import text

    from app.services.cache import get_redis

    db_ok = False
    redis_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    try:
        r = get_redis()
        redis_ok = bool(r and r.ping())
    except Exception:  # noqa: BLE001
        redis_ok = False
    status = "ok" if db_ok and redis_ok else "degraded"
    return {
        "status": status,
        "service": "radius-os-phase1-4",
        "postgres": db_ok,
        "redis": redis_ok,
    }
