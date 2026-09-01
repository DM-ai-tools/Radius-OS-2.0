"""Phase 5 keyword clustering — LLM-invented keywords must pass the same
business-relevance gate as every seeded keyword, not just get null metrics.

Every keyword reaching the LLM in `cleaned` already survived
keyword_relevance.filter_relevant_keywords. If the LLM's clustering response
names a term that isn't in that grounded set, it's either a harmless paraphrase
or a genuinely invented, possibly off-topic term — and until this fix, either
way it kept its place in the cluster (even as the public-facing primary
keyword) with only its volume/difficulty nulled out. Nothing checked whether
the invented term was actually about the client's business.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.keyword_clustering import run_keyword_clustering
from app.services.keyword_llm_clustering import build_cluster_report_from_llm
from app.services.keyword_relevance import build_relevance_context


def _cluster_settings():
    return patch(
        "app.config.get_settings",
        return_value=type(
            "S",
            (),
            {"keyword_cluster_use_llm": True},
        )(),
    )

SEEDED_ROWS = [
    {"keyword": "technical seo audit", "volume": 500, "difficulty": 30, "intent": "commercial"},
    {"keyword": "seo audit checklist", "volume": 300, "difficulty": 25, "intent": "informational"},
    {"keyword": "site speed audit", "volume": 200, "difficulty": 20, "intent": "informational"},
]


def _ctx():
    return build_relevance_context(
        services=["technical SEO audits"],
        cdd_keywords=["seo audit"],
        brand_name="Acme SEO",
        domain="acme-seo.example",
    )


@pytest.mark.asyncio
async def test_llm_invented_irrelevant_keyword_is_dropped_not_just_unmetriced():
    llm_response = {
        "clusters": [
            {
                "name": "SEO Audits",
                "intent": "commercial",
                "funnel": "MOFU",
                "keywords": [
                    {"keyword": "vegan recipes for beginners", "role": "Primary"},
                    {"keyword": "technical seo audit", "role": "Supporting"},
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
        report = await run_keyword_clustering(
            SEEDED_ROWS, relevance_context=_ctx(), use_llm=True
        )

    assert report["clusters"], "cluster should survive with its grounded keyword"
    cluster = report["clusters"][0]
    kws = {k["keyword"] for k in cluster["keywords"]}
    assert "vegan recipes for beginners" not in kws
    assert "technical seo audit" in kws
    # The dropped, off-topic invention got promoted out of Primary — the
    # remaining grounded keyword takes over instead of an unrelated term
    # becoming the cluster's public-facing primary_keyword.
    assert cluster["primary_keyword"] == "technical seo audit"
    dropped = report.get("llm_invented_dropped") or []
    assert any(d["keyword"] == "vegan recipes for beginners" for d in dropped)


@pytest.mark.asyncio
async def test_llm_invented_relevant_paraphrase_is_kept_with_null_metrics():
    """An invented term that DOES overlap business evidence (a paraphrase, not
    an off-topic invention) should still be kept — just without fabricated
    volume/difficulty, per the existing ungrounded-metrics rule."""
    llm_response = {
        "clusters": [
            {
                "name": "SEO Audits",
                "intent": "commercial",
                "funnel": "MOFU",
                "keywords": [
                    {"keyword": "technical seo audit", "role": "Primary"},
                    {"keyword": "seo audit service pricing", "role": "Supporting"},
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
        report = await run_keyword_clustering(
            SEEDED_ROWS, relevance_context=_ctx(), use_llm=True
        )

    cluster = report["clusters"][0]
    invented = next(
        k for k in cluster["keywords"] if k["keyword"] == "seo audit service pricing"
    )
    assert invented["ungrounded"] is True
    assert invented["volume"] is None
    assert invented["difficulty"] is None


@pytest.mark.asyncio
async def test_cluster_dropped_entirely_when_every_keyword_is_irrelevant():
    llm_response = {
        "clusters": [
            {
                "name": "Off Topic",
                "intent": "informational",
                "funnel": "TOFU",
                "keywords": [
                    {"keyword": "vegan recipes", "role": "Primary"},
                    {"keyword": "best hiking boots", "role": "Supporting"},
                ],
            },
            {
                "name": "SEO Audits",
                "intent": "commercial",
                "funnel": "MOFU",
                "keywords": [{"keyword": "technical seo audit", "role": "Primary"}],
            },
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
        report = await run_keyword_clustering(
            SEEDED_ROWS, relevance_context=_ctx(), use_llm=True
        )

    names = [c["name"] for c in report["clusters"]]
    assert "Off Topic" not in names
    assert "SEO Audits" in names


@pytest.mark.asyncio
async def test_without_relevance_context_ungrounded_keywords_keep_prior_behavior():
    """No relevance context available (e.g. a client with no CDD/website data
    yet) — conservative default: don't drop, just null the metrics, same as
    before this fix. There's no basis to judge relevance without a context."""
    llm_response = {
        "clusters": [
            {
                "name": "SEO Audits",
                "intent": "commercial",
                "funnel": "MOFU",
                "keywords": [
                    {"keyword": "technical seo audit", "role": "Primary"},
                    {"keyword": "vegan recipes for beginners", "role": "Supporting"},
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
        report = await run_keyword_clustering(SEEDED_ROWS, relevance_context=None, use_llm=True)

    kws = {k["keyword"] for k in report["clusters"][0]["keywords"]}
    assert "vegan recipes for beginners" in kws
    assert report.get("llm_invented_dropped") == []
