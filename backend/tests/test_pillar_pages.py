"""Service-page keywords stay on the pillar. Questions become guides. The rest stay unused."""

from app.services.pillar_pages import build_pillar_and_guide_clusters, url_map_rows_from_clusters


SYNONYMS = [
    {
        "service": "SEO",
        "suggested": ["search engine optimisation", "organic search", "google ranking"],
        "skipped": [],
    },
    {
        "service": "PPC Management",
        "suggested": ["ppc advertising", "pay per click management"],
        "skipped": [],
    },
]


def _rows() -> list[dict]:
    return [
        {"keyword": "seo agency melbourne", "volume": 1600, "cpc": 12.0},
        {"keyword": "melbourne seo agency", "volume": 800, "cpc": 11.0},
        {"keyword": "seo agency sydney", "volume": 900, "cpc": 10.0},
        {"keyword": "what is seo", "volume": 2400, "cpc": 2.0},
        {"keyword": "seo meaning", "volume": 700, "cpc": 1.5},
        {"keyword": "seo tips for small business", "volume": 80, "cpc": 3.0},
        {"keyword": "how to do seo", "volume": 20, "cpc": 1.0},
        {"keyword": "google lens", "volume": 5000, "cpc": 0.2},
        {"keyword": "ppc management melbourne", "volume": 400, "cpc": 18.0},
        {"keyword": "pay per click management", "volume": 200, "cpc": 16.0},
        {"keyword": "ppc vs seo", "volume": 300, "cpc": 8.0},
    ]


def test_one_pillar_cluster_and_guides_leave_the_rest_unused():
    result = build_pillar_and_guide_clusters(
        _rows(),
        offer_synonyms=SYNONYMS,
        services=["SEO", "PPC Management"],
        industry="Digital Marketing Agency",
    )
    pillars = {row["service"]: row for row in result["pillar_pages"]}
    seo = pillars["SEO"]
    assert seo["primary_keyword"] == "seo agency melbourne"
    assert "melbourne seo agency" in seo["secondary_keywords"]
    assert "seo agency sydney" not in seo["secondary_keywords"]
    assert "what is seo" not in seo["secondary_keywords"]

    guide_primaries = {row["primary_keyword"] for row in result["guides"] if row["service"] == "SEO"}
    assert "what is seo" in guide_primaries
    assert "how to do seo" not in guide_primaries
    assert "google lens" not in guide_primaries
    assert result["unused_keywords"] > 0

    mapped = url_map_rows_from_clusters(result)
    assert mapped[0]["l1_category"] in {"SEO", "PPC Management"}
    assert all(row["l1_category"] != "Melbourne / Local" for row in mapped)
    assert all("/ Local" not in str(row["l1_category"]) for row in mapped)
