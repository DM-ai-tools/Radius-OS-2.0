"""Tests for Technical SEO priority and scoring."""

from __future__ import annotations

from app.services.technical_seo_prioritization import compute_priority
from app.services.technical_seo_rules import compute_category_scores, overall_score, severity_summary
from app.services.technical_seo_schemas import TechnicalSEOIssue


def test_compute_priority_scales_with_severity_and_urls():
    low = compute_priority(severity="Low", category="Metadata", affected_url_count=1)
    high = compute_priority(severity="Critical", category="Indexability", affected_url_count=100)
    assert high > low
    assert 1 <= high <= 100


def test_category_scores_marks_missing_pages_not_available():
    scores = compute_category_scores([], pages_available=False, ahrefs_health_score=None)
    assert scores["Metadata"]["status"] == "NOT_AVAILABLE"


def test_overall_score_prefers_ahrefs_health():
    scores = {
        "Crawlability": {"score": 70, "status": "available"},
        "Indexability": {"score": 80, "status": "available"},
    }
    assert overall_score(scores, ahrefs_health_score=85) == 85
    assert overall_score(scores, ahrefs_health_score=None) == 75


def test_severity_summary_counts():
    issues = [
        TechnicalSEOIssue(
            rule_id="A",
            category="Crawlability",
            title="t",
            description="d",
            severity="High",
            priority=50,
        ),
        TechnicalSEOIssue(
            rule_id="B",
            category="Crawlability",
            title="t2",
            description="d2",
            severity="High",
            priority=40,
        ),
    ]
    summary = severity_summary(issues)
    assert summary["High"] == 2
