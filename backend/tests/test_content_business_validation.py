"""Drafts are checked against the business rules in the content context."""

from app.services.content_signals.validation import (
    business_rules_block,
    validate_business_logic,
)


def _context(**signals: dict) -> dict:
    return {
        "funnel_stage": signals.pop("funnel_stage", None),
        "signals": signals,
    }


def _available(data: dict, notes: list[str] | None = None) -> dict:
    return {"available": True, "data": data, "notes": notes or []}


def test_small_business_overclaim_fails():
    result = validate_business_logic(
        {"markdown": "We are the industry-leading plumber."},
        _context(business_size=_available({"size": "small", "label": "Small business", "claims": "modest"})),
    )
    assert result["ok"] is False
    assert result["failures"][0]["name"] == "size_claims"


def test_tofu_hard_sell_fails():
    result = validate_business_logic(
        {"markdown": "Read the guide.", "cta": "Book now"},
        _context(funnel_stage="TOFU", funnel=_available({"avoid": "book now urgency"})),
    )
    assert any(c["name"] == "funnel_pressure" and not c["ok"] for c in result["checks"])


def test_online_near_me_fails_and_local_place_warns():
    online = validate_business_logic(
        {"markdown": "Best shoes near me."},
        _context(reach=_available({"model": "online", "label": "E-commerce / online-only"})),
    )
    assert any(c["name"] == "reach_online" and not c["ok"] for c in online["checks"])

    local = validate_business_logic(
        {"markdown": "We repair drains."},
        _context(
            reach=_available(
                {"model": "local", "label": "Local service area", "locations_served": ["Richmond"]}
            )
        ),
    )
    warn = next(c for c in local["checks"] if c["name"] == "reach_place")
    assert warn["ok"] is False
    assert warn["severity"] == "warn"
    assert local["ok"] is True


def test_inactive_offer_and_duplicate_page_fail():
    result = validate_business_logic(
        {"markdown": "Use the Spring Sale this week.", "url": "/blog/new"},
        _context(
            offers={
                "available": True,
                "data": {"excluded": ["Offer 'Spring Sale' excluded: expired 2026-01-01."]},
                "notes": [],
            },
            past_performance=_available(
                {"cannibalisation_clash": {"url": "/services/drain-cleaning", "keyword": "drain cleaning"}}
            ),
        ),
    )
    names = {c["name"] for c in result["failures"]}
    assert "inactive_offers" in names
    assert "already_covered" in names


def test_refreshing_the_existing_url_is_allowed():
    result = validate_business_logic(
        {"markdown": "Updated service page.", "url": "/services/drain-cleaning"},
        _context(
            past_performance=_available(
                {"cannibalisation_clash": {"url": "/services/drain-cleaning/"}}
            )
        ),
    )
    clash = next(c for c in result["checks"] if c["name"] == "already_covered")
    assert clash["ok"] is True


def test_rules_block_lists_the_checks_the_writer_must_honour():
    block = business_rules_block(
        _context(
            funnel_stage="TOFU",
            funnel=_available({"avoid": "pricing tables"}),
            business_type=_available({"type": "agency", "compliance_notes": ["no guaranteed-ranking claims"]}),
        )
    )
    assert "TOFU" in block
    assert "no guaranteed-ranking claims" in block
