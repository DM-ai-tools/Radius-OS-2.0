"""Live commercial DataForSEO calls: ranking pages, traffic, and the daily cap."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import get_settings
from app.integrations import dataforseo
from app.ml.tier_competitors import _stamp_measured_traffic
from app.services.content_audit import _attach_rival_urls
from app.services.content_production import _point_rivals_at_ranking_pages


def _labs_body(items):
    return {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": items}]}]}


def test_pick_ranking_url_requires_the_same_keyword_and_a_url():
    rows = [
        {"keyword": "seo services melbourne", "url": "https://rival.example/seo"},
        {"keyword": "seo services", "url": None},
        {"keyword": "seo services", "url": "https://rival.example/services/seo"},
    ]
    match = dataforseo.pick_ranking_url(rows, "SEO Services")
    assert match["url"] == "https://rival.example/services/seo"


@pytest.mark.asyncio
async def test_top_searches_and_relevant_pages_keep_measured_fields():
    async def _post(path, payload):
        if path.endswith("/top_searches/live"):
            return _labs_body(
                [
                    {
                        "keyword": "seo services",
                        "keyword_info": {"search_volume": 2400},
                        "ranked_serp_element": {
                            "serp_item": {"url": "https://rival.example/seo", "rank_group": 4}
                        },
                    }
                ]
            )
        return _labs_body(
            [
                {
                    "page_address": "https://rival.example/seo",
                    "metrics": {"organic": {"etv": 120.4, "count": 18}},
                }
            ]
        )

    with patch("app.integrations.dataforseo._post", new=AsyncMock(side_effect=_post)), patch(
        "app.integrations.dataforseo._auth_header", return_value="Basic x"
    ):
        searches, search_errors = await dataforseo.top_searches("rival.example")
        pages, page_errors = await dataforseo.relevant_pages("https://www.rival.example")

    assert search_errors == []
    assert searches[0]["keyword"] == "seo services"
    assert searches[0]["url"] == "https://rival.example/seo"
    assert searches[0]["position"] == 4
    assert searches[0]["volume"] == 2400
    assert page_errors == []
    assert pages[0]["url"] == "https://rival.example/seo"
    assert pages[0]["organic_traffic"] == 120
    assert pages[0]["keyword_count"] == 18


@pytest.mark.asyncio
async def test_bulk_traffic_omits_a_domain_with_no_estimate():
    body = _labs_body(
        [
            {
                "target": "rival.example",
                "metrics": {"organic": {"etv": 500.2, "estimated_paid_traffic_cost": 80.5}},
            },
            {"target": "empty.example", "metrics": {"organic": {}}},
        ]
    )
    with patch("app.integrations.dataforseo._post", new=AsyncMock(return_value=body)), patch(
        "app.integrations.dataforseo._auth_header", return_value="Basic x"
    ):
        rows, errors = await dataforseo.bulk_traffic_estimation(
            ["https://www.rival.example", "empty.example"]
        )
    assert errors == []
    assert rows == [
        {
            "domain": "rival.example",
            "organic_traffic": 500,
            "organic_traffic_cost": 80.5,
            "source": "dataforseo",
        }
    ]


@pytest.mark.asyncio
async def test_daily_budget_stops_the_call_before_http():
    settings = get_settings()
    previous = (
        settings.dataforseo_daily_budget_usd,
        settings.dataforseo_login,
        settings.dataforseo_password,
        settings.use_mock_providers,
    )
    settings.dataforseo_daily_budget_usd = 1
    settings.dataforseo_login = "user"
    settings.dataforseo_password = "pw"
    settings.use_mock_providers = False
    try:
        with (
            patch(
                "app.services.api_meter.provider_spend_today_usd",
                new=AsyncMock(return_value=0.99),
            ),
            patch("app.services.api_meter.record_api_call", new=AsyncMock()),
            patch("app.integrations.dataforseo.httpx.AsyncClient") as client,
        ):
            result = await dataforseo._post(
                "/dataforseo_labs/google/top_searches/live",
                [{"target": "rival.example"}],
            )
        assert result is None
        client.assert_not_called()
    finally:
        (
            settings.dataforseo_daily_budget_usd,
            settings.dataforseo_login,
            settings.dataforseo_password,
            settings.use_mock_providers,
        ) = previous


@pytest.mark.asyncio
async def test_disabled_budget_does_not_read_spend():
    settings = get_settings()
    previous = settings.dataforseo_daily_budget_usd
    settings.dataforseo_daily_budget_usd = 0
    try:
        with patch(
            "app.services.api_meter.provider_spend_today_usd",
            new=AsyncMock(return_value=100),
        ) as spend:
            assert await dataforseo._over_daily_budget() is False
        spend.assert_not_awaited()
    finally:
        settings.dataforseo_daily_budget_usd = previous


@pytest.mark.asyncio
async def test_unreadable_meter_does_not_block():
    settings = get_settings()
    previous = settings.dataforseo_daily_budget_usd
    settings.dataforseo_daily_budget_usd = 1
    try:
        with patch(
            "app.services.api_meter.provider_spend_today_usd",
            new=AsyncMock(return_value=None),
        ):
            assert await dataforseo._over_daily_budget() is False
    finally:
        settings.dataforseo_daily_budget_usd = previous


def test_audit_uses_the_rival_url_that_ranks_for_the_keyword():
    inventory = [{"keyword": "seo services", "url": "/services/seo"}]
    _attach_rival_urls(
        inventory,
        [
            {
                "domain": "rival.example",
                "top_searches": [
                    {"keyword": "seo services", "url": "https://rival.example/seo", "position": 4}
                ],
            }
        ],
    )
    assert inventory[0]["rival_ranking_url"] == "https://rival.example/seo"
    assert inventory[0]["rival_domain"] == "rival.example"


@pytest.mark.asyncio
async def test_comparison_fetch_uses_the_ranking_url():
    with patch(
        "app.integrations.dataforseo.top_searches",
        new=AsyncMock(
            return_value=(
                [{"keyword": "seo comparison", "url": "https://rival.example/compare", "position": 3}],
                [],
            )
        ),
    ):
        pointed = await _point_rivals_at_ranking_pages(
            [{"name": "Rival", "url": "https://rival.example"}],
            "seo comparison",
        )
    assert pointed[0]["url"] == "https://rival.example/compare"
    assert pointed[0]["ranking_position"] == 3


@pytest.mark.asyncio
async def test_measured_traffic_does_not_change_the_tier():
    card = {
        "name": "Rival",
        "url": "https://rival.example",
        "tier": 2,
        "composite": 72.0,
    }
    with patch(
        "app.integrations.dataforseo.bulk_traffic_estimation",
        new=AsyncMock(
            return_value=(
                [
                    {
                        "domain": "rival.example",
                        "organic_traffic": 500,
                        "organic_traffic_cost": 80.5,
                        "source": "dataforseo",
                    },
                    {
                        "domain": "acme.example",
                        "organic_traffic": 90,
                        "organic_traffic_cost": None,
                        "source": "dataforseo",
                    },
                ],
                [],
            )
        ),
    ):
        client_fields = await _stamp_measured_traffic([card], "https://acme.example")
    assert card["tier"] == 2
    assert card["composite"] == 72.0
    assert card["organic_traffic"] == 500
    assert card["organic_traffic_source"] == "dataforseo"
    assert client_fields["organic_traffic"] == 90
