"""Keyword pool sizing and cross-seed dedupe."""

from app.services.keyword_llm_relevance import _dedupe_across_seeds
from app.services.keyword_pool import (
    resolve_client_pool_target,
    resolve_keyword_pool_limits,
)


class _Settings:
    keyword_pool_target = 4500
    keyword_max_seeds = 50
    keyword_relevance_max_per_seed = 60
    keyword_relevance_max_input_per_seed = 80


def test_resolve_keyword_pool_limits_scales_with_target():
    limits = resolve_keyword_pool_limits(pool_target=4500, seed_count=40, settings=_Settings())
    assert limits["keyword_pool_target"] == 4500
    assert limits["max_seeds"] >= 40
    assert limits["limit_per_mode"] >= 35
    assert limits["keyword_relevance_max_per_seed"] >= 60


def test_resolve_client_pool_target_prefers_commercial_override():
    target = resolve_client_pool_target(
        commercial={"keyword_pool_target": 5000},
        marketing={},
        settings=_Settings(),
    )
    assert target == 5000


def test_dedupe_across_seeds_keeps_highest_volume():
    rows = [
        {"keyword": "seo audit", "seed": "seo services", "volume": 100},
        {"keyword": "seo audit", "seed": "seo audit", "volume": 250},
        {"keyword": "local seo", "seed": "local seo", "volume": 80},
    ]
    kept, dropped = _dedupe_across_seeds(rows)
    assert len(kept) == 2
    audit = next(r for r in kept if r["keyword"] == "seo audit")
    assert audit["volume"] == 250
    assert any(r.get("relevance_drop_reason") == "cross_seed_dedupe" for r in dropped)
