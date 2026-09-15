"""AUDIT-021: hot-path FK columns (client_id/user_id/session_id filters used
on readiness computation, chat message posting, review approve/reject) had
no index. Model-level index=True covers fresh databases via create_all();
ensure_hot_path_indexes() is the retrofit path for databases that already
existed before this change, mirroring db.py's existing ensure_* convention
for columns. This pins that the patcher runs cleanly and produces the
expected indexes (a duplicate name from create_all already having created
it via index=True is the expected, harmless case here — IF NOT EXISTS makes
that a no-op)."""

from __future__ import annotations

from sqlalchemy import text

from app.db import engine, ensure_hot_path_indexes

EXPECTED_INDEXES = {
    "ix_discovery_responses_client_id",
    "ix_tracking_audits_client_id",
    "ix_website_audits_client_id",
    "ix_competitor_profiles_client_id",
    "ix_backlink_snapshots_client_id",
    "ix_backlink_snapshots_competitor_profile_id",
    "ix_chat_sessions_client_id",
    "ix_chat_sessions_user_id",
    "ix_agent_jobs_session_id",
    "ix_api_credentials_client_id",
    "ix_readiness_scores_client_id",
}


async def test_ensure_hot_path_indexes_creates_expected_indexes(db_session):
    await ensure_hot_path_indexes()

    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
        names = {row[0] for row in result}

    missing = EXPECTED_INDEXES - names
    assert not missing, f"expected indexes missing: {missing}"


async def test_ensure_hot_path_indexes_is_idempotent(db_session):
    await ensure_hot_path_indexes()
    await ensure_hot_path_indexes()  # must not raise on the second run
