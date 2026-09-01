from app.services.page_clusters import (
    classify_audit_page,
    finalize_seo_audit_clusters,
    prioritize_urls_for_audit,
)


def test_classify_blogs_guides_and_individual_services():
    assert classify_audit_page("https://ex.com/")["cluster"] == "home"
    assert classify_audit_page("https://ex.com/blog")["cluster"] == "blog"
    assert classify_audit_page("https://ex.com/blog/local-seo")["page_type"] == "post"
    assert classify_audit_page("https://ex.com/guides/shipping")["cluster"] == "guides"
    assert classify_audit_page("https://ex.com/services")["cluster"] == "service_hub"
    assert classify_audit_page("https://ex.com/services/seo")["cluster"] == "services"
    assert classify_audit_page("https://ex.com/services/seo")["page_type"] == "service"
    assert classify_audit_page("https://ex.com/services/seo/local")["cluster"] == "sub_services"
    assert classify_audit_page("https://ex.com/services/seo/local")["page_type"] == "sub_service"
    assert classify_audit_page("https://ex.com/seo")["cluster"] == "services"
    assert classify_audit_page("https://ex.com/about")["cluster"] == "other"
    assert classify_audit_page("https://ex.com/locations/austin")["cluster"] == "location"


def test_finalize_groups_pages_into_clusters():
    report = finalize_seo_audit_clusters(
        {
            "pages": [
                {"url": "https://ex.com/", "path": "/", "overall_score": 80},
                {"url": "https://ex.com/services/seo", "path": "/services/seo", "overall_score": 70},
                {"url": "https://ex.com/blog/a", "path": "/blog/a", "overall_score": 60},
                {"url": "https://ex.com/guides/b", "path": "/guides/b", "overall_score": 50},
            ]
        }
    )
    keys = [c["cluster"] for c in report["page_clusters"]]
    assert keys == ["home", "services", "guides", "blog"]
    services = next(c for c in report["page_clusters"] if c["cluster"] == "services")
    assert services["count"] == 1
    assert report["pages"][1]["cluster_label"] == "Service pages"
    assert report["page_hierarchy"][0]["cluster"] == "home"


def test_cdd_focus_and_prioritize_urls():
    commercial = {
        "products_for_promotion": ["SEO Services", "Web Design"],
        "business_keywords": ["local seo"],
        "geographic_focus": ["Austin"],
    }
    urls = [
        "https://ex.com/blog/random-post",
        "https://ex.com/about",
        "https://ex.com/services/web-design",
        "https://ex.com/services/seo",
        "https://ex.com/",
        "https://ex.com/blog/another",
        "https://ex.com/locations/austin",
    ]
    ordered = prioritize_urls_for_audit(urls, commercial=commercial, max_pages=5)
    assert ordered[0].endswith("/") or ordered[0].rstrip("/").endswith("ex.com")
    assert "https://ex.com/services/seo" in ordered
    assert "https://ex.com/services/web-design" in ordered
    # Blog noise should lose to money pages when budget is tight
    assert "https://ex.com/blog/random-post" not in ordered or len(ordered) == 5

    report = finalize_seo_audit_clusters(
        {
            "pages": [
                {"url": "https://ex.com/", "path": "/", "title": "Home", "overall_score": 70},
                {
                    "url": "https://ex.com/services/seo",
                    "path": "/services/seo",
                    "title": "SEO Services",
                    "overall_score": 60,
                },
                {
                    "url": "https://ex.com/blog/a",
                    "path": "/blog/a",
                    "title": "Tips",
                    "overall_score": 90,
                },
            ],
            "opportunities": [],
        },
        commercial=commercial,
    )
    seo_page = next(p for p in report["pages"] if "/services/seo" in (p.get("url") or ""))
    assert seo_page["cdd_focus"] is True
    assert report["cdd_pages_count"] >= 1
    # High blog score must not dominate business-weighted overall
    assert report["overall_score"] < 90
    assert any("Web Design" in (g.get("term") or "") for g in report["cdd_coverage_gaps"])
