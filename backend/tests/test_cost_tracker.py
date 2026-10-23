"""Cost tracker aggregation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cost_tracker import build_client_cost_summary, build_cost_tracker


@pytest.mark.asyncio
async def test_build_cost_tracker_returns_summary(db_session: AsyncSession):
    payload = await build_cost_tracker(db_session, days=7)
    assert "summary" in payload
    assert "cost_by_provider" in payload
    assert "cost_by_vendor" in payload
    assert "cost_by_model" in payload
    assert "cost_by_agent" in payload
    assert "cost_by_client" in payload
    assert "recent_api_calls" in payload
    assert payload["window_days"] == 7


@pytest.mark.asyncio
async def test_build_client_cost_summary_empty(db_session: AsyncSession):
    import uuid

    payload = await build_client_cost_summary(db_session, uuid.uuid4(), days=30)
    assert payload["call_count"] == 0
    assert payload["estimated_cost_usd"] == 0


async def _seed_usage(db: AsyncSession):
    """One client with 260 calls (more than any old row cap), plus unlinked spend."""
    from decimal import Decimal

    from app.models import ApiUsageLog, Client

    client = Client(
        legal_name="Busy Client Pty Ltd",
        display_name="Busy Client",
        primary_url="https://busy.example",
    )
    db.add(client)
    await db.flush()
    for i in range(260):
        db.add(
            ApiUsageLog(
                client_id=client.id,
                agent_key="search_demand" if i % 2 else "content_production",
                provider="anthropic" if i % 2 else "dataforseo",
                operation="messages.create" if i % 2 else "/v3/serp",
                model="claude-sonnet-5" if i % 2 else None,
                estimated_cost_usd=Decimal("0.01"),
                status="success",
            )
        )
    db.add(ApiUsageLog(client_id=client.id, provider="anthropic", operation="messages.create",
                       estimated_cost_usd=Decimal(5), status="error"))
    db.add(ApiUsageLog(provider="dataforseo", operation="/v3/serp",
                       estimated_cost_usd=Decimal("0.5"), status="success"))
    await db.commit()
    return client


@pytest.mark.asyncio
async def test_client_totals_count_every_call_not_the_latest_200(db_session: AsyncSession):
    client = await _seed_usage(db_session)
    payload = await build_client_cost_summary(db_session, client.id, days=30)
    assert payload["call_count"] == 260
    assert payload["estimated_cost_usd"] == pytest.approx(2.60)
    assert payload["failed_calls"] == 1
    # the breakdowns add up to the total
    assert sum(r["cost_usd"] for r in payload["cost_by_vendor"]) == pytest.approx(2.60)
    assert sum(r["cost_usd"] for r in payload["cost_by_agent"]) == pytest.approx(2.60)
    labels = {r["label"] for r in payload["cost_by_agent"]}
    assert labels == {"Search Demand", "Content Production"}
    assert sum(d["cost_usd"] for d in payload["daily"]) == pytest.approx(2.60)
    assert payload["last_used_at"]


@pytest.mark.asyncio
async def test_platform_breakdowns_add_up_to_the_total(db_session: AsyncSession):
    await _seed_usage(db_session)
    payload = await build_cost_tracker(db_session, days=7)
    total = payload["summary"]["estimated_cost_usd"]
    assert total == pytest.approx(3.10)
    assert payload["summary"]["api_calls"] == 261
    assert payload["summary"]["failed_calls"] == 1
    assert payload["summary"]["previous_cost_usd"] == 0
    assert sum(r["cost_usd"] for r in payload["cost_by_vendor"]) == pytest.approx(total)
    assert sum(r["cost_usd"] for r in payload["cost_by_client"]) == pytest.approx(total)
    by_name = {r["client_name"]: r for r in payload["cost_by_client"]}
    assert by_name["Busy Client"]["client_url"] == "https://busy.example"
    assert by_name["Not linked to a client"]["cost_usd"] == pytest.approx(0.5)
    assert len(payload["daily"]) >= 7


@pytest.mark.asyncio
async def test_unpriced_calls_stay_on_the_model_list(db_session: AsyncSession):
    from app.models import ApiUsageLog

    db_session.add(
        ApiUsageLog(
            provider="manus",
            operation="site_preview.build",
            estimated_cost_usd=None,
            status="success",
        )
    )
    await db_session.commit()
    payload = await build_cost_tracker(db_session, days=7)
    manus = next(row for row in payload["cost_by_model"] if row["provider"] == "manus")
    assert manus["calls"] == 1
    assert manus["unpriced_calls"] == 1
    assert manus["cost_usd"] == 0
