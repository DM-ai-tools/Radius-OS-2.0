"""Keyword seeding runs on DataForSEO alone — there is no SEMrush subscription.

SEMrush was the primary source with DataForSEO only topping up classes it left
empty. With no SEMrush key, every class has to come from its own DataForSEO
endpoint in one pass: suggestions=phrase, related=related, ideas=broad.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import keyword_seeding
from app.services.keyword_seeding import expand_seed


class _Settings:
    use_mock_providers = False
    use_mock_llm = True
    semrush_api_key = ""
    keyword_pool_target = 4500


def _live_settings():
    return patch.object(keyword_seeding, "get_settings", lambda: _Settings())


def _df_rows(seed):
    return (
        [
            {"keyword": f"{seed} cost", "volume": 300, "ahrefs_endpoint": "dataforseo_keyword_suggestions"},
            {"keyword": f"{seed} near me", "volume": 210, "ahrefs_endpoint": "dataforseo_keyword_suggestions"},
            {"keyword": f"emergency {seed}", "volume": 140, "ahrefs_endpoint": "dataforseo_related_keywords"},
            {"keyword": f"{seed} tips", "volume": 90, "ahrefs_endpoint": "dataforseo_keyword_ideas"},
        ],
        [],
    )


async def test_expansion_asks_dataforseo_for_every_match_class():
    seen = {}

    async def _fake(seed, *, location_code, min_volume=10, limit=60, classes=None, language_code=None):
        seen["classes"] = tuple(classes or ())
        return _df_rows(seed)

    with _live_settings(), patch.object(keyword_seeding, "expand_seed_dataforseo", _fake):
        rows, errors = await expand_seed("drain cleaning", location_code=2036)

    assert set(seen["classes"]) == {"phrase", "related", "broad"}
    assert [r["keyword"] for r in rows if r["keyword"] != "drain cleaning"]
    assert "semrush_unavailable" not in errors


def test_seeding_module_has_no_semrush_dependency():
    """Stronger than mocking it out: the import is gone entirely."""
    import inspect

    src = inspect.getsource(keyword_seeding)
    assert "semrush" not in src
    assert not hasattr(keyword_seeding, "semrush")


async def test_rows_are_attributed_to_dataforseo_not_semrush():
    async def _fake(seed, *, location_code, min_volume=10, limit=60, classes=None, language_code=None):
        return _df_rows(seed)

    with _live_settings(), patch.object(keyword_seeding, "expand_seed_dataforseo", _fake):
        rows, _errors = await expand_seed("drain cleaning", location_code=2036)
    sources = {r.get("source") for r in rows if r.get("ahrefs_endpoint") != "seed"}
    assert sources == {"dataforseo"}


async def test_missing_location_code_is_reported_not_crashed():
    with _live_settings():
        rows, errors = await expand_seed("drain cleaning", location_code=None)
    assert any("location" in e for e in errors)
    # The coverage fallback still keeps the phase usable, but every fabricated
    # row must be flagged — that flag is what the reports key off.
    assert all(
        r.get("provider_fallback") or r["keyword"] == "drain cleaning" for r in rows
    )


async def test_no_padding_when_dataforseo_returns_nothing():
    async def _empty(seed, *, location_code, min_volume=10, limit=60, classes=None, language_code=None):
        return [], ["dataforseo_keyword_suggestions_error"]

    with _live_settings(), patch.object(keyword_seeding, "expand_seed_dataforseo", _empty):
        rows, errors = await expand_seed(
            "drain cleaning", location_code=2036, min_keywords=20
        )
    assert "provider_expansion_unavailable" in errors
    # Nothing is fabricated to reach min_keywords; the outage is the result.
    assert len(rows) < 20
    assert not [r for r in rows if r.get("provider_fallback")]
