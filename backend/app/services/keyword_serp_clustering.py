"""Refine head-term clusters using real SERP overlap.

Two keywords that return the same pages are the same query regardless of how
differently they are worded, and two that share no results are different
queries however similar they look. Token similarity cannot see either fact.

SERP data is priced per keyword (one request each), so this runs only over the
head terms — the highest-volume measured keywords — and the rest of the pool
keeps deterministic token clustering. Unmeasured keywords never earn a call.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

from app.integrations import dataforseo
from app.logging_config import get_logger

log = get_logger("keyword_serp_clustering")

# Shared results needed before two head terms are treated as one query. Three
# of the top ten is the threshold the SEO literature settles on; below it,
# overlap is mostly incidental big-brand pages.
DEFAULT_MIN_SHARED = 4
# Live SERP is one HTTP call per keyword — keep the head small enough that
# Phase 5 does not stall for minutes after metrics already finished.
DEFAULT_HEAD_TERMS = 40
DEFAULT_CONCURRENCY = 10


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _norm_url(url: Any) -> str:
    """Compare results by host+path, ignoring scheme and www."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = (parsed.path or "").rstrip("/")
        return f"{host}{path}"
    raw = raw.lower().removeprefix("www.")
    return raw.rstrip("/")


def _volume(row: dict[str, Any]) -> int | None:
    value = row.get("volume")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def select_head_terms(
    rows: list[dict[str, Any]],
    *,
    limit: int = DEFAULT_HEAD_TERMS,
) -> list[str]:
    """The highest-volume measured keywords, deduped, at most ``limit`` of them.

    Only measured keywords qualify: spending a SERP request on a keyword the
    provider had no data for buys nothing.
    """
    best: dict[str, int] = {}
    for row in rows:
        keyword = _norm(row.get("keyword"))
        volume = _volume(row)
        if not keyword or volume is None:
            continue
        if keyword not in best or volume > best[keyword]:
            best[keyword] = volume
    ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    return [kw for kw, _ in ranked[: max(0, int(limit))]]


async def fetch_serp_urls(
    keywords: list[str],
    *,
    location_code: int,
    language_code: str,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> tuple[dict[str, list[str]], list[str], dict[str, dict[str, Any]]]:
    """Top organic result URLs per keyword.

    Live SERP accepts exactly one keyword per POST, so this fans out with a
    semaphore. Uses the regular (organic-only) endpoint — clustering only
    needs URLs, and advanced SERP is slower/heavier for no gain here.
    """
    wanted = [k for k in dict.fromkeys(_norm(k) for k in keywords) if k]
    if not wanted:
        return {}, [], {}

    errors: list[str] = []
    snapshots: dict[str, dict[str, Any]] = {}
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def one(keyword: str) -> tuple[str, list[str]]:
        async with semaphore:
            try:
                organic, errs = await dataforseo.serp_overview(
                    keyword,
                    location_code=location_code,
                    language_code=language_code,
                )
            except Exception as exc:  # one bad keyword must not sink the batch
                log.warning("serp_fetch_failed", keyword=keyword, error=str(exc)[:200])
                errors.append("dataforseo_serp_exception")
                return keyword, []
            errors.extend(errs)
            urls = [
                _norm_url(item.get("url"))
                for item in (organic or [])
                if isinstance(item, dict) and item.get("url")
            ]
            urls = [u for u in urls if u][:10]
            snapshots[keyword] = {
                "urls": urls,
                "item_types": ["organic"] if urls else [],
                "ai_overview": False,
            }
            return keyword, urls

    pairs = await asyncio.gather(*(one(k) for k in wanted))
    return dict(pairs), errors, snapshots


def serp_overlap_groups(
    serp_by_keyword: dict[str, list[str]],
    *,
    min_shared: int = DEFAULT_MIN_SHARED,
) -> list[list[str]]:
    """Group keywords whose result sets overlap by at least ``min_shared`` URLs.

    Grouping is transitive: a-b and b-c overlapping puts all three together.
    """
    keywords = list(serp_by_keyword.keys())
    # Normalize here too: the comparison must not depend on whether the caller
    # already stripped scheme/www. _norm_url is idempotent.
    url_sets = {
        k: {n for n in (_norm_url(u) for u in (serp_by_keyword.get(k) or [])) if n}
        for k in keywords
    }

    parent = {k: k for k in keywords}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    threshold = max(1, int(min_shared))
    for i, left in enumerate(keywords):
        for right in keywords[i + 1 :]:
            if len(url_sets[left] & url_sets[right]) >= threshold:
                union(left, right)

    grouped: dict[str, list[str]] = {}
    for keyword in keywords:
        grouped.setdefault(find(keyword), []).append(keyword)
    return list(grouped.values())


# Worst-first: a merge inherits the least favourable verdict of its parts.
_VERDICT_SEVERITY = {"drop": 3, "review": 2, "keep": 1}


def _carry_worst_verdict(target: dict[str, Any], folded: dict[str, Any]) -> None:
    """Propagate the harsher of two clusters' validation verdicts onto the merge.

    The cluster sense gate has already judged both. Merging must not let a
    'drop' cluster ride into page targets on a 'keep' cluster's ticket —
    ``_cluster_is_unverified`` is an ``all()`` over members, so a laundered
    verdict is the only thing standing between a rejected cluster and
    publishing.
    """
    current = str(target.get("cluster_sense_verdict") or "")
    incoming = str(folded.get("cluster_sense_verdict") or "")
    if _VERDICT_SEVERITY.get(incoming, 0) > _VERDICT_SEVERITY.get(current, 0):
        target["cluster_sense_verdict"] = incoming
    if folded.get("cluster_coherence_ok") is False:
        target["cluster_coherence_ok"] = False
    if folded.get("relevance_unverified"):
        target["relevance_unverified"] = True


def merge_clusters_by_serp_overlap(
    clusters: list[dict[str, Any]],
    groups: list[list[str]],
) -> list[dict[str, Any]]:
    """Merge clusters whose primary keywords proved to share a SERP.

    Clusters no group touches are returned unchanged. Every keyword survives
    the merge; only cluster boundaries move.
    """
    if not groups:
        return [dict(c) for c in clusters]

    # Map each primary keyword to the group that claims it.
    group_of: dict[str, int] = {}
    for index, group in enumerate(groups):
        for keyword in group:
            group_of[_norm(keyword)] = index

    merged: dict[int, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for cluster in clusters:
        primary = _norm(cluster.get("primary_keyword"))
        index = group_of.get(primary)
        if index is None:
            out.append(dict(cluster))
            continue
        existing = merged.get(index)
        if existing is None:
            head = dict(cluster)
            head["keywords"] = list(cluster.get("keywords") or [])
            merged[index] = head
            out.append(head)
            continue
        # Fold this cluster into the one that claimed the group first.
        seen = {_norm(k.get("keyword")) for k in existing.get("keywords") or []}
        for row in cluster.get("keywords") or []:
            if _norm(row.get("keyword")) not in seen:
                existing.setdefault("keywords", []).append(row)
                seen.add(_norm(row.get("keyword")))
        _carry_worst_verdict(existing, cluster)
        existing["serp_validated"] = True
        merged_from = list(existing.get("serp_merged_from") or [])
        merged_from.append(cluster.get("cluster_name") or cluster.get("primary_keyword"))
        existing["serp_merged_from"] = merged_from

    return out


async def refine_clusters_with_serp(
    clusters: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    location_code: int | None,
    language_code: str | None,
    enabled: bool | None = None,
    head_terms: int | None = None,
    min_shared: int | None = None,
    concurrency: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge head-term clusters that share a SERP. Returns (clusters, meta).

    Skipped — with no provider spend — when disabled or when the locale is
    unresolved: SERP results are market-specific, and guessing a market would
    buy data for the wrong country.
    """
    from app.config import get_settings

    settings = get_settings()
    if enabled is None:
        enabled = bool(getattr(settings, "serp_clustering_enabled", True))
    head_terms = int(
        head_terms
        if head_terms is not None
        else getattr(settings, "serp_clustering_head_terms", DEFAULT_HEAD_TERMS)
    )
    min_shared = int(
        min_shared
        if min_shared is not None
        else getattr(settings, "serp_clustering_min_shared", DEFAULT_MIN_SHARED)
    )
    concurrency = int(
        concurrency
        if concurrency is not None
        else getattr(settings, "serp_clustering_concurrency", DEFAULT_CONCURRENCY)
    )

    meta: dict[str, Any] = {
        "skipped": True,
        "reason": None,
        "head_terms": 0,
        "clusters_before": len(clusters),
        "clusters_after": len(clusters),
        "errors": [],
    }
    if not enabled:
        meta["reason"] = "disabled"
        return clusters, meta
    if not location_code or not language_code:
        meta["reason"] = "locale_unresolved"
        return clusters, meta

    head = select_head_terms(rows, limit=head_terms)
    if not head:
        meta["reason"] = "no_measured_head_terms"
        return clusters, meta

    serps, errors, snapshots = await fetch_serp_urls(
        head,
        location_code=location_code,
        language_code=language_code,
        concurrency=concurrency,
    )
    groups = serp_overlap_groups(serps, min_shared=min_shared)
    refined = merge_clusters_by_serp_overlap(clusters, groups)

    meta.update(
        {
            "skipped": False,
            "head_terms": len(head),
            "clusters_after": len(refined),
            "errors": errors[:10],
            "groups_found": sum(1 for g in groups if len(g) > 1),
            "serp_snapshots": snapshots,
        }
    )
    log.info(
        "serp_cluster_refinement",
        head_terms=len(head),
        before=len(clusters),
        after=len(refined),
    )
    return refined, meta
