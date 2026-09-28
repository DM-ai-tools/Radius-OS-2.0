"""Where competitor content comes from, in priority order.

Three tiers, tried in sequence per competitor. Each returns the same
``PageContent`` shape, with ``depth_available`` saying whether body text was
obtained — because two of the three tiers cannot supply it, and a benchmark must
report "not measurable" rather than score a page as zero words.

  1. ``cached``  — snapshots Phase 5 already crawled (``competitor_sites``).
                   Free, no new requests. Title, H1/H2, meta only.
  2. ``live``    — fetch the most relevant page with web_fetch (firecrawl
                   fallback is inside fetch_url). Full body text, so depth,
                   readability and heading structure all become measurable.
  3. ``pasted``  — content the operator pasted. Always works, always complete.

Limits worth knowing: tier 1 has no body text at all; tier 2 is defeated by
WAFs, consent walls and JS-only rendering, and is rate-limited by politeness;
tier 3 is manual and goes stale.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.logging_config import get_logger

log = get_logger("content_benchmark.fetch")

FETCH_TIMEOUT = 20.0
DEFAULT_CONCURRENCY = 3


@dataclass
class PageContent:
    """One page's content, however it was obtained."""

    competitor_id: str = ""
    name: str = ""
    domain: str = ""
    url: str = ""
    source: str = "none"  # cached | live | pasted | none
    title: str = ""
    meta_description: str = ""
    h1s: list[str] = field(default_factory=list)
    h2s: list[str] = field(default_factory=list)
    h3s: list[str] = field(default_factory=list)
    body_text: str = ""
    schema_types: list[str] = field(default_factory=list)
    depth_available: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(
            self.title or self.h1s or self.h2s or self.body_text
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "competitor_id": self.competitor_id,
            "name": self.name,
            "domain": self.domain,
            "url": self.url,
            "source": self.source,
            "title": self.title,
            "meta_description": self.meta_description,
            "h1s": list(self.h1s),
            "h2s": list(self.h2s),
            "h3s": list(self.h3s),
            "word_count": word_count(self.body_text) if self.depth_available else None,
            "schema_types": list(self.schema_types),
            "depth_available": self.depth_available,
            "error": self.error,
        }


def word_count(text: Any) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", str(text or "")))


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _domain_of(value: Any) -> str:
    raw = _norm(value)
    if "://" in raw:
        raw = (urlparse(raw).hostname or "")
    return raw.split("/")[0].removeprefix("www.")


def _relevance(page: dict[str, Any], terms: set[str]) -> int:
    """How well a crawled page matches the service — used to pick which page to compare."""
    blob = _norm(
        " ".join(
            [
                str(page.get("title") or ""),
                str(page.get("path") or page.get("url") or ""),
                " ".join(str(h) for h in (page.get("h1s") or [])),
            ]
        )
    )
    if not blob:
        return 0
    hits = sum(1 for term in terms if term and term in blob)
    # A service/product page beats a blog post for a service benchmark.
    kind = _norm(page.get("kind"))
    bonus = 2 if kind in ("service", "product", "page") else 0
    return hits * 3 + bonus


def pick_best_page(
    snapshot: dict[str, Any], *, service_terms: set[str]
) -> dict[str, Any] | None:
    """The crawled page most likely to be this competitor's take on the service."""
    pages = [p for p in (snapshot.get("pages") or []) if isinstance(p, dict)]
    if not pages:
        return None
    scored = sorted(pages, key=lambda p: -_relevance(p, service_terms))
    best = scored[0]
    return best if _relevance(best, service_terms) > 0 else scored[0]


def from_cached_snapshots(
    competitor: dict[str, Any],
    *,
    snapshots: list[dict[str, Any]] | None,
    service_terms: set[str],
) -> PageContent | None:
    """Tier 1 — reuse what Phase 5 already crawled. No body text."""
    domain = _domain_of(competitor.get("domain") or competitor.get("url"))
    for snapshot in snapshots or []:
        if not isinstance(snapshot, dict):
            continue
        if _domain_of(snapshot.get("domain") or snapshot.get("url")) != domain:
            continue
        page = pick_best_page(snapshot, service_terms=service_terms)
        if not page:
            continue
        return PageContent(
            competitor_id=str(competitor.get("id") or ""),
            name=str(competitor.get("name") or domain),
            domain=domain,
            url=str(page.get("url") or snapshot.get("url") or ""),
            source="cached",
            title=str(page.get("title") or ""),
            meta_description=str(page.get("meta_description") or ""),
            h1s=[str(h) for h in (page.get("h1s") or [])],
            h2s=[str(h) for h in (page.get("h2s") or [])],
            depth_available=False,
        )
    return None


def from_pasted(competitor: dict[str, Any], pasted: str) -> PageContent:
    """Tier 3 — operator-supplied text. Headings parsed from markdown-ish lines."""
    text = str(pasted or "")
    h1s = re.findall(r"^#\s+(.+)$", text, re.MULTILINE)
    h2s = re.findall(r"^##\s+(.+)$", text, re.MULTILINE)
    h3s = re.findall(r"^###\s+(.+)$", text, re.MULTILINE)
    domain = _domain_of(competitor.get("domain") or competitor.get("url"))
    return PageContent(
        competitor_id=str(competitor.get("id") or ""),
        name=str(competitor.get("name") or domain),
        domain=domain,
        url=str(competitor.get("url") or ""),
        source="pasted",
        title=(h1s[0].strip() if h1s else ""),
        h1s=[h.strip() for h in h1s],
        h2s=[h.strip() for h in h2s],
        h3s=[h.strip() for h in h3s],
        body_text=text,
        depth_available=bool(text.strip()),
    )


_SCHEMA_TYPE_RE = re.compile(r'"@type"\s*:\s*"([A-Za-z]+)"')


async def from_live_fetch(
    competitor: dict[str, Any], *, service_terms: set[str]
) -> PageContent:
    """Tier 2 — fetch the competitor's page for real. The only tier giving depth."""
    from app.integrations.web_fetch import fetch_url, parse_html

    domain = _domain_of(competitor.get("domain") or competitor.get("url"))
    url = str(competitor.get("url") or (f"https://{domain}" if domain else ""))
    base = PageContent(
        competitor_id=str(competitor.get("id") or ""),
        name=str(competitor.get("name") or domain),
        domain=domain,
        url=url,
        source="live",
    )
    if not url:
        base.error = "no_url"
        return base
    try:
        fetched = await asyncio.wait_for(fetch_url(url), timeout=FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        base.error = "timeout"
        return base
    except Exception as exc:  # noqa: BLE001
        base.error = f"fetch_failed:{type(exc).__name__}"
        return base

    html = (fetched or {}).get("text") or ""
    status = (fetched or {}).get("status_code")
    if not html:
        base.error = f"empty_response(status={status})"
        return base

    try:
        parsed = parse_html(html)
    except Exception as exc:  # noqa: BLE001
        base.error = f"parse_failed:{type(exc).__name__}"
        return base

    base.url = str((fetched or {}).get("url") or url)
    base.title = str(getattr(parsed, "title", "") or "")[:200]
    base.meta_description = str((getattr(parsed, "meta", None) or {}).get("description") or "")[:400]
    base.h1s = [str(h).strip() for h in (getattr(parsed, "h1s", None) or []) if str(h).strip()][:4]
    base.h2s = [str(h).strip() for h in (getattr(parsed, "h2s", None) or []) if str(h).strip()][:30]
    base.h3s = [str(h).strip() for h in (getattr(parsed, "h3s", None) or []) if str(h).strip()][:40]
    base.body_text = str(getattr(parsed, "text", "") or "")
    base.schema_types = sorted(set(_SCHEMA_TYPE_RE.findall(html)))[:12]
    base.depth_available = word_count(base.body_text) > 50
    if not base.ok:
        base.error = base.error or "no_content_extracted"
    return base


async def gather_competitor_content(
    competitors: list[dict[str, Any]],
    *,
    snapshots: list[dict[str, Any]] | None = None,
    service_terms: set[str] | None = None,
    pasted: dict[str, str] | None = None,
    allow_live: bool = True,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[PageContent]:
    """Content for every competitor, best tier first, failures isolated.

    One competitor failing never affects the others — the report renders what it
    has and marks the rest.
    """
    terms = {t for t in (service_terms or set()) if t}
    pasted = {str(k): v for k, v in (pasted or {}).items()}
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def one(competitor: dict[str, Any]) -> PageContent:
        cid = str(competitor.get("id") or "")
        if pasted.get(cid, "").strip():
            return from_pasted(competitor, pasted[cid])
        if allow_live:
            async with semaphore:
                live = await from_live_fetch(competitor, service_terms=terms)
            if live.ok:
                return live
            # Live failed — fall back to whatever Phase 5 already had.
            cached = from_cached_snapshots(
                competitor, snapshots=snapshots, service_terms=terms
            )
            if cached and cached.ok:
                cached.error = None
                cached.source = "cached"
                log.info(
                    "benchmark_fell_back_to_cache",
                    domain=cached.domain,
                    live_error=live.error,
                )
                return cached
            return live
        cached = from_cached_snapshots(competitor, snapshots=snapshots, service_terms=terms)
        if cached:
            return cached
        out = PageContent(
            competitor_id=cid,
            name=str(competitor.get("name") or ""),
            domain=_domain_of(competitor.get("domain") or competitor.get("url")),
            url=str(competitor.get("url") or ""),
        )
        out.error = "no_cached_snapshot_and_live_disabled"
        return out

    results = await asyncio.gather(
        *(one(c) for c in competitors or []), return_exceptions=True
    )
    out: list[PageContent] = []
    for competitor, result in zip(competitors or [], results):
        if isinstance(result, BaseException):
            log.warning(
                "benchmark_content_failed",
                competitor=str(competitor.get("name") or ""),
                error=str(result)[:200],
            )
            failed = PageContent(
                competitor_id=str(competitor.get("id") or ""),
                name=str(competitor.get("name") or ""),
                domain=_domain_of(competitor.get("domain") or competitor.get("url")),
            )
            failed.error = f"unhandled:{type(result).__name__}"
            out.append(failed)
        else:
            out.append(result)
    return out
