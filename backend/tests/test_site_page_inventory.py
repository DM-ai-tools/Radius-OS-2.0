"""Tests for site-page-inventory helpers (no live Perplexity call)."""

from app.services.site_page_inventory import (
    inventory_pages_for_sitemap,
    inventory_to_csv,
)
from app.services.site_sitemap import build_client_sitemap


def test_inventory_to_csv_headers_and_rows():
    pages = [
        {
            "url": "https://acme.example/",
            "page_type": "home",
            "title": "Acme",
            "title_length": 4,
            "desc_length": 10,
            "h1_count": 1,
            "h2_count": 2,
            "word_count": 100,
            "html_kb": 40,
            "images": 3,
            "robots": "index,follow",
            "in_xml_sitemap": True,
            "orphan": False,
            "last_modified": None,
            "status_code": 200,
            "issues": [],
        },
        {
            "url": "https://acme.example/services",
            "page_type": "service",
            "title": "Services",
            "title_length": 8,
            "desc_length": 0,
            "h1_count": 1,
            "h2_count": 0,
            "word_count": 50,
            "html_kb": 20,
            "images": 1,
            "robots": None,
            "in_xml_sitemap": False,
            "orphan": True,
            "last_modified": None,
            "status_code": 200,
            "issues": ["missing_meta_description"],
        },
    ]
    csv_text = inventory_to_csv(pages)
    assert "url,page_type,title" in csv_text
    assert "https://acme.example/services" in csv_text
    assert "missing_meta_description" in csv_text


def test_build_client_sitemap_merges_perplexity_inventory():
    inventory = {
        "pages": [
            {
                "url": "https://acme.example/services/seo",
                "path": "/services/seo",
                "title": "SEO Services",
                "status_code": 200,
                "page_type": "service",
                "cluster": "service",
                "word_count": 800,
                "in_xml_sitemap": True,
                "orphan": False,
                "issues": [],
            }
        ],
        "stats": {"pages_found": 1},
        "findings": [{"severity": "info", "finding": "ok", "evidence": []}],
        "method_note": "test",
        "csv": "url\n",
    }
    sitemap = build_client_sitemap(
        primary_url="https://acme.example/",
        crawl={"discovered_urls": ["https://acme.example/about"], "pages_found": 1},
        page_inventory=inventory,
    )
    assert "perplexity_inventory" in sitemap["sources"]
    assert sitemap["url_count"] >= 2
    assert sitemap["page_inventory_stats"]["pages_found"] == 1
    paths = {p["path"] for p in sitemap["pages"]}
    assert "/services/seo" in paths
    assert "/about" in paths


def test_inventory_pages_for_sitemap_shapes_blog():
    rows = inventory_pages_for_sitemap(
        {
            "pages": [
                {
                    "url": "https://acme.example/blog/post",
                    "path": "/blog/post",
                    "title": "Post",
                    "page_type": "blog_article",
                    "status_code": 200,
                }
            ]
        }
    )
    assert rows[0]["cluster"] == "blog"
