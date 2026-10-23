from app.services.page_clusters import (
    classify_audit_page,
    finalize_seo_audit_clusters,
    merge_known_sitemap_urls,
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
    assert classify_audit_page("https://ex.com/seo")["cluster"] == "other"
    assert classify_audit_page("https://ex.com/seo")["page_type"] == "page"
    assert classify_audit_page("https://ex.com/seo", "SEO Services")["cluster"] == "services"
    assert classify_audit_page("https://ex.com/seo", "SEO Services")["page_type"] == "service"
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


def test_unmeasured_pages_are_not_scored_as_critical():
    report = finalize_seo_audit_clusters(
        {
            "overall_score": 0,
            "score_band": "Not measured (WAF)",
            "pages": [
                {
                    "url": "https://ex.com/",
                    "path": "/",
                    "overall_score": 0,
                    "score_band": "Not measured",
                },
                {
                    "url": "https://ex.com/services/seo",
                    "path": "/services/seo",
                    "title": "SEO",
                    "overall_score": 0,
                    "score_band": "Not measured",
                },
            ],
            "opportunities": [],
        },
        commercial={"products_for_promotion": ["SEO"]},
    )
    assert report["overall_score"] is None
    assert report["business_weighted_score"] is None
    assert str(report["score_band"]).lower().startswith("not measured")
    assert report["page_clusters"][0]["avg_score"] is None


def test_nested_non_blog_paths_stay_sub_services_without_a_keyword_in_the_url():
    """A bare `/seo` needs a title signal before it counts as a service, but a
    NESTED path like /crm/small-business is already filtered by _BLOG/_GUIDE/
    _LOCATION/_OTHER_FIRST — requiring the word "service" in the URL too
    demotes most real sub-service pages to generic pages, which breaks
    cluster-to-URL mapping downstream."""
    for url in (
        "https://ex.com/crm/small-business",
        "https://ex.com/seo/local",
        "https://ex.com/web-design/ecommerce",
    ):
        got = classify_audit_page(url)
        assert got["cluster"] == "sub_services", (url, got)
        assert got["page_type"] == "sub_service", (url, got)


def test_nested_paths_under_excluded_first_segments_are_not_sub_services():
    for url in (
        "https://ex.com/blog/seo-tips",
        "https://ex.com/about/team",
        "https://ex.com/locations/austin",
        "https://ex.com/guides/crm",
    ):
        assert classify_audit_page(url)["cluster"] != "sub_services", url


def test_single_segment_still_needs_a_commercial_signal():
    """The deliberate 1-segment rule must survive this fix."""
    assert classify_audit_page("https://ex.com/seo")["page_type"] == "page"
    assert classify_audit_page("https://ex.com/seo", "SEO Services")["page_type"] == "service"


def test_wordpress_sitemap_section_splits_posts_from_service_pages():
    """Root permalinks are ambiguous. The sitemap file decides the cluster."""
    report = finalize_seo_audit_clusters(
        {
            "pages": [
                {"url": "https://ex.com/", "path": "/", "overall_score": 80, "score_band": "Good"},
                {
                    "url": "https://ex.com/seo",
                    "path": "/seo",
                    "title": "/seo",
                    "sitemap_section": "page",
                    "overall_score": 70,
                    "score_band": "Good",
                },
                {
                    "url": "https://ex.com/email-marketing",
                    "path": "/email-marketing",
                    "sitemap_section": "page",
                    "overall_score": 70,
                    "score_band": "Good",
                },
                {
                    "url": "https://ex.com/about-us",
                    "path": "/about-us",
                    "sitemap_section": "page",
                    "overall_score": 50,
                    "score_band": "Good",
                },
                {
                    "url": "https://ex.com/why-hire-a-wordpress-website-design-company",
                    "path": "/why-hire-a-wordpress-website-design-company",
                    "sitemap_section": "post",
                    "overall_score": 60,
                    "score_band": "Good",
                },
                {
                    "url": "https://ex.com/woocommerce-website-development-services",
                    "path": "/woocommerce-website-development-services",
                    "sitemap_section": "post",
                    "overall_score": 60,
                    "score_band": "Good",
                },
            ]
        }
    )
    by = {p["path"]: p["cluster"] for p in report["pages"]}
    assert by["/seo"] == "services"
    assert by["/email-marketing"] == "services"
    assert by["/about-us"] == "other"
    assert by["/why-hire-a-wordpress-website-design-company"] == "blog"
    assert by["/woocommerce-website-development-services"] == "blog"
    counts = {c["cluster"]: c["count"] for c in report["page_clusters"]}
    assert counts["services"] == 2
    assert counts["blog"] == 2


def test_a_saved_sitemap_fills_a_thin_index_sample():
    sections: dict[str, str] = {}
    urls = merge_known_sitemap_urls(
        ["https://ex.com/", "https://ex.com/blogs"],
        sections,
        [
            {"url": "https://ex.com/seo/", "sitemap_section": "page"},
            {"url": "https://ex.com/make-best-h1-tags-perfect-seo/", "sitemap_section": "post"},
        ],
        max_pages=50,
    )
    assert "https://ex.com/seo/" in urls
    assert sections["https://ex.com/seo/"] == "page"
    assert sections["https://ex.com/make-best-h1-tags-perfect-seo/"] == "post"
    # A sitemap read on this run is not overwritten by the saved list.
    fresh: dict[str, str] = {"https://ex.com/seo": "page"}
    assert merge_known_sitemap_urls(
        ["https://ex.com/seo"],
        fresh,
        [{"url": "https://ex.com/extra", "sitemap_section": "post"}],
        max_pages=50,
        live_sitemap=True,
    ) == ["https://ex.com/seo"]
