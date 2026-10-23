"""Every measured field a DataForSEO keyword response carries must be read.

The Labs keyword endpoints return trend, provider intent, bid range, SERP
backlink averages and language detection inside the same item we already pay
for. The parser used to keep only keyword/volume/competition/cpc/difficulty,
so expansions reached scoring with no demand direction and unlabelled intent,
and the pipeline paid for a second call to recover what it had already bought.

The rule these tests protect: read everything the provider measured, and leave
None for everything it did not. Nothing here may be derived or defaulted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.integrations import dataforseo


def _labs_body(items):
    return {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": items}]}]}


def _rising_months():
    return [
        {"year": 2026, "month": m, "search_volume": v}
        for m, v in ((4, 100), (5, 110), (6, 105), (7, 300), (8, 320), (9, 340))
    ]


def _full_item():
    """A Labs item with every block the documented response can carry."""
    return {
        "keyword": "drain cleaning melbourne",
        "keyword_info": {
            "search_volume": 720,
            "competition": 0.41,
            "competition_level": "MEDIUM",
            "cpc": 5.2,
            "low_top_of_page_bid": 3.1,
            "high_top_of_page_bid": 9.4,
            "categories": [10021, 13418],
            "monthly_searches": _rising_months(),
            "search_volume_trend": {"monthly": 4, "quarterly": 18, "yearly": 62},
        },
        "keyword_properties": {
            "keyword_difficulty": 33,
            "detected_language": "en",
            "is_another_language": False,
        },
        "search_intent_info": {
            "main_intent": "commercial",
            "foreign_intent": ["informational"],
        },
        "avg_backlinks_info": {
            "backlinks": 1200.5,
            "referring_domains": 100.3,
            "main_domain_rank": 2.1,
        },
        "serp_info": {"se_results_count": 1300000, "serp_item_types": ["organic", "local_pack"]},
        "keyword_info_normalized_with_clickstream": {"search_volume": 640},
    }


async def _suggestions(items):
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=_labs_body(items))),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.keyword_suggestions("drain cleaning")
    assert errors == []
    return rows


async def test_labs_row_keeps_every_measured_field():
    row = (await _suggestions([_full_item()]))[0]

    assert row["keyword"] == "drain cleaning melbourne"
    assert row["volume"] == 720
    assert row["competition"] == 0.41
    assert row["competition_level"] == "MEDIUM"
    assert row["cpc"] == 5.2
    assert row["difficulty"] == 33
    # The four that used to be dropped on the floor.
    assert row["trend"] == "rising"
    assert row["intent"] == "commercial"
    assert row["bid_low"] == 3.1
    assert row["bid_high"] == 9.4


async def test_labs_row_keeps_serp_difficulty_evidence():
    """Backlink averages say how hard the SERP really is, not how hard the word looks."""
    row = (await _suggestions([_full_item()]))[0]

    assert row["serp_backlinks"] == 1200.5
    assert row["serp_referring_domains"] == 100.3
    assert row["serp_domain_rank"] == 2.1
    assert row["serp_results_count"] == 1300000
    assert row["serp_item_types"] == ["organic", "local_pack"]


async def test_labs_row_keeps_language_detection_and_clickstream_volume():
    row = (await _suggestions([_full_item()]))[0]

    assert row["detected_language"] == "en"
    assert row["is_another_language"] is False
    # Clickstream volume arrives free here; bulk_search_volume is a separate charge.
    assert row["volume_clickstream"] == 640


async def test_labs_row_keeps_secondary_intents_without_inventing_probabilities():
    """foreign_intent has no probabilities. Reporting one would be a fabrication."""
    row = (await _suggestions([_full_item()]))[0]

    assert row["secondary_intents"] == [{"label": "informational", "probability": None}]


async def test_labs_row_reports_the_percentage_trend_object():
    row = (await _suggestions([_full_item()]))[0]

    assert row["volume_trend_pct"] == {"monthly": 4.0, "quarterly": 18.0, "yearly": 62.0}


# --- the measured-only rule ---


async def test_a_sparse_item_reports_no_trend_rather_than_stable():
    """The regression that matters most.

    Labs items routinely omit monthly_searches. Stamping "stable" on them
    would hand the blog virality gate a demand signal the provider never gave,
    which is exactly the invented-metric failure the pipeline forbids.
    """
    rows = await _suggestions([{"keyword": "drain cleaning", "keyword_info": {"search_volume": 90}}])

    assert rows[0]["volume"] == 90
    assert rows[0]["trend"] is None


async def test_a_sparse_item_leaves_every_unmeasured_field_none():
    rows = await _suggestions([{"keyword": "drain cleaning", "keyword_info": {"search_volume": 90}}])
    row = rows[0]

    for field in ("cpc", "competition", "difficulty", "intent", "bid_low", "bid_high"):
        assert row[field] is None, field


async def test_trend_falls_back_to_the_percentage_object_when_months_are_absent():
    rows = await _suggestions(
        [
            {
                "keyword": "emergency plumber",
                "keyword_info": {"search_volume": 400, "search_volume_trend": {"yearly": -45}},
            }
        ]
    )
    assert rows[0]["trend"] == "declining"


@pytest.mark.parametrize(
    ("months", "expected"),
    [
        ([], None),
        ([{"year": 2026, "month": 9, "search_volume": 10}], None),
        ("not-a-list", None),
        ([{"search_volume": None}] * 6, None),
    ],
)
def test_monthly_trend_refuses_to_answer_without_three_measured_months(months, expected):
    assert dataforseo._monthly_trend(months) is expected


def test_google_ads_trend_helper_keeps_its_stable_default():
    """search_volume() still defaults to stable — Google Ads always sends months."""
    assert dataforseo._trend_from_monthly([]) == "stable"


# --- google_ads/search_volume ---


async def test_search_volume_keeps_the_bid_range_and_monthly_series():
    body = {
        "status_code": 20000,
        "tasks": [
            {
                "status_code": 20000,
                "result": [
                    {
                        "keyword": "drain cleaning melbourne",
                        "search_volume": 720,
                        "cpc": 5.2,
                        "competition": "HIGH",
                        "competition_index": 88,
                        "low_top_of_page_bid": 3.1,
                        "high_top_of_page_bid": 9.4,
                        "monthly_searches": _rising_months(),
                    }
                ],
            }
        ],
    }
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.search_volume(["drain cleaning melbourne"])

    assert errors == []
    row = rows[0]
    assert row["bid_low"] == 3.1
    assert row["bid_high"] == 9.4
    assert row["competition_index"] == 88
    # A band is kept verbatim; turning HIGH into a float would invent a number.
    assert row["competition_level"] == "HIGH"
    assert row["competition"] is None
    assert len(row["monthly_searches"]) == 6
    assert row["trend"] == "rising"


# --- ranked_keywords and keyword_gap ---


async def test_ranked_keywords_keeps_difficulty_intent_and_the_ranking_url():
    item = _full_item()
    body = _labs_body(
        [
            {
                "keyword_data": item,
                "ranked_serp_element": {
                    "serp_item": {
                        "rank_absolute": 3,
                        "etv": 120.5,
                        "url": "https://northside.example/drain-cleaning",
                        "title": "Drain Cleaning Melbourne",
                    }
                },
            }
        ]
    )
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.ranked_keywords("northside.example")

    assert errors == []
    row = rows[0]
    assert row["position"] == 3
    assert row["volume"] == 720
    # The URL names the page already competing, so an audit can fetch it.
    assert row["url"] == "https://northside.example/drain-cleaning"
    assert row["difficulty"] == 33
    assert row["intent"] == "commercial"
    assert row["trend"] == "rising"


async def test_keyword_gap_keeps_the_metrics_it_already_paid_for():
    body = _labs_body([{"keyword_data": _full_item()}])
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.keyword_gap("client.example", ["rival.example"])

    assert errors == []
    row = rows[0]
    assert row["gap_flag"] is True
    assert row["volume"] == 720
    assert row["difficulty"] == 33
    assert row["cpc"] == 5.2
    assert row["intent"] == "commercial"


async def test_top_searches_keeps_metrics_alongside_the_ranking_url():
    body = _labs_body(
        [
            {
                "keyword_data": _full_item(),
                "ranked_serp_element": {
                    "serp_item": {"rank_absolute": 5, "url": "https://x.example/a"}
                },
            }
        ]
    )
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.top_searches("x.example")

    assert errors == []
    assert rows[0]["url"] == "https://x.example/a"
    assert rows[0]["position"] == 5
    assert rows[0]["difficulty"] == 33


async def test_a_row_with_no_keyword_is_still_skipped():
    assert await _suggestions([{"keyword": "", "keyword_info": {"search_volume": 10}}]) == []
