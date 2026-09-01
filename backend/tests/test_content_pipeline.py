"""Content pipeline — cluster validation through title selection."""

from app.services.content_pipeline import (
    identify_primary_keyword,
    identify_secondary_keywords,
    run_cluster_page_pipeline,
    run_clusters_page_pipeline,
)
from app.services.headline_framework import (
    extract_title_patterns,
    score_titles_against_serp,
    validate_title,
)
from app.services.keyword_clustering import validate_clusters


def _sample_cluster():
    return {
        "name": "Local SEO Melbourne",
        "primary_keyword": "local seo melbourne",
        "intent": "commercial",
        "content_type": "service",
        "keywords": [
            {"keyword": "local seo melbourne", "role": "Primary", "volume": 1200},
            {"keyword": "seo agency melbourne", "role": "Secondary", "volume": 800},
        ],
        "competitor_domains": ["competitor.com"],
    }


def test_validate_clusters_flags_duplicate_primary():
    c1 = _sample_cluster()
    c2 = {**_sample_cluster(), "name": "Duplicate"}
    result = validate_clusters([c1, c2])
    assert result["valid"] is False
    assert any(i["issue"] == "duplicate_primary_keyword" for i in result["issues"])


def test_validate_clusters_passes_clean_cluster():
    result = validate_clusters([_sample_cluster()])
    assert result["valid"] is True


def test_extract_title_patterns_detects_how_to():
    patterns = extract_title_patterns(
        [
            "How to Improve Local SEO",
            "How to Rank in Google Maps",
            "Local SEO Guide for SMBs",
        ]
    )
    assert patterns["counts"]["how_to"] >= 2
    assert patterns["dominant_pattern"] == "how_to"


def test_run_cluster_page_pipeline_selects_scored_title():
    serp = {
        "validated": True,
        "dominant_format": "guide",
        "intent": "informational",
        "organic": [
            {"position": 1, "title": "How to Do Local SEO", "domain": "competitor.com"},
            {"position": 2, "title": "How to Rank Locally", "domain": "example.com"},
        ],
    }
    out = run_cluster_page_pipeline(
        _sample_cluster(),
        serp_summary=serp,
        preflight={"ia": {"url": "/services/local-seo", "page_type": "service"}},
        client_name="Click Trends",
        audience="SMB owners",
        location="Melbourne",
        page_type="service",
    )
    assert out["primary_keyword"] == "local seo melbourne"
    assert len(out["secondary_keywords"]) >= 1
    assert out["title_candidates"]
    assert out["selected_title"].get("title")
    assert "title_validation" in out
    assert out["pipeline_stages"][-1] == "validate_title"


def test_validate_title_rejects_missing_keyword_tokens():
    result = validate_title(
        "Complete Digital Playbook",
        keyword="local seo melbourne",
        serp_summary={"validated": True},
    )
    assert result["ok"] is False
    assert "missing_keyword_tokens" in result["issues"]


def test_run_clusters_page_pipeline_batch():
    report = {
        "clusters": [_sample_cluster()],
    }
    batch = run_clusters_page_pipeline(
        report,
        serp_by_keyword={"local seo melbourne": {"validated": True, "organic": []}},
        client_name="Click Trends",
    )
    assert batch["page_count"] == 1
    assert batch["pages"][0]["primary_keyword"] == "local seo melbourne"


def test_score_titles_prefers_serp_template():
    from app.services.headline_framework import build_headline_options

    candidates = build_headline_options(
        "local seo melbourne",
        page_type="guide",
        intent="informational",
        keep_scores=True,
        limit=6,
    )
    ranked = score_titles_against_serp(
        candidates,
        serp_summary={
            "validated": True,
            "organic": [{"title": "How to Improve Local SEO"}],
        },
        primary_keyword="local seo melbourne",
        intent="informational",
    )
    assert ranked[0].get("score") is not None
