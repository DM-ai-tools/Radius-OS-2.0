"""Shrink chat payloads on the way to the page.

The stored report stays complete. Checkpoint rows repeat that report, and the
seed dump repeats the service clusters, so the page was downloading tens of
megabytes and never painting the finished keyword research.
"""

from __future__ import annotations

from typing import Any


def display_message_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a page-sized copy. Does not mutate the stored payload."""
    if not isinstance(payload, dict):
        return payload
    if payload.get("event_type") == "checkpoint":
        return {
            "event_type": "checkpoint",
            "card_type": payload.get("card_type"),
            "title": payload.get("title"),
            "agent_key": payload.get("agent_key"),
        }
    if payload.get("card_type") != "search_demand_report":
        return payload
    report = payload.get("cluster_report")
    if not isinstance(report, dict) or not report.get("service_clusters"):
        return payload
    trimmed = dict(payload)
    # The service groups already carry these keywords. The top-level copies
    # are the same lists again.
    trimmed.pop("seed_clusters", None)
    if report.get("clusters"):
        trimmed.pop("clusters", None)
    if report.get("cluster_intent"):
        trimmed.pop("cluster_intent", None)
    return trimmed
