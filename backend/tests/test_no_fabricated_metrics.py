"""No keyword and no metric may exist unless a provider returned it.

Phase 5 used to paper over a provider outage with `build_coverage_expansions`,
which invented both the keyword strings ("{tail} growth playbook 1") and their
search volumes (`max(min_volume + 25, 35)`), tagged them `provider_fallback`
and let them flow into clustering, briefs and the workbook. The flag was
honest, but a flagged fabrication is still a fabrication: those rows became
cluster primaries and page targets.

The rule now: every number comes from a DataForSEO response, missing data is
null, and a keyword no provider returned does not exist. A provider outage
produces a small honest pool and a loud error, not a padded fake one.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from app.config import get_settings
from app.services import keyword_seeding
from app.services.keyword_seeding import expand_seed, summarize_keyword_data_quality


@contextmanager
def _live_settings():
    settings = get_settings()
    before = (settings.use_mock_providers, settings.dataforseo_login, settings.dataforseo_password)
    settings.use_mock_providers = False
    settings.dataforseo_login = "u"
    settings.dataforseo_password = "p"
    try:
        yield settings
    finally:
        (
            settings.use_mock_providers,
            settings.dataforseo_login,
            settings.dataforseo_password,
        ) = before


async def _dead_provider(seed, *, location_code, min_volume=10, limit=60, classes=None, language_code=None):
    return [], ["dataforseo_keyword_suggestions_error"]


async def test_a_dead_provider_invents_no_keywords():
    with _live_settings(), patch.object(
        keyword_seeding, "expand_seed_dataforseo", _dead_provider
    ):
        rows, errors = await expand_seed(
            "drain cleaning", location_code=2036, min_keywords=20
        )

    assert any("dataforseo" in e for e in errors)
    assert not [r for r in rows if r.get("provider_fallback")]
    # Only the seed placeholder may survive; nothing was expanded.
    assert all(str(r.get("ahrefs_endpoint")) == "seed" for r in rows)


async def test_a_dead_provider_reports_the_outage_rather_than_padding():
    with _live_settings(), patch.object(
        keyword_seeding, "expand_seed_dataforseo", _dead_provider
    ):
        rows, errors = await expand_seed(
            "drain cleaning", location_code=2036, min_keywords=20
        )

    assert "provider_expansion_unavailable" in errors
    assert len(rows) < 20


async def test_the_fabricating_generator_is_gone():
    assert not hasattr(keyword_seeding, "build_coverage_expansions")


async def test_a_seed_placeholder_carries_no_invented_volume():
    with _live_settings(), patch.object(
        keyword_seeding, "expand_seed_dataforseo", _dead_provider
    ):
        rows, _ = await expand_seed("drain cleaning", location_code=2036)

    for row in rows:
        assert row.get("volume") is None, row
        assert row.get("difficulty") is None, row
        assert row.get("cpc") is None, row


async def test_missing_location_is_reported_and_invents_nothing():
    with _live_settings():
        rows, errors = await expand_seed("drain cleaning", location_code=None)

    assert any("location" in e for e in errors)
    assert not [r for r in rows if r.get("provider_fallback")]


def test_data_quality_summary_has_nothing_to_flag_when_nothing_is_fabricated():
    rows = [
        {"keyword": "drain cleaning", "volume": 480},
        {"keyword": "blocked drain", "volume": None},
    ]
    summary = summarize_keyword_data_quality(rows)
    assert summary["estimated_count"] == 0
    assert summary["has_estimated_volumes"] is False
    assert summary["warning"] is None


# --- the outage must stay visible to the operator --------------------------


async def test_a_provider_outage_is_reported_up_to_the_caller():
    """Removing the fabricated padding must not also remove the warning. The
    operator has to know the thin pool is an outage, not a thin market."""
    from app.services.keyword_seeding import run_multi_mode_seeding

    with _live_settings(), patch.object(
        keyword_seeding, "expand_seed_dataforseo", _dead_provider
    ):
        seeding = await run_multi_mode_seeding(
            ["drain cleaning"], location_code=2036, min_volume=10
        )

    assert seeding["provider_expansion_unavailable"] is True
    assert any("unavailable" in e for e in seeding["provider_errors"])
