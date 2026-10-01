"""Phase 1 Discovery starts with a CDD upload prompt — no live research."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.agents import discovery as discovery_mod


@pytest.mark.asyncio
async def test_run_discovery_asks_for_cdd_without_live_research():
    profile = SimpleNamespace(
        discovery_status="not_started",
        commercial_scope={},
        marketing_context={},
        overall_readiness_score=0,
    )
    client = SimpleNamespace(
        id=uuid4(),
        display_name="Click Trends",
        primary_url="https://clicktrends.com.au",
        industry="Digital Marketing",
    )
    db = AsyncMock()
    db.flush = AsyncMock()

    with (
        patch.object(discovery_mod, "get_profile", AsyncMock(return_value=profile)),
        patch.object(discovery_mod, "load_skill", return_value=""),
        patch.object(discovery_mod, "load_discovery_prior_rows", AsyncMock(return_value=[])),
        patch.object(discovery_mod, "log_event", AsyncMock()),
        patch("app.integrations.llm.live_pre_research", new_callable=AsyncMock) as research,
    ):
        events = await discovery_mod.run_discovery(
            db,
            client=client,
            session_id=uuid4(),
            user_id=uuid4(),
            message="run discovery",
        )

    research.assert_not_awaited()
    assert profile.discovery_status == "in_progress"
    cards = [
        e.get("payload")
        for e in events
        if e.get("type") == "structured_card"
    ]
    assert cards and cards[0].get("card_type") == "discovery_cdd_prompt"
    assert "upload_cdd" in (cards[0].get("actions") or [])


@pytest.mark.asyncio
async def test_run_discovery_shows_questionnaire_when_cdd_already_uploaded():
    profile = SimpleNamespace(
        discovery_status="not_started",
        commercial_scope={},
        marketing_context={},
        overall_readiness_score=0,
    )
    client = SimpleNamespace(
        id=uuid4(),
        display_name="Click Trends",
        primary_url="https://clicktrends.com.au",
        industry="Digital Marketing",
    )
    cdd_row = SimpleNamespace(
        field_key="business_model",
        source="cdd_upload",
        field_value={"value": "Agency"},
        created_at=None,
    )
    db = AsyncMock()
    db.flush = AsyncMock()

    with (
        patch.object(discovery_mod, "get_profile", AsyncMock(return_value=profile)),
        patch.object(discovery_mod, "load_skill", return_value=""),
        patch.object(
            discovery_mod, "load_discovery_prior_rows", AsyncMock(return_value=[cdd_row])
        ),
        patch.object(discovery_mod, "log_event", AsyncMock()),
    ):
        events = await discovery_mod.run_discovery(
            db,
            client=client,
            session_id=uuid4(),
            user_id=uuid4(),
            message="run discovery",
        )

    cards = [
        e.get("payload")
        for e in events
        if e.get("type") == "structured_card"
    ]
    assert cards and cards[0].get("card_type") == "discovery_report"
    assert cards[0]["fields"]["business_model"]["value"] == "Agency"
    assert cards[0]["fields"]["business_model"]["from_cdd_upload"] is True


def test_questionnaire_marks_cdd_and_ai_sources():
    rows = [
        SimpleNamespace(
            field_key="business_model",
            source="cdd_upload",
            field_value={"value": "Agency"},
        ),
        SimpleNamespace(
            field_key="positioning",
            source="ai_gap_fill",
            field_value={"value": "Hands-on SEO for local service brands"},
        ),
        SimpleNamespace(
            field_key="business_model",
            source="client_questionnaire",
            field_value={"value": "Agency"},
        ),
    ]
    # Newest first, matching load_discovery_prior_rows.
    fields = discovery_mod.build_questionnaire_fields(prior_rows=list(reversed(rows)))
    assert fields["business_model"]["from_cdd_upload"] is True
    assert fields["business_model"]["from_ai"] is False
    assert fields["positioning"]["from_ai"] is True
    assert fields["positioning"]["from_cdd_upload"] is False
