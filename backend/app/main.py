from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, chat, clients, cost_tracker, engine_room, findings, integrations, oauth, readiness, sessions, technical_seo, workbook
from app.config import get_settings
from app.db import Base, AsyncSessionLocal, engine
from app.logging_config import get_logger, setup_logging
from app.seed import seed_all

# Ensure models are registered
import app.models  # noqa: F401

setup_logging()
log = get_logger("main")
settings = get_settings()


def _resolve_static_dir() -> Path | None:
    """Return SPA build dir when present (production Docker); None in API-only/dev.

    In local development, auto-serving ``frontend/dist`` causes 404s on hashed
    Vite assets when the live UI is on the Vite dev server (5173). Only serve
    static when ``STATIC_DIR`` is set or ``ENVIRONMENT=production``.
    """
    candidates: list[Path] = []
    if settings.static_dir:
        candidates.append(Path(settings.static_dir))
    if settings.environment == "production":
        candidates.extend(
            [
                Path("/app/static"),
                Path(__file__).resolve().parent.parent / "static",
                Path(__file__).resolve().parents[2] / "frontend" / "dist",
            ]
        )
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir() and (path / "index.html").is_file():
            return path
    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio

    # Railway Postgres/Redis may not be reachable on the first instant after deploy.
    last_err: Exception | None = None
    for attempt in range(1, 21):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            from app.db import ensure_phase56_columns

            await ensure_phase56_columns()
            async with AsyncSessionLocal() as db:
                await seed_all(db, seed_demo_data=settings.environment != "production")
                await db.commit()
            log.info(
                "startup_complete",
                database=settings.database_url.split("://")[0],
                attempt=attempt,
            )
            last_err = None
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("db_startup_retry", attempt=attempt, error=str(exc))
            await asyncio.sleep(3)
    if last_err is not None:
        # Still bind HTTP so Railway healthchecks get a response (see /health).
        log.error("db_startup_failed_continuing", error=str(last_err))

    static = _resolve_static_dir()
    if static is not None:
        log.info("spa_static_enabled", path=str(static))
    else:
        log.warning(
            "spa_static_missing",
            hint="Root Dockerfile must copy frontend/dist to /app/static",
        )
    yield
    await engine.dispose()


app = FastAPI(
    title="Radius OS Onboarding & Audit Agent",
    description="Phase 1–6 orchestration API",
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

prefix = "/api/v1"
app.include_router(auth.router, prefix=prefix)
app.include_router(clients.router, prefix=prefix)
app.include_router(sessions.router, prefix=prefix)
app.include_router(findings.router, prefix=prefix)
app.include_router(oauth.router, prefix=prefix)
app.include_router(integrations.router, prefix=prefix)
app.include_router(readiness.router, prefix=prefix)
app.include_router(chat.router, prefix=prefix)
app.include_router(engine_room.router, prefix=prefix)
app.include_router(cost_tracker.router, prefix=prefix)
app.include_router(technical_seo.router, prefix=prefix)
app.include_router(workbook.router, prefix=prefix)


@app.get("/health")
@app.get("/healthz")
async def health():
    """Liveness probe — always HTTP 200 once the process is listening.

    Postgres/Redis status is reported in the body for diagnostics; Railway only
    needs a successful HTTP response to mark the replica healthy.
    """
    import asyncio

    from sqlalchemy import text

    from app.services.cache import get_redis

    db_ok = False
    redis_ok = False

    async def _check_db() -> bool:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    try:
        db_ok = await asyncio.wait_for(_check_db(), timeout=3)
    except Exception:  # noqa: BLE001
        db_ok = False
    try:
        # Sync Redis must not block the event loop (socket timeouts on client).
        r = await asyncio.to_thread(get_redis)
        redis_ok = bool(r and await asyncio.to_thread(r.ping))
    except Exception:  # noqa: BLE001
        redis_ok = False
    static = _resolve_static_dir()
    status = "ok" if db_ok and redis_ok else "degraded"
    return {
        "status": status,
        "service": "radius-os-phase1-6",
        "build": "ungated-phase56",
        "postgres": db_ok,
        "redis": redis_ok,
        "spa": static is not None,
        "static_dir": str(static) if static else None,
    }


_API_PREFIXES = {
    "api",
    "health",
    "healthz",
    "docs",
    "redoc",
    "openapi.json",
    "assets",
    "media",
}


@app.get("/")
async def spa_or_api_root():
    static = _resolve_static_dir()
    if static is not None:
        return FileResponse(static / "index.html")
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Radius OS</title></head><body style='font-family:system-ui;padding:2rem'>"
        "<h1>Radius OS API</h1>"
        "<p>The web UI bundle is not in this container (<code>/app/static</code> missing).</p>"
        "<p>In Railway: set the service to use the <strong>root Dockerfile</strong> "
        "(not <code>backend/Dockerfile</code>), Root Directory = <code>/</code>.</p>"
        "<p><a href='/docs'>Open API docs</a> · <a href='/healthz'>Health</a></p>"
        "</body></html>",
        status_code=200,
    )


# Mount hashed Vite assets when present (resolved once at import; Docker image has them).
_static_dir = _resolve_static_dir()
if _static_dir is not None:
    _assets = _static_dir / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="spa-assets")
    log.info("spa_static_enabled", path=str(_static_dir))

_draft_media = Path(__file__).resolve().parents[1] / "data" / "draft_images"
_draft_media.mkdir(parents=True, exist_ok=True)
app.mount("/media/drafts", StaticFiles(directory=_draft_media), name="draft-images")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    head = full_path.split("/", 1)[0]
    if head in _API_PREFIXES:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")
    static = _resolve_static_dir()
    if static is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")
    candidate = static / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(static / "index.html")
