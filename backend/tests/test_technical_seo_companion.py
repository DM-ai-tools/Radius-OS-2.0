"""Tests for Phase 7 companion checks."""

from __future__ import annotations

from app.services.html_seo_signals import inspect_images
from app.services.technical_seo_companion import (
    assign_issue_confidence,
    build_internal_linking_snapshot,
    build_score_comparison,
    build_suggestion_items,
    cluster_issues_by_category,
    hreflang_findings_for_html,
    merge_sections,
)
from app.services.technical_seo_schemas import TechnicalSEOPage


def test_assign_issue_confidence_ahrefs_high():
    assert (
        assign_issue_confidence(
            {
                "source": "ahrefs",
                "ahrefs_issue_id": "x",
                "affected_url_count": 5,
                "severity": "High",
            }
        )
        == "high"
    )


def test_build_internal_linking_snapshot_orphans():
    pages = [
        TechnicalSEOPage(url="https://example.com/orphan", status_code=200, indexable=True, internal_link_count=0),
        TechnicalSEOPage(url="https://example.com/deep", status_code=200, depth=5),
    ]
    snap = build_internal_linking_snapshot(site_architecture={}, pages=pages)
    assert snap["orphan_count"] == 1
    assert snap["deep_count"] == 1
    assert len(snap["link_suggestions"]) >= 1


def test_merge_sections_combines_findings():
    merged = merge_sections(
        {"crawlability": {"score": 80, "findings": ["Ahrefs issue"]}},
        {"crawlability": {"score": 70, "findings": ["robots.txt OK"]}, "security": {"score": 90, "findings": ["HTTPS"]}},
    )
    assert "robots.txt OK" in merged["crawlability"]["findings"]
    assert "security" in merged


def test_build_score_comparison_delta():
    comp = build_score_comparison(82, {"score": 74})
    assert comp is not None
    assert comp["delta"] == 8
    assert comp["direction"] == "improved"


def test_cluster_and_suggestion_items():
    issues = [
        {
            "rule_id": "A",
            "category": "Indexation",
            "title": "Noindex",
            "severity": "High",
            "priority": 80,
            "affected_url_count": 3,
            "affected_urls": ["https://example.com/a"],
            "recommended_action": "Remove noindex",
            "source": "ahrefs",
            "ahrefs_issue_id": "n1",
        }
    ]
    grouped = cluster_issues_by_category(issues)
    assert "Indexation" in grouped
    items = build_suggestion_items(issues)
    assert items[0]["confidence"] == "high"
    assert items[0]["status"] == "pending"


def test_hreflang_sample_flags_code_host_self_and_canonical():
    html = """
    <link rel="canonical" href="https://example.com/" />
    <link rel="alternate" hreflang="en-uk" href="https://example.com/uk" />
    <link rel="alternate" hreflang="de" href="https://other.example/de" />
    """
    issues = hreflang_findings_for_html("https://example.com/en/", html)
    checks = {item["check"] for item in issues}
    assert "invalid_code" in checks
    assert "cross_host" in checks
    assert "missing_self_reference" in checks
    assert "canonical_conflict" in checks
    invalid = next(item for item in issues if item["check"] == "invalid_code")
    assert "en-gb" in invalid["issue"]


def test_image_signals_flag_png_without_dimensions():
    report = inspect_images('<img src="/hero.png" alt="Hero">')
    issues = report["findings"][0]["issues"]
    assert "missing width and height" in issues
    assert "raster format is not WebP or AVIF" in issues
    assert "first image missing srcset" in issues
    assert "missing alt" not in issues


def test_image_signals_say_when_the_page_was_not_fetched():
    report = inspect_images(None)
    assert report["fetched"] is False
    assert report["images"] == []
    assert report["note"] == "Page was not fetched — image checks were not run."
