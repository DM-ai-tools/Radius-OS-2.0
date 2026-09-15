"""Phase 7 IA fallback when Site Architecture is not approved."""

from app.services.technical_seo_ia import build_phase7_ia_fallback


def test_build_phase7_ia_fallback_from_clusters():
    ia = build_phase7_ia_fallback(
        primary_url="https://example.com",
        website={"crawl_technical": {"status": "complete", "summary": "Crawl ok"}},
        demand={
            "clusters": [
                {"name": "SEO Services", "primary_keyword": "seo services melbourne"},
                {"name": "PPC", "primary_keyword": "google ads agency"},
            ]
        },
    )
    assert ia["source"] == "phase7_website_demand_fallback"
    assert len(ia["target_url_tree"]) >= 2
    assert any("seo-services" in n["url"] for n in ia["target_url_tree"])
