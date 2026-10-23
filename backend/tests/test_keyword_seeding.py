"""Tests for Ahrefs multi-mode keyword seeding (exact / related / broad)."""

import pytest

from app.integrations import ahrefs as _ahrefs
from app.integrations import semrush as _semrush
from app.services.keyword_seeding import (
    build_seed_clusters,
    classify_expansion,
    classify_vs_seed,
    clean_provider_seed,
    expand_seed,
    flatten_dataset,
    run_multi_mode_seeding,
)


@pytest.fixture(autouse=True)
def _reset_ahrefs_explorer_block():
    _ahrefs.reset_keywords_explorer_block()
    _semrush.reset_keywords_explorer_block()
    yield
    _ahrefs.reset_keywords_explorer_block()
    _semrush.reset_keywords_explorer_block()


def test_no_generator_fabricates_coverage_keywords():
    """build_coverage_expansions invented keywords and volumes to pad a thin
    pool. Every metric must come from a provider, so it is gone."""
    from app.services import keyword_seeding

    assert not hasattr(keyword_seeding, "build_coverage_expansions")


@pytest.mark.asyncio
async def test_expand_reports_the_outage_instead_of_padding(monkeypatch):
    """DataForSEO is the only provider. When it fails the seed yields almost
    nothing and says so — it does not invent keywords to hit min_keywords."""
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    async def _empty_dfs(*args, **kwargs):
        return [], ["dataforseo_failed"]

    monkeypatch.setattr(keyword_seeding, "expand_seed_dataforseo", _empty_dfs)

    rows, errors = await expand_seed(
        "Email Marketing",
        country="au",
        min_volume=10,
        location_code=2036,
        min_keywords=20,
    )
    assert "provider_expansion_unavailable" in errors
    assert rows == []
    assert not [r for r in rows if r.get("provider_fallback")]


def test_clean_provider_seed_strips_rejected_chars():
    assert clean_provider_seed("Website & Conversion Optimisation (CRO)") == (
        "Website Conversion Optimisation"
    )
    assert clean_provider_seed("Google Ads (PPC)") == "Google Ads"
    assert clean_provider_seed("Google Ads/PPC Management") == "Google Ads PPC Management"
    assert clean_provider_seed("AEO&GEO") == "AEO GEO"
    # Already-clean seeds pass through unchanged
    assert clean_provider_seed("SEO Services") == "SEO Services"


def test_classify_exact_phrase_related_broad():
    assert classify_vs_seed("seo services", "seo services") == "exact"
    # Plural / punctuation differences are still the same query
    assert classify_vs_seed("seo service", "seo services") == "exact"
    # Contiguous seed inside the keyword = phrase, wherever the seed sits
    assert classify_vs_seed("local seo services", "seo services") == "phrase"
    assert classify_vs_seed("best seo services melbourne", "seo services") == "phrase"
    # All seed tokens present but split apart = related
    assert classify_vs_seed("services for seo", "seo services") == "related"
    assert classify_vs_seed("seo audit and services", "seo services") == "related"
    # Partial or purely topical overlap = broad
    assert classify_vs_seed("seo agency pricing", "seo services") == "broad"
    assert classify_vs_seed("digital marketing tips", "seo services") == "broad"


def test_classification_ignores_which_endpoint_returned_the_row():
    """The Ahrefs 'terms' feed returns contiguous phrase matches too — trusting the
    endpoint label was what collapsed every expansion into 'related'."""
    for hint in ("matching-terms", "related-terms", "matching-terms-phrase"):
        assert classify_expansion("seo services melbourne", "seo services", source_hint=hint) == (
            "phrase"
        )


def test_single_word_seed_still_splits_across_classes():
    """A one-word seed makes contiguity and full-token-coverage identical, so the
    purpose-built endpoint decides phrase vs related vs broad."""
    assert classify_expansion("seo", "seo") == "exact"
    assert (
        classify_expansion("seo services", "seo", source_hint="matching-terms-phrase") == "phrase"
    )
    assert classify_expansion("seo services", "seo", source_hint="matching-terms") == "related"
    assert classify_expansion("seo services", "seo", source_hint="related-terms") == "broad"
    # No overlap at all is broad regardless of source
    assert classify_expansion("ppc management", "seo", source_hint="matching-terms") == "broad"


def test_build_seed_clusters_and_dataset():
    rows = [
        {
            "keyword": "seo services",
            "volume": 1200,
            "difficulty": 40,
            "seed": "seo services",
            "match_class": "exact",
            "source": "ahrefs",
        },
        {
            "keyword": "best seo services",
            "volume": 400,
            "difficulty": 35,
            "seed": "seo services",
            "match_class": "related",
            "source": "ahrefs",
        },
        {
            "keyword": "content marketing agency",
            "volume": 80,
            "difficulty": 28,
            "seed": "seo services",
            "match_class": "broad",
            "source": "ahrefs",
        },
        {
            "keyword": "web design",
            "volume": 900,
            "difficulty": 30,
            "seed": "web design",
            "match_class": "exact",
            "source": "ahrefs",
        },
        {
            "keyword": "tiny volume keyword",
            "volume": 5,
            "difficulty": 10,
            "seed": "seo services",
            "match_class": "related",
            "source": "ahrefs",
        },
    ]
    # volume<=10 should already be filtered upstream; clusters still accept rows given
    clusters = build_seed_clusters(
        [r for r in rows if (r.get("volume") or 0) > 10],
        ["seo services", "web design"],
        seed_targets={
            "seo services": {"target": "SEO Services", "target_type": "service"},
            "web design": {"target": "Web Design", "target_type": "page"},
        },
    )
    assert len(clusters) == 2
    seo = next(c for c in clusters if c["seed"] == "seo services")
    assert seo["target_type"] == "service"
    assert seo["target"] == "SEO Services"
    assert len(seo["exact"]) == 1
    assert len(seo["related"]) == 1
    assert len(seo["broad"]) == 1
    flat = flatten_dataset(clusters)
    assert len(flat) == 4
    assert {r["match_class"] for r in flat} == {"exact", "related", "broad"}
    assert all(r.get("target_type") for r in flat)


def test_flat_dataset_preserves_keyword_under_each_seed():
    clusters = build_seed_clusters(
        [
            {
                "keyword": "digital marketing agency",
                "volume": 200,
                "seed": "seo services",
                "match_class": "broad",
            },
            {
                "keyword": "digital marketing agency",
                "volume": 200,
                "seed": "ppc agency",
                "match_class": "broad",
            },
        ],
        ["seo services", "ppc agency"],
    )
    rows = flatten_dataset(clusters)
    assert len(rows) == 2
    assert {row["seed"] for row in rows} == {"seo services", "ppc agency"}


async def test_dataforseo_supplies_the_full_class_set(monkeypatch):
    """One provider now covers every class — nothing is conditional on a second
    provider having failed first."""
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    async def _dfs_rows(seed, **kwargs):
        return (
            [
                {"keyword": seed, "volume": 1200, "difficulty": 40, "cpc": 3.0},
                {"keyword": f"{seed} agency", "volume": 300, "difficulty": 35, "cpc": 2.0},
                {"keyword": f"affordable {seed}", "volume": 90, "difficulty": 20, "cpc": 1.0},
                {"keyword": "digital marketing tips", "volume": 210, "difficulty": 25, "cpc": 1.5},
            ],
            [],
        )

    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", _dfs_rows)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _dfs_rows)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", _dfs_rows)

    rows, errors = await expand_seed(
        "seo services",
        country="au",
        min_volume=10,
        location_code=2036,
    )

    classes = {r["match_class"] for r in rows}
    assert "related" in classes or "broad" in classes
    assert "provider_coverage_fallback" not in errors
    assert any(r.get("source") == "dataforseo" for r in rows)
    assert any((r.get("volume") or 0) > 10 for r in rows)


async def test_every_class_comes_from_its_dedicated_dataforseo_source(monkeypatch):
    """DataForSEO is the only provider, so one pass must fill every class.

    Was: SEMrush ran first and DataForSEO only topped up whatever SEMrush left
    empty. With no SEMrush subscription that left every class empty.
    """
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    called: list[str] = []

    async def _suggestions(seed, **kwargs):
        called.append("suggestions")
        return [{"keyword": "seo services melbourne", "volume": 260, "difficulty": 25}], []

    async def _related_kw(seed, **kwargs):
        called.append("related")
        return [{"keyword": "seo audit and services", "volume": 140, "difficulty": 18}], []

    async def _ideas(seed, **kwargs):
        called.append("ideas")
        return [{"keyword": "ppc management", "volume": 180, "difficulty": 20}], []

    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", _suggestions)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _related_kw)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", _ideas)

    rows, _ = await expand_seed(
        "seo services", country="au", min_volume=10, location_code=2036
    )

    classes = {r["match_class"] for r in rows}
    assert classes == {"phrase", "related", "broad"}
    # All three sources are queried in a single pass — nothing is conditional
    # on another provider having failed first.
    assert set(called) == {"suggestions", "related", "ideas"}
    assert all(
        r.get("source") == "dataforseo"
        for r in rows
        if r.get("ahrefs_endpoint") != "seed"
    )


async def test_low_measured_volume_is_dropped_and_missing_volume_is_flagged(monkeypatch):
    """A measured volume must be greater than 9 (9 and 4 stay out). A keyword with no
    volume is kept and flagged volume_unverified, never silently dropped (2026-10-13)."""
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    async def _suggestions(seed, **kwargs):
        assert kwargs["min_volume"] == 10
        assert kwargs["keep_null_volume"] is True
        return [
            {"keyword": "seo services melbourne", "volume": 10, "difficulty": 12},
            {"keyword": "seo services brisbane", "volume": 9, "difficulty": 11},
            {"keyword": "seo services sydney", "volume": None, "difficulty": 8},
        ], []

    async def _related_kw(seed, **kwargs):
        return [{"keyword": "services for seo", "volume": 300}], []

    async def _ideas(seed, **kwargs):
        return [{"keyword": "ppc management", "volume": 4}], []

    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", _suggestions)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _related_kw)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", _ideas)

    rows, _ = await expand_seed(
        "seo services", country="au", min_volume=10, location_code=2036
    )
    by_kw = {r["keyword"]: r for r in rows}
    assert "seo services melbourne" in by_kw and "services for seo" in by_kw
    assert "seo services brisbane" not in by_kw  # measured 9
    assert "ppc management" not in by_kw  # measured 4
    missing = by_kw["seo services sydney"]
    assert missing["volume"] is None
    assert missing["volume_unverified"] is True and missing["volume_status"] == "unverified"
    assert by_kw["seo services melbourne"]["volume_status"] == "measured"
    assert by_kw["seo services melbourne"]["volume_unverified"] is False


async def test_seeding_reports_per_seed_class_coverage(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", True)

    result = await run_multi_mode_seeding(
        ["seo services", "web design"], country="us", min_volume=10, max_seeds=5
    )

    assert result["seeds_with_all_classes"] == 2
    assert len(result["class_coverage"]) == 2
    for entry in result["class_coverage"]:
        assert entry["missing"] == []
        assert all(entry["counts"][c] >= 1 for c in ("exact", "phrase", "related", "broad"))
    for cluster in result["seed_clusters"]:
        assert cluster["classes_missing"] == []


async def test_run_multi_mode_seeding_mock(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", True)

    result = await run_multi_mode_seeding(
        ["seo services", "web design"],
        country="us",
        min_volume=10,
        max_seeds=5,
        seed_targets={
            "seo services": {"target": "SEO Services", "target_type": "service"},
            "web design": {"target": "Web Design", "target_type": "page"},
        },
    )
    assert result["keyword_count"] > 0
    assert result["class_counts"]["exact"] >= 1
    assert result["class_counts"]["phrase"] >= 1
    assert result["class_counts"]["related"] >= 1
    assert result["class_counts"]["broad"] >= 1
    assert len(result["seed_clusters"]) == 2
    assert result["target_type_counts"].get("service") == 1
    assert result["target_type_counts"].get("page") == 1
    for row in result["keyword_dataset"]:
        assert (row.get("volume") or 0) > 10
        assert row.get("match_class") in ("exact", "phrase", "related", "broad")
        assert row.get("seed")
        assert row.get("target_type") in ("service", "page", "keyword")


@pytest.mark.asyncio
async def test_ahrefs_insufficient_plan_disables_later_explorer_calls(monkeypatch):
    from app.config import get_settings
    from app.integrations import ahrefs

    ahrefs.reset_keywords_explorer_block()
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "ahrefs_api_key", "test-key", raising=False)

    calls = {"n": 0}

    class _Resp:
        status_code = 403
        text = '{ "error":"Insufficient plan" }'

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None, headers=None):
            calls["n"] += 1
            return _Resp()

    monkeypatch.setattr(ahrefs.httpx, "AsyncClient", lambda timeout=None: _Client())

    rows, errs = await ahrefs.matching_terms("seo")
    assert rows == []
    assert "ahrefs_insufficient_plan" in errs
    assert ahrefs.keywords_explorer_blocked() == "ahrefs_insufficient_plan"

    rows2, errs2 = await ahrefs.related_terms("web design")
    assert rows2 == []
    assert "ahrefs_insufficient_plan" in errs2
    assert calls["n"] == 1
    ahrefs.reset_keywords_explorer_block()


@pytest.mark.asyncio
async def test_ahrefs_plan_failure_is_rechecked_after_the_window(monkeypatch):
    from app.config import get_settings
    from app.integrations import ahrefs, provider_access

    ahrefs.reset_keywords_explorer_block()
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "ahrefs_api_key", "test-key", raising=False)
    clock = {"t": 1_000.0}
    monkeypatch.setattr(provider_access, "_now", lambda: clock["t"])

    calls = {"n": 0}

    class _Resp:
        def __init__(self, status, text, payload=None):
            self.status_code = status
            self.text = text
            self._payload = payload or {}

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(403, '{ "error":"Insufficient plan" }')
            return _Resp(200, "{}", {"keywords": [{"keyword": "seo", "volume": 100}]})

    monkeypatch.setattr(ahrefs.httpx, "AsyncClient", lambda timeout=None: _Client())

    rows, errs = await ahrefs.matching_terms("seo")
    assert "ahrefs_insufficient_plan" in errs
    assert ahrefs.keywords_explorer_blocked() == "ahrefs_insufficient_plan"

    clock["t"] += provider_access.RECHECK_AFTER[provider_access.PLAN] + 1
    assert ahrefs.keywords_explorer_blocked() is None

    rows2, errs2 = await ahrefs.matching_terms("seo")
    assert errs2 == []
    assert rows2[0]["keyword"] == "seo"
    assert ahrefs.keywords_explorer_blocked() is None
    assert calls["n"] == 2
    ahrefs.reset_keywords_explorer_block()


@pytest.mark.asyncio
async def test_duplicate_keywords_keep_measured_metrics(monkeypatch):
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    async def _suggestions(seed, **kwargs):
        return [
            {
                "keyword": "Local SEO",
                "volume": 400,
                "difficulty": 22,
                "cpc": None,
                "sources": ["keyword_suggestions"],
            },
            {
                "keyword": "local seo services",
                "volume": 80,
                "difficulty": 18,
                "cpc": 1.2,
                "sources": ["keyword_suggestions"],
            },
        ], []

    async def _related_kw(seed, **kwargs):
        return [
            {
                "keyword": "local seo",
                "volume": None,
                "difficulty": None,
                "cpc": 3.5,
                "sources": ["related_keywords"],
            }
        ], []

    async def _ideas(seed, **kwargs):
        return [{"keyword": "local seo", "volume": 150, "difficulty": 30, "cpc": None}], []

    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", _suggestions)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _related_kw)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", _ideas)

    rows, errors = await expand_seed(
        "local seo", country="au", min_volume=10, location_code=2036
    )
    assert errors == [] or "provider_expansion_unavailable" not in errors
    match = next(r for r in rows if r["keyword"].lower() == "local seo")
    assert match["volume"] == 400
    assert match["difficulty"] == 22
    assert match["cpc"] == 3.5
    assert "keyword_suggestions" in match["sources"]
    assert "related_keywords" in match["sources"]
    assert match["seed"] == "local seo"


@pytest.mark.asyncio
async def test_failed_endpoint_keeps_the_others(monkeypatch):
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    async def _suggestions(seed, **kwargs):
        raise TimeoutError("timed out")

    async def _related_kw(seed, **kwargs):
        return [{"keyword": "seo audit and services", "volume": 40, "difficulty": 11}], []

    async def _ideas(seed, **kwargs):
        return [], ["dataforseo_ideas_failed"]

    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", _suggestions)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _related_kw)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", _ideas)

    rows, errors = await expand_seed(
        "seo services", country="au", min_volume=10, location_code=2036
    )
    assert any("keyword_suggestions" in err for err in errors)
    assert any("ideas" in err for err in errors)
    assert [r["keyword"] for r in rows] == ["seo audit and services"]
    assert rows[0]["volume"] == 40


@pytest.mark.asyncio
async def test_every_configured_seed_is_fetched(monkeypatch):
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    seen: list[str] = []

    async def _rows(seed, **kwargs):
        seen.append(seed)
        return [{"keyword": f"{seed} agency", "volume": 25, "difficulty": 10}], []

    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", _rows)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _rows)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", _rows)

    async def _no_volume(keywords, **kwargs):
        return [], []

    monkeypatch.setattr(keyword_seeding.dataforseo, "search_volume", _no_volume)

    result = await run_multi_mode_seeding(
        ["seo services", "content marketing", "google ads"],
        country="au",
        min_volume=10,
        max_seeds=30,
        location_code=2036,
        language_code="en",
    )
    assert set(seen) == {"seo services", "content marketing", "google ads"}
    assert {row["seed"] for row in result["keyword_dataset"]} == {
        "seo services",
        "content marketing",
        "google ads",
    }
    assert all(int(row["volume"]) > 9 for row in result["keyword_dataset"])
