"""Pre-clustering live site scan (Phase 5, before clustering finalizes and
before Phase 6b URL mapping runs). Pins the fallback order: plain HTTP crawl
first, Playwright only for thin/JS-shell pages (gated + degrades safely if
unavailable), Perplexity via OpenRouter only if the crawl stays thin overall."""

from __future__ import annotations

from app.config import get_settings
from app.services import live_site_scan


def test_looks_thin_or_js_shell_flags_empty_html():
    assert live_site_scan._looks_thin_or_js_shell("") is True


def test_looks_thin_or_js_shell_flags_react_root_shell():
    html = '<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>'
    assert live_site_scan._looks_thin_or_js_shell(html) is True


def test_looks_thin_or_js_shell_accepts_real_content_page():
    paragraph = "Acme provides SEO services for local businesses. " * 20
    html = f"<html><body><h1>SEO Services</h1><p>{paragraph}</p></body></html>"
    assert live_site_scan._looks_thin_or_js_shell(html) is False


async def test_render_with_playwright_returns_none_when_disabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_playwright_rendering", False)
    result = await live_site_scan._render_with_playwright("https://acme.example/")
    assert result is None


async def test_render_with_playwright_returns_none_for_unsafe_host(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_playwright_rendering", True)
    result = await live_site_scan._render_with_playwright("http://169.254.169.254/")
    assert result is None


async def test_render_with_playwright_degrades_when_not_installed(monkeypatch):
    """Playwright is a fallback-only dependency — if it (or its browser
    binary) isn't installed in a given environment, the scan must not crash,
    just skip rendering for that page."""
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_playwright_rendering", True)
    result = await live_site_scan._render_with_playwright("https://8.8.8.8/")
    assert result is None  # either genuinely not installed, or a real launch failure — both degrade to None


async def test_scan_live_site_uses_http_pages_when_crawl_is_rich(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_playwright_rendering", False)

    async def _fake_discover(url, *, max_pages):
        return [f"{url}/about", f"{url}/services", f"{url}/blog/post-1"]

    paragraph = "Acme is a real business with real content on every page. " * 10

    async def _fake_fetch(url, **kwargs):
        return {
            "url": url,
            "status_code": 200,
            "text": f"<html><body><h1>Page</h1><p>{paragraph}</p></body></html>",
            "error": None,
        }

    monkeypatch.setattr(live_site_scan, "discover_site_urls", _fake_discover)
    monkeypatch.setattr(live_site_scan, "fetch_url", _fake_fetch)

    result = await live_site_scan.scan_live_site("https://acme.example")
    assert result["page_count"] == 3
    assert result["source_counts"]["http"] == 3
    assert result["source_counts"]["playwright"] == 0
    assert result["source_counts"]["perplexity"] == 0
    assert all(p["scan_source"] == "http" for p in result["pages"])


async def test_scan_live_site_falls_back_to_playwright_for_thin_pages(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_playwright_rendering", True)

    async def _fake_discover(url, *, max_pages):
        return [f"{url}/spa-page"]

    async def _fake_fetch(url, **kwargs):
        return {
            "url": url,
            "status_code": 200,
            "text": '<html><body><div id="root"></div></body></html>',
            "error": None,
        }

    paragraph = "Rendered content that only exists after JS runs. " * 10
    rendered_html = f"<html><body><h1>Rendered</h1><p>{paragraph}</p></body></html>"

    async def _fake_render(url, **kwargs):
        return rendered_html

    monkeypatch.setattr(live_site_scan, "discover_site_urls", _fake_discover)
    monkeypatch.setattr(live_site_scan, "fetch_url", _fake_fetch)
    monkeypatch.setattr(live_site_scan, "_render_with_playwright", _fake_render)

    result = await live_site_scan.scan_live_site("https://acme.example")
    assert result["page_count"] == 1
    assert result["source_counts"]["playwright"] == 1
    assert result["pages"][0]["scan_source"] == "playwright"


async def test_scan_live_site_skips_blocked_unsafe_host_results(monkeypatch):
    async def _fake_discover(url, *, max_pages):
        return ["http://169.254.169.254/"]

    async def _fake_fetch(url, **kwargs):
        return {"url": url, "status_code": 0, "text": "", "error": "blocked_unsafe_host"}

    monkeypatch.setattr(live_site_scan, "discover_site_urls", _fake_discover)
    monkeypatch.setattr(live_site_scan, "fetch_url", _fake_fetch)

    result = await live_site_scan.scan_live_site("https://acme.example")
    assert result["pages"] == []


async def test_scan_live_site_falls_back_to_perplexity_when_thin(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_playwright_rendering", False)

    async def _fake_discover(url, *, max_pages):
        return [f"{url}/"]

    async def _fake_fetch(url, **kwargs):
        return {"url": url, "status_code": 200, "text": "", "error": None}

    async def _fake_research_crawl(url, *, max_pages):
        return {
            "pages": [
                {"url": f"{url}about", "status_code": 200, "title": "About Acme"},
                {"url": f"{url}services", "status_code": 200, "title": "Services"},
            ],
            "source": "perplexity_research",
        }

    monkeypatch.setattr(live_site_scan, "discover_site_urls", _fake_discover)
    monkeypatch.setattr(live_site_scan, "fetch_url", _fake_fetch)

    import app.integrations.site_research as site_research

    monkeypatch.setattr(site_research, "research_ready", lambda: True)
    monkeypatch.setattr(site_research, "research_site_crawl", _fake_research_crawl)

    result = await live_site_scan.scan_live_site("https://acme.example/")
    assert result["source_counts"]["perplexity"] == 2
    assert any(p["scan_source"] == "perplexity" for p in result["pages"])


async def test_scan_live_site_never_raises_when_discovery_fails(monkeypatch):
    async def _boom(url, *, max_pages):
        raise RuntimeError("dns failure")

    monkeypatch.setattr(live_site_scan, "discover_site_urls", _boom)

    result = await live_site_scan.scan_live_site("https://acme.example")
    assert result["pages"] == []
    assert result["page_count"] == 0
