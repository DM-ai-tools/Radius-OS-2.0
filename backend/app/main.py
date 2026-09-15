from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

# Ensure models are registered with Base.metadata before create_all runs below.
# Imported for the side effect only — never delete as "unused".
import app.models  # noqa: F401
from app.api import (
    auth,
    chat,
    clients,
    cost_tracker,
    engine_room,
    findings,
    integrations,
    oauth,
    ops,
    readiness,
    sessions,
    technical_seo,
    workbook,
)
from app.config import get_settings
from app.db import AsyncSessionLocal, Base, engine
from app.logging_config import get_logger, setup_logging
from app.seed import seed_all
from app.services.public_seo import build_robots_txt, build_sitemap_xml

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


@app.middleware("http")
async def _security_headers(request, call_next):
    """Baseline hardening headers. No CSP here on purpose — this app also
    serves the SPA build, and a wrong CSP silently breaks the frontend in a
    way this pass can't visually verify; the headers below are safe defaults
    with no such risk."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response


_DEAD_MAN_ALLOW_PREFIXES = (
    "/health",
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/assets",
    "/media",
    "/robots.txt",
    "/sitemap.xml",
    "/api/v1/ops/dead-man-switch/",
)


@app.middleware("http")
async def _dead_man_switch_guard(request, call_next):
    """If the operational dead man's switch has tripped, freeze API traffic.

    Health, docs, SPA assets, and the check-in/status endpoints stay reachable
    so operators can still revive the deployment. No data is deleted.
    """
    from fastapi.responses import JSONResponse

    path = request.url.path or "/"
    if path == "/" or any(path == p or path.startswith(p) for p in _DEAD_MAN_ALLOW_PREFIXES):
        return await call_next(request)
    # Only gate API — SPA deep links still get index.html from the catch-all.
    if not path.startswith("/api/"):
        return await call_next(request)

    from app.services.dead_man_switch import get_status, is_tripped

    if not await is_tripped():
        return await call_next(request)

    status = await get_status()
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "dead_man_switch_tripped",
                "message": (
                    "This deployment is locked: the dead man's switch timer expired. "
                    "An operator must POST /api/v1/ops/dead-man-switch/check-in "
                    "with DEAD_MAN_SWITCH_TOKEN to restore service."
                ),
                "expires_at": status.get("expires_at"),
                "checked_at": status.get("checked_at"),
            }
        },
        headers={"Retry-After": "3600"},
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
app.include_router(ops.router, prefix=prefix)


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
        # get_redis() is async and already offloads its own connect+ping via
        # asyncio.to_thread — only the extra explicit ping below needs it here.
        r = await get_redis()
        redis_ok = bool(r and await asyncio.to_thread(r.ping))
    except Exception:  # noqa: BLE001
        redis_ok = False
    static = _resolve_static_dir()
    status = "ok" if db_ok and redis_ok else "degraded"
    dms: dict = {"enabled": False, "tripped": False}
    try:
        from app.services.dead_man_switch import get_status as dms_status

        dms = await dms_status()
        if dms.get("tripped"):
            status = "locked"
    except Exception:  # noqa: BLE001
        pass
    return {
        "status": status,
        "service": "radius-os-phase1-6",
        "build": "ungated-phase56",
        "postgres": db_ok,
        "redis": redis_ok,
        "spa": static is not None,
        "static_dir": str(static) if static else None,
        "dead_man_switch": {
            "enabled": bool(dms.get("enabled")),
            "tripped": bool(dms.get("tripped")),
            "days_remaining": dms.get("days_remaining"),
            "expires_at": dms.get("expires_at"),
        },
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
    "robots.txt",
    "sitemap.xml",
}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Crawl policy — public landing only; private workspace/API disallowed."""
    return PlainTextResponse(
        build_robots_txt(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sitemap.xml")
async def sitemap_xml():
    """XML sitemap with the single public canonical URL."""
    return Response(
        content=build_sitemap_xml(),
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
        "<p><a href='/docs'>Open API docs</a> · <a href='/healthz'>Health</a> · "
        "<a href='/robots.txt'>robots.txt</a> · <a href='/sitemap.xml'>sitemap</a></p>"
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
