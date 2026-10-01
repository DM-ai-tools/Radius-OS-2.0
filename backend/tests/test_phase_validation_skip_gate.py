"""Phase validation must not retry mid-gate Search Demand turns."""

from __future__ import annotations

from app.services.phase_validation.service import _should_skip_validation


def test_skip_validation_for_service_prioritization_card():
    events = [
        {
            "type": "structured_card",
            "payload": {
                "card_type": "service_prioritization",
                "agent_key": "search_demand",
                "title": "Select services",
            },
        }
    ]
    assert _should_skip_validation({}, events) == "awaiting_service_selection"


def test_skip_validation_for_awaiting_status_payload():
    events = [
        {
            "type": "structured_card",
            "payload": {
                "card_type": "search_demand_report",
                "agent_key": "search_demand",
            },
        }
    ]
    output = {"search_demand_status": "awaiting_service_selection"}
    assert _should_skip_validation(output, events) == "awaiting_service_selection"


def test_full_search_demand_deliverable_is_not_skipped():
    events = [
        {
            "type": "structured_card",
            "payload": {
                "card_type": "search_demand_report",
                "agent_key": "search_demand",
                "cluster_report": {"clusters": [{"name": "Meta Ads"}]},
            },
        }
    ]
    assert _should_skip_validation({"cluster_report": {"clusters": []}}, events) is None
