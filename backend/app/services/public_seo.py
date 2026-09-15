"""Public crawl controls for the Radius OS SPA.

Only the marketing landing page is indexable. Authenticated workspace routes,
API surfaces, and draft media stay out of robots and the XML sitemap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from app.config import get_settings

# Paths crawlers should skip — private app, APIs, ops, and generated media.
_DISALLOW_PATHS = (
    "/app",
    "/clients",
    "/api/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/healthz",
    "/media/",
)


def public_site_origin() -> str:
    """Canonical public origin from FRONTEND_URL (no trailing slash)."""
    settings = get_settings()
    raw = (settings.frontend_url or "").strip() or "http://localhost:5173"
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return raw.rstrip("/")


def build_robots_txt() -> str:
    origin = public_site_origin()
    lines = [
        "# Radius OS — public marketing surface only",
        "User-agent: *",
        "Allow: /",
        "Allow: /landing/",
    ]
    for path in _DISALLOW_PATHS:
        lines.append(f"Disallow: {path}")
    lines.append("")
    lines.append(f"Sitemap: {origin}/sitemap.xml")
    lines.append("")
    return "\n".join(lines)


def build_sitemap_xml() -> str:
    """Sitemap with the single public canonical URL (landing)."""
    origin = public_site_origin()
    loc = f"{origin}/"
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{loc}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
