"""SERP-overlap clustering for head terms.

Two keywords that return the same pages are the same query, whatever their
wording; two that share no results are different queries however similar they
read. Token-similarity clustering cannot see that, so it splits "blocked drain"
from "drain unblocking" and merges "plumber" with "plumber apprenticeship".

SERP data costs one request per keyword, so this refines only the head terms —
the rest of the pool keeps deterministic token clustering.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import keyword_serp_clustering as ksc
from app.services.keyword_serp_clustering import (
    merge_clusters_by_serp_overlap,
    select_head_terms,
    serp_overlap_groups,
)


def _rows(*pairs):
    return [{"keyword": k, "volume": v} for k, v in pairs]


# --- choosing which keywords are worth a SERP call -------------------------


def test_head_terms_are_the_highest_volume_measured_keywords():
    rows = _rows(("a", 10), ("b", 900), ("c", 500), ("d", 50))
    assert select_head_terms(rows, limit=2) == ["b", "c"]


def test_head_terms_never_include_unmeasured_keywords():
    """An unmeasured keyword has no claim on a paid SERP call."""
    rows = _rows(("measured", 100)) + [{"keyword": "unmeasured", "volume": None}]
    assert select_head_terms(rows, limit=10) == ["measured"]


def test_head_terms_dedupe_and_respect_the_limit():
    rows = _rows(("a", 100), ("A", 100), ("b", 90), ("c", 80))
    assert select_head_terms(rows, limit=2) == ["a", "b"]


def test_head_terms_of_an_empty_pool_is_empty():
    assert select_head_terms([], limit=100) == []


# --- grouping by shared results -------------------------------------------


def test_keywords_sharing_enough_results_group_together():
    serps = {
        "blocked drain": ["x.com/1", "y.com/2", "z.com/3", "q.com/9"],
        "drain unblocking": ["x.com/1", "y.com/2", "z.com/3", "w.com/8"],
        "plumber apprenticeship": ["jobs.com/1", "seek.com/2", "uni.edu/3"],
    }
    groups = serp_overlap_groups(serps, min_shared=3)
    assert ["blocked drain", "drain unblocking"] in [sorted(g) for g in groups]
    assert ["plumber apprenticeship"] in [sorted(g) for g in groups]


def test_keywords_below_the_overlap_threshold_stay_apart():
    serps = {
        "blocked drain": ["x.com/1", "y.com/2", "z.com/3"],
        "drain unblocking": ["x.com/1", "other.com/7", "more.com/8"],
    }
    groups = serp_overlap_groups(serps, min_shared=3)
    assert sorted(sorted(g) for g in groups) == [["blocked drain"], ["drain unblocking"]]


def test_overlap_is_transitive_across_a_chain():
    serps = {
        "a": ["1", "2", "3"],
        "b": ["1", "2", "3"],
        "c": ["1", "2", "3"],
    }
    groups = serp_overlap_groups(serps, min_shared=3)
    assert len(groups) == 1
    assert sorted(groups[0]) == ["a", "b", "c"]


def test_a_keyword_with_no_serp_results_is_its_own_group():
    serps = {"a": ["1", "2", "3"], "lonely": []}
    groups = serp_overlap_groups(serps, min_shared=3)
    assert ["lonely"] in [sorted(g) for g in groups]


def test_urls_are_compared_without_scheme_or_www():
    serps = {
        "a": ["https://www.x.com/1", "http://y.com/2", "https://z.com/3"],
        "b": ["http://x.com/1", "https://www.y.com/2", "http://www.z.com/3"],
    }
    groups = serp_overlap_groups(serps, min_shared=3)
    assert len(groups) == 1


# --- applying the groups to clusters --------------------------------------


def test_clusters_whose_primaries_share_a_serp_are_merged():
    clusters = [
        {"cluster_name": "Blocked Drain", "primary_keyword": "blocked drain", "keywords": [
            {"keyword": "blocked drain"}]},
        {"cluster_name": "Drain Unblocking", "primary_keyword": "drain unblocking", "keywords": [
            {"keyword": "drain unblocking"}]},
        {"cluster_name": "Hot Water", "primary_keyword": "hot water repair", "keywords": [
            {"keyword": "hot water repair"}]},
    ]
    groups = [["blocked drain", "drain unblocking"], ["hot water repair"]]
    merged = merge_clusters_by_serp_overlap(clusters, groups)

    assert len(merged) == 2
    combined = next(c for c in merged if len(c["keywords"]) == 2)
    assert {k["keyword"] for k in combined["keywords"]} == {
        "blocked drain",
        "drain unblocking",
    }
    # The higher-volume/first primary wins the merged cluster's identity.
    assert combined["primary_keyword"] == "blocked drain"
    assert combined["serp_validated"] is True


def test_clusters_untouched_by_a_group_survive_unchanged():
    clusters = [
        {"cluster_name": "Tail", "primary_keyword": "some long tail", "keywords": [
            {"keyword": "some long tail"}]},
    ]
    merged = merge_clusters_by_serp_overlap(clusters, [])
    assert len(merged) == 1
    assert merged[0]["primary_keyword"] == "some long tail"
    assert merged[0].get("serp_validated") is not True


def test_merging_keeps_every_keyword():
    clusters = [
        {"cluster_name": "A", "primary_keyword": "a", "keywords": [
            {"keyword": "a"}, {"keyword": "a2"}]},
        {"cluster_name": "B", "primary_keyword": "b", "keywords": [
            {"keyword": "b"}, {"keyword": "b2"}]},
    ]
    merged = merge_clusters_by_serp_overlap(clusters, [["a", "b"]])
    kws = {k["keyword"] for c in merged for k in c["keywords"]}
    assert kws == {"a", "a2", "b", "b2"}


# --- fetching --------------------------------------------------------------


async def test_fetch_serp_urls_asks_once_per_keyword():
    seen: list[str] = []

    async def fake_serp(keyword, **kwargs):
        seen.append(keyword)
        return {"organic": [{"url": f"https://site.com/{keyword}"}]}, []

    with patch.object(ksc.dataforseo, "serp_advanced", fake_serp):
        serps, errors, _snapshots = await ksc.fetch_serp_urls(
            ["a", "b"], location_code=2036, language_code="en"
        )

    assert sorted(seen) == ["a", "b"]
    assert serps["a"] == ["site.com/a"]
    assert errors == []


async def test_fetch_serp_urls_reports_failures_without_losing_the_rest():
    async def fake_serp(keyword, **kwargs):
        if keyword == "bad":
            return {"organic": []}, ["dataforseo_serp_failed"]
        return {"organic": [{"url": "https://site.com/ok"}]}, []

    with patch.object(ksc.dataforseo, "serp_advanced", fake_serp):
        serps, errors, _snapshots = await ksc.fetch_serp_urls(
            ["ok", "bad"], location_code=2036, language_code="en"
        )

    assert serps["ok"] == ["site.com/ok"]
    assert serps["bad"] == []
    assert "dataforseo_serp_failed" in errors


async def test_fetch_serp_urls_makes_no_calls_for_an_empty_head():
    called = {"n": 0}

    async def fake_serp(keyword, **kwargs):
        called["n"] += 1
        return {"organic": []}, []

    with patch.object(ksc.dataforseo, "serp_advanced", fake_serp):
        serps, _, _snapshots = await ksc.fetch_serp_urls([], location_code=2036, language_code="en")

    assert called["n"] == 0
    assert serps == {}


# --- orchestration ---------------------------------------------------------


async def test_refine_merges_head_term_clusters_and_reports_what_it_did():
    rows = _rows(("blocked drain", 900), ("drain unblocking", 800), ("hot water repair", 700))
    clusters = [
        {"cluster_name": "A", "primary_keyword": "blocked drain",
         "keywords": [{"keyword": "blocked drain"}]},
        {"cluster_name": "B", "primary_keyword": "drain unblocking",
         "keywords": [{"keyword": "drain unblocking"}]},
        {"cluster_name": "C", "primary_keyword": "hot water repair",
         "keywords": [{"keyword": "hot water repair"}]},
    ]

    async def fake_serp(keyword, **kwargs):
        shared = [
            {"url": "https://a.com/1"},
            {"url": "https://b.com/2"},
            {"url": "https://c.com/3"},
            {"url": "https://d.com/4"},
        ]
        other = [{"url": "https://x.com/9"}, {"url": "https://y.com/8"}, {"url": "https://z.com/7"}]
        return {"organic": shared if "drain" in keyword else other}, []

    with patch.object(ksc.dataforseo, "serp_advanced", fake_serp):
        refined, meta = await ksc.refine_clusters_with_serp(
            clusters, rows, location_code=2036, language_code="en"
        )

    assert len(refined) == 2
    assert meta["head_terms"] == 3
    assert meta["clusters_before"] == 3
    assert meta["clusters_after"] == 2
    assert meta["skipped"] is False


async def test_refine_is_skipped_without_a_locale():
    """No guessing a market to spend SERP requests against."""
    clusters = [{"cluster_name": "A", "primary_keyword": "a", "keywords": [{"keyword": "a"}]}]
    called = {"n": 0}

    async def fake_serp(keyword, **kwargs):
        called["n"] += 1
        return {"organic": []}, []

    with patch.object(ksc.dataforseo, "serp_advanced", fake_serp):
        refined, meta = await ksc.refine_clusters_with_serp(
            clusters, _rows(("a", 10)), location_code=None, language_code="en"
        )

    assert called["n"] == 0
    assert meta["skipped"] is True
    assert refined == clusters


async def test_refine_is_skipped_when_disabled():
    clusters = [{"cluster_name": "A", "primary_keyword": "a", "keywords": [{"keyword": "a"}]}]
    called = {"n": 0}

    async def fake_serp(keyword, **kwargs):
        called["n"] += 1
        return {"organic": []}, []

    with patch.object(ksc.dataforseo, "serp_advanced", fake_serp):
        _refined, meta = await ksc.refine_clusters_with_serp(
            clusters, _rows(("a", 10)), location_code=2036, language_code="en", enabled=False
        )

    assert called["n"] == 0
    assert meta["skipped"] is True


async def test_refine_never_spends_more_calls_than_the_head_term_budget():
    rows = _rows(*[(f"kw {i}", 1000 - i) for i in range(50)])
    clusters = [
        {"cluster_name": f"C{i}", "primary_keyword": f"kw {i}", "keywords": [{"keyword": f"kw {i}"}]}
        for i in range(50)
    ]
    called = {"n": 0}

    async def fake_serp(keyword, **kwargs):
        called["n"] += 1
        return {"organic": []}, []

    with patch.object(ksc.dataforseo, "serp_advanced", fake_serp):
        await ksc.refine_clusters_with_serp(
            clusters, rows, location_code=2036, language_code="en", head_terms=10
        )

    assert called["n"] == 10


# --- merging must not launder a quarantined cluster ------------------------


def test_merging_a_dropped_cluster_does_not_launder_its_verdict():
    """The cluster sense gate already judged these. Folding a "drop" cluster
    into a "keep" one must not resurrect it as a page target."""
    from app.services.keyword_clustering import _cluster_is_unverified

    clusters = [
        {
            "cluster_name": "Good", "primary_keyword": "blocked drain",
            "cluster_coherence_ok": True, "cluster_sense_verdict": "keep",
            "keywords": [{"keyword": "blocked drain"}],
        },
        {
            "cluster_name": "Bad", "primary_keyword": "drain unblocking",
            "cluster_coherence_ok": False, "cluster_sense_verdict": "drop",
            "relevance_unverified": True,
            "keywords": [{"keyword": "drain unblocking", "relevance_unverified": True}],
        },
    ]
    merged = merge_clusters_by_serp_overlap(
        clusters, [["blocked drain", "drain unblocking"]]
    )
    combined = merged[0]
    assert len(combined["keywords"]) == 2
    # The worst verdict in the merge governs the result.
    assert combined["cluster_sense_verdict"] in ("drop", "review")
    assert combined["cluster_coherence_ok"] is False
    assert _cluster_is_unverified(combined) is True


def test_merging_keeps_unverified_member_keywords_flagged():
    """`_cluster_is_unverified` is an `all()` over members, so an unverified
    keyword folded in beside verified ones would otherwise ride through."""
    clusters = [
        {"cluster_name": "A", "primary_keyword": "a", "cluster_sense_verdict": "keep",
         "keywords": [{"keyword": "a"}]},
        {"cluster_name": "B", "primary_keyword": "b", "cluster_sense_verdict": "keep",
         "keywords": [{"keyword": "b", "relevance_unverified": True,
                       "commercial_target_eligible": False}]},
    ]
    merged = merge_clusters_by_serp_overlap(clusters, [["a", "b"]])
    folded = next(
        k for k in merged[0]["keywords"] if k["keyword"] == "b"
    )
    assert folded["relevance_unverified"] is True
    assert folded["commercial_target_eligible"] is False


def test_a_review_verdict_survives_a_merge_with_a_keep():
    clusters = [
        {"cluster_name": "A", "primary_keyword": "a", "cluster_sense_verdict": "keep",
         "cluster_coherence_ok": True, "keywords": [{"keyword": "a"}]},
        {"cluster_name": "B", "primary_keyword": "b", "cluster_sense_verdict": "review",
         "keywords": [{"keyword": "b"}]},
    ]
    merged = merge_clusters_by_serp_overlap(clusters, [["a", "b"]])
    assert merged[0]["cluster_sense_verdict"] == "review"
