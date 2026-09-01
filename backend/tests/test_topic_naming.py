"""Topic naming specificity tests."""

from app.services.keyword_clustering import build_clusters_deterministic
from app.services.topic_naming import (
    is_generic_label,
    phrase_title,
    specific_cluster_name,
    specific_page_title,
    specific_pillar_label,
)


def test_phrase_title_keeps_acronyms():
    assert phrase_title("local seo services") == "Local SEO Services"
    assert "Complete Guide" not in specific_page_title("local seo services", content_type="guide")


def test_generic_heads_get_qualified():
    assert is_generic_label("SEO")
    assert is_generic_label("Marketing")
    assert not is_generic_label("Local SEO Services")
    name = specific_cluster_name("seo", intent="informational")
    assert name != "Seo"
    assert "SEO" in name
    assert len(name.split()) >= 2


def test_specific_primary_becomes_cluster_name():
    name = specific_cluster_name("enterprise seo audit checklist", intent="commercial")
    assert "Enterprise SEO Audit Checklist" == name


def test_pillar_prefers_keyword_over_generic_title():
    label = specific_pillar_label("SEO", "b2b saas seo strategy", intent="commercial")
    assert "B2B" in label or "SaaS" in label or "saas" in label.lower()
    assert label != "SEO"


def test_clustering_uses_specific_primary_name():
    rows = [
        {"keyword": "local seo services", "volume": 1200, "difficulty": 30, "opportunity_score": 80},
        {"keyword": "local seo company", "volume": 800, "difficulty": 35, "opportunity_score": 70},
        {"keyword": "local seo near me", "volume": 600, "difficulty": 28, "opportunity_score": 75},
        {"keyword": "seo", "volume": 50000, "difficulty": 90, "opportunity_score": 20},
    ]
    report = build_clusters_deterministic(rows, brand_name="Acme SEO", domain="acme.example")
    names = [c["name"] for c in report.get("clusters") or []]
    assert names
    # Should not collapse everything to bare "Seo"
    assert not any(n.strip().lower() == "seo" for n in names)
