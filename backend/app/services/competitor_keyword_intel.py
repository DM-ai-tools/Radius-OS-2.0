"""Phase 5 — keywords harvested from competitor pages, feeding the cluster pool.

Provider keyword-gap feeds tell you what competitors *rank* for. They do not
tell you what those competitors chose to *target*: the phrase in the title, the
H1 on the service page, the sub-service in an H2. That targeting signal is on
the page, so this module reads the pages.

The pipeline is deliberately conservative, because page text is noisy and
competitor sites are hostile (WAFs, 403s, dead hosts):

  crawl (time-boxed, capped)
    -> extract 2–4 word phrases from title / H1 / H2 / meta / URL slug
    -> drop competitor brands, client brand noise, boilerplate, stale years
    -> drop anything the client's own relevance context rejects
    -> volume-check the survivors with one DataForSEO batch
    -> keep only terms with real volume, shaped like seeded keywords

Every failure path returns no keywords. Phase 5 must never depend on a
competitor's site being reachable.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

from app.integrations import dataforseo
from app.logging_config import get_logger
from app.services.competitive_context import fetch_competitor_site_snapshots
from app.services.keyword_opportunity import (
    detect_funnel,
    detect_intent,
    is_noisy_keyword,
    is_stale_year_keyword,
    normalize_provider_intent,
)
from app.services.keyword_relevance import (
    RelevanceContext,
    empty_cleaning_audit,
    filter_relevant_keywords,
    is_competitor_brand_term,
    is_url_like,
)

log = get_logger("competitor_keyword_intel")

# One DataForSEO search_volume task accepts 1000 keywords (not 40, as this cap
# previously assumed), and the endpoint is priced per request rather than per
# keyword — so pricing the full candidate set costs the same single call.
MAX_CANDIDATES_TO_PRICE = 1000
DEFAULT_MAX_COMPETITORS = 4
DEFAULT_MAX_PAGES_EACH = 8
DEFAULT_CRAWL_TIMEOUT = 90.0

# Where a phrase was found, and how much that placement means.
_FIELD_WEIGHTS = {
    "title": 3,
    "h1": 3,
    "h2": 2,
    "slug": 2,
    "meta_description": 1,
}

_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "at",
    "by", "from", "we", "our", "us", "you", "your", "is", "are", "be", "this",
    "that", "it", "as", "all", "more", "get", "now",
}

# Site furniture that shows up on every page and means nothing as a keyword.
_BOILERPLATE = {
    "contact us", "about us", "read more", "learn more", "get in touch",
    "our team", "our story", "privacy policy", "terms of service", "home page",
    "book now", "call now", "free quote", "get a quote", "click here",
    "sign up", "follow us", "site map", "all rights reserved", "skip to content",
    "what we do", "who we are", "case studies", "our work", "meet the team",
}

# Title separators — the segment after one is almost always the brand.
_SEGMENT_SPLIT = re.compile(r"\s*[|·•–—]\s*|\s+-\s+")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _norm(text)) if w]


def _slug_text(path: str) -> str:
    """Last meaningful URL segment as words: /services/drain-cleaning -> drain cleaning."""
    segments = [s for s in str(path or "").strip("/").split("/") if s]
    if not segments:
        return ""
    last = segments[-1]
    if "." in last:  # index.html, page.php
        last = last.rsplit(".", 1)[0]
    return re.sub(r"[-_]+", " ", last)


def _phrases_from_text(text: str) -> list[str]:
    """2–4 word phrases from one text field, split on brand separators first."""
    out: list[str] = []
    for segment in _SEGMENT_SPLIT.split(str(text or "")):
        words = _words(segment)
        if len(words) < 2:
            continue
        for size in (2, 3, 4):
            for i in range(len(words) - size + 1):
                gram = words[i : i + size]
                # A phrase that is only stopwords, or starts/ends on one, is noise.
                if gram[0] in _STOP or gram[-1] in _STOP:
                    continue
                if all(w in _STOP for w in gram):
                    continue
                if any(w.isdigit() for w in gram):
                    continue
                out.append(" ".join(gram))
    return out


def _page_fields(page: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [("title", str(page.get("title") or ""))]
    for h1 in page.get("h1s") or []:
        fields.append(("h1", str(h1)))
    for h2 in page.get("h2s") or []:
        fields.append(("h2", str(h2)))
    fields.append(("meta_description", str(page.get("meta_description") or "")))
    slug = _slug_text(page.get("path") or urlparse(str(page.get("url") or "")).path)
    if slug:
        fields.append(("slug", slug))
    return fields


def extract_candidate_terms(
    snapshots: list[dict[str, Any]],
    *,
    max_per_competitor: int = 60,
) -> list[dict[str, Any]]:
    """Candidate keyword phrases from crawled competitor pages.

    Ranked by how many distinct competitors use the phrase, then by where it
    appeared. A phrase two competitors put in a title is a far better keyword
    bet than one buried in a single meta description.
    """
    by_keyword: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots or []:
        if not isinstance(snapshot, dict):
            continue
        domain = str(snapshot.get("domain") or "").lower()
        seen_for_competitor = 0
        for page in snapshot.get("pages") or []:
            if not isinstance(page, dict):
                continue
            for field, text in _page_fields(page):
                weight = _FIELD_WEIGHTS.get(field, 1)
                for phrase in _phrases_from_text(text):
                    if phrase in _BOILERPLATE:
                        continue
                    if is_stale_year_keyword(phrase) or is_url_like(phrase):
                        continue
                    if is_noisy_keyword(phrase):
                        continue
                    entry = by_keyword.get(phrase)
                    if entry is None:
                        if seen_for_competitor >= max_per_competitor:
                            continue
                        seen_for_competitor += 1
                        by_keyword[phrase] = {
                            "keyword": phrase,
                            "prominence": weight,
                            "competitor_domain": domain,
                            "competitor_domains": [domain] if domain else [],
                            "competitor_count": 1 if domain else 0,
                            "evidence_url": page.get("url"),
                            "evidence_field": field,
                            "evidence_title": page.get("title"),
                            "page_kind": page.get("kind"),
                        }
                        continue
                    entry["prominence"] += weight
                    if domain and domain not in entry["competitor_domains"]:
                        entry["competitor_domains"].append(domain)
                        entry["competitor_count"] = len(entry["competitor_domains"])
                    # Keep the strongest placement as the cited evidence.
                    if weight > _FIELD_WEIGHTS.get(str(entry["evidence_field"]), 0):
                        entry["evidence_field"] = field
                        entry["evidence_url"] = page.get("url")

    return sorted(
        by_keyword.values(),
        key=lambda r: (
            -int(r.get("competitor_count") or 0),
            -int(r.get("prominence") or 0),
            len(str(r.get("keyword") or "")),
            str(r.get("keyword") or ""),
        ),
    )


def filter_candidate_terms(
    candidates: list[dict[str, Any]],
    relevance_context: RelevanceContext | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop competitor brands and anything outside the client's own topic space."""
    if not candidates:
        return [], empty_cleaning_audit()

    ctx = relevance_context
    staged: list[dict[str, Any]] = []
    audit = empty_cleaning_audit()
    audit["input_count"] = len(candidates)
    brand_dropped = 0

    for row in candidates:
        keyword = str(row.get("keyword") or "")
        if ctx is not None and is_competitor_brand_term(
            keyword,
            brand_full=ctx.competitor_brand_full,
            brand_tokens=ctx.competitor_brand_tokens,
        ):
            brand_dropped += 1
            continue
        staged.append(row)

    if ctx is None:
        audit["kept_count"] = len(staged)
        audit["removed_count"] = brand_dropped
        if brand_dropped:
            audit["removed_by_reason"] = {"competitor_brand": brand_dropped}
        return staged, audit

    kept, _excluded, rel_audit = filter_relevant_keywords(
        staged, ctx, source_label="competitor_page"
    )
    rel_audit["input_count"] = len(candidates)
    rel_audit["removed_count"] = int(rel_audit.get("removed_count") or 0) + brand_dropped
    if brand_dropped:
        reasons = dict(rel_audit.get("removed_by_reason") or {})
        reasons["competitor_brand"] = int(reasons.get("competitor_brand") or 0) + brand_dropped
        rel_audit["removed_by_reason"] = reasons
    return kept, rel_audit


def build_competitor_keyword_rows(
    candidates: list[dict[str, Any]],
    *,
    volumes: dict[str, dict[str, Any]],
    client_keywords: set[str],
    min_volume: int = 10,
) -> list[dict[str, Any]]:
    """Shape volume-qualified candidates like seeded keyword rows.

    A candidate with no volume evidence is dropped rather than carried with an
    unknown volume — unpriced page phrases would pollute clustering.
    """
    client = {_norm(k) for k in (client_keywords or set())}
    rows: list[dict[str, Any]] = []
    for candidate in candidates or []:
        keyword = str(candidate.get("keyword") or "").strip()
        if not keyword:
            continue
        metrics = volumes.get(_norm(keyword))
        if not metrics:
            continue
        volume = metrics.get("volume")
        try:
            volume_int = int(volume) if volume is not None else None
        except (TypeError, ValueError):
            volume_int = None
        if volume_int is None or volume_int <= int(min_volume):
            continue

        provider_intent = normalize_provider_intent(metrics.get("intent"))
        intent = detect_intent(keyword, provider_intent)
        rows.append(
            {
                "keyword": keyword,
                "volume": volume_int,
                "difficulty": metrics.get("difficulty"),
                "cpc": metrics.get("cpc"),
                "traffic_potential": None,
                "intent": intent,
                "intent_basis": "measured" if provider_intent else "unmeasured",
                "provider_intent": provider_intent,
                "funnel": detect_funnel(keyword, intent),
                "parent_topic": keyword,
                "source": "competitor_page",
                "ahrefs_endpoint": "competitor_page",
                "match_class": "broad",
                "seed": None,
                "target": candidate.get("competitor_domain"),
                "target_type": "competitor_page",
                "gap_flag": _norm(keyword) not in client,
                "competitor_domain": candidate.get("competitor_domain"),
                "competitor_count": candidate.get("competitor_count"),
                "evidence_url": candidate.get("evidence_url"),
                "evidence_field": candidate.get("evidence_field"),
                "from_competitor_page": True,
            }
        )
    return rows


def merge_competitor_rows_into_cluster_input(
    cluster_input: list[dict[str, Any]],
    competitor_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add competitor page keywords to the clustering pool, without duplicates.

    A keyword already in the seeded pool keeps its seeded row — that row carries
    the seed/target association clustering relies on.
    """
    if not competitor_rows:
        return cluster_input
    merged = list(cluster_input)
    seen = {_norm(str(r.get("keyword") or "")) for r in merged if isinstance(r, dict)}
    for row in competitor_rows:
        if not isinstance(row, dict):
            continue
        key = _norm(str(row.get("keyword") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def refresh_gap_flags(
    rows: list[dict[str, Any]],
    client_keyword_positions: dict[str, Any],
) -> list[dict[str, Any]]:
    """Recompute gap_flag once the client's own ranking data is known.

    Harvesting happens before the client's organic keywords are pulled, so the
    first pass assumes every competitor term is a gap. This corrects it.
    """
    positions = {_norm(k): v for k, v in (client_keyword_positions or {}).items()}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _norm(str(row.get("keyword") or ""))
        if key in positions:
            row["gap_flag"] = False
            row["client_position"] = positions[key]
        else:
            row["gap_flag"] = True
    return rows


async def collect_competitor_page_keywords(
    *,
    competitors: list[dict[str, Any]],
    relevance_context: RelevanceContext | None,
    location_code: int,
    client_keywords: set[str] | None = None,
    max_competitors: int = DEFAULT_MAX_COMPETITORS,
    max_pages_each: int = DEFAULT_MAX_PAGES_EACH,
    crawl_timeout: float = DEFAULT_CRAWL_TIMEOUT,
    min_volume: int = 10,
) -> dict[str, Any]:
    """Crawl competitor pages and return volume-qualified keyword rows.

    Returns ``{"keywords": [...], "snapshots": [...], "stats": {...}}``. The
    snapshots are returned even when keyword qualification fails, because Phase
    5's downstream consumers (content strategy, site architecture, the PDF
    report) read them directly.
    """
    stats: dict[str, Any] = {
        "competitors_crawled": 0,
        "pages": 0,
        "candidates": 0,
        "relevant": 0,
        "qualified": 0,
        "error": None,
    }
    if not competitors:
        stats["error"] = "no_competitors"
        return {"keywords": [], "snapshots": [], "stats": stats}

    try:
        snapshots = await asyncio.wait_for(
            fetch_competitor_site_snapshots(
                competitors,
                max_competitors=max_competitors,
                max_pages_each=max_pages_each * 3,
                sample_titles=max_pages_each,
            ),
            timeout=crawl_timeout,
        )
    except asyncio.TimeoutError:
        log.warning("competitor_page_crawl_timeout", timeout=crawl_timeout)
        stats["error"] = "crawl_timeout"
        return {"keywords": [], "snapshots": [], "stats": stats}
    except Exception as exc:  # noqa: BLE001
        log.warning("competitor_page_crawl_failed", error=str(exc)[:200])
        stats["error"] = f"crawl_failed:{type(exc).__name__}"
        return {"keywords": [], "snapshots": [], "stats": stats}

    snapshots = [s for s in (snapshots or []) if isinstance(s, dict)]
    stats["competitors_crawled"] = len(snapshots)
    stats["pages"] = sum(len(s.get("pages") or []) for s in snapshots)

    candidates = extract_candidate_terms(snapshots)
    stats["candidates"] = len(candidates)
    relevant, audit = filter_candidate_terms(candidates, relevance_context)
    stats["relevant"] = len(relevant)
    stats["cleaning_audit"] = audit
    if not relevant:
        return {"keywords": [], "snapshots": snapshots, "stats": stats}

    priced = relevant[:MAX_CANDIDATES_TO_PRICE]
    try:
        rows, errors = await dataforseo.search_volume(
            [str(r.get("keyword") or "") for r in priced],
            location_code=location_code,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("competitor_page_volume_failed", error=str(exc)[:200])
        stats["error"] = f"volume_failed:{type(exc).__name__}"
        return {"keywords": [], "snapshots": snapshots, "stats": stats}

    if errors:
        stats["provider_errors"] = sorted(set(errors))
    volumes = {
        _norm(str(row.get("keyword") or "")): row
        for row in (rows or [])
        if isinstance(row, dict) and row.get("keyword")
    }
    keywords = build_competitor_keyword_rows(
        priced,
        volumes=volumes,
        client_keywords=client_keywords or set(),
        min_volume=min_volume,
    )
    stats["qualified"] = len(keywords)
    log.info(
        "competitor_page_keywords_collected",
        competitors=stats["competitors_crawled"],
        candidates=stats["candidates"],
        qualified=stats["qualified"],
    )
    return {"keywords": keywords, "snapshots": snapshots, "stats": stats}
