"""Which live page of the client's site the preview copies its design from.

Their rule (2026-10-16): a page is built in the design of the same kind of page.
A blog post copies a live blog post, a service page a live service page, and a
draft that optimises an existing page copies that exact page. Order:

1. Optimising an existing page: that page itself.
2. A live page of the same kind, from the site crawl (nearest in the URL tree).
   Blog posts often sit at the site root where the crawl calls them plain "page",
   so for a blog draft the site's WordPress API is asked for its newest posts, and
   failing that the blog index page is used.
3. Its parent: the linking-plan hub or the site-structure parent.
4. The home page.

Pages that are only planned, or that fail to load, are never used: every candidate
is fetched first, so the agent is not sent to a missing page. Many client sites
block server fetches (403) while the agent's real browser gets through, so a
blocked page still counts when the crawl listed it or it is the page being optimised.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.integrations.web_fetch import fetch_url
from app.logging_config import get_logger

log = get_logger("site_preview.design_page")

MAX_CHECKS = 6
CHECK_TIMEOUT = 12.0

# Draft page type -> the kinds of live page whose design fits, best first.
_FITS: dict[str, list[str]] = {
    "blog": ["blog"],
    "service": ["service", "sub_service", "hub"],
    "sub_service": ["sub_service", "service"],
    "hub": ["hub", "service"],
    "spoke": ["sub_service", "service", "blog"],
    "location": ["location", "service"],
    "landing": ["landing", "service"],
    "product": ["product"],
}
_DRAFT_KIND = {
    "blog": "blog", "article": "blog", "blog_article": "blog", "post": "blog", "guide": "blog",
    "listicle": "blog", "comparison": "blog", "faq": "blog", "news": "blog",
    "service": "service", "core_service": "service", "commercial": "service",
    "sub_service": "sub_service", "subservice": "sub_service",
    "hub": "hub", "service_hub": "hub", "pillar": "hub",
    "spoke": "spoke", "cluster": "spoke",
    "location": "location", "local": "location",
    "landing": "landing", "conversion": "landing",
    "product": "product",
}
_LIVE_KIND = {
    "blog": "blog", "blog_article": "blog", "article": "blog", "post": "blog", "guide": "blog", "news": "blog",
    "service": "service", "core_service": "service",
    "sub_service": "sub_service", "subservice": "sub_service",
    "hub": "hub", "service_hub": "hub", "pillar": "hub",
    "location": "location",
    "landing": "landing",  # not "conversion": that is the contact page, not a landing page design
    "product": "product",
}
_BLOG_INDEX = re.compile(r"^/(blog|news|insights?|articles?|resources|journal)$", re.IGNORECASE)
_BLOG_PATH = re.compile(r"/(blog|news|insights?|articles?|resources|guides?|posts?|stories|learn|journal)/[^/]+", re.IGNORECASE)
_REFRESH_ACTIONS = {"refresh", "optimize", "optimise", "update", "rewrite", "improve"}
_KIND_LABEL = {
    "blog": "blog post", "service": "service page", "sub_service": "sub-service page", "hub": "service hub page",
    "location": "location page", "landing": "landing page", "product": "product page",
}


@dataclass
class DesignPage:
    url: str
    reason: str
    kind: str  # "own_page" | "same_kind" | "parent" | "home"


def _abs(url: str, primary_url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return primary_url.rstrip("/") + (url if url.startswith("/") else f"/{url}")


def _path(url: str) -> str:
    return (urlparse(url).path if "://" in url else url).rstrip("/").lower() or "/"


def draft_kind(page_type: str, url: str = "") -> str:
    kind = _DRAFT_KIND.get(str(page_type or "").strip().lower().replace("-", "_").replace(" ", "_"), "")
    if not kind and url and _BLOG_PATH.search(_path(url)):
        kind = "blog"
    return kind


def live_kind(page: dict[str, Any]) -> str:
    kind = _LIVE_KIND.get(str(page.get("page_type") or "").strip().lower(), "")
    url = str(page.get("url") or page.get("canonical_url") or page.get("path") or "")
    if not kind and _BLOG_PATH.search(_path(url)):
        kind = "blog"
    return kind


def _status_ok(page: dict[str, Any]) -> bool | None:
    raw = str(page.get("status") or "").strip()
    if not raw or raw.lower() == "none":
        return None  # listed but not checked by the crawl
    return raw in {"200", "ok", "live"}


def same_kind_candidates(
    kind: str, draft_url: str, live_pages: list[dict[str, Any]], primary_url: str
) -> list[tuple[str, str]]:
    """Live pages whose kind fits the draft, best first: the closest kind, a page the
    crawl saw load, then the one sharing the most of the draft's URL path."""
    fits = _FITS.get(kind) or []
    if not fits:
        return []
    home = _path(primary_url)
    own = _path(draft_url) if draft_url else ""
    draft_parts = [p for p in _path(draft_url).split("/") if p] if draft_url else []
    scored: list[tuple[tuple[int, int, int, int], str, str]] = []
    seen: set[str] = set()
    for page in live_pages:
        if not isinstance(page, dict):
            continue
        page_kind = live_kind(page)
        if page_kind not in fits or _status_ok(page) is False:
            continue
        url = _abs(str(page.get("url") or page.get("canonical_url") or page.get("path") or ""), primary_url)
        path = _path(url)
        if not url or path in seen or path == home or (own and path == own):
            continue
        seen.add(path)
        parts = [p for p in path.split("/") if p]
        shared = 0
        for a, b in zip(parts, draft_parts):
            if a != b:
                break
            shared += 1
        depth_gap = abs(len(parts) - len(draft_parts)) if draft_parts else len(parts)
        score = (fits.index(page_kind), 0 if _status_ok(page) else 1, -shared, depth_gap)
        scored.append((score, url, page_kind))
    scored.sort(key=lambda s: s[0])
    return [(url, page_kind) for _score, url, page_kind in scored]


_BLOCKED_STATUS = {401, 403, 429, 503}


async def page_status(url: str) -> str:
    """"ok" when the page loads, "blocked" when the site refuses this server (the
    agent's browser usually gets through), "missing" otherwise."""
    try:
        result = await fetch_url(url, timeout=CHECK_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — an unreachable page is simply not used
        log.info("design_page_check_failed", url=url[:160], error=str(exc)[:160])
        return "missing"
    status = int(result.get("status_code") or 0)
    content_type = str((result.get("headers") or {}).get("content-type") or "")
    if status == 200 and ("html" in content_type or not content_type) and not result.get("error"):
        return "ok"
    if status in _BLOCKED_STATUS or result.get("error") in {"bot_challenge_blocked", "soft_forbidden"}:
        return "blocked"
    return "missing"


async def page_loads(url: str, *, known_live: bool = False) -> bool:
    """Usable as a design reference: it loads, or it is blocked but known to exist."""
    status = await page_status(url)
    return status == "ok" or (status == "blocked" and known_live)


def _same_site(url: str, primary_url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    home = (urlparse(primary_url).hostname or "").lower().removeprefix("www.")
    return bool(host) and host == home


async def latest_blog_posts(primary_url: str, limit: int = 3) -> list[str]:
    """Newest post addresses from the site's WordPress API; empty when it is not
    WordPress or the API is closed."""
    api = primary_url.rstrip("/") + f"/wp-json/wp/v2/posts?per_page={limit}&_fields=link"
    try:
        result = await fetch_url(api, timeout=CHECK_TIMEOUT)
        rows = json.loads(result.get("text") or "[]") if int(result.get("status_code") or 0) == 200 else []
    except Exception as exc:  # noqa: BLE001 — no API simply means no posts from it
        log.info("design_page_wp_posts_unavailable", error=str(exc)[:160])
        return []
    links = [str(r.get("link") or "") for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return [link for link in links if link.startswith(("http://", "https://")) and _same_site(link, primary_url)]


def blog_index_candidates(live_pages: list[dict[str, Any]], primary_url: str) -> list[str]:
    found = []
    for page in live_pages:
        if not isinstance(page, dict) or _status_ok(page) is False:
            continue
        url = _abs(str(page.get("url") or page.get("canonical_url") or page.get("path") or ""), primary_url)
        if url and _BLOG_INDEX.match(_path(url)) and url not in found:
            found.append(url)
    return found or [primary_url.rstrip("/") + "/blog"]


async def choose_design_page(
    draft: dict[str, Any],
    *,
    primary_url: str,
    live_pages: list[dict[str, Any]],
    parent: dict[str, str] | None = None,
) -> DesignPage:
    """The page whose design the preview copies. ``parent`` is the linking-plan or
    site-structure parent (from ``design_capture.resolve_previous_design_page``)."""
    draft_url = _abs(str(draft.get("url") or ""), primary_url)
    kind = draft_kind(str(draft.get("page_type") or ""), draft_url)
    action = str(draft.get("action") or "").strip().lower()
    checks = 0

    listed = {
        _path(_abs(str(p.get("url") or p.get("canonical_url") or p.get("path") or ""), primary_url))
        for p in live_pages
        if isinstance(p, dict) and _status_ok(p) is not False
    }

    async def usable(url: str, *, known_live: bool = False) -> bool:
        nonlocal checks
        if not url or checks >= MAX_CHECKS:
            return False
        checks += 1
        return await page_loads(url, known_live=known_live or _path(url) in listed)

    if action in _REFRESH_ACTIONS and draft_url and _path(draft_url) != _path(primary_url):
        if await usable(draft_url, known_live=True):
            return DesignPage(draft_url, "the live page this draft optimises", "own_page")
        log.info("design_page_own_page_unavailable", url=draft_url[:160])

    label = _KIND_LABEL.get(kind, "page")
    for url, page_kind in same_kind_candidates(kind, draft_url, live_pages, primary_url)[:3]:
        if await usable(url):
            if page_kind == kind or (kind == "spoke" and page_kind == "sub_service"):
                reason = f"a live {label} on the site, the same kind of page as this draft"
            else:
                reason = f"a live {_KIND_LABEL.get(page_kind, 'page')}, the closest kind to this {label} the site has"
            return DesignPage(url, reason, "same_kind")

    if kind == "blog":
        for url in (await latest_blog_posts(primary_url))[:2]:
            if _path(url) != _path(draft_url) and await usable(url):
                return DesignPage(url, "a live blog post on the site (its newest posts)", "same_kind")
        for url in blog_index_candidates(live_pages, primary_url)[:1]:
            if await usable(url):
                return DesignPage(
                    url,
                    "the blog index; open one of the recent posts it lists and copy that post's layout",
                    "same_kind",
                )

    parent_url = _abs(str((parent or {}).get("url") or ""), primary_url)
    if parent_url and _path(parent_url) != _path(primary_url) and await usable(parent_url):
        return DesignPage(parent_url, "this page's parent on the site; no live page of the same kind was found", "parent")

    if kind == "blog":
        return DesignPage(
            primary_url,
            "the home page; no live blog post could be identified from here. Find the site's blog in "
            "its navigation and copy a recent post's layout; if there is no blog, use the home page's styles",
            "home",
        )
    return DesignPage(primary_url, "the home page; no live page of the same kind or parent page was found", "home")
