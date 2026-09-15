"""Phase 3 client website sitemap inventory."""

from __future__ import annotations

from app.services.site_sitemap import build_client_sitemap


def test_build_client_sitemap_from_seo_audit_pages():
    sitemap = build_client_sitemap(
        primary_url="https://argfinance.com.au",
        seo_audit={
            "pages": [
                {
                    "url": "https://argfinance.com.au/",
                    "path": "/",
                    "title": "Home",
                    "status": 200,
                    "cluster": "home",
                    "cluster_label": "Home",
                    "hierarchy_level": 0,
                    "cdd_focus": True,
                },
                {
                    "url": "https://argfinance.com.au/home-loans",
                    "path": "/home-loans",
                    "title": "Home loans",
                    "status": 200,
                    "cluster": "service",
                    "cluster_label": "Service pages",
                    "hierarchy_level": 2,
                },
                {
                    "url": "https://argfinance.com.au/blog/rates",
                    "path": "/blog/rates",
                    "title": "Rates guide",
                    "status": 200,
                    "cluster": "blog",
                    "cluster_label": "Blog / articles",
                    "hierarchy_level": 6,
                },
            ]
        },
    )
    assert sitemap["url_count"] == 3
    assert sitemap["sources"] == ["seo_audit"]
    assert [s["cluster"] for s in sitemap["sections"]] == ["home", "service", "blog"]
    assert sitemap["pages"][0]["path"] == "/"
    assert sitemap["pages"][0]["cdd_focus"] is True


def test_build_client_sitemap_falls_back_to_crawl_urls():
    sitemap = build_client_sitemap(
        primary_url="https://example.com",
        crawl={
            "pages_found": 3,
            "discovered_urls": [
                "https://example.com/",
                "https://example.com/services",
                "https://example.com/services/seo",
            ],
            "status_samples": [
                {"url": "https://example.com/services", "status": 200, "title": "Services"},
            ],
        },
    )
    assert sitemap["url_count"] == 3
    assert "crawl" in sitemap["sources"]
    services = next(p for p in sitemap["pages"] if p["path"] == "/services")
    assert services["title"] == "Services"
    assert services["status"] == 200


def test_build_client_sitemap_does_not_invent_urls():
    sitemap = build_client_sitemap(primary_url="https://example.com", seo_audit={}, crawl={})
    assert sitemap["url_count"] == 0
    assert sitemap["pages"] == []
    assert sitemap["sections"] == []


def test_extract_and_sitemap_urls_from_website_pack():
    from app.services.site_sitemap import (
        extract_site_sitemap,
        sitemap_pages,
        sitemap_urls,
    )

    pack = {
        "site_sitemap": {
            "url_count": 2,
            "urls": ["https://example.com/", "https://example.com/services"],
            "pages": [
                {"url": "https://example.com/", "path": "/", "title": "Home"},
                {"url": "https://example.com/services", "path": "/services", "title": "Services"},
            ],
            "sections": [],
        }
    }
    assert extract_site_sitemap(pack) is not None
    assert len(sitemap_pages(pack)) == 2
    assert sitemap_urls(pack, limit=1) == ["https://example.com"]


def test_extract_site_sitemap_from_crawl_technical_nested():
    from app.services.site_sitemap import extract_site_sitemap, sitemap_urls

    pack = {
        "crawl_technical": {
            "summary": {
                "site_sitemap": {
                    "url_count": 1,
                    "urls": ["https://example.com/a"],
                    "pages": [{"url": "https://example.com/a", "path": "/a", "title": "A"}],
                }
            }
        }
    }
    sm = extract_site_sitemap(pack)
    assert sm and sm["url_count"] == 1
    assert sitemap_urls(pack) == ["https://example.com/a"]


def test_sitemap_entry_carries_the_site_map_entity_fields():
    """The site map is the source of truth for the existing IA, so each row must
    carry hierarchy, page type, canonical and keyword context — not just a URL."""
    sitemap = build_client_sitemap(
        primary_url="https://example.com",
        seo_audit={
            "pages": [
                {
                    "url": "https://example.com/services",
                    "path": "/services",
                    "title": "Services",
                    "cluster": "service_hub",
                    "status": 200,
                },
                {
                    "url": "https://example.com/services/seo",
                    "path": "/services/seo",
                    "title": "SEO Services",
                    "cluster": "service",
                    "status": 200,
                    "canonical_url": "https://example.com/services/seo",
                    "word_count": 120,
                    "target_keywords": ["seo services", "seo agency"],
                },
            ]
        },
    )
    by_path = {p["path"]: p for p in sitemap["pages"]}
    seo = by_path["/services/seo"]

    assert seo["parent_url"] == "/services"
    assert seo["page_type"] == "service"
    assert seo["canonical_url"] == "https://example.com/services/seo"
    assert seo["existing_keywords"] == ["seo services", "seo agency"]
    assert seo["content_quality"] == "thin"
    assert "Thin content" in (seo["notes"] or "")
    assert by_path["/services"]["parent_url"] == "/"


def test_sitemap_flags_overlapping_and_orphaned_pages():
    sitemap = build_client_sitemap(
        primary_url="https://example.com",
        seo_audit={
            "pages": [
                {"url": "https://example.com/seo-services", "path": "/seo-services", "title": "SEO Services"},
                {"url": "https://example.com/services/seo", "path": "/services/seo", "title": "SEO Services"},
            ]
        },
    )
    by_path = {p["path"]: p for p in sitemap["pages"]}

    # Two pages targeting the same topic — the cannibalization the workflow
    # must see before it maps anything to either of them.
    assert "/services/seo" in by_path["/seo-services"]["potential_cannibalization"]
    assert "/seo-services" in by_path["/services/seo"]["potential_cannibalization"]
    # /services is not in the inventory, so /services/seo has no parent.
    assert by_path["/services/seo"]["orphaned"] is True
    assert by_path["/seo-services"]["orphaned"] is False


def test_collect_crawl_pages_prefers_site_sitemap():
    from app.services.url_mapping import collect_crawl_pages

    pages = collect_crawl_pages(
        website={
            "sample_urls": ["https://example.com/old"],
            "site_sitemap": {
                "pages": [
                    {"url": "https://example.com/services", "path": "/services", "title": "Services"},
                    {"url": "https://example.com/blog/x", "path": "/blog/x", "title": "Post"},
                ]
            },
        }
    )
    paths = {p["path"] for p in pages}
    assert "/services" in paths
    assert "/blog/x" in paths
