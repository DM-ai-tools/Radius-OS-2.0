"""Firecrawl — scrape a live reference page so the publish preview matches the real site.

Phase 12 needs to show a reviewer what the page will look like *in context*. Firecrawl
returns rendered HTML/markdown for a template page (JS included, which the plain httpx
fetcher in web_fetch cannot do), from which we lift the heading pattern and layout hints.

Scraping is read-only and hits the client's own site. Every failure degrades to
``available: False`` rather than raising — a missing reference page must not block a
preview.
"""

from __future__ import annotations

import re
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


async def scrape_page(
    url: str,
    *,
    formats: list[str] | None = None,
    branding: bool = False,
    full_page: bool = False,
) -> dict[str, Any]:
    """Scrape one URL. Returns {available, html, markdown, metadata} — never raises.

    ``branding=True`` asks Firecrawl v2 for the page's measured colors, fonts,
    and type sizes (used to style the content draft preview).
    """
    settings = get_settings()
    if not settings.firecrawl_api_key:
        return {"available": False, "error": "firecrawl_not_configured", "url": url}

    ok, target = _safe_target(url)
    if not ok:
        log.warning("firecrawl_blocked_target", url=url, reason=target)
        return {"available": False, "error": target, "url": url}

    if branding:
        endpoint = "https://api.firecrawl.dev/v2/scrape"
        payload = {
            "url": target,
            "formats": formats or ["markdown", "html", "branding"],
            "onlyMainContent": False,
        }
    else:
        endpoint = f"{BASE}/scrape"
        payload = {
            "url": target,
            "formats": formats or ["markdown", "html"],
            # full_page keeps navigation, header and footer (rendering comparisons).
            "onlyMainContent": not full_page,
        }
    headers = {
        "Authorization": f"Bearer {settings.firecrawl_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001
        log.warning("firecrawl_request_failed", url=target, error=str(exc))
        from app.services.api_meter import record_api_call

        await record_api_call(
            provider="firecrawl",
            operation="scrape_branding" if branding else "scrape",
            status="error",
            error_detail="request_failed",
        )
        return {"available": False, "error": f"request_failed: {exc}", "url": target}

    from app.services.api_meter import record_api_call

    operation = "scrape_branding" if branding else "scrape"
    if resp.status_code >= 400:
        await record_api_call(
            provider="firecrawl",
            operation=operation,
            status="error",
            error_detail=f"http_{resp.status_code}",
        )
    if resp.status_code == 401:
        return {"available": False, "error": "unauthorized", "url": target}
    if resp.status_code == 429:
        return {"available": False, "error": "rate_limited", "url": target}
    if resp.status_code >= 400:
        log.warning("firecrawl_http_error", url=target, status=resp.status_code)
        return {"available": False, "error": f"http_{resp.status_code}", "url": target}
    await record_api_call(provider="firecrawl", operation=operation, status="success")

    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"bad_json: {exc}", "url": target}

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    branding_raw = data.get("branding") if isinstance(data.get("branding"), dict) else {}
    return {
        "available": bool(data.get("html") or data.get("rawHtml") or data.get("markdown") or branding_raw),
        "url": meta.get("sourceURL") or target,
        "html": data.get("html") or "",
        # rawHtml keeps <head> (canonical, meta, JSON-LD); "html" is a cleaned copy.
        "raw_html": data.get("rawHtml") or "",
        "markdown": data.get("markdown") or "",
        "branding": branding_raw,
        "metadata": {
            "title": meta.get("title"),
            "description": meta.get("description"),
            "status_code": meta.get("statusCode"),
        },
        "source": "firecrawl",
    }


def branding_tokens(scrape: dict[str, Any]) -> dict[str, str]:
    """Measured colors and type from a Firecrawl branding payload.

    Values that are not real CSS colors, sizes, or font names are dropped.
    Nothing is invented to fill a gap.
    """
    branding = scrape.get("branding") if isinstance(scrape.get("branding"), dict) else {}
    if not branding:
        return {}
    colors = branding.get("colors") if isinstance(branding.get("colors"), dict) else {}
    typo = branding.get("typography") if isinstance(branding.get("typography"), dict) else {}
    families = typo.get("fontFamilies") if isinstance(typo.get("fontFamilies"), dict) else {}
    sizes = typo.get("fontSizes") if isinstance(typo.get("fontSizes"), dict) else {}
    weights = typo.get("fontWeights") if isinstance(typo.get("fontWeights"), dict) else {}
    spacing = branding.get("spacing") if isinstance(branding.get("spacing"), dict) else {}
    components = branding.get("components") if isinstance(branding.get("components"), dict) else {}
    button = components.get("buttonPrimary") if isinstance(components.get("buttonPrimary"), dict) else {}
    images = branding.get("images") if isinstance(branding.get("images"), dict) else {}

    def _hex(value: Any) -> str:
        text = str(value or "").strip()
        return text if re.fullmatch(r"#[0-9A-Fa-f]{3,8}", text) else ""

    def _size(value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if re.fullmatch(r"\d+(\.\d+)?(px|rem|em)", text) else ""

    def _font(value: Any) -> str:
        text = re.sub(r"[^A-Za-z0-9 ,\-]", "", str(value or "")).strip(" ,")
        if not text or len(text) > 80:
            return ""
        if text.lower() in {"inherit", "serif", "sans-serif", "monospace", "system-ui"}:
            return ""
        return text

    body_font = _font(families.get("primary"))
    heading_font = _font(families.get("heading")) or body_font
    if not body_font:
        fonts = branding.get("fonts") or []
        first = fonts[0] if fonts else ""
        if isinstance(first, dict):
            body_font = _font(first.get("family"))
        else:
            body_font = _font(first)
        heading_font = heading_font or body_font

    weight = ""
    raw_weight = weights.get("bold") or weights.get("medium")
    if isinstance(raw_weight, (int, float)) and 100 <= int(raw_weight) <= 900:
        weight = str(int(raw_weight))
    elif isinstance(raw_weight, str) and raw_weight.isdigit() and 100 <= int(raw_weight) <= 900:
        weight = str(int(raw_weight))

    radius = str(spacing.get("borderRadius") or "").strip().lower()
    if not re.fullmatch(r"\d+(\.\d+)?(px|rem|%)", radius):
        radius = ""

    tokens = {
        "primary": _hex(colors.get("primary") or colors.get("accent")),
        "background": _hex(colors.get("background")),
        "text": _hex(colors.get("textPrimary")),
        "muted": _hex(colors.get("textSecondary")),
        "link": _hex(colors.get("link") or colors.get("primary") or colors.get("accent")),
        "body_font": body_font,
        "heading_font": heading_font,
        "h1": _size(sizes.get("h1")),
        "h2": _size(sizes.get("h2")),
        "h3": _size(sizes.get("h3")),
        "body": _size(sizes.get("body")),
        "weight": weight,
        "radius": radius,
        "button_bg": _hex(button.get("background")),
        "button_text": _hex(button.get("textColor")),
        "logo": str(images.get("logo") or branding.get("logo") or "").strip(),
    }
    if not any(tokens.get(key) for key in ("primary", "background", "text", "body_font", "heading_font")):
        return {}
    logo = tokens.get("logo") or ""
    if logo and not (logo.startswith("http://") or logo.startswith("https://")):
        tokens["logo"] = ""
    return {key: value for key, value in tokens.items() if value}


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
