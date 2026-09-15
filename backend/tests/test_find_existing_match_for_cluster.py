"""find_existing_match_for_cluster + classify_clusters_against_sitemap."""

from __future__ import annotations

from app.services.url_mapping import (
    classify_clusters_against_sitemap,
    find_existing_match_for_cluster,
)


def _cluster(**over):
    base = {
        "name": "SEO Services",
        "primary_keyword": "seo services",
        "keywords": [{"keyword": "seo services", "volume": 500, "cpc": 12.5}],
        "intent": "commercial",
        "funnel": "MOFU",
        "recommended_url": "/services/seo",
    }
    base.update(over)
    return base


def test_matches_an_existing_page_with_strong_keyword_overlap():
    pages = [
        {"url": "https://acme.example/services/seo", "path": "/services/seo", "title": "SEO Services | Acme"},
        {"url": "https://acme.example/about", "path": "/about", "title": "About Us"},
    ]
    result = find_existing_match_for_cluster(_cluster(), crawled_pages=pages)
    assert result["matched"] is True
    assert result["matched_url"] == "/services/seo"
    assert result["match_band"] in ("HIGH", "MEDIUM")


def test_no_match_when_no_page_covers_the_topic():
    pages = [{"url": "https://acme.example/careers", "path": "/careers", "title": "Careers at Acme"}]
    result = find_existing_match_for_cluster(_cluster(), crawled_pages=pages)
    assert result["matched"] is False
    assert result["matched_url"] is None


def test_no_match_when_no_pages_at_all():
    result = find_existing_match_for_cluster(
        _cluster(recommended_url=None), crawled_pages=[]
    )
    assert result["matched"] is False
    assert result["matched_url"] is None
    assert result["match_score"] == 0.0
    assert result["match_band"] == "LOW"


def test_classify_clusters_against_sitemap_splits_existing_and_new():
    clusters = [
        _cluster(),
        _cluster(
            name="Payroll Software Pricing",
            primary_keyword="payroll software pricing",
            keywords=[{"keyword": "payroll software pricing", "volume": 200}],
            intent="commercial",
            funnel="MOFU",
            recommended_url="/blog/payroll-software-pricing",
        ),
    ]
    sitemap = [
        {"url": "https://acme.example/services/seo", "path": "/services/seo", "title": "SEO Services"},
    ]
    report = classify_clusters_against_sitemap(clusters, sitemap_pages=sitemap, live_pages=[])
    assert report["pages_used"] >= 1
    assert clusters[0]["topic_disposition"] in ("existing_topic", "existing_review")
    assert clusters[1]["topic_disposition"] == "new_topic"
    assert report["new_topic_count"] + report["existing_topic_count"] + report["existing_review_count"] == 2
    assert "topic_disposition" in clusters[0]
