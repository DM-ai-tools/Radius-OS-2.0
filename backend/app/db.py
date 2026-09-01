from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine_kwargs: dict = {"echo": False}
if settings.is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
elif "postgresql" in settings.database_url or "postgres" in settings.database_url:
    # Fail faster when Postgres is wedged or unreachable (avoids endless "Please wait…")
    engine_kwargs["connect_args"] = {"timeout": 10, "command_timeout": 15}
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_timeout"] = 10
    engine_kwargs["pool_recycle"] = 300

engine = create_async_engine(settings.database_url, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ensure_phase56_columns() -> None:
    """Add Phase 5/6 CDP columns on existing DBs (create_all won't alter)."""
    from sqlalchemy import text

    # SQLite before 3.35 lacks IF NOT EXISTS for ADD COLUMN — try/ignore duplicates
    async with engine.begin() as conn:
        dialect = conn.dialect.name
        json_type = "JSONB" if dialect == "postgresql" else "JSON"
        statements = [
            f"ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS search_demand_summary {json_type}",
            f"ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS seo_strategy_summary {json_type}",
            f"ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS site_architecture_summary {json_type}",
            (
                "ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS "
                "search_demand_status TEXT DEFAULT 'not_started'"
            ),
            (
                "ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS "
                "seo_strategy_status TEXT DEFAULT 'not_started'"
            ),
            (
                "ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS "
                "site_architecture_status TEXT DEFAULT 'not_started'"
            ),
        ]
        for stmt in statements:
            sql = stmt
            if dialect == "sqlite":
                sql = sql.replace(" IF NOT EXISTS", "").replace(" JSON", " TEXT")
            try:
                await conn.execute(text(sql))
            except Exception:  # noqa: BLE001
                # Column already exists or unsupported dialect nuance
                pass
        # Architecture v1.9 — author ≠ reviewer
        try:
            sql = "ALTER TABLE findings_ledger ADD COLUMN IF NOT EXISTS created_by UUID"
            if dialect == "sqlite":
                sql = "ALTER TABLE findings_ledger ADD COLUMN created_by TEXT"
            await conn.execute(text(sql))
        except Exception:  # noqa: BLE001
            pass

    await ensure_phase712_columns()
    await ensure_operations_tables()


async def ensure_operations_tables() -> None:
    """api_usage_log for cost tracking / engine room."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        dialect = conn.dialect.name
        json_type = "JSONB" if dialect == "postgresql" else "JSON"
        statements = [
            f"""
            CREATE TABLE IF NOT EXISTS api_usage_log (
                id {'UUID' if dialect == 'postgresql' else 'TEXT'} PRIMARY KEY,
                client_id {'UUID' if dialect == 'postgresql' else 'TEXT'},
                session_id {'UUID' if dialect == 'postgresql' else 'TEXT'},
                agent_key TEXT,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                estimated_cost_usd NUMERIC(12, 6),
                latency_ms INTEGER,
                status TEXT NOT NULL DEFAULT 'success',
                error_detail TEXT,
                request_meta {json_type},
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """.strip(),
        ]
        if dialect == "sqlite":
            # create_all handles sqlite; skip raw DDL
            return
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
            except Exception:  # noqa: BLE001
                pass


async def ensure_phase712_columns() -> None:
    """Add Phase 7–12 CDP columns on existing DBs (create_all won't alter)."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        dialect = conn.dialect.name
        json_type = "JSONB" if dialect == "postgresql" else "JSON"
        summaries = (
            "technical_seo_summary",
            "content_audit_summary",
            "content_planning_summary",
            "content_production_summary",
            "on_page_seo_summary",
            "publishing_summary",
        )
        statuses = (
            "technical_seo_status",
            "content_audit_status",
            "content_planning_status",
            "content_production_status",
            "on_page_seo_status",
            "publishing_status",
        )
        statements = [
            *[
                f"ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS {col} {json_type}"
                for col in summaries
            ],
            *[
                (
                    "ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS "
                    f"{col} TEXT DEFAULT 'not_started'"
                )
                for col in statuses
            ],
        ]
        for stmt in statements:
            sql = stmt
            if dialect == "sqlite":
                sql = sql.replace(" IF NOT EXISTS", "").replace(" JSON", " TEXT")
            try:
                await conn.execute(text(sql))
            except Exception:  # noqa: BLE001
                pass


async def ensure_governance_columns() -> None:
    """Ensure findings_ledger.created_by exists (Architecture v1.9 author≠reviewer)."""
    await ensure_phase56_columns()
