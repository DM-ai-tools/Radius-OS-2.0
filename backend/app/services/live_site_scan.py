"""Pre-clustering live site scan.

Runs once, early in Phase 5 (search demand), before keyword clustering
finalizes and before Phase 6b's URL mapping runs — so both phases work from
one fresh crawl of the client's *current* site instead of Phase 3's
by-then-stale website audit (or nothing, if Phase 3 hasn't run).

Scan order, each step only running when the previous one came up short:
  1. Plain HTTP crawl (web_fetch.discover_site_urls / fetch_url) — fast, no JS.
  2. Playwright render — only for pages that come back thin/JS-shell from (1).
     Gated by settings.enable_playwright_rendering; degrades to "skip" (never
     a hard failure) if Playwright or its browser binary isn't installed.
  3. Perplexity via OpenRouter (site_research.research_site_crawl) — only if
     the crawl is still thin overall after (1)+(2), the same threshold
     web_fetch.discover_site_urls already uses for its own gap-fill.

The result is intentionally shaped as a ``{"pages": [...]}`` dict — that is
exactly what ``url_mapping.collect_crawl_pages`` already reads from a
``website`` argument, so callers can hand this straight to
``build_final_url_map(..., website=scan_result)`` with no changes needed on
the url_mapping side.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.integrations.web_fetch import (
    DEFAULT_HEADERS,
    assert_safe_url,
    discover_site_urls,
    fetch_url,
    page_text_excerpt,
    parse_html,
)
from app.logging_config import get_logger

log = get_logger("live_site_scan")

# A page under ~100 chars of real text, or one carrying an SPA framework's
# root-mount marker, is almost never real content — it's a shell waiting on
# client-side JS the plain HTTP fetch never ran.
_SPA_SHELL_MARKERS = (
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    "ng-version",
    "data-reactroot",
    "__next",
    "data-server-rendered",
)
_THIN_TEXT_CHARS = 120


def _looks_thin_or_js_shell(html: str) -> bool:
    if not html:
        return True
    text = page_text_excerpt(parse_html(html), limit=2000)
    if len(text) >= 400:
        return False
    lowered = html.lower()
    return len(text) < _THIN_TEXT_CHARS or any(m in lowered for m in _SPA_SHELL_MARKERS)


async def _render_with_playwright(url: str, *, timeout_ms: int = 15000) -> str | None:
    """Best-effort JS render. Never raises — any failure just means this page
    stays on its plain-HTTP result, the same as if rendering weren't tried."""
    if not get_settings().enable_playwright_rendering:
        return None
    # Playwright makes its own network connection, entirely outside httpx —
    # it needs the same SSRF guard fetch_url() already applies internally.
    ok, safe_url = assert_safe_url(url)
    if not ok:
        return None
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("playwright_not_installed", url=safe_url)
        return None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
                await page.goto(safe_url, wait_until="networkidle", timeout=timeout_ms)
                return await page.content()
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("playwright_render_failed", url=safe_url, error=str(exc))
        return None


def _page_from_html(url: str, html: str, *, source: str) -> dict[str, Any]:
    parser = parse_html(html)
    return {
        "url": url,
        "path": urlparse(url).path or "/",
        "title": parser.title,
        "meta_description": parser.meta.get("description", ""),
        "h1": parser.h1s[0] if parser.h1s else "",
        "text_excerpt": page_text_excerpt(parser, limit=600),
        "scan_source": source,
    }


async def scan_live_site(
    primary_url: str,
    *,
    max_pages: int | None = None,
    seed_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Fresh crawl of the client's current site, HTTP-first with fallbacks.

    When ``seed_urls`` is provided (Phase 3 ``site_sitemap``), those URLs are
    scanned first so later phases stay aligned with the approved inventory.
    Returns ``{"pages": [...], "page_count", "source_counts", "scanned_at"}``.
    Never raises — a total scan failure just yields an empty page list, the
    same "nothing to match against" state as if this step didn't exist.
    """
    settings = get_settings()
    cap = max_pages or settings.live_site_scan_max_pages
    source_counts = {"http": 0, "playwright": 0, "perplexity": 0}

    urls: list[str] = []
    seen: set[str] = set()
    for raw in seed_urls or []:
        u = str(raw or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        urls.append(u)
        if len(urls) >= cap:
            break

    if len(urls) < cap:
        try:
            discovered = await discover_site_urls(primary_url, max_pages=cap)
        except Exception as exc:  # noqa: BLE001
            log.warning("live_scan_discovery_failed", url=primary_url, error=str(exc))
            discovered = []
        for u in discovered:
            if u in seen:
                continue
            seen.add(u)
            urls.append(u)
            if len(urls) >= cap:
                break

    if not urls and primary_url:
        urls = [primary_url]

    sem = asyncio.Semaphore(6)

    async def _scan_one(url: str) -> dict[str, Any] | None:
        async with sem:
            try:
                fetched = await fetch_url(url)
            except Exception as exc:  # noqa: BLE001
                log.warning("live_scan_fetch_failed", url=url, error=str(exc))
                return None
            if fetched.get("error") == "blocked_unsafe_host":
                return None
            html = fetched.get("text") or ""
            source = "http"
            if not html or _looks_thin_or_js_shell(html):
                rendered = await _render_with_playwright(url)
                if rendered and not _looks_thin_or_js_shell(rendered):
                    html, source = rendered, "playwright"
            if not html:
                return None
            source_counts[source] += 1
            return _page_from_html(url, html, source=source)

    scanned = await asyncio.gather(*[_scan_one(u) for u in urls])
    pages: list[dict[str, Any]] = [p for p in scanned if p]

    if len(pages) < 8:
        try:
            from app.integrations.site_research import research_ready, research_site_crawl

            if research_ready():
                research = await research_site_crawl(primary_url, max_pages=cap)
                seen = {p["path"] for p in pages}
                for row in research.get("pages") or []:
                    url = row.get("url")
                    if not url or int(row.get("status_code") or 0) >= 400:
                        continue
                    path = urlparse(url).path or "/"
                    if path in seen:
                        continue
                    seen.add(path)
                    h1 = row.get("h1") or []
                    pages.append(
                        {
                            "url": url,
                            "path": path,
                            "title": row.get("title") or "",
                            "meta_description": row.get("meta_description") or "",
                            "h1": h1[0] if isinstance(h1, list) and h1 else "",
                            "text_excerpt": "",
                            "scan_source": "perplexity",
                        }
                    )
                    source_counts["perplexity"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("live_scan_perplexity_fallback_failed", url=primary_url, error=str(exc))

    return {
        "pages": pages,
        "page_count": len(pages),
        "source_counts": source_counts,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
