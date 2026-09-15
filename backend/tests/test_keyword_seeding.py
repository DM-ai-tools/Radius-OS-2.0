"""Tests for Ahrefs multi-mode keyword seeding (exact / related / broad)."""

import pytest

from app.services.keyword_seeding import (
    build_seed_clusters,
    classify_expansion,
    classify_vs_seed,
    clean_provider_seed,
    expand_seed_ahrefs,
    flatten_dataset,
    run_multi_mode_seeding,
)


def test_build_coverage_expansions_reaches_twenty():
    from app.services.keyword_seeding import build_coverage_expansions

    rows = build_coverage_expansions("Email Marketing", min_count=20)
    assert len(rows) >= 20
    classes = {r["match_class"] for r in rows}
    assert "exact" in classes
    assert "phrase" in classes
    assert "related" in classes or "broad" in classes
    assert all(r.get("provider_fallback") for r in rows)


@pytest.mark.asyncio
async def test_expand_uses_coverage_fallback_when_providers_fail(monkeypatch):
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "ahrefs_api_key", "test-key", raising=False)

    async def _empty_matching(*args, **kwargs):
        return [], ["ahrefs_matching_terms_failed"]

    async def _empty_related(*args, **kwargs):
        return [], ["ahrefs_related_terms_failed"]

    async def _empty_dfs(*args, **kwargs):
        return [], ["dataforseo_failed"]

    monkeypatch.setattr(keyword_seeding.ahrefs, "matching_terms", _empty_matching)
    monkeypatch.setattr(keyword_seeding.ahrefs, "related_terms", _empty_related)
    monkeypatch.setattr(keyword_seeding, "expand_seed_dataforseo", _empty_dfs)

    rows, errors = await expand_seed_ahrefs(
        "Email Marketing",
        country="au",
        min_volume=10,
        location_code=2036,
        min_keywords=20,
    )
    assert "provider_coverage_fallback" in errors
    assert len(rows) >= 20
    assert sum(1 for r in rows if r.get("match_class") != "exact") >= 15


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


async def test_expand_falls_back_to_dataforseo_when_ahrefs_exhausted(monkeypatch):
    """When Ahrefs is quota-limited (no expansions), DataForSEO must populate
    related/broad keyword sets so each seed still yields a full set."""
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "ahrefs_api_key", "test-key", raising=False)

    async def _empty_matching(*args, **kwargs):
        return [], ["ahrefs_matching_terms_failed"]

    async def _empty_related(*args, **kwargs):
        return [], ["ahrefs_related_terms_failed"]

    async def _dfs_related(seed, **kwargs):
        return (
            [
                {"keyword": seed, "volume": 1200, "difficulty": 40, "cpc": 3.0},
                {"keyword": f"{seed} agency", "volume": 300, "difficulty": 35, "cpc": 2.0},
                {"keyword": f"affordable {seed}", "volume": 90, "difficulty": 20, "cpc": 1.0},
                {"keyword": "digital marketing tips", "volume": 210, "difficulty": 25, "cpc": 1.5},
            ],
            [],
        )

    monkeypatch.setattr(keyword_seeding.ahrefs, "matching_terms", _empty_matching)
    monkeypatch.setattr(keyword_seeding.ahrefs, "related_terms", _empty_related)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _dfs_related)

    rows, errors = await expand_seed_ahrefs(
        "seo services",
        country="au",
        min_volume=10,
        location_code=2036,
    )

    classes = {r["match_class"] for r in rows}
    assert "related" in classes or "broad" in classes
    assert any(r.get("source") == "dataforseo" for r in rows)
    assert any((r.get("volume") or 0) > 10 for r in rows)


async def test_missing_classes_topped_up_from_dedicated_dfs_sources(monkeypatch):
    """Ahrefs returning only 'terms' data must not leave phrase/broad empty — each
    missing class is topped up from the DataForSEO endpoint built for it."""
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "ahrefs_api_key", "test-key", raising=False)

    called: list[str] = []

    async def _matching(seed, **kwargs):
        # Only the 'terms' feed works; the phrase feed is quota-blocked
        if kwargs.get("match_mode") == "phrase":
            return [], ["ahrefs_http_error"]
        return [{"keyword": "services for seo", "volume": 300, "difficulty": 30}], []

    async def _related_terms(seed, **kwargs):
        return [], ["ahrefs_http_error"]

    async def _suggestions(seed, **kwargs):
        called.append("suggestions")
        return [{"keyword": "seo services melbourne", "volume": 260, "difficulty": 25}], []

    async def _ideas(seed, **kwargs):
        called.append("ideas")
        return [{"keyword": "ppc management", "volume": 180, "difficulty": 20}], []

    async def _related_kw(seed, **kwargs):
        called.append("related")
        return [{"keyword": "seo audit and services", "volume": 140, "difficulty": 18}], []

    monkeypatch.setattr(keyword_seeding.ahrefs, "matching_terms", _matching)
    monkeypatch.setattr(keyword_seeding.ahrefs, "related_terms", _related_terms)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", _suggestions)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", _ideas)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", _related_kw)

    rows, _ = await expand_seed_ahrefs(
        "seo services", country="au", min_volume=10, location_code=2036
    )

    classes = {r["match_class"] for r in rows}
    assert classes == {"exact", "phrase", "related", "broad"}
    # 'related' already came from Ahrefs terms, so only the empty classes are fetched
    assert "suggestions" in called and "ideas" in called
    assert "related" not in called


async def test_empty_class_filled_from_real_sub_threshold_keywords(monkeypatch):
    """A class with no above-floor keyword is filled from real lower-volume
    results rather than left blank — nothing is invented."""
    from app.config import get_settings
    from app.services import keyword_seeding

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "ahrefs_api_key", "test-key", raising=False)

    async def _matching(seed, **kwargs):
        if kwargs.get("match_mode") == "phrase":
            return [{"keyword": "seo services melbourne", "volume": 400}], []
        return [{"keyword": "services for seo", "volume": 300}], []

    async def _related_terms(seed, **kwargs):
        # Only sub-threshold broad results exist
        return [{"keyword": "ppc management", "volume": 4}], []

    monkeypatch.setattr(keyword_seeding.ahrefs, "matching_terms", _matching)
    monkeypatch.setattr(keyword_seeding.ahrefs, "related_terms", _related_terms)

    rows, _ = await expand_seed_ahrefs(
        "seo services", country="au", min_volume=10, location_code=None
    )

    broad = [r for r in rows if r["match_class"] == "broad"]
    assert broad, "broad class should be filled from sub-threshold results"
    assert all(r.get("below_volume_floor") for r in broad)
    assert broad[0]["keyword"] == "ppc management"


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
