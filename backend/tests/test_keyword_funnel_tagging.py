"""Phase 5 — every individual keyword carries its own funnel/stage tag, not just
the cluster or topic it belongs to. A Secondary/Supporting keyword can sit at a
different TOFU/MOFU/BOFU stage than its cluster's Primary keyword even inside
the same cluster (e.g. "what is seo" (TOFU) supporting a "seo services" (BOFU)
primary) — collapsing to one funnel value per cluster hid that."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.keyword_clustering import run_keyword_clustering
from app.services.keyword_llm_clustering import build_cluster_report_from_llm
from app.services.keyword_opportunity import detect_funnel, rank_opportunities


def _cluster_settings():
    return patch(
        "app.config.get_settings",
        return_value=type(
            "S",
            (),
            {"keyword_cluster_use_llm": True},
        )(),
    )


# --- detect_funnel: canonical heuristic, now shared across seeding/opportunity/clustering

def test_detect_funnel_text_pattern_wins_over_intent():
    # "beginner" pattern -> TOFU even though intent says commercial.
    assert detect_funnel("seo for beginners", "commercial") == "TOFU"
    assert detect_funnel("seo pricing", "informational") == "BOFU"


def test_detect_funnel_falls_back_to_intent():
    assert detect_funnel("random seo phrase", "transactional") == "BOFU"
    assert detect_funnel("random seo phrase", "commercial") == "MOFU"
    assert detect_funnel("random seo phrase", "informational") == "TOFU"
    assert detect_funnel("random seo phrase", None) == "TOFU"


# --- rank_opportunities: every scored row carries funnel -----------------------

def test_rank_opportunities_stamps_funnel_on_every_bucket():
    rows = [
        {"keyword": "seo pricing", "volume": 200, "difficulty": 30, "intent": "transactional"},
        {"keyword": "what is seo", "volume": 500, "difficulty": 20, "intent": "informational"},
    ]
    result = rank_opportunities(rows, seeds=["seo"], products=["SEO services"])
    all_rows = result["all_scored"]
    assert all_rows
    for row in all_rows:
        assert row.get("funnel") in ("TOFU", "MOFU", "BOFU")
    by_kw = {r["keyword"]: r["funnel"] for r in all_rows}
    assert by_kw["seo pricing"] == "BOFU"
    assert by_kw["what is seo"] == "TOFU"


# --- run_keyword_clustering: per-keyword funnel, not per-cluster ---------------

DETERMINISTIC_ROWS = [
    {"keyword": "seo services", "volume": 500, "difficulty": 40, "intent": "commercial"},
    {"keyword": "what is seo", "volume": 300, "difficulty": 20, "intent": "informational"},
    {"keyword": "seo pricing", "volume": 150, "difficulty": 25, "intent": "transactional"},
]


@pytest.mark.asyncio
async def test_deterministic_clustering_gives_each_keyword_its_own_funnel():
    report = await run_keyword_clustering(DETERMINISTIC_ROWS, use_llm=False)
    assert report["clusters"]
    all_kw_rows = [k for c in report["clusters"] for k in c["keywords"]]
    for row in all_kw_rows:
        assert row.get("funnel") in ("TOFU", "MOFU", "BOFU")
    by_kw = {row["keyword"]: row["funnel"] for row in all_kw_rows}
    # Different keywords in the same keyword universe get different stages —
    # this is the actual bug: everything used to inherit one cluster-level value.
    assert len({by_kw.get("seo services"), by_kw.get("what is seo"), by_kw.get("seo pricing")}) > 1


@pytest.mark.asyncio
async def test_llm_clustering_path_stamps_funnel_per_keyword():
    llm_response = {
        "clusters": [
            {
                "name": "SEO",
                "intent": "commercial",
                "funnel": "MOFU",
                "keywords": [
                    {"keyword": "seo services", "role": "Primary"},
                    {"keyword": "what is seo", "role": "Supporting"},
                ],
            }
        ]
    }
    with _cluster_settings(), patch(
        "app.services.keyword_llm_clustering.llm_cluster_keywords",
        new=AsyncMock(
            side_effect=lambda cleaned, **kw: build_cluster_report_from_llm(
                llm_response, cleaned, relevance_context=kw.get("relevance_context")
            )
        ),
    ):
        report = await run_keyword_clustering(DETERMINISTIC_ROWS, use_llm=True)

    kw_rows = report["clusters"][0]["keywords"]
    by_kw = {r["keyword"]: r["funnel"] for r in kw_rows}
    assert by_kw["seo services"] in ("TOFU", "MOFU", "BOFU")
    assert by_kw["what is seo"] == "TOFU"
