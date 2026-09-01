"""Waterfall predecessor gates for phases 1–8."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.content_audit import run_content_audit
from app.agents.competitor import run_competitor
from app.agents.search_demand import run_search_demand
from app.agents.tracking import run_tracking
from app.agents.website import run_website


def _client():
    return SimpleNamespace(
        id=uuid4(),
        display_name="Acme",
        primary_url="https://acme.example",
        industry="marketing",
    )


def _profile(**statuses):
    base = {
        "discovery_status": "not_started",
        "tracking_status": "not_started",
        "website_status": "not_started",
        "competitor_status": "not_started",
        "search_demand_status": "not_started",
        "seo_strategy_status": "not_started",
        "site_architecture_status": "not_started",
        "technical_seo_status": "not_started",
        "content_audit_status": "not_started",
        "commercial_scope": {},
        "marketing_context": {},
        "competitive_landscape_summary": {},
        "website_situation_summary": {},
        "search_demand_summary": {},
        "seo_strategy_summary": {},
        "site_architecture_summary": {},
        "technical_seo_summary": {},
        "content_audit_summary": {},
    }
    base.update(statuses)
    return SimpleNamespace(**base)


def _blocked(events: list[dict]) -> bool:
    return any(
        (e.get("type") == "checkpoint" and "blocked" in str(e).lower())
        or (e.get("type") == "system_notice" and "Approve Phase" in str(e.get("content") or ""))
        or (e.get("payload") or {}).get("card_type") == "handoff_blocked"
        or "blocked" in str((e.get("payload") or {}).get("card_type") or "").lower()
        for e in events
    )


def _has_block_route(events: list[dict], route: str) -> bool:
    blob = str(events)
    return route in blob and ("blocked" in blob.lower() or "Approve Phase" in blob)


@pytest.mark.asyncio
async def test_tracking_blocked_without_discovery_approve():
    profile = _profile(discovery_status="pending_signoff")
    db = AsyncMock()
    with patch("app.agents.tracking.get_profile", AsyncMock(return_value=profile)):
        with patch("app.agents.tracking.load_skill", return_value=""):
            events = await run_tracking(
                db, client=_client(), session_id=uuid4(), user_id=uuid4(), message="Run tracking"
            )
    assert _has_block_route(events, "discovery_agent")
    assert profile.tracking_status != "in_progress"


@pytest.mark.asyncio
async def test_website_blocked_without_tracking_approve():
    profile = _profile(discovery_status="complete", tracking_status="pending_signoff")
    db = AsyncMock()
    with patch("app.agents.website.get_profile", AsyncMock(return_value=profile)):
        with patch("app.agents.website.load_skill", return_value=""):
            events = await run_website(
                db,
                client=_client(),
                session_id=uuid4(),
                user_id=uuid4(),
                message="Run website situation",
            )
    assert _has_block_route(events, "tracking_access_agent")
    assert profile.website_status != "in_progress"


@pytest.mark.asyncio
async def test_competitor_blocked_without_website_approve():
    profile = _profile(
        discovery_status="complete",
        tracking_status="complete",
        website_status="pending_signoff",
    )
    db = AsyncMock()
    with patch("app.agents.competitor.get_profile", AsyncMock(return_value=profile)):
        with patch("app.agents.competitor.load_skill", return_value=""):
            events = await run_competitor(
                db,
                client=_client(),
                session_id=uuid4(),
                user_id=uuid4(),
                message="Refresh competitor scan",
            )
    assert _has_block_route(events, "website_situation_agent")
    assert profile.competitor_status != "in_progress"


@pytest.mark.asyncio
async def test_search_demand_blocked_without_competitor_approve():
    profile = _profile(
        discovery_status="complete",
        competitor_status="pending_signoff",
        commercial_scope={"business_keywords": "seo"},
    )
    db = AsyncMock()
    with patch("app.agents.search_demand.get_profile", AsyncMock(return_value=profile)):
        with patch("app.agents.search_demand.load_skill", return_value=""):
            with patch("app.agents.search_demand.load_skill_file", return_value=""):
                events = await run_search_demand(
                    db,
                    client=_client(),
                    session_id=uuid4(),
                    user_id=uuid4(),
                    message="Run keyword research",
                )
    assert _has_block_route(events, "competitor_market_agent")
    assert profile.search_demand_status != "in_progress"


@pytest.mark.asyncio
async def test_content_audit_blocked_without_technical_seo_approve():
    profile = _profile(
        technical_seo_status="pending_signoff",
        website_status="complete",
        search_demand_status="complete",
        website_situation_summary={"sample_urls": ["/"]},
    )
    db = AsyncMock()
    with patch("app.agents.content_audit.get_profile", AsyncMock(return_value=profile)):
        with patch("app.agents.content_audit.load_skill", return_value=""):
            with patch("app.agents.content_audit.skill_system_preamble", return_value=""):
                events = await run_content_audit(
                    db,
                    client=_client(),
                    session_id=uuid4(),
                    user_id=uuid4(),
                    message="Run content audit",
                )
    assert _has_block_route(events, "technical_seo")
    assert profile.content_audit_status != "in_progress"


def test_priority_queue_carries_phase5_secondaries():
    from app.services.content_strategy import build_priority_queue

    queue = build_priority_queue(
        best=[{"keyword": "meta ads library", "volume": 100, "intent": "informational"}],
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[],
        domain="acme.example",
        topic_plan={
            "topic_ideas": [
                {
                    "title": "Struggling With Meta Ads Library?",
                    "keyword": "meta ads library",
                    "primary_keyword": "meta ads library",
                    "secondary_keywords": ["meta ads manager", "facebook ads campaign"],
                    "supporting_keywords": ["meta ads manager", "facebook ads campaign"],
                    "intent": "informational",
                    "angle": "pain-point",
                }
            ]
        },
    )
    hit = next(r for r in queue if r["keyword"] == "meta ads library")
    assert hit["from_phase5_topic"] is True
    assert "meta ads manager" in hit["secondary_keywords"]
    assert hit["supporting_keywords"] == hit["secondary_keywords"]
