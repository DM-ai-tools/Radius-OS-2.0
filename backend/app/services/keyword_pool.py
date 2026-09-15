"""Keyword pool sizing — scale seeding + relevance caps toward a target pool size."""

from __future__ import annotations

from typing import Any

from app.config import Settings


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
    """Derive seeding + relevance limits from a target post-filter pool size."""
    target = max(500, min(10000, int(pool_target)))
    seeds = seed_count or int(getattr(settings, "keyword_max_seeds", 50))
    seeds = max(20, min(80, seeds, max(20, target // 80)))
    # ~4 match classes per seed; expect heavy overlap → aim ~2.5× raw expansion
    per_mode = max(35, min(100, target // max(seeds, 1) // 3))
    per_seed = max(
        int(getattr(settings, "keyword_relevance_max_per_seed", 60)),
        min(100, target // max(seeds, 1) // 2),
    )
    max_input = max(
        int(getattr(settings, "keyword_relevance_max_input_per_seed", 80)),
        min(120, per_mode + 20),
    )
    return {
        "keyword_pool_target": target,
        "max_seeds": seeds,
        "limit_per_mode": per_mode,
        "keyword_relevance_max_per_seed": per_seed,
        "keyword_relevance_max_input_per_seed": max_input,
    }
