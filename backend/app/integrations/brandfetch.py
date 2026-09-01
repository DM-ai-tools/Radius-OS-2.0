"""Brandfetch — pull a client's real brand assets for the Phase 12 publish preview.

A preview rendered in generic styling tells the reviewer nothing about how the page will
actually look on the client's site. This fetches the live logo, palette, and typeface so
the preview is representative. Never invents brand values: when the API is unavailable
the caller gets ``available: False`` and the preview falls back to neutral styling that
is visibly labelled as such.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("brandfetch")

BASE = "https://api.brandfetch.io/v2"
TIMEOUT = 20.0
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def configured() -> bool:
    return bool(get_settings().brandfetch_api_key)


def brand_domain(url: str) -> str:
    """Bare registrable host for the Brandfetch lookup key."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.netloc or parsed.path).split("/")[0].lower().removeprefix("www.")


def _clean_hex(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    return text if _HEX.match(text) else None


def _pick_logo(logos: Any) -> dict[str, Any]:
    """Prefer a light-background logo, then any logo, then an icon."""
    if not isinstance(logos, list):
        return {}
    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in logos:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").lower()
        theme = str(entry.get("theme") or "").lower()
        formats = entry.get("formats")
        if not isinstance(formats, list) or not formats:
            continue
        # Prefer raster/SVG with an explicit src
        best = None
        for fmt in formats:
            if isinstance(fmt, dict) and fmt.get("src"):
                if best is None or str(fmt.get("format")) in ("svg", "png"):
                    best = fmt
        if not best:
            continue
        score = 0
        if kind == "logo":
            score += 2
        if theme == "light":
            score += 1
        ranked.append(
            (
                score,
                {
                    "url": best.get("src"),
                    "format": best.get("format"),
                    "type": kind or None,
                    "theme": theme or None,
                },
            )
        )
    if not ranked:
        return {}
    ranked.sort(key=lambda r: r[0], reverse=True)
    return ranked[0][1]


def _pick_colors(colors: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"palette": []}
    if not isinstance(colors, list):
        return out
    for entry in colors:
        if not isinstance(entry, dict):
            continue
        hex_value = _clean_hex(entry.get("hex"))
        if not hex_value:
            continue
        role = str(entry.get("type") or "").lower()
        out["palette"].append({"hex": hex_value, "role": role or None})
        if role in ("primary", "accent", "brand") and not out.get("primary"):
            out["primary"] = hex_value
    if not out.get("primary") and out["palette"]:
        out["primary"] = out["palette"][0]["hex"]
    return out


def _pick_font(fonts: Any) -> dict[str, Any]:
    if not isinstance(fonts, list):
        return {}
    for entry in fonts:
        if isinstance(entry, dict) and entry.get("name"):
            return {"name": str(entry["name"]), "type": str(entry.get("type") or "") or None}
    return {}


async def fetch_brand(url_or_domain: str) -> dict[str, Any]:
    """Return normalised brand tokens for a domain.

    Always returns a dict. ``available`` is False (with ``error``) when the brand could
    not be fetched — callers must render neutral styling rather than guessing colours.
    """
    domain = brand_domain(url_or_domain)
    if not domain:
        return {"available": False, "error": "no_domain", "domain": ""}
    settings = get_settings()
    if not settings.brandfetch_api_key:
        return {"available": False, "error": "brandfetch_not_configured", "domain": domain}

    headers = {"Authorization": f"Bearer {settings.brandfetch_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{BASE}/brands/{domain}", headers=headers)
    except Exception as exc:  # noqa: BLE001
        log.warning("brandfetch_request_failed", domain=domain, error=str(exc))
        return {"available": False, "error": f"request_failed: {exc}", "domain": domain}

    if resp.status_code == 404:
        return {"available": False, "error": "brand_not_found", "domain": domain}
    if resp.status_code == 401:
        log.warning("brandfetch_unauthorized", domain=domain)
        return {"available": False, "error": "unauthorized", "domain": domain}
    if resp.status_code >= 400:
        log.warning("brandfetch_http_error", domain=domain, status=resp.status_code)
        return {
            "available": False,
            "error": f"http_{resp.status_code}",
            "domain": domain,
        }

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"bad_json: {exc}", "domain": domain}

    colors = _pick_colors(data.get("colors"))
    brand = {
        "available": True,
        "domain": domain,
        "name": data.get("name") or domain,
        "description": data.get("description"),
        "logo": _pick_logo(data.get("logos")),
        "primary_color": colors.get("primary"),
        "palette": colors.get("palette", [])[:6],
        "font": _pick_font(data.get("fonts")),
        "source": "brandfetch",
    }
    log.info(
        "brandfetch_ok",
        domain=domain,
        has_logo=bool(brand["logo"]),
        colors=len(brand["palette"]),
    )
    return brand
