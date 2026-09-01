"""Tests for Phase 7 companion checks."""

from __future__ import annotations

import pytest

from app.services.technical_seo_companion import (
    assign_issue_confidence,
    build_internal_linking_snapshot,
    build_score_comparison,
    build_suggestion_items,
    cluster_issues_by_category,
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
            "category": "Indexability",
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
    assert "Indexability" in grouped
    items = build_suggestion_items(issues)
    assert items[0]["confidence"] == "high"
    assert items[0]["status"] == "pending"
