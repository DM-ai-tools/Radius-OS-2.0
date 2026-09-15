"""Phase 5 business-relevance keyword filter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.keyword_llm_relevance import (
    _business_description,
    llm_filter_keywords_by_seed,
)


def test_business_description_includes_all_known_fields():
    desc = _business_description(
        company_url="https://acme-seo.example",
        business_name="Acme SEO",
        industry="Digital Marketing",
        services=["Technical SEO audits", "Content strategy"],
        geography="Melbourne, Australia",
    )
    assert "Acme SEO" in desc
    assert "acme-seo.example" in desc


@pytest.mark.asyncio
async def test_filter_sends_compact_prompt():
    captured: list[str] = []

    async def _fake_chat(*, system, user, model, max_tokens):
        captured.append(user)
        return json.dumps({"seed": "seo audit", "relevant": ["seo audit checklist"], "dropped": []})

    rows = [{"keyword": "seo audit checklist", "seed": "seo audit", "volume": 100}]
    with patch("app.services.keyword_llm_relevance._openrouter_chat", new=_fake_chat):
        with patch("app.services.keyword_llm_relevance.get_settings") as mock_settings:
            mock_settings.return_value.use_mock_llm = False
            mock_settings.return_value.openrouter_api_key = "test-key"
            mock_settings.return_value.keyword_cluster_model = "google/gemini-2.5-flash"
            mock_settings.return_value.keyword_relevance_max_per_seed = 25
            mock_settings.return_value.keyword_relevance_max_input_per_seed = 40
            mock_settings.return_value.keyword_relevance_max_tokens = 2048
            # Single row is under cap — API skipped; use enough rows to trigger API
            rows = [
                {"keyword": f"seo audit {i}", "seed": "seo audit", "volume": 100 + i}
                for i in range(30)
            ]
            await llm_filter_keywords_by_seed(
                rows,
                company_url="https://acme-seo.example",
                competitor_urls=[],
                business_name="Acme SEO",
                industry="Digital Marketing",
                services=["Technical SEO audits"],
            )

    assert captured
    assert "KWS:" in captured[0]
    assert "seo audit 0" in captured[0]


@pytest.mark.asyncio
async def test_filter_keeps_relevant_drops_irrelevant():
    async def _fake_chat(*, system, user, model, max_tokens):
        return json.dumps(
            {
                "seed": "seo audit",
                "relevant": ["seo audit checklist"],
                "dropped": ["vegan recipes"],
            }
        )

    rows = [
        {"keyword": "seo audit checklist", "seed": "seo audit", "volume": 100},
        {"keyword": "vegan recipes", "seed": "seo audit", "volume": 50},
    ] + [
        {"keyword": f"filler {i}", "seed": "seo audit", "volume": i}
        for i in range(30)
    ]
    with patch("app.services.keyword_llm_relevance._openrouter_chat", new=_fake_chat):
        with patch("app.services.keyword_llm_relevance.get_settings") as mock_settings:
            mock_settings.return_value.use_mock_llm = False
            mock_settings.return_value.openrouter_api_key = "test-key"
            mock_settings.return_value.keyword_cluster_model = "google/gemini-2.5-flash"
            mock_settings.return_value.keyword_relevance_max_per_seed = 25
            mock_settings.return_value.keyword_relevance_max_input_per_seed = 40
            mock_settings.return_value.keyword_relevance_max_tokens = 2048
            kept, dropped, audit = await llm_filter_keywords_by_seed(
                rows,
                company_url="https://acme-seo.example",
                competitor_urls=[],
                business_name="Acme SEO",
                industry="Digital Marketing",
            )

    assert any(r["keyword"] == "seo audit checklist" for r in kept)
    assert any(r["keyword"] == "vegan recipes" for r in dropped)
    assert audit["api_calls"] == 1


@pytest.mark.asyncio
async def test_service_seed_recovers_to_minimum_twenty_after_overfilter():
    async def _fake_chat(*, system, user, model, max_tokens):
        return json.dumps(
            {
                "seed": "Technical SEO",
                "relevant": ["technical seo audit 0"],
                "dropped": [f"technical seo audit {i}" for i in range(1, 30)],
            }
        )

    with patch("app.services.keyword_llm_relevance._openrouter_chat", new=_fake_chat):
        with patch("app.services.keyword_llm_relevance.get_settings") as mock_settings:
            mock_settings.return_value.use_mock_llm = False
            mock_settings.return_value.openrouter_api_key = "test-key"
            mock_settings.return_value.keyword_cluster_model = "google/gemini-2.5-flash"
            mock_settings.return_value.keyword_relevance_max_per_seed = 25
            mock_settings.return_value.keyword_relevance_max_input_per_seed = 40
            mock_settings.return_value.keyword_relevance_max_tokens = 2048
            rows = [
                {
                    "keyword": f"technical seo audit {i}",
                    "seed": "Technical SEO",
                    "target_type": "service",
                    "match_class": "phrase",
                    "volume": 100 + i,
                }
                for i in range(30)
            ]
            kept, _, _ = await llm_filter_keywords_by_seed(
                rows,
                company_url="https://acme.example",
                competitor_urls=[],
                seed_targets={"technical seo": {"target_type": "service"}},
            )

    assert len(kept) == 20
    assert sum(1 for row in kept if row.get("relevance_rescued")) == 19


@pytest.mark.asyncio
async def test_skips_api_when_seed_under_cap():
    with patch("app.services.keyword_llm_relevance._openrouter_chat", new=AsyncMock()) as mock_chat:
        with patch("app.services.keyword_llm_relevance.get_settings") as mock_settings:
            mock_settings.return_value.use_mock_llm = False
            mock_settings.return_value.openrouter_api_key = "test-key"
            mock_settings.return_value.keyword_cluster_model = "google/gemini-2.5-flash"
            mock_settings.return_value.keyword_relevance_max_per_seed = 25
            mock_settings.return_value.keyword_relevance_max_input_per_seed = 40
            mock_settings.return_value.keyword_relevance_max_tokens = 2048
            rows = [
                {"keyword": f"seo audit {i}", "seed": "seo audit", "volume": i}
                for i in range(10)
            ]
            kept, dropped, audit = await llm_filter_keywords_by_seed(
                rows, company_url="https://acme-seo.example", competitor_urls=[]
            )
    mock_chat.assert_not_called()
    assert len(kept) == 10
    assert audit["seeds_skipped_api"] == 1


@pytest.mark.asyncio
async def test_cap_without_api_when_seed_under_limit():
    with patch("app.services.keyword_llm_relevance._openrouter_chat", new=AsyncMock()) as mock_chat:
        with patch("app.services.keyword_llm_relevance.get_settings") as mock_settings:
            mock_settings.return_value.use_mock_llm = False
            mock_settings.return_value.openrouter_api_key = "test-key"
            mock_settings.return_value.keyword_cluster_model = "google/gemini-2.5-flash"
            mock_settings.return_value.keyword_relevance_max_per_seed = 25
            mock_settings.return_value.keyword_relevance_max_input_per_seed = 40
            mock_settings.return_value.keyword_relevance_max_tokens = 2048
            rows = [
                {"keyword": f"kw {i}", "seed": "seo audit", "volume": i}
                for i in range(20)
            ]
            kept, dropped, audit = await llm_filter_keywords_by_seed(
                rows, company_url="https://acme-seo.example", competitor_urls=[]
            )
    mock_chat.assert_not_called()
    assert len(kept) == 20
    assert audit["seeds_skipped_api"] == 1


@pytest.mark.asyncio
async def test_pretrim_keeps_low_volume_tier2():
    async def _fake_chat(*, system, user, model, max_tokens):
        return json.dumps(
            {"seed": "seo audit", "relevant": [f"kw {i}" for i in range(40, 50)], "dropped": []}
        )

    with patch("app.services.keyword_llm_relevance._openrouter_chat", new=_fake_chat):
        with patch("app.services.keyword_llm_relevance.get_settings") as mock_settings:
            mock_settings.return_value.use_mock_llm = False
            mock_settings.return_value.openrouter_api_key = "test-key"
            mock_settings.return_value.keyword_cluster_model = "google/gemini-2.5-flash"
            mock_settings.return_value.keyword_relevance_max_per_seed = 25
            mock_settings.return_value.keyword_relevance_max_input_per_seed = 40
            mock_settings.return_value.keyword_relevance_max_tokens = 2048
            rows = [
                {"keyword": f"kw {i}", "seed": "seo audit", "volume": i}
                for i in range(50)
            ]
            kept, dropped, audit = await llm_filter_keywords_by_seed(
                rows, company_url="https://acme-seo.example", competitor_urls=[]
            )
    assert audit["pre_volume_tier2_kept"] == 10
    assert audit["api_calls"] == 1
    assert len(kept) == 20
    assert not any(r.get("relevance_drop_reason") == "pre_volume_cap" for r in dropped)
