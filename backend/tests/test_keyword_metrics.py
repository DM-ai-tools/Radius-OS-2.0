"""One place where measured metrics enter the keyword pipeline.

Phase 5 previously truncated its own pool before asking for metrics — 60
keywords for volume, 30 for difficulty — and filled the rest from a formula.
This service asks for the whole pool, at the provider's documented batch size,
and leaves every field the provider did not answer as None.

Nothing here derives a metric. Intent comes from the search_intent endpoint,
not from a regex over the keyword string.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import keyword_metrics
from app.services.keyword_metrics import (
    MissingLocaleError,
    fetch_keyword_metrics,
    merge_metrics_into_rows,
)


def _volume_rows(keywords):
    return [
        {
            "keyword": k,
            "volume": 100 + i,
            "cpc": 1.5,
            "competition": 0.4,
            "trend": "flat",
            "source": "dataforseo",
        }
        for i, k in enumerate(keywords)
    ]


def _kd_rows(keywords):
    return [{"keyword": k, "difficulty": 33, "source": "dataforseo"} for k in keywords]


def _intent_rows(keywords):
    return [
        {
            "keyword": k,
            "intent": "transactional",
            "intent_probability": 0.91,
            "secondary_intents": [],
            "source": "dataforseo",
        }
        for k in keywords
    ]


def _patch_providers(volume=None, kd=None, intent=None, seen=None):
    async def fake_volume(keywords, **kw):
        if seen is not None:
            seen.setdefault("volume", []).extend(keywords)
        return (volume if volume is not None else _volume_rows(keywords)), []

    async def fake_kd(keywords, **kw):
        if seen is not None:
            seen.setdefault("kd", []).extend(keywords)
        return (kd if kd is not None else _kd_rows(keywords)), []

    async def fake_intent(keywords, **kw):
        if seen is not None:
            seen.setdefault("intent", []).extend(keywords)
        return (intent if intent is not None else _intent_rows(keywords)), []

    async def fake_click(keywords, **kw):
        return ([], [])

    return patch.multiple(
        keyword_metrics.dataforseo,
        search_volume=fake_volume,
        clickstream_search_volume=fake_click,
        keyword_difficulty=fake_kd,
        search_intent=fake_intent,
    )


async def test_every_keyword_in_the_pool_is_sent_for_metrics():
    """The old path asked about 60 of 3,000 keywords and guessed the rest."""
    keywords = [f"keyword number {i}" for i in range(3000)]
    seen: dict[str, list[str]] = {}

    with _patch_providers(seen=seen):
        metrics, errors = await fetch_keyword_metrics(
            keywords, location_code=2036, language_code="en"
        )

    assert len(seen["volume"]) == 3000
    assert len(seen["kd"]) == 3000
    assert len(seen["intent"]) == 3000
    assert len(metrics) == 3000
    assert errors == []


async def test_metrics_come_back_keyed_by_normalized_keyword():
    with _patch_providers():
        metrics, _ = await fetch_keyword_metrics(
            ["Drain Cleaning"], location_code=2036, language_code="en"
        )
    assert "drain cleaning" in metrics
    row = metrics["drain cleaning"]
    assert row["volume"] == 100
    assert row["cpc"] == 1.5
    assert row["competition"] == 0.4
    assert row["difficulty"] == 33
    assert row["intent"] == "transactional"
    assert row["intent_probability"] == 0.91


async def test_a_keyword_the_provider_skipped_has_null_metrics_not_guesses():
    with _patch_providers(volume=[], kd=[], intent=[]):
        metrics, _ = await fetch_keyword_metrics(
            ["blocked drain"], location_code=2036, language_code="en"
        )
    row = metrics["blocked drain"]
    assert row["volume"] is None
    assert row["cpc"] is None
    assert row["competition"] is None
    assert row["difficulty"] is None
    assert row["intent"] is None


async def test_partial_provider_coverage_fills_only_what_came_back():
    """Volume answered, difficulty did not — difficulty stays null."""
    with _patch_providers(kd=[]):
        metrics, _ = await fetch_keyword_metrics(
            ["drain cleaning"], location_code=2036, language_code="en"
        )
    row = metrics["drain cleaning"]
    assert row["volume"] == 100
    assert row["difficulty"] is None


async def test_a_missing_location_fails_fast():
    with pytest.raises(MissingLocaleError):
        await fetch_keyword_metrics(["drain cleaning"], location_code=None, language_code="en")


async def test_a_missing_language_fails_fast():
    with pytest.raises(MissingLocaleError):
        await fetch_keyword_metrics(["drain cleaning"], location_code=2036, language_code="")


async def test_intent_can_be_skipped_without_affecting_the_other_metrics():
    seen: dict[str, list[str]] = {}
    with _patch_providers(seen=seen):
        metrics, _ = await fetch_keyword_metrics(
            ["drain cleaning"], location_code=2036, language_code="en", with_intent=False
        )
    assert "intent" not in seen
    assert metrics["drain cleaning"]["volume"] == 100
    assert metrics["drain cleaning"]["intent"] is None


async def test_provider_errors_are_reported_not_swallowed():
    async def failing_volume(keywords, **kw):
        return [], ["dataforseo_volume_failed"]

    with _patch_providers(), patch.object(
        keyword_metrics.dataforseo, "search_volume", failing_volume
    ):
        _metrics, errors = await fetch_keyword_metrics(
            ["drain cleaning"], location_code=2036, language_code="en"
        )
    assert "dataforseo_volume_failed" in errors


async def test_empty_pool_makes_no_calls():
    called = {"n": 0}

    async def counting(keywords, **kw):
        called["n"] += 1
        return [], []

    with patch.multiple(
        keyword_metrics.dataforseo,
        search_volume=counting,
        keyword_difficulty=counting,
        search_intent=counting,
    ):
        metrics, errors = await fetch_keyword_metrics(
            [], location_code=2036, language_code="en"
        )
    assert called["n"] == 0
    assert metrics == {}


# --- stamping rows ---------------------------------------------------------


def test_merge_stamps_measured_metrics_onto_rows():
    rows = [{"keyword": "Drain Cleaning", "volume": None}]
    metrics = {
        "drain cleaning": {
            "volume": 480,
            "cpc": 6.1,
            "competition": 0.42,
            "trend": "rising",
            "difficulty": 24,
            "intent": "transactional",
            "intent_probability": 0.97,
            "secondary_intents": [],
        }
    }
    out = merge_metrics_into_rows(rows, metrics)
    assert out[0]["volume"] == 480
    assert out[0]["difficulty"] == 24
    assert out[0]["intent"] == "transactional"
    assert out[0]["volume_basis"] == "measured"


def test_merge_marks_a_row_the_provider_never_answered():
    rows = [{"keyword": "blocked drain"}]
    out = merge_metrics_into_rows(rows, {})
    assert out[0]["volume"] is None
    assert out[0]["volume_basis"] == "unmeasured"
    assert out[0].get("volume_estimated") is not True


def test_merge_never_overwrites_a_measured_value_with_none():
    rows = [{"keyword": "drain cleaning", "volume": 480, "difficulty": 24}]
    out = merge_metrics_into_rows(rows, {"drain cleaning": {"volume": None, "difficulty": None}})
    assert out[0]["volume"] == 480
    assert out[0]["difficulty"] == 24


# --- pool helper used by Phase 5 -------------------------------------------


async def test_measured_rows_for_pool_sends_the_whole_pool():
    """Phase 5 used to slice to [:60] for volume and [:30] for difficulty."""
    from app.services.keyword_metrics import measured_rows_for_pool

    keywords = [f"keyword number {i}" for i in range(500)]
    seen: dict[str, list[str]] = {}
    with _patch_providers(seen=seen):
        volume_rows, kd_rows, _errors = await measured_rows_for_pool(
            keywords, location_code=2036, language_code="en"
        )

    assert len(seen["volume"]) == 500
    assert len(seen["kd"]) == 500
    assert len(volume_rows) == 500
    assert len(kd_rows) == 500


async def test_measured_rows_for_pool_emits_provider_shaped_rows():
    from app.services.keyword_metrics import measured_rows_for_pool

    with _patch_providers():
        volume_rows, kd_rows, _ = await measured_rows_for_pool(
            ["drain cleaning"], location_code=2036, language_code="en"
        )

    assert volume_rows[0]["keyword"] == "drain cleaning"
    assert volume_rows[0]["volume"] == 100
    assert volume_rows[0]["source"] == "dataforseo"
    assert volume_rows[0]["intent"] == "transactional"
    assert kd_rows[0]["difficulty"] == 33


async def test_measured_rows_for_pool_omits_keywords_with_no_measurement():
    """A row with nothing measured must not enter the pool as a zero."""
    from app.services.keyword_metrics import measured_rows_for_pool

    with _patch_providers(volume=[], kd=[], intent=[]):
        volume_rows, kd_rows, _ = await measured_rows_for_pool(
            ["blocked drain"], location_code=2036, language_code="en"
        )
    assert volume_rows == []
    assert kd_rows == []


# --- provenance of intent --------------------------------------------------


def test_a_provider_intent_is_marked_measured():
    rows = [{"keyword": "drain cleaning"}]
    out = merge_metrics_into_rows(
        rows, {"drain cleaning": {"intent": "transactional", "intent_probability": 0.9}}
    )
    assert out[0]["intent_basis"] == "measured"


def test_an_absent_intent_is_marked_unmeasured_so_a_guess_cannot_pass_as_data():
    """detect_intent still fills a blank intent from keyword text downstream.
    That guess must stay distinguishable from a provider label."""
    rows = [{"keyword": "drain cleaning"}]
    out = merge_metrics_into_rows(rows, {})
    assert out[0]["intent"] is None
    assert out[0]["intent_basis"] == "unmeasured"


async def test_pool_rows_carry_the_intent_basis():
    from app.services.keyword_metrics import measured_rows_for_pool

    with _patch_providers(intent=[]):
        volume_rows, _kd, _ = await measured_rows_for_pool(
            ["drain cleaning"], location_code=2036, language_code="en"
        )
    assert volume_rows[0]["intent"] is None
    assert volume_rows[0]["intent_basis"] == "unmeasured"
