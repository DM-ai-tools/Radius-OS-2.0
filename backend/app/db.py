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
    await ensure_client_memory_retention_columns()
    await ensure_operations_tables()
    await ensure_hot_path_indexes()


async def ensure_client_memory_retention_columns() -> None:
    """Add onboarding/archive columns used by client-memory retention."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        dialect = conn.dialect.name
        bool_true = "TRUE" if dialect == "postgresql" else "1"
        bool_false = "FALSE" if dialect == "postgresql" else "0"
        statements = [
            (
                "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
                f"is_onboarding BOOLEAN DEFAULT {bool_true}"
            ),
            (
                "ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS "
                f"is_onboarding BOOLEAN DEFAULT {bool_true}"
            ),
            (
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
                f"is_onboarding BOOLEAN DEFAULT {bool_false}"
            ),
            "ALTER TABLE client_digital_profiles ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE",
        ]
        for stmt in statements:
            sql = stmt
            if dialect == "sqlite":
                sql = (
                    sql.replace(" IF NOT EXISTS", "")
                    .replace(" BOOLEAN", " INTEGER")
                    .replace(" TIMESTAMP WITH TIME ZONE", "")
                )
            try:
                await conn.execute(text(sql))
            except Exception:  # noqa: BLE001
                pass
        try:
            await conn.execute(
                text(
                    "UPDATE clients SET is_onboarding = "
                    "CASE WHEN LOWER(status) = 'onboarding' THEN true ELSE false END "
                    "WHERE is_onboarding IS NULL OR is_onboarding = true"
                )
                if dialect == "postgresql"
                else text(
                    "UPDATE clients SET is_onboarding = "
                    "CASE WHEN LOWER(status) = 'onboarding' THEN 1 ELSE 0 END"
                )
            )
        except Exception:  # noqa: BLE001
            pass
        for name, table, column in (
            ("ix_clients_is_onboarding", "clients", "is_onboarding"),
            (
                "ix_client_digital_profiles_is_onboarding",
                "client_digital_profiles",
                "is_onboarding",
            ),
            ("ix_chat_sessions_is_onboarding", "chat_sessions", "is_onboarding"),
            ("ix_client_digital_profiles_archived_at", "client_digital_profiles", "archived_at"),
            ("ix_chat_sessions_archived_at", "chat_sessions", "archived_at"),
            ("ix_chat_messages_archived_at", "chat_messages", "archived_at"),
            ("ix_agent_jobs_archived_at", "agent_jobs", "archived_at"),
        ):
            try:
                await conn.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
                )
            except Exception:  # noqa: BLE001
                pass


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


async def ensure_hot_path_indexes() -> None:
    """Add indexes on FK columns filtered in hot read paths (readiness, chat,
    review) that predate this fix — create_all() only creates missing
    tables, it never adds an index to a table that already exists."""
    from sqlalchemy import text

    indexes = [
        ("ix_discovery_responses_client_id", "discovery_responses", "client_id"),
        ("ix_tracking_audits_client_id", "tracking_audits", "client_id"),
        ("ix_website_audits_client_id", "website_audits", "client_id"),
        ("ix_competitor_profiles_client_id", "competitor_profiles", "client_id"),
        ("ix_backlink_snapshots_client_id", "backlink_snapshots", "client_id"),
        (
            "ix_backlink_snapshots_competitor_profile_id",
            "backlink_snapshots",
            "competitor_profile_id",
        ),
        ("ix_chat_sessions_client_id", "chat_sessions", "client_id"),
        ("ix_chat_sessions_user_id", "chat_sessions", "user_id"),
        ("ix_agent_jobs_session_id", "agent_jobs", "session_id"),
        ("ix_api_credentials_client_id", "api_credentials", "client_id"),
        ("ix_readiness_scores_client_id", "readiness_scores", "client_id"),
    ]
    async with engine.begin() as conn:
        for name, table, column in indexes:
            try:
                await conn.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
                )
            except Exception:  # noqa: BLE001
                pass


async def ensure_governance_columns() -> None:
    """Ensure findings_ledger.created_by exists (Architecture v1.9 author≠reviewer)."""
    await ensure_phase56_columns()
