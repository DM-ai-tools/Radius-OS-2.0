from app.integrations.dataforseo import coerce_labs_location, sanitize_keyword
from app.services.competitive_context import resolve_geographic_focus


def test_melbourne_uses_australia_for_labs():
    geo = resolve_geographic_focus({"geographic_focus": "Melbourne, Victoria"})
    assert geo["country"] == "au"
    assert geo["location_code"] == 1000259
    assert geo["labs_location_code"] == 2036


def test_coerce_labs_maps_melbourne_city_code():
    assert coerce_labs_location(1000259) == 2036
    assert coerce_labs_location(2036) == 2036
    assert coerce_labs_location(2840) == 2840


def test_sanitize_keyword_strips_parenthetical_notes():
    assert sanitize_keyword("Car loans (not a focus)") == "Car loans"
    assert sanitize_keyword("Website & Conversion Optimisation (CRO)") == (
        "Website Conversion Optimisation"
    )
