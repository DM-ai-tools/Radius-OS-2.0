"""Site map accuracy — is the inventory fetched correctly and does it describe
the real site?

Drives ``discover_site_urls`` against a controlled fake site (redirect, sitemap
index, child sitemaps, pagination, assets, a page only reachable by crawling)
and then checks the inventory that ``build_client_sitemap`` derives from it.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from app.integrations import web_fetch
from app.integrations.web_fetch import (
    extract_sitemap_locs,
    normalize_primary_url,
)
from app.services.site_sitemap import (
    _guess_cluster,
    build_client_sitemap,
    sitemap_is_truncated,
)

ROBOTS = "User-agent: *\nAllow: /\nSitemap: https://www.acme.com/sitemap_index.xml\n"

SM_INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.acme.com/page-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.acme.com/post-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.acme.com/tag-sitemap.xml</loc></sitemap>
</sitemapindex>"""

SM_PAGE = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.acme.com/</loc></url>
  <url><loc>https://www.acme.com/about/</loc></url>
  <url><loc>https://www.acme.com/services/</loc></url>
  <url><loc>https://www.acme.com/services/seo/</loc></url>
  <url><loc>https://www.acme.com/services/seo/local/</loc></url>
  <url><loc>https://www.acme.com/contact/</loc></url>
</urlset>"""

SM_POST = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.acme.com/blog/seo-guide/</loc></url>
  <url><loc>https://www.acme.com/blog/page/2/</loc></url>
  <url><loc>https://www.acme.com/logo.png</loc></url>
</urlset>"""

SM_TAG = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.acme.com/tag/seo/</loc></url>
</urlset>"""

HOME_HTML = """<html><head><title>Acme</title></head><body>
  <a href="/about/">About</a>
  <a href="/services/">Services</a>
  <a href="/crawl-only/">Only reachable by crawling</a>
  <a href="https://twitter.com/acme">Twitter</a>
</body></html>"""

DOCS = {
    "https://www.acme.com/robots.txt": (200, ROBOTS),
    "https://www.acme.com/sitemap_index.xml": (200, SM_INDEX),
    "https://www.acme.com/page-sitemap.xml": (200, SM_PAGE),
    "https://www.acme.com/post-sitemap.xml": (200, SM_POST),
    "https://www.acme.com/tag-sitemap.xml": (200, SM_TAG),
    "https://www.acme.com/sitemap.xml": (404, ""),
}


@pytest.fixture
def fake_site(monkeypatch):
    """acme.com 301s to www.acme.com; everything else serves HOME_HTML."""
    log: list[str] = []

    async def fake_fetch(url, *, timeout=20.0, follow=True):
        log.append(url)
        resolved = url.replace("https://acme.com", "https://www.acme.com")
        if resolved in DOCS:
            status, text = DOCS[resolved]
            return {"url": resolved, "status_code": status, "text": text}
        if resolved.endswith(".xml"):
            return {"url": resolved, "status_code": 404, "text": ""}
        return {"url": resolved, "status_code": 200, "text": HOME_HTML}

    monkeypatch.setattr(web_fetch, "fetch_url", fake_fetch)
    return log


def _paths(urls):
    return [urlparse(u).path or "/" for u in urls]


# --- fetching ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_follows_robots_to_the_sitemap_index(fake_site):
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)
    assert "https://www.acme.com/sitemap_index.xml" in fake_site
    # Child sitemaps referenced by the index were pulled.
    assert "https://www.acme.com/page-sitemap.xml" in fake_site
    assert "https://www.acme.com/post-sitemap.xml" in fake_site
    assert "/services/seo/local" in _paths(urls)
    assert "/blog/seo-guide" in _paths(urls)


@pytest.mark.asyncio
async def test_tag_sitemap_is_not_followed(fake_site):
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)
    assert "https://www.acme.com/tag-sitemap.xml" not in fake_site
    assert "/tag/seo" not in _paths(urls)


@pytest.mark.asyncio
async def test_pagination_and_assets_are_excluded(fake_site):
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)
    paths = _paths(urls)
    assert "/blog/page/2" not in paths
    assert "/logo.png" not in paths


@pytest.mark.asyncio
async def test_crawl_finds_pages_missing_from_the_sitemap(fake_site):
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)
    assert "/crawl-only" in _paths(urls)


@pytest.mark.asyncio
async def test_offsite_links_are_not_collected(fake_site):
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)
    assert all("twitter.com" not in u for u in urls)


@pytest.mark.asyncio
async def test_redirected_host_yields_one_homepage_not_two(fake_site):
    """acme.com -> www.acme.com must not leave both in the inventory."""
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)

    assert {urlparse(u).netloc for u in urls} == {"www.acme.com"}
    assert _paths(urls).count("/") == 1
    assert len(urls) == len(set(urls))


@pytest.mark.asyncio
async def test_index_fallback_is_skipped_when_direct_discovery_worked(fake_site, monkeypatch):
    """Google `site:` results are unverified — they must not pad a healthy crawl."""
    called: list[str] = []

    async def fake_indexed(domain, *, limit=100):
        called.append(domain)
        return [f"https://www.acme.com/ghost-{i}" for i in range(5)]

    import app.integrations.dataforseo as dfs

    monkeypatch.setattr(dfs, "indexed_site_urls", fake_indexed)
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)

    assert called == []
    assert all("ghost" not in u for u in urls)


@pytest.mark.asyncio
async def test_index_fallback_runs_when_the_site_blocks_discovery(monkeypatch):
    """A WAF-blocked site is exactly what the fallback exists for."""
    async def blocked(url, *, timeout=20.0, follow=True):
        if url.endswith("robots.txt") or url.endswith(".xml"):
            return {"url": url, "status_code": 403, "text": ""}
        return {"url": url, "status_code": 200, "text": "<html><body>no links</body></html>"}

    called: list[str] = []

    async def fake_indexed(domain, *, limit=100):
        called.append(domain)
        return ["https://acme.com/from-index", "https://acme.com/also-indexed"]

    import app.integrations.dataforseo as dfs

    monkeypatch.setattr(web_fetch, "fetch_url", blocked)
    monkeypatch.setattr(dfs, "indexed_site_urls", fake_indexed)
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)

    assert called == ["acme.com"]
    assert "/from-index" in _paths(urls)


def test_extract_sitemap_locs_handles_namespaced_and_duplicate_locs():
    locs = extract_sitemap_locs(SM_PAGE + SM_PAGE)
    assert locs.count("https://www.acme.com/about/") == 1
    assert len(locs) == 6


# --- the inventory derived from the fetch -----------------------------------


@pytest.mark.asyncio
async def test_inventory_matches_the_real_site(fake_site):
    urls = await web_fetch.discover_site_urls("acme.com", max_pages=50)
    sitemap = build_client_sitemap(
        primary_url="acme.com", crawl={"discovered_urls": urls}
    )
    paths = {p["path"] for p in sitemap["pages"]}

    assert paths == {
        "/",
        "/about",
        "/services",
        "/services/seo",
        "/services/seo/local",
        "/contact",
        "/blog/seo-guide",
        "/crawl-only",
    }
    assert sitemap["url_count"] == len(paths)
    # Hierarchy reflects the real URL structure.
    by_path = {p["path"]: p for p in sitemap["pages"]}
    assert by_path["/services/seo"]["parent_url"] == "/services"
    assert by_path["/services/seo/local"]["parent_url"] == "/services/seo"
    assert by_path["/"]["cluster"] == "home"


def test_schemeless_primary_url_does_not_mint_a_phantom_page():
    sitemap = build_client_sitemap(
        primary_url="acme.com",
        crawl={"discovered_urls": ["https://www.acme.com/about"]},
    )
    paths = {p["path"] for p in sitemap["pages"]}
    assert "/acme.com" not in paths
    assert paths == {"/", "/about"}


def test_host_aliases_collapse_to_one_page():
    sitemap = build_client_sitemap(
        primary_url="https://acme.com",
        crawl={
            "discovered_urls": [
                "https://acme.com/services",
                "https://www.acme.com/services",
                "http://acme.com/services/",
            ]
        },
    )
    assert [p["path"] for p in sitemap["pages"]].count("/services") == 1


def test_status_and_title_survive_the_merge():
    sitemap = build_client_sitemap(
        primary_url="https://acme.com",
        crawl={
            "discovered_urls": ["https://acme.com/services"],
            "status_samples": [
                {"url": "https://acme.com/services", "status": 200, "title": "Our Services"}
            ],
        },
    )
    page = next(p for p in sitemap["pages"] if p["path"] == "/services")
    assert page["title"] == "Our Services"
    assert page["status"] == 200


# --- page typing ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/about", "/about-us", "/contact", "/careers", "/privacy-policy", "/terms", "/team"],
)
def test_corporate_pages_are_not_labelled_service_hubs(path):
    cluster, _ = _guess_cluster(path)
    assert cluster == "other", f"{path} classified as {cluster}"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", "home"),
        ("/services", "service_hub"),
        ("/services/seo", "service"),
        ("/services/seo/local", "sub_service"),
        ("/blog/post", "blog"),
        ("/guides/how-to", "guide"),
        ("/locations/sydney", "location"),
    ],
)
def test_page_types_follow_the_url_structure(path, expected):
    cluster, _ = _guess_cluster(path)
    assert cluster == expected


@pytest.mark.parametrize(
    "path",
    [
        "/the-smashing-newsletter",  # found live: "/news" matched inside it
        "/newsletter",
        "/bloggers-guide",
        "/newsagent-supplies",
    ],
)
def test_page_typing_matches_segments_not_substrings(path):
    cluster, _ = _guess_cluster(path)
    assert cluster != "blog", f"{path} mis-typed as blog"


def test_siblings_are_not_flagged_on_their_shared_section_name():
    """Found live on smashingmagazine.com: /category/ai and /category/ux read as
    the same topic because the only surviving token was 'category'."""
    sitemap = build_client_sitemap(
        primary_url="https://acme.com",
        crawl={
            "discovered_urls": [
                "https://acme.com/category/ai",
                "https://acme.com/category/ux",
                "https://acme.com/category/css",
            ]
        },
    )
    for page in sitemap["pages"]:
        assert page["potential_cannibalization"] == [], page["path"]


# --- structural flags -------------------------------------------------------


def test_section_without_an_index_page_is_not_reported_as_orphans():
    sitemap = build_client_sitemap(
        primary_url="https://acme.com",
        crawl={
            "discovered_urls": [
                "https://acme.com/blog/one",
                "https://acme.com/blog/two",
                "https://acme.com/blog/three",
            ]
        },
    )
    posts = [p for p in sitemap["pages"] if p["path"].startswith("/blog/")]
    assert posts and all(p["orphaned"] is False for p in posts)
    assert all(p["section_root"] is True for p in posts)
    assert "no index page" in (posts[0]["notes"] or "")


def test_a_hub_does_not_cannibalize_its_own_children():
    """Found live on python.org: /community was flagged against every
    /community/* page purely because they share the word."""
    sitemap = build_client_sitemap(
        primary_url="https://acme.com",
        crawl={
            "discovered_urls": [
                "https://acme.com/community",
                "https://acme.com/community/awards",
                "https://acme.com/community/forums",
                "https://acme.com/community/logos",
            ]
        },
    )
    for page in sitemap["pages"]:
        assert page["potential_cannibalization"] == [], page["path"]


def test_genuinely_duplicated_topics_are_still_flagged():
    sitemap = build_client_sitemap(
        primary_url="https://acme.com",
        seo_audit={
            "pages": [
                {"url": "https://acme.com/seo-services", "path": "/seo-services", "title": "SEO Services"},
                {"url": "https://acme.com/services/seo", "path": "/services/seo", "title": "SEO Services"},
            ]
        },
    )
    by_path = {p["path"]: p for p in sitemap["pages"]}
    assert "/services/seo" in by_path["/seo-services"]["potential_cannibalization"]


def test_a_genuinely_stranded_page_is_still_an_orphan():
    sitemap = build_client_sitemap(
        primary_url="https://acme.com",
        crawl={
            "discovered_urls": [
                "https://acme.com/services",
                "https://acme.com/orphan-section/lonely",
            ]
        },
    )
    lonely = next(p for p in sitemap["pages"] if p["path"] == "/orphan-section/lonely")
    assert lonely["orphaned"] is True


# --- truncation -------------------------------------------------------------


def test_truncated_inventory_is_detectable():
    pages = [
        {"url": f"https://acme.com/p{i}", "path": f"/p{i}", "title": f"P{i}"} for i in range(12)
    ]
    sitemap = build_client_sitemap(
        primary_url="https://acme.com", seo_audit={"pages": pages}, max_entries=5
    )
    assert sitemap["truncated"] is True
    assert sitemap_is_truncated({"site_sitemap": sitemap}) is True
    assert sitemap_is_truncated({"site_sitemap": {"url_count": 3, "pages": [1, 2, 3]}}) is False


def test_truncated_inventory_lowers_confidence_in_new_topic_calls():
    from app.services.topic_classification import classify_clusters_against_site_map

    pages = [
        {"url": f"https://acme.com/p{i}", "path": f"/p{i}", "title": f"Page {i}"}
        for i in range(8)
    ]
    cluster = {
        "name": "Payroll Pricing",
        "primary_keyword": "payroll software pricing",
        "intent": "commercial",
        "keywords": [{"keyword": "payroll software pricing", "role": "Primary", "volume": 800}],
    }
    whole = dict(cluster)
    classify_clusters_against_site_map([whole], pages=pages, inventory_complete=True)
    partial = dict(cluster)
    classify_clusters_against_site_map([partial], pages=pages, inventory_complete=False)

    assert partial["topic_confidence"] < whole["topic_confidence"]
    assert "truncated" in partial["topic_reason"]


# --- primary URL normalization ---------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme.com", "https://acme.com"),
        ("acme.com/", "https://acme.com"),
        ("www.acme.com/services/", "https://www.acme.com/services"),
        ("http://acme.com", "http://acme.com"),
        # Hostnames are case-insensitive; the path is left alone.
        ("HTTPS://ACME.COM/Services", "https://acme.com/Services"),
        ("//acme.com", "https://acme.com"),
        ("", ""),
    ],
)
def test_normalize_primary_url(raw, expected):
    assert normalize_primary_url(raw) == expected
