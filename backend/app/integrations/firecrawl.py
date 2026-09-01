"""Firecrawl — scrape a live reference page so the publish preview matches the real site.

Phase 12 needs to show a reviewer what the page will look like *in context*. Firecrawl
returns rendered HTML/markdown for a template page (JS included, which the plain httpx
fetcher in web_fetch cannot do), from which we lift the heading pattern and layout hints.

Scraping is read-only and hits the client's own site. Every failure degrades to
``available: False`` rather than raising — a missing reference page must not block a
preview.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.integrations.web_fetch import _is_public_host  # SSRF guard, shared
from app.logging_config import get_logger

log = get_logger("firecrawl")

BASE = "https://api.firecrawl.dev/v1"
TIMEOUT = 60.0


def configured() -> bool:
    return bool(get_settings().firecrawl_api_key)


def _safe_target(url: str) -> tuple[bool, str]:
    """Reuse the web_fetch SSRF guard: never scrape private/loopback/metadata hosts."""
    from urllib.parse import urlparse

    target = url if "://" in url else f"https://{url}"
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https"):
        return False, "unsupported_scheme"
    ok = _is_public_host(parsed.hostname)
    if ok is False:
        return False, "blocked_unsafe_host"
    if ok is None:
        return False, "dns_resolution_failed"
    return True, target


async def scrape_page(url: str, *, formats: list[str] | None = None) -> dict[str, Any]:
    """Scrape one URL. Returns {available, html, markdown, metadata} — never raises."""
    settings = get_settings()
    if not settings.firecrawl_api_key:
        return {"available": False, "error": "firecrawl_not_configured", "url": url}

    ok, target = _safe_target(url)
    if not ok:
        log.warning("firecrawl_blocked_target", url=url, reason=target)
        return {"available": False, "error": target, "url": url}

    payload: dict[str, Any] = {
        "url": target,
        "formats": formats or ["markdown", "html"],
        "onlyMainContent": True,
    }
    headers = {
        "Authorization": f"Bearer {settings.firecrawl_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(f"{BASE}/scrape", json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001
        log.warning("firecrawl_request_failed", url=target, error=str(exc))
        return {"available": False, "error": f"request_failed: {exc}", "url": target}

    if resp.status_code == 401:
        return {"available": False, "error": "unauthorized", "url": target}
    if resp.status_code == 429:
        return {"available": False, "error": "rate_limited", "url": target}
    if resp.status_code >= 400:
        log.warning("firecrawl_http_error", url=target, status=resp.status_code)
        return {"available": False, "error": f"http_{resp.status_code}", "url": target}

    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"bad_json: {exc}", "url": target}

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "available": bool(data.get("html") or data.get("markdown")),
        "url": meta.get("sourceURL") or target,
        "html": data.get("html") or "",
        "markdown": data.get("markdown") or "",
        "metadata": {
            "title": meta.get("title"),
            "description": meta.get("description"),
            "status_code": meta.get("statusCode"),
        },
        "source": "firecrawl",
    }


def layout_hints(scrape: dict[str, Any]) -> dict[str, Any]:
    """Derive light structural hints from a scrape — headings and rough section count.

    Deliberately shallow: this informs how the preview is framed, it does not try to
    reconstruct the client's theme.
    """
    if not scrape.get("available"):
        return {"available": False}
    md = str(scrape.get("markdown") or "")
    h1s = [ln[2:].strip() for ln in md.splitlines() if ln.startswith("# ")]
    h2s = [ln[3:].strip() for ln in md.splitlines() if ln.startswith("## ")]
    return {
        "available": True,
        "reference_url": scrape.get("url"),
        "reference_title": (scrape.get("metadata") or {}).get("title"),
        "h1_sample": h1s[:1],
        "h2_pattern": h2s[:8],
        "section_count": len(h2s),
    }
