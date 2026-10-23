"""Engine room dashboard aggregation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentJob, ApiUsageLog, ChatSession, Client, Role, User
from app.security import hash_password
from app.services import engine_room
from app.services.engine_room import (
    _decision_summary,
    build_engine_room,
    readable_error,
)


def test_readable_error_extracts_provider_message():
    raw = (
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'Your credit balance is too low to access the Anthropic API.'}}"
    )
    assert readable_error(raw) == "Your credit balance is too low to access the Anthropic API."


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("ReadTimeout", "Requests timed out"),
        ("httpx.ReadTimeout", "Requests timed out"),
        ("ConnectTimeout", "Could not connect in time"),
        ("ConnectError", "Could not connect"),
        ("", "Unknown error"),
        ("Internal Server Error.", "Internal Server Error."),
    ],
)
def test_readable_error_plain_language(raw, plain):
    assert readable_error(raw) == plain


def test_decision_summary_counts_each_decision():
    assert _decision_summary(["needs_revision", "reject", "needs_revision"]) == "2 needed changes, 1 rejected"


def _catalog():
    return [
        {"provider": "anthropic", "name": "Claude", "configured": True, "mock": False},
        {"provider": "dataforseo", "name": "DataForSEO", "configured": True, "mock": False},
        {"provider": "ahrefs", "name": "Ahrefs", "configured": False, "mock": False},
        {"provider": "firecrawl", "name": "Firecrawl", "configured": True, "mock": False},
    ]


async def _seed(db: AsyncSession) -> Client:
    role = (await db.execute(select(Role).where(Role.name == "head_of_department"))).scalar_one()
    user = User(
        email="engine-room@example.com",
        full_name="Engine Room",
        role_id=role.id,
        hashed_password=hash_password("testpass123"),
        is_active=True,
    )
    client = Client(legal_name="Room Client Pty Ltd", display_name="Room Client", primary_url="https://room.example")
    db.add_all([user, client])
    await db.flush()
    session = ChatSession(client_id=client.id, user_id=user.id)
    db.add(session)
    await db.flush()
    now = datetime.now(UTC)
    # Keyword research left "running" two hours ago: past its budget, so it was stopped.
    db.add(AgentJob(session_id=session.id, agent_key="search_demand", job_type="run", status="running",
                    created_at=now - timedelta(hours=2)))
    db.add(AgentJob(session_id=session.id, agent_key="competitor_market_agent", job_type="run",
                    status="succeeded", created_at=now - timedelta(minutes=30),
                    completed_at=now - timedelta(minutes=28)))
    # Claude: 3 older successes, then the latest 3 calls all fail (half of them) -> failing.
    for i in range(3):
        db.add(ApiUsageLog(provider="anthropic", operation="messages.create", status="success",
                           created_at=now - timedelta(minutes=60 - i)))
    for i in range(3):
        db.add(ApiUsageLog(provider="anthropic", operation="messages.create", status="error",
                           error_detail="Error code: 400 - {'message': 'Your credit balance is too low.'}",
                           created_at=now - timedelta(minutes=10 - i)))
    # DataForSEO: one old timeout among later successes -> intermittent, not failing.
    db.add(ApiUsageLog(provider="dataforseo", operation="/v3/serp", status="error", error_detail="ReadTimeout",
                       agent_key="search_demand", created_at=now - timedelta(minutes=40)))
    for i in range(5):
        db.add(ApiUsageLog(provider="dataforseo", operation="/v3/serp", status="success",
                           created_at=now - timedelta(minutes=20 - i)))
    await db.commit()
    return client


@pytest.mark.asyncio
async def test_engine_room_providers_runs_and_problems(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(engine_room, "_provider_catalog", _catalog)
    await _seed(db_session)

    payload = await build_engine_room(db_session, days=7)

    states = {p["provider"]: p for p in payload["providers"]}
    assert states["anthropic"]["state"] == "failing"
    assert states["anthropic"]["last_error"]["message"] == "Your credit balance is too low."
    assert states["dataforseo"]["state"] == "intermittent"
    assert states["ahrefs"]["state"] == "no_key"
    assert states["firecrawl"]["state"] == "set_up"

    summary = payload["summary"]
    assert summary["runs"] == 2
    assert summary["failed_runs"] == 1  # the stopped run
    assert summary["api_calls"] == 12
    assert summary["failed_calls"] == 4

    runs = {r["agent_key"]: r for r in payload["recent_runs"]}
    assert runs["search_demand"]["state"] == "stopped"
    assert runs["search_demand"]["client_name"] == "Room Client"
    assert runs["competitor_market_agent"]["state"] == "succeeded"
    assert runs["competitor_market_agent"]["seconds"] == 120

    titles = [p["title"] for p in payload["problems"]]
    assert "Claude calls are failing" in titles
    assert "Search Demand runs stopped before finishing" in titles
    assert "DataForSEO: Requests timed out" in titles
    stopped = next(p for p in payload["problems"] if p["kind"] == "run" or "stopped" in p["title"])
    assert stopped["severity"] == "error"
    assert stopped["clients"] == ["Room Client"]
    # Errors sort before warnings.
    severities = [p["severity"] for p in payload["problems"] if p["severity"] != "info"]
    assert severities == sorted(severities, key=lambda s: 0 if s == "error" else 1)

    search = next(p for p in payload["phases"] if p["agent_key"] == "search_demand")
    assert search["phase"] == 5 and search["last_state"] == "stopped" and search["failed"] == 1


@pytest.mark.asyncio
async def test_build_engine_room_returns_agents(db_session: AsyncSession):
    payload = await build_engine_room(db_session, days=7)
    assert payload["summary"]["agents_total"] >= 10
    assert isinstance(payload["agents"], list)
    assert any(a["agent_key"] == "search_demand" for a in payload["agents"])
    assert "integrations" in payload
    assert "issues" in payload
