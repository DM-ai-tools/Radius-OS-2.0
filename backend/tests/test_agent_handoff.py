"""Inter-agent handoff / bounce helpers."""

from __future__ import annotations

from app.services.agent_handoff import (
    approve_handoff,
    blocked_events,
    bounce_events,
    consume_events,
    handoff_events,
    next_agent_for,
)


def test_next_agent_linear():
    assert next_agent_for("content_strategy") == "site_architecture"
    assert next_agent_for("content_planning") == "content_production"
    assert next_agent_for("publishing") is None


def test_next_agent_v19_demand_to_strategy_not_audit():
    assert next_agent_for("search_demand") == "content_strategy"
    assert (
        next_agent_for(
            "search_demand",
            phase_statuses={"website": "complete", "content_audit": "not_started"},
        )
        == "content_strategy"
    )
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
    assert h["to_agent"] == "site_architecture"
    assert "Site Architecture" in h["message"]
