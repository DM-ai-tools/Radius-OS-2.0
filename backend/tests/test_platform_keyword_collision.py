"""Google/Meta platform collision must not survive Meta Ads seed lineage."""

from __future__ import annotations

import pytest

from app.services.keyword_clustering import build_clusters_deterministic
from app.services.keyword_opportunity import service_token_overlap
from app.services.keyword_relevance import build_relevance_context, evaluate_keyword


def meta_only_ctx():
    return build_relevance_context(
        services=["Meta Ads", "AI-Powered Email Marketing Automation"],
        cdd_keywords=["meta ads melbourne", "facebook ads agency"],
        brand_name="Click Trends",
        domain="clicktrends.com.au",
        seeds=["Meta Ads"],
    )


@pytest.mark.parametrize(
    "keyword,match_class",
    [
        ("google ads", "related"),
        ("google ads agency", "related"),
        ("google adwords services", "broad"),
        ("google ads management", "phrase"),
        ("gads campaign", "related"),
    ],
)
def test_meta_seed_rejects_google_platform_expansions(keyword, match_class):
    keep, reason, _ = evaluate_keyword(
        keyword,
        meta_only_ctx(),
        match_class=match_class,
        seed="Meta Ads",
    )
    assert keep is False
    # A bare platform name is caught earlier as platform navigation (2026-10-22).
    assert reason in {"platform_mismatch", "platform_navigation"}


@pytest.mark.parametrize(
    "keyword,match_class",
    [
        ("meta ads", "exact"),
        ("meta ads specialist", "phrase"),
        ("facebook ads library", "related"),
        ("instagram ads agency", "broad"),
    ],
)
def test_meta_seed_keeps_meta_family_expansions(keyword, match_class):
    keep, reason, _ = evaluate_keyword(
        keyword,
        meta_only_ctx(),
        match_class=match_class,
        seed="Meta Ads",
    )
    assert keep is True, (keyword, reason)


def test_meta_ads_manager_is_the_platform_not_a_buyer_search():
    # The advertiser's own tool. Changed from a keep on 2026-10-22 (buyer fit).
    keep, reason, _ = evaluate_keyword(
        "meta ads manager", meta_only_ctx(), match_class="phrase", seed="Meta Ads"
    )
    assert keep is False and reason == "platform_navigation"


def test_ads_alone_is_not_service_fit_between_google_and_meta():
    assert service_token_overlap("google ads", "Meta Ads") == 0
    assert service_token_overlap("google ads agency", "Meta Ads") == 0
    assert service_token_overlap("meta ads manager", "Meta Ads") >= 1


def test_tiny_google_cluster_does_not_merge_into_meta_on_shared_ads_token():
    rows = [
        {
            "keyword": "meta ads",
            "volume": 12100,
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "parent_topic": "Meta Ads",
            "match_class": "exact",
        },
        {
            "keyword": "meta ads manager",
            "volume": 4400,
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "parent_topic": "Meta Ads",
            "match_class": "phrase",
        },
        {
            "keyword": "meta ads library",
            "volume": 3600,
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "parent_topic": "Meta Ads",
            "match_class": "phrase",
        },
        {
            "keyword": "google ads",
            "volume": 74000,
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "parent_topic": "Meta Ads",
            "match_class": "related",
        },
        {
            "keyword": "google ads agency",
            "volume": 720,
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "parent_topic": "Meta Ads",
            "match_class": "related",
        },
    ]
    report = build_clusters_deterministic(rows)
    clusters = report.get("clusters") or []
    meta_members: list[str] = []
    for c in clusters:
        members = [
            str(k.get("keyword") or "").lower()
            for k in (c.get("keywords") or [])
            if isinstance(k, dict)
        ]
        if any(m.startswith("meta ") or m == "meta ads" for m in members):
            meta_members.extend(members)
        primary = str(c.get("primary_keyword") or "").lower()
        if primary.startswith("meta ") or primary == "meta ads":
            meta_members.append(primary)
    assert "google ads" not in meta_members
    assert "google ads agency" not in meta_members
    assert any(m.startswith("meta ") or m == "meta ads" for m in meta_members)
