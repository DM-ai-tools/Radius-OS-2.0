from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import asyncpg_engine_settings, get_settings

settings = get_settings()
database_url, _ssl_connect_args = asyncpg_engine_settings(settings.database_url)

engine_kwargs: dict = {"echo": False}
if settings.is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
elif "postgresql" in database_url or "postgres" in database_url:
    # Fail faster when Postgres is wedged or unreachable (avoids endless "Please wait…")
    engine_kwargs["connect_args"] = {
        "timeout": 10,
        "command_timeout": 15,
        **_ssl_connect_args,
    }
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_timeout"] = 10
    engine_kwargs["pool_recycle"] = 300

engine = create_async_engine(database_url, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

if "postgresql" in database_url or "postgres" in database_url:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _register_pgvector_codec(dbapi_connection, _connection_record):
        """Teach asyncpg the vector type once the extension exists."""
        try:
            from pgvector.asyncpg import register_vector

            dbapi_connection.run_async(register_vector)
        except Exception:  # noqa: BLE001 — extension not created yet, or sqlite-shaped driver
            return


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


EMBEDDING_DIMENSION = 768


def embedding_fits_vector(value, dimension: int = EMBEDDING_DIMENSION) -> bool:
    """True when this stored embedding can become one vector without dropping it."""
    if value is None:
        return True
    if isinstance(value, str):
        text_value = value.strip()
        if not (text_value.startswith("[") and text_value.endswith("]")):
            return False
        import json

        try:
            value = json.loads(text_value)
        except json.JSONDecodeError:
            return False
    if not isinstance(value, (list, tuple)) or len(value) != dimension:
        return False
    return all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)


def _as_embedding_list(value, dimension: int = EMBEDDING_DIMENSION) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        import json

        value = json.loads(value)
    return [float(item) for item in value]


def embeddings_match(before, after, dimension: int = EMBEDDING_DIMENSION) -> bool:
    """True when the vector read-back is the same embedding at float32 precision."""
    left = _as_embedding_list(before, dimension)
    right = _as_embedding_list(after, dimension)
    if left is None or right is None:
        return left is right
    if len(left) != len(right):
        return False
    for original, stored in zip(left, right):
        # pgvector stores vector as float32. This is rounding, not a dropped row.
        limit = max(1e-5, abs(original) * 1e-5)
        if abs(original - stored) > limit:
            return False
    return True


async def _public_table_counts(conn) -> dict[str, int]:
    from sqlalchemy import text

    names = (
        await conn.execute(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "ORDER BY c.relname"
            )
        )
    ).scalars().all()
    counts: dict[str, int] = {}
    for name in names:
        quoted = '"' + str(name).replace('"', '""') + '"'
        counts[str(name)] = int(await conn.scalar(text(f"SELECT count(*) FROM {quoted}")))
    return counts


class _PgvectorUnavailable(Exception):
    """The server cannot load pgvector. The existing column stays as it is."""


async def ensure_pgvector() -> dict[str, object]:
    """Turn on pgvector and store content embeddings as vector(768).

    Every public table is counted before and after. The embedding values are
    compared at float32 precision. Any mismatch rolls the conversion back, and
    a value that is not a 768-number array is left as it is.
    """
    from app.logging_config import get_logger

    log = get_logger("db")
    if engine.dialect.name != "postgresql":
        return {"status": "skipped"}
    try:
        async with engine.begin() as conn:
            result = await _convert_embeddings(conn, log)
    except _PgvectorUnavailable as exc:
        log.warning("pgvector_extension_unavailable", error=str(exc)[:200])
        await engine.dispose()
        return {"status": "extension_unavailable"}
    await engine.dispose()
    return result


async def _convert_embeddings(conn, log) -> dict[str, object]:
    from sqlalchemy import text

    try:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as exc:  # noqa: BLE001 — missing library or privilege; keep the current column
        raise _PgvectorUnavailable(str(exc)) from exc

    present = await conn.scalar(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'content_embeddings'"
        )
    )
    if not present:
        return {"status": "extension_ready"}
    current_type = await conn.scalar(
        text(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'content_embeddings' "
            "AND column_name = 'embedding'"
        )
    )
    if current_type == "vector":
        await _ensure_embedding_index(conn)
        return {"status": "already_vector"}

    before_counts = await _public_table_counts(conn)
    stored = (
        await conn.execute(text("SELECT id::text, embedding::text FROM content_embeddings ORDER BY id"))
    ).all()
    refused = [row[0] for row in stored if not embedding_fits_vector(row[1])]
    if refused:
        log.error("pgvector_migration_refused", rows=len(refused))
        return {"status": "refused", "rows": len(refused)}

    await conn.execute(
        text(
            "ALTER TABLE content_embeddings "
            "ALTER COLUMN embedding TYPE vector(768) "
            "USING CASE WHEN embedding IS NULL THEN NULL ELSE embedding::text::vector END"
        )
    )
    await _ensure_embedding_index(conn)
    after = (
        await conn.execute(text("SELECT id::text, embedding::text FROM content_embeddings ORDER BY id"))
    ).all()
    if [row[0] for row in stored] != [row[0] for row in after]:
        raise RuntimeError("content_embeddings rows changed during the vector conversion")
    for original, converted in zip(stored, after):
        if not embeddings_match(original[1], converted[1]):
            raise RuntimeError("an embedding value changed beyond float32 precision")
    after_counts = await _public_table_counts(conn)
    if before_counts != after_counts:
        raise RuntimeError("a table row count changed during the vector conversion")
    log.info(
        "pgvector_embeddings_converted",
        embeddings=len(stored),
        tables=len(after_counts),
    )
    return {"status": "migrated", "embeddings": len(stored), "tables": len(after_counts)}


async def _ensure_embedding_index(conn) -> None:
    from sqlalchemy import text

    nested = await conn.begin_nested()
    try:
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS content_embeddings_embedding_hnsw "
                "ON content_embeddings USING hnsw (embedding vector_cosine_ops)"
            )
        )
        await nested.commit()
    except Exception:  # noqa: BLE001 — a zero-norm embedding cannot use cosine
        await nested.rollback()
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS content_embeddings_embedding_hnsw "
                "ON content_embeddings USING hnsw (embedding vector_l2_ops)"
            )
        )
