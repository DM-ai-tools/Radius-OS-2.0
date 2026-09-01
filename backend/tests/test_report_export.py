from __future__ import annotations

from app.services.report_export import (
    CARD_ORDER,
    _is_report_payload,
    _sort_key,
)


def test_noise_event_types_skipped():
    assert _is_report_payload({"event_type": "checkpoint", "card_type": "website_audit"}) is False
    assert _is_report_payload({"event_type": "job_progress"}) is False


def test_structured_card_with_card_type_is_report():
    assert _is_report_payload(
        {"event_type": "structured_card", "card_type": "content_strategy_report", "title": "Strategy"}
    )


def test_sort_key_orders_phases():
    a = {"card_type": "content_strategy_report"}
    b = {"card_type": "discovery_profile"}
    assert _sort_key(a) > _sort_key(b)
    assert CARD_ORDER["content_strategy_report"] < CARD_ORDER["content_audit_report"]


def test_resolve_report_title_uses_payload_title():
    from app.services.report_export import resolve_report_title

    report = {
        "card_type": "content_strategy_report",
        "title": "Content Strategy: Acme Corp",
        "payload": {"card_type": "content_strategy_report", "title": "Content Strategy: Acme Corp"},
    }
    assert resolve_report_title(report, {"name": "Acme Corp"}) == "Content Strategy: Acme Corp"


def test_resolve_report_title_falls_back_to_card_type():
    from app.services.report_export import resolve_report_title

    report = {
        "card_type": "site_architecture_blueprint",
        "payload": {"card_type": "site_architecture_blueprint"},
    }
    assert (
        resolve_report_title(report, {"name": "Acme Corp"})
        == "Site Architecture Blueprint: Acme Corp"
    )


def test_find_report_returns_match():
    bundle = {
        "reports": [
            {"card_type": "website_audit", "title": "Website"},
            {"card_type": "content_strategy_report", "title": "Strategy"},
        ]
    }
    from app.services.report_export import find_report

    assert find_report(bundle, "content_strategy_report")["title"] == "Strategy"
    assert find_report(bundle, "missing") is None

