"""Inter-agent handoff / bounce helpers."""

from __future__ import annotations

import pytest

from app.services.agent_handoff import (
    approve_handoff,
    blocked_events,
    bounce_events,
    consume_events,
    handoff_events,
    next_agent_for,
    NEXT_PROMPTS,
)


def test_next_agent_linear():
    assert next_agent_for("search_demand") == "site_architecture"
    assert next_agent_for("site_architecture") == "content_strategy"
    assert next_agent_for("content_strategy") == "technical_seo"
    assert next_agent_for("content_planning") == "content_production"
    assert next_agent_for("publishing") is None


def test_next_agent_keywords_then_url_map_then_calendar():
    assert next_agent_for("search_demand") == "site_architecture"
    assert (
        next_agent_for(
            "search_demand",
            phase_statuses={"website": "complete", "content_audit": "not_started"},
        )
        == "site_architecture"
    )
    assert next_agent_for("content_strategy") == "technical_seo"
    assert next_agent_for("technical_seo") == "content_audit"
    assert next_agent_for("content_audit") == "content_planning"


def test_consume_and_handoff_events():
    start = consume_events("content_planning", pack_notes=["strategy=complete"])
    assert start[0]["type"] == "system_notice"
    assert "Strategy" in start[0]["content"] or "reading" in start[0]["content"].lower()

    end = handoff_events("content_planning", result_line="12 pages locked.")
    assert any(e["type"] == "agent_message" for e in end)
    assert any(
        (e.get("payload") or {}).get("event_type") == "agent_handoff"
        for e in end
        if e["type"] == "system_notice"
    )
    assert "Content Production" in end[0]["content"]


def test_bounce_routes_strategy_only_to_architecture():
    events = bounce_events(
        "content_planning",
        gaps=[
            {
                "url_n": "/planned",
                "reason": "strategy_only — topic planned but no URL/parent assigned",
                "source_pack": "strategy",
            },
            {
                "url_n": "/orphan",
                "reason": "architecture_only — URL in taxonomy with no content plan",
                "source_pack": "architecture",
            },
        ],
    )
    texts = " ".join(e.get("content") or "" for e in events)
    assert "Site Architecture" in texts
    assert "Content Strategy" in texts
    assert "Bounce" in texts


def test_blocked_routes_upstream():
    ev = blocked_events(
        "content_production",
        "Phase 10 must not brief from an unlocked roadmap.",
        route_to="content_planning",
    )
    assert "Blocked" in ev[0]["content"]
    assert ev[0]["payload"]["route_to"] == "content_planning"


def test_approve_handoff_payload():
    h = approve_handoff(
        "content_strategy",
        phase_statuses={"seo_strategy": "complete"},
    )
    assert h["to_agent"] == "technical_seo"
    assert "Technical SEO" in h["message"]


def test_production_handoff_prompt_does_not_look_like_keyword_research():
    prompt = NEXT_PROMPTS["content_production"].lower()
    assert "write the full draft" in prompt
    assert "keyword cluster" not in prompt


@pytest.mark.asyncio
async def test_draft_handoff_routes_to_content_production_not_search_demand():
    from app.integrations.llm import route_agent

    statuses = {
        "discovery_status": "complete",
        "tracking_status": "complete",
        "website_status": "complete",
        "competitor_status": "complete",
        "search_demand_status": "complete",
        "site_architecture_status": "complete",
        "seo_strategy_status": "complete",
        "technical_seo_status": "complete",
        "content_audit_status": "complete",
        "content_planning_status": "complete",
        "content_production_status": "not_started",
        "on_page_seo_status": "not_started",
        "publishing_status": "not_started",
    }
    old = "Draft the selected topic, weave in the keyword cluster, and show the preview"
    new = NEXT_PROMPTS["content_production"]
    assert await route_agent(old, statuses) == "content_production"
    assert await route_agent(new, statuses) == "content_production"
