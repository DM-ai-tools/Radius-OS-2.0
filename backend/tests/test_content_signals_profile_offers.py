"""Signals 7-10 in isolation: business type, size, reach, offers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.content_signals.business_profile import (
    collect_business_size,
    collect_business_type,
    collect_reach,
)
from app.services.content_signals.offers import (
    active_offers,
    collect_offers,
    offer_is_active,
    parse_iso,
    score_offer_match,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


# --- signal 7: business type ----------------------------------------------


def test_business_type_inferred_from_cdd_business_model():
    r = collect_business_type(commercial={"business_model": "Services"})
    assert r.available
    assert r.data["type"] == "service"
    assert r.data["preferred_cta"]


def test_business_type_matches_a_multiword_industry():
    r = collect_business_type(commercial={"industry_targeting": "Digital Marketing Agency"})
    assert r.data["type"] == "agency"


def test_healthcare_carries_compliance_notes():
    r = collect_business_type(commercial={"business_model": "dental clinic"})
    assert r.data["type"] == "healthcare"
    assert r.data["compliance_notes"], "healthcare must carry compliance constraints"


def test_operator_override_beats_inference():
    r = collect_business_type(
        commercial={"business_model": "Services", "business_profile": {"business_type": "saas"}}
    )
    assert r.data["type"] == "saas"
    assert r.data["source"] == "operator override"


def test_business_type_missing_is_unavailable_not_an_error():
    r = collect_business_type(commercial={}, marketing={})
    assert r.available is False
    assert r.notes and "No business type" in r.notes[0]


# --- signal 8: business size ----------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [(0.5, "solo"), (2.4, "solo"), (2.5, "small"), (4.9, "small"),
     (5.0, "mid"), (7.4, "mid"), (7.5, "enterprise"), (10.0, "enterprise")],
)
def test_size_bands_from_phase4_company_size(score, expected):
    r = collect_business_size(competitive={"client_scores": {"company_size": score}})
    assert r.available
    assert r.data["size"] == expected


def test_smaller_businesses_get_a_lower_keyword_difficulty_ceiling():
    solo = collect_business_size(competitive={"client_scores": {"company_size": 1}})
    ent = collect_business_size(competitive={"client_scores": {"company_size": 9}})
    assert solo.data["max_keyword_difficulty"] < ent.data["max_keyword_difficulty"]
    assert "long-tail" in solo.data["keyword_strategy"]


def test_size_unavailable_when_phase4_ran_slim():
    """The real Click Trends profile: Phase 4 stored competitors but no scorecards."""
    r = collect_business_size(competitive={"competitors": [], "analysis_mode": "slim"})
    assert r.available is False
    assert "slim mode" in r.notes[0]


def test_size_operator_override_needs_no_phase4():
    r = collect_business_size(commercial={"business_profile": {"business_size": "small"}})
    assert r.available
    assert r.data["size"] == "small"


# --- signal 9: reach -------------------------------------------------------


def test_reach_derived_from_geographic_focus_city():
    r = collect_reach(commercial={"geographic_focus": "Melbourne, Australia"})
    assert r.available
    assert r.data["model"] in {"city_wide", "local"}
    assert r.data["local_seo"] is True
    assert any("schema" in e.lower() for e in r.data["elements"])


def test_ecommerce_business_type_implies_online_reach():
    r = collect_reach(commercial={"geographic_focus": "Melbourne"}, business_type="ecommerce")
    assert r.data["model"] == "online"
    assert r.data["local_seo"] is False
    assert any("Product" in e for e in r.data["elements"])


def test_reach_override_wins_and_carries_locations():
    r = collect_reach(
        commercial={
            "geographic_focus": "Melbourne",
            "business_profile": {
                "location_model": "regional",
                "locations_served": ["Melbourne", "Sydney"],
            },
        }
    )
    assert r.data["model"] == "regional"
    assert r.data["locations_served"] == ["Melbourne", "Sydney"]


def test_reach_unavailable_without_geography():
    r = collect_reach(commercial={})
    assert r.available is False


# --- signal 10: offers ----------------------------------------------------


def _offer(**kw):
    base = {
        "id": "o1",
        "name": "Free SEO audit",
        "type": "lead_magnet",
        "description": "A free 30-minute audit",
        "url": "/offers/free-audit",
        "cta": "Claim your free audit",
        "starts_at": "2026-09-01T00:00:00+00:00",
        "ends_at": "2026-12-01T00:00:00+00:00",
        "active": True,
        "applies_to": ["SEO"],
        "locations": [],
        "funnel_stages": ["TOFU", "MOFU"],
        "priority": 1,
    }
    base.update(kw)
    return base


def test_parse_iso_handles_z_and_naive():
    assert parse_iso("2026-09-01T00:00:00Z").tzinfo is not None
    assert parse_iso("2026-09-01T00:00:00").tzinfo == timezone.utc
    assert parse_iso("not a date") is None
    assert parse_iso("") is None


def test_an_active_offer_inside_its_window_is_active():
    ok, reason = offer_is_active(_offer(), now=NOW)
    assert ok is True and reason == "active"


def test_active_false_beats_a_valid_window():
    ok, reason = offer_is_active(_offer(active=False), now=NOW)
    assert ok is False and "inactive" in reason


def test_an_expired_offer_is_never_active():
    ok, reason = offer_is_active(_offer(ends_at="2026-09-01T00:00:00+00:00"), now=NOW)
    assert ok is False and "expired" in reason


def test_a_future_offer_is_not_yet_active():
    ok, reason = offer_is_active(_offer(starts_at="2026-12-01T00:00:00+00:00"), now=NOW)
    assert ok is False and "starts" in reason


def test_an_unparseable_date_fails_closed():
    ok, reason = offer_is_active(_offer(ends_at="whenever"), now=NOW)
    assert ok is False and "unparseable" in reason


def test_an_offer_with_no_dates_is_active():
    ok, _ = offer_is_active(_offer(starts_at=None, ends_at=None), now=NOW)
    assert ok is True


def test_expired_offers_never_reach_the_context():
    result = collect_offers(
        commercial={"offers": [_offer(name="Expired", ends_at="2026-01-01T00:00:00+00:00")]},
        keyword="seo services",
        funnel_stage="MOFU",
        now=NOW,
    )
    assert result.available is False
    assert any("Expired" in n for n in result.notes)


def test_a_funnel_mismatch_scores_zero():
    score, reasons = score_offer_match(
        _offer(funnel_stages=["BOFU"]), keyword="what is seo", funnel_stage="TOFU"
    )
    assert score == 0
    assert "BOFU" in reasons[0]


def test_a_location_restricted_offer_does_not_match_another_location():
    score, reasons = score_offer_match(
        _offer(locations=["Sydney"]),
        keyword="seo services",
        funnel_stage="MOFU",
        locations=["Melbourne"],
    )
    assert score == 0
    assert "location-restricted" in reasons[0]


def test_priority_one_outranks_priority_three():
    high, _ = score_offer_match(_offer(priority=1), keyword="seo", funnel_stage="MOFU")
    low, _ = score_offer_match(_offer(priority=3), keyword="seo", funnel_stage="MOFU")
    assert high > low


def test_the_best_matching_offer_becomes_primary():
    result = collect_offers(
        commercial={
            "offers": [
                _offer(id="a", name="Generic", applies_to=[], priority=3),
                _offer(id="b", name="SEO audit", applies_to=["SEO"], priority=1),
            ]
        },
        keyword="seo services melbourne",
        funnel_stage="MOFU",
        now=NOW,
    )
    assert result.available
    assert result.data["primary_offer"]["name"] == "SEO audit"
    assert result.data["primary_offer"]["match_score"] > 0
    assert result.suggests


def test_no_offers_configured_is_unavailable_not_an_error():
    assert collect_offers(commercial={}, keyword="seo", now=NOW).available is False


def test_active_offers_splits_and_explains():
    live, notes = active_offers(
        [_offer(name="Live"), _offer(name="Dead", active=False)], now=NOW
    )
    assert [o["name"] for o in live] == ["Live"]
    assert any("Dead" in n for n in notes)


def test_an_offer_without_a_name_is_ignored():
    live, _ = active_offers([{"id": "x", "active": True}], now=NOW)
    assert live == []


def test_offer_ending_exactly_now_is_still_active():
    ok, _ = offer_is_active(_offer(ends_at=NOW.isoformat()), now=NOW)
    assert ok is True


def test_offer_window_uses_timezone_not_local_date():
    """An offer expiring at 23:00 UTC is still live at 22:00 UTC."""
    ok, _ = offer_is_active(
        _offer(ends_at="2026-09-25T23:00:00+00:00"),
        now=NOW.replace(hour=22),
    )
    assert ok is True
    ok2, _ = offer_is_active(
        _offer(ends_at="2026-09-25T23:00:00+00:00"),
        now=NOW.replace(hour=23) + timedelta(minutes=1),
    )
    assert ok2 is False
