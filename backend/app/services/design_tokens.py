"""Local, free design read of a live page.

Context.dev reports computed browser styles. This module covers what that
response has no field for: CSS custom properties, button declarations that
travel together, the logo, and the page's band sequence.

Stdlib HTML parser + httpx + regex. No BeautifulSoup, no headless browser.
"""

from __future__ import annotations

import base64
import colorsys
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.integrations.web_fetch import DEFAULT_HEADERS, assert_safe_url

_MAX_STYLESHEETS = 8
_MAX_SHEET_BYTES = 400_000
_VAR_DEPTH = 4
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL_RE = re.compile(r"([A-Za-z-]+)\s*:\s*([^;]+)")
_HEX_RE = re.compile(r"#([0-9A-Fa-f]{3,8})\b")
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*[\d.]+)?\s*\)",
    re.I,
)
_HSL_RE = re.compile(
    r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%",
    re.I,
)
_STYLESHEET_RE = re.compile(r"""<link[^>]+rel=["'][^"']*stylesheet[^"']*["'][^>]*>""", re.I)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_LOGO_HINT = re.compile(r"logo|brand|wordmark|site-title", re.I)
_LOGO_ANTI = re.compile(r"icon-|sprite|avatar|payment|social|placeholder|1x1", re.I)
_BANDS = ("header", "nav", "main", "section", "footer")


def strip_css_comments(css: str) -> str:
    return _COMMENT_RE.sub("", css or "")


def _clamp(n: int) -> int:
    return max(0, min(255, n))


def _hex(r: int, g: int, b: int) -> str:
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"


def normalize_color(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw or raw.lower() in {"transparent", "inherit", "initial", "unset", "currentcolor"}:
        return None
    hex_match = _HEX_RE.search(raw)
    if hex_match:
        h = hex_match.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h[:3])
        return f"#{h[:6].lower()}"
    rgb = _RGB_RE.search(raw)
    if rgb:
        return _hex(int(rgb.group(1)), int(rgb.group(2)), int(rgb.group(3)))
    hsl = _HSL_RE.search(raw)
    if hsl:
        hue = float(hsl.group(1)) / 360
        sat = float(hsl.group(2)) / 100
        light = float(hsl.group(3)) / 100
        r, g, b = colorsys.hls_to_rgb(hue, light, sat)
        return _hex(int(r * 255), int(g * 255), int(b * 255))
    return None


def parse_declarations(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in _DECL_RE.findall(block or ""):
        out[name.strip().lower()] = value.strip()
    return out


def parse_rules(css: str) -> list[tuple[str, dict[str, str]]]:
    rules: list[tuple[str, dict[str, str]]] = []
    for selector, block in _RULE_RE.findall(strip_css_comments(css)):
        decls = parse_declarations(block)
        if decls:
            rules.append((selector.strip(), decls))
    return rules


def custom_properties(css: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for _selector, decls in parse_rules(css):
        for name, value in decls.items():
            if name.startswith("--"):
                props[name] = value
    return props


def resolve_vars(value: str, props: dict[str, str], *, depth: int = _VAR_DEPTH) -> str:
    current = value or ""
    for _ in range(depth):
        match = re.search(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,\s*([^)]+))?\)", current)
        if not match:
            break
        name = match.group(1)
        fallback = (match.group(2) or "").strip()
        replacement = props.get(name) or fallback
        current = current[: match.start()] + replacement + current[match.end() :]
    return current


def _role_for(prop: str) -> str | None:
    name = prop.lower()
    if name in {"background", "background-color"}:
        return "background"
    if name in {"color"}:
        return "text"
    if "border" in name and "color" in name:
        return "border"
    return None


def colors_by_role(css: str) -> dict[str, list[tuple[str, int]]]:
    props = custom_properties(css)
    counts: dict[str, dict[str, int]] = {"background": {}, "text": {}, "border": {}}
    for _selector, decls in parse_rules(css):
        for name, raw in decls.items():
            role = _role_for(name)
            if not role:
                continue
            color = normalize_color(resolve_vars(raw, props))
            if not color:
                continue
            counts[role][color] = counts[role].get(color, 0) + 1
    ranked = {
        role: sorted(bucket.items(), key=lambda item: item[1], reverse=True)[:8]
        for role, bucket in counts.items()
    }
    return ranked


def button_styles(css: str) -> list[dict[str, str]]:
    props = custom_properties(css)
    found: list[dict[str, str]] = []
    for selector, decls in parse_rules(css):
        if not re.search(r"button|\.btn|\[type=.?submit", selector, re.I):
            continue
        resolved = {k: resolve_vars(v, props) for k, v in decls.items()}
        style = {
            "selector": selector[:180],
            "background": normalize_color(resolved.get("background-color") or resolved.get("background") or "") or "",
            "color": normalize_color(resolved.get("color") or "") or "",
            "radius": resolved.get("border-radius") or "",
            "padding": resolved.get("padding") or "",
            "weight": resolved.get("font-weight") or "",
        }
        if any(style[key] for key in ("background", "color", "radius")):
            found.append(style)
    return found[:6]


class _PageWalker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stylesheets: list[str] = []
        self.images: list[dict[str, str]] = []
        self.svgs: list[str] = []
        self.bands: list[str] = []
        self.nav_labels: list[str] = []
        self._stack: list[str] = []
        self._text = ""
        self._in_svg = 0
        self._svg = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        self._stack.append(tag)
        if tag in _BANDS:
            self.bands.append(tag)
        if tag == "link" and "stylesheet" in attr.get("rel", ""):
            href = attr.get("href") or ""
            if href:
                self.stylesheets.append(href)
        if tag == "img":
            self.images.append(
                {
                    "src": attr.get("src") or "",
                    "alt": attr.get("alt") or "",
                    "class": attr.get("class") or "",
                    "id": attr.get("id") or "",
                    "context": " ".join(self._stack[-6:]),
                }
            )
        if tag == "svg":
            self._in_svg += 1
            self._svg = "<svg"
        elif self._in_svg:
            self._svg += f"<{tag}"

    def handle_endtag(self, tag: str) -> None:
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag == "a" and "nav" in self._stack and self._text.strip():
            label = " ".join(self._text.split())
            if label and label not in self.nav_labels:
                self.nav_labels.append(label)
            self._text = ""
        if tag == "svg" and self._in_svg:
            self._in_svg -= 1
            if self._svg and len(self.svgs) < 12:
                self.svgs.append(self._svg[:2000])
            self._svg = ""

    def handle_data(self, data: str) -> None:
        if "nav" in self._stack and "a" in self._stack:
            self._text += data
        if self._in_svg:
            self._svg += data


def walk_html(html: str) -> _PageWalker:
    parser = _PageWalker()
    parser.feed(html or "")
    parser.close()
    return parser


def _logo_score(candidate: dict[str, str]) -> int:
    blob = " ".join(candidate.get(k) or "" for k in ("src", "alt", "class", "id", "context"))
    if _LOGO_ANTI.search(blob):
        return -5
    score = 0
    if _LOGO_HINT.search(blob):
        score += 5
    if "header" in candidate.get("context", ""):
        score += 3
    if candidate.get("src"):
        score += 1
    return score


def find_logo(candidates: list[dict[str, str]]) -> dict[str, str] | None:
    ranked = sorted(candidates, key=_logo_score, reverse=True)
    for item in ranked:
        if _logo_score(item) > 0 and item.get("src"):
            return item
    return None


def page_structure(html: str, *, page_url: str) -> dict[str, Any]:
    walker = walk_html(html)
    sequence = [band for band in walker.bands if band in _BANDS]
    return {
        "bands": sequence,
        "nav_labels": walker.nav_labels[:16],
        "svg_count": len(walker.svgs),
        "page_url": page_url,
    }


def tokens_from_css(css: str) -> dict[str, Any]:
    props = custom_properties(css)
    resolved = {name: resolve_vars(value, props) for name, value in props.items()}
    named = {
        name: color
        for name, value in resolved.items()
        if (color := normalize_color(value))
    }
    roles = colors_by_role(css)
    accent = next(
        (named[name] for name in named if re.search(r"brand|primary|accent", name, re.I)),
        None,
    )
    if not accent:
        texts = [color for color, _n in roles.get("text") or []]
        accent = texts[0] if texts else ""
    background = next((color for color, _n in roles.get("background") or []), "")
    return {
        "custom_properties": named,
        "colors_by_role": {role: [color for color, _n in rows] for role, rows in roles.items()},
        "buttons": button_styles(css),
        "accent": accent or "",
        "background": background,
    }


def design_markdown_logo(logo: dict[str, Any] | None) -> list[str]:
    """Absolute URL first. A data URI alone burns the model budget and the logo never lands."""
    if not logo:
        return ["NOT AVAILABLE — no logo candidate survived the hint and image check."]
    url = str(logo.get("url") or "").strip()
    lines = []
    if url:
        lines.append(f"url: {url}")
    data_uri = str(logo.get("data_uri") or "").strip()
    if data_uri:
        lines.append(f"data_uri: {data_uri}")
    return lines or ["NOT AVAILABLE — logo candidate had no fetchable URL."]


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    ok, safe = assert_safe_url(url)
    if not ok:
        return ""
    resp = await client.get(safe)
    if resp.status_code >= 400:
        return ""
    return resp.text or ""


async def extract_design_tokens(url: str) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "available": False,
        "source": "local",
        "error": "not_fetched",
        "page_url": url,
    }
    ok, safe = assert_safe_url(url)
    if not ok:
        return empty | {"error": safe}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
            home = await client.get(safe)
            if home.status_code >= 400 or not home.text:
                return empty | {"error": f"http_{home.status_code}"}
            html = home.text
            page_url = str(home.url)
            walker = walk_html(html)
            sheets: list[str] = []
            for href in walker.stylesheets:
                absolute = urljoin(page_url, href)
                if absolute not in sheets:
                    sheets.append(absolute)
            css_parts = []
            for sheet_url in sheets[:_MAX_STYLESHEETS]:
                text = await _fetch_text(client, sheet_url)
                if text:
                    css_parts.append(text[:_MAX_SHEET_BYTES])
            css = "\n".join(css_parts)
            tokens = tokens_from_css(css)
            logo = await _verified_logo(client, walker.images, page_url)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return empty | {"error": type(exc).__name__}
    structure = page_structure(html, page_url=page_url)
    header, footer = _regions(html, page_url)
    available = bool(tokens.get("accent") or tokens.get("custom_properties") or header or logo)
    return {
        "available": available,
        "source": "local",
        "page_url": page_url,
        "html": html,
        "custom_properties": tokens.get("custom_properties") or {},
        "colors_by_role": tokens.get("colors_by_role") or {},
        "buttons": tokens.get("buttons") or [],
        "accent": tokens.get("accent") or "",
        "background": tokens.get("background") or "",
        "stylesheets": sheets[:_MAX_STYLESHEETS],
        "logo": logo,
        "structure": structure,
        "header_html": header,
        "footer_html": footer,
        "error": "" if available else "no_readable_tokens",
    }


async def _verified_logo(
    client: httpx.AsyncClient,
    images: list[dict[str, str]],
    page_url: str,
) -> dict[str, Any] | None:
    candidate = find_logo(images)
    if not candidate:
        return None
    src = urljoin(page_url, candidate.get("src") or "")
    if not src.startswith("http"):
        return None
    host = urlparse(page_url).hostname
    ok, safe = assert_safe_url(src)
    if not ok or urlparse(safe).hostname != host:
        return None
    resp = await client.get(safe)
    ctype = resp.headers.get("content-type", "")
    if resp.status_code >= 400 or not ctype.startswith("image/"):
        return None
    body = resp.content or b""
    data_uri = ""
    if body and len(body) <= 24_000:
        data_uri = f"data:{ctype.split(';')[0]};base64,{base64.b64encode(body).decode('ascii')}"
    return {"url": safe, "data_uri": data_uri, "alt": candidate.get("alt") or ""}


def _regions(html: str, page_url: str) -> tuple[str, str]:
    base = page_url if page_url.endswith("/") else page_url + "/"

    def grab(tag: str) -> str:
        match = re.search(rf"<{tag}\b[^>]*>.*?</{tag}>", html or "", re.I | re.S)
        if not match:
            return ""
        fragment = re.sub(r"<script\b[^>]*>.*?</script>", "", match.group(0), flags=re.I | re.S)

        def repl(item: re.Match[str]) -> str:
            url = urljoin(base, item.group(2))
            return f'{item.group(1)}="{url}"'

        return re.sub(r"""(src|href)=["']([^"']+)["']""", repl, fragment, flags=re.I)[:80_000]

    return grab("header"), grab("footer")
