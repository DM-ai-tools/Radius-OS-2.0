"""Read a WordPress site's public design for previews.

Elementor kit CSS and the rendered header/footer are public. The builder JSON
is not. Previews use what a visitor already sees, not Brandfetch guesses.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.integrations.web_fetch import DEFAULT_HEADERS, assert_safe_url

TIMEOUT = 12.0
_KIT_RE = re.compile(r"elementor-kit-(\d+)")
_COLOR_RE = re.compile(r"--e-global-color-([a-z0-9_-]+)\s*:\s*(#[0-9A-Fa-f]{3,8})")
_FONT_RE = re.compile(
    r"--e-global-typography-([a-z0-9_-]+)-font-family\s*:\s*([^;}{]+)",
    re.I,
)
_STYLESHEET_RE = re.compile(
    r"""<link[^>]+rel=["'][^"']*stylesheet[^"']*["'][^>]*>""",
    re.I,
)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_REGION_RE = {
    "header": re.compile(r"<header\b[^>]*>.*?</header>", re.I | re.S),
    "footer": re.compile(r"<footer\b[^>]*>.*?</footer>", re.I | re.S),
}
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
_ATTR_RE = re.compile(r"""(?P<attr>src|href)=["'](?P<url>[^"']+)["']""", re.I)


def parse_kit_css(css: str) -> dict[str, Any]:
    colors = {name: value for name, value in _COLOR_RE.findall(css or "")}
    fonts: dict[str, str] = {}
    for name, raw in _FONT_RE.findall(css or ""):
        font = raw.strip().strip("\"'").split(",")[0].strip().strip("\"'")
        if font and font.lower() not in {"inherit", "initial"}:
            fonts[name] = font
    primary = colors.get("primary") or colors.get("accent") or ""
    font = fonts.get("primary") or fonts.get("text") or fonts.get("body") or next(iter(fonts.values()), "")
    return {
        "colors": colors,
        "fonts": fonts,
        "primary_color": primary,
        "font": font,
    }


def _absolute(base: str, url: str) -> str:
    if not url or url.startswith(("data:", "mailto:", "tel:", "#", "javascript:")):
        return url
    return urljoin(base, url)


def _rewrite_fragment(fragment: str, base: str) -> str:
    cleaned = _SCRIPT_RE.sub("", fragment)

    def repl(match: re.Match[str]) -> str:
        url = _absolute(base, match.group("url"))
        return f'{match.group("attr")}="{url}"'

    return _ATTR_RE.sub(repl, cleaned)


def _stylesheets(html: str, base: str) -> list[str]:
    found: list[str] = []
    for tag in _STYLESHEET_RE.findall(html or ""):
        href = _HREF_RE.search(tag)
        if not href:
            continue
        url = _absolute(base, href.group(1))
        if url not in found:
            found.append(url)
    return found[:12]


def design_from_html(html: str, *, page_url: str, kit_css: str = "") -> dict[str, Any]:
    """Turn a public homepage into the tokens and chrome a preview can wear."""
    base = page_url if page_url.endswith("/") else page_url + "/"
    kit = _KIT_RE.search(html or "")
    kit_id = kit.group(1) if kit else ""
    tokens = parse_kit_css(kit_css)
    stylesheets = _stylesheets(html, page_url)
    if kit_id:
        kit_url = urljoin(page_url, f"/wp-content/uploads/elementor/css/post-{kit_id}.css")
        if kit_url not in stylesheets:
            stylesheets.insert(0, kit_url)
    theme = re.search(r"/wp-content/themes/([^/]+)/", html or "")
    header = _REGION_RE["header"].search(html or "")
    footer = _REGION_RE["footer"].search(html or "")
    header_html = _rewrite_fragment(header.group(0), base)[:80_000] if header else ""
    footer_html = _rewrite_fragment(footer.group(0), base)[:80_000] if footer else ""
    available = bool(tokens.get("primary_color") or tokens.get("font") or header_html or stylesheets)
    return {
        "available": available,
        "source": "wordpress",
        "page_url": page_url,
        "kit_id": kit_id,
        "theme": theme.group(1) if theme else "",
        "primary_color": tokens.get("primary_color") or "",
        "font": tokens.get("font") or "",
        "colors": tokens.get("colors") or {},
        "stylesheets": stylesheets,
        "header_html": header_html,
        "footer_html": footer_html,
        "chrome": bool(header_html or footer_html),
    }


async def fetch_wordpress_design(primary_url: str) -> dict[str, Any]:
    """Public read. Never uses the Application Password or the REST login header."""
    empty = {"available": False, "source": "wordpress", "error": "not_fetched"}
    raw = (primary_url or "").strip()
    if not raw:
        return empty | {"error": "missing_url"}
    if "://" not in raw:
        raw = f"https://{raw}"
    ok, safe = assert_safe_url(raw)
    if not ok:
        return empty | {"error": safe}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
            home = await client.get(safe)
            if home.status_code >= 400 or not home.text:
                return empty | {"error": f"http_{home.status_code}"}
            html = home.text
            page_url = str(home.url)
            kit = _KIT_RE.search(html)
            kit_css = ""
            if kit:
                kit_url = urljoin(page_url, f"/wp-content/uploads/elementor/css/post-{kit.group(1)}.css")
                kit_ok, kit_safe = assert_safe_url(kit_url)
                if kit_ok and urlparse(kit_safe).hostname == urlparse(page_url).hostname:
                    sheet = await client.get(kit_safe)
                    if sheet.status_code < 400:
                        kit_css = sheet.text or ""
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return empty | {"error": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001
        return empty | {"error": type(exc).__name__}
    design = design_from_html(html, page_url=page_url, kit_css=kit_css)
    if not design["available"]:
        design["error"] = "no_public_design"
    return design
