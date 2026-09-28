"""Locale must be explicit and configurable, never an accidental default.

Every DataForSEO request carries a location_code and language_code. They were
previously implicit: location fell back to 2840 (United States) inside the
client, and language was only ever the `DEFAULT_LANGUAGE = "en"` default
argument — nothing in the pipeline ever set it. A client targeting Melbourne
could be priced against US volumes with nothing in the output saying so.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.competitive_context import resolve_geographic_focus
from app.services.keyword_metrics import MissingLocaleError, fetch_keyword_metrics


def test_settings_carry_a_default_location_and_language():
    settings = get_settings()
    assert isinstance(settings.dataforseo_location_code, int)
    assert settings.dataforseo_location_code > 0
    assert isinstance(settings.dataforseo_language_code, str)
    assert settings.dataforseo_language_code


def test_geographic_focus_resolves_an_explicit_language_code():
    geo = resolve_geographic_focus({"geographic_focus": "Melbourne, Australia"}, {}, {})
    assert geo["language_code"] == get_settings().dataforseo_language_code
    assert geo["location_code"]


def test_geographic_focus_language_can_be_overridden_per_project():
    geo = resolve_geographic_focus(
        {"geographic_focus": "Berlin, Germany", "language_code": "de"}, {}, {}
    )
    assert geo["language_code"] == "de"


async def test_metrics_refuse_to_run_without_a_resolved_locale():
    with pytest.raises(MissingLocaleError):
        await fetch_keyword_metrics(["x"], location_code=None, language_code=None)
