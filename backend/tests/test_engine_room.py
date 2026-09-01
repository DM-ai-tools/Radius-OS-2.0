"""Engine room dashboard aggregation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.engine_room import build_engine_room


@pytest.mark.asyncio
async def test_build_engine_room_returns_agents(db_session: AsyncSession):
    payload = await build_engine_room(db_session, days=7)
    assert payload["summary"]["agents_total"] >= 10
    assert isinstance(payload["agents"], list)
    assert any(a["agent_key"] == "search_demand" for a in payload["agents"])
    assert "integrations" in payload
    assert "issues" in payload
