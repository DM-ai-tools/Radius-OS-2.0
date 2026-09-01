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
