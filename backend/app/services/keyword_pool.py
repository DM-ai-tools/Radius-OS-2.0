"""Keyword pool sizing — scale seeding + relevance caps toward a target pool size."""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings


def _norm_kw(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def row_volume_key(row: dict[str, Any]) -> int:
    try:
        return int(row.get("volume") or 0)
    except (TypeError, ValueError):
        return 0


def dedupe_across_seeds(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep one row per normalized keyword — highest volume wins.

    Note the relevance gate deliberately does NOT use this: it judges each
    (service, keyword) pair separately, because a keyword can genuinely fit one
    service and not another. This stays available for pool-shaping callers that
    want a flat, one-row-per-keyword view.
    """
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        kw = _norm_kw(row.get("keyword"))
        if not kw:
            continue
        prev = best.get(kw)
        if not prev:
            best[kw] = row
            order.append(kw)
            continue
        if row_volume_key(row) > row_volume_key(prev):
            best[kw] = row
    kept = [best[kw] for kw in order if kw in best]
    dropped: list[dict[str, Any]] = []
    seen_kept = {_norm_kw(r.get("keyword")) for r in kept}
    for row in rows:
        kw = _norm_kw(row.get("keyword"))
        if not kw or kw not in seen_kept:
            continue
        if row is not best[kw]:
            dropped.append({**row, "relevance_drop_reason": "cross_seed_dedupe"})
    return kept, dropped


def resolve_client_pool_target(
    *,
    commercial: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    settings: Settings,
) -> int:
    """Per-client override in commercial_scope or marketing_context, else global default."""
    commercial = dict(commercial or {})
    marketing = dict(marketing or {})
    for src in (commercial, marketing, dict(commercial.get("service_prioritization") or {})):
        raw = src.get("keyword_pool_target")
        if raw is not None:
            try:
                return max(500, min(10000, int(raw)))
            except (TypeError, ValueError):
                pass
    return max(500, int(getattr(settings, "keyword_pool_target", 4500)))


def resolve_keyword_pool_limits(
    *,
    pool_target: int,
    seed_count: int,
    settings: Settings,
) -> dict[str, int]:
    """Derive seeding limits from a target post-filter pool size.

    Each DataForSEO call asks for the Labs ceiling of 1,000 keywords. The
    seed count still sizes how many roots a run may expand.
    """
    target = max(500, min(10000, int(pool_target)))
    expanding = max(
        1,
        int(seed_count) if seed_count else int(getattr(settings, "keyword_max_seeds", 50)),
    )
    # Room for extra roots when the caller has them. Do not invent a minimum
    # that is larger than the roots on hand — that only dilutes the per-call ask.
    max_seeds = min(80, max(expanding, min(80, target // 120)))
    # One page per endpoint, capped at the Labs maximum of 1,000 rows.
    per_mode = max(20, min(1000, int(getattr(settings, "keyword_labs_page_limit", 1000) or 1000)))
    per_seed = max(
        int(getattr(settings, "keyword_relevance_max_per_seed", 60)),
        min(250, target // expanding),
    )
    max_input = max(
        int(getattr(settings, "keyword_relevance_max_input_per_seed", 80)),
        min(300, per_mode + 40),
    )
    return {
        "keyword_pool_target": target,
        "max_seeds": max_seeds,
        "limit_per_mode": per_mode,
        "keyword_relevance_max_per_seed": per_seed,
        "keyword_relevance_max_input_per_seed": max_input,
    }
