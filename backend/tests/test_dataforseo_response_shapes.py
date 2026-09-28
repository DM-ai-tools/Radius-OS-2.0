"""DataForSEO returns two different response shapes. Both must parse.

Labs endpoints nest items under result[0].items. The keywords_data endpoints
(google_ads/search_volume) return the keyword objects directly in result.
Assuming only the Labs shape made search_volume() silently return nothing —
which killed volume data for the whole keyword pool and dropped every
competitor-page keyword, because those require volume evidence.

Shapes here match the documented responses at docs.dataforseo.com.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.integrations import dataforseo


def _labs_body(items):
    return {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": items}]}]}


def _keywords_data_body(rows):
    """google_ads/search_volume: result IS the array of keyword objects."""
    return {"status_code": 20000, "tasks": [{"status_code": 20000, "result": rows}]}


def test_task_items_parses_the_labs_nested_shape():
    body = _labs_body([{"keyword": "drain cleaning", "keyword_difficulty": 24}])
    assert dataforseo._task_items(body) == [
        {"keyword": "drain cleaning", "keyword_difficulty": 24}
    ]


def test_task_items_parses_the_flat_keywords_data_shape():
    body = _keywords_data_body(
        [{"keyword": "drain cleaning", "search_volume": 720, "cpc": 5.0}]
    )
    items = dataforseo._task_items(body)
    assert [i["keyword"] for i in items] == ["drain cleaning"]
    assert items[0]["search_volume"] == 720


def test_task_items_ignores_non_dict_entries_in_a_flat_result():
    body = _keywords_data_body([{"keyword": "ok", "search_volume": 10}, "junk", None])
    assert [i["keyword"] for i in dataforseo._task_items(body)] == ["ok"]


def test_task_items_returns_empty_for_a_failed_task():
    body = {"tasks": [{"status_code": 40501, "status_message": "bad", "result": None}]}
    assert dataforseo._task_items(body) == []


def test_task_items_survives_empty_and_missing_bodies():
    assert dataforseo._task_items(None) == []
    assert dataforseo._task_items({}) == []
    assert dataforseo._task_items({"tasks": []}) == []
    assert dataforseo._task_items({"tasks": [{"result": []}]}) == []


async def test_search_volume_keeps_the_captured_google_ads_rows():
    """Live Google Ads body: rows sit directly under result, not result[0].items.

    The captured call returned vacuum feedthrough 170, cleanroom tacky mats 110,
    and emi shielding gasket 260. Reading only nested items discarded all three
    and raised no error.
    """
    body = {
        "status_code": 20000,
        "status_message": "Ok.",
        "tasks": [
            {
                "status_code": 20000,
                "status_message": "Ok.",
                "result_count": 3,
                "cost": 0.09,
                "result": [
                    {
                        "keyword": "vacuum feedthrough",
                        "search_volume": 170,
                        "cpc": 1.2,
                        "competition": "LOW",
                    },
                    {
                        "keyword": "cleanroom tacky mats",
                        "search_volume": 110,
                        "cpc": 0.8,
                        "competition": "MEDIUM",
                    },
                    {
                        "keyword": "emi shielding gasket",
                        "search_volume": 260,
                        "cpc": 2.4,
                        "competition": "HIGH",
                    },
                ],
            }
        ],
    }
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.search_volume(
            ["vacuum feedthrough", "cleanroom tacky mats", "emi shielding gasket"]
        )
    assert errors == []
    assert {row["keyword"]: row["volume"] for row in rows} == {
        "vacuum feedthrough": 170,
        "cleanroom tacky mats": 110,
        "emi shielding gasket": 260,
    }


async def test_search_volume_reports_a_200_body_it_cannot_parse():
    body = _keywords_data_body(
        [{"keyword": "vacuum feedthrough", "search_volume": 170}]
    )
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
        patch.object(dataforseo, "_task_items", lambda _body: []),
    ):
        rows, errors = await dataforseo.search_volume(["vacuum feedthrough"])
    assert rows == []
    assert "dataforseo_volume_unparsed" in errors


async def test_search_volume_returns_rows_for_the_documented_response():
    """The regression that mattered: this used to come back empty."""
    body = _keywords_data_body(
        [
            {
                "keyword": "drain cleaning melbourne",
                "search_volume": 720,
                "cpc": 5.2,
                "competition": 0.41,
                "monthly_searches": [
                    {"year": 2026, "month": 8, "search_volume": 700},
                    {"year": 2026, "month": 9, "search_volume": 740},
                ],
            }
        ]
    )
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.search_volume(["drain cleaning melbourne"])
    assert errors == []
    assert len(rows) == 1
    assert rows[0]["keyword"] == "drain cleaning melbourne"
    assert rows[0]["volume"] == 720
    assert rows[0]["cpc"] == 5.2
    assert rows[0]["source"] == "dataforseo"


async def test_keyword_difficulty_still_parses_its_labs_shape():
    body = _labs_body([{"keyword": "drain cleaning", "keyword_difficulty": 33}])
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, _errors = await dataforseo.keyword_difficulty(["drain cleaning"])
    assert rows[0]["difficulty"] == 33


def test_labs_wrapper_with_null_items_yields_nothing_not_the_wrapper():
    """Regression guard on the shape fix itself.

    DataForSEO sends "items": null for an empty Labs result. A naive flat
    fallback would return the wrapper object as if it were a keyword row.
    """
    body = {
        "status_code": 20000,
        "tasks": [
            {
                "status_code": 20000,
                "result": [
                    {
                        "se_type": "google",
                        "seed_keyword": "drain cleaning",
                        "location_code": 2036,
                        "items_count": 0,
                        "items": None,
                    }
                ],
            }
        ],
    }
    assert dataforseo._task_items(body) == []


def test_labs_wrapper_with_items_count_but_no_items_key_yields_nothing():
    body = {
        "tasks": [
            {"status_code": 20000, "result": [{"se_type": "google", "items_count": 0}]}
        ]
    }
    assert dataforseo._task_items(body) == []


# --- ranked_keywords: the DataForSEO replacement for SEMrush organic_keywords ---


def _ranked_body(rows):
    return {
        "status_code": 20000,
        "tasks": [{"status_code": 20000, "result": [{"items": rows}]}],
    }


async def test_ranked_keywords_extracts_keyword_volume_and_position():
    body = _ranked_body(
        [
            {
                "keyword_data": {
                    "keyword": "drain cleaning melbourne",
                    "keyword_info": {"search_volume": 720, "cpc": 5.1, "competition": 0.4},
                },
                "ranked_serp_element": {"serp_item": {"rank_absolute": 3, "etv": 120.5}},
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
    assert row["keyword"] == "drain cleaning melbourne"
    assert row["volume"] == 720
    assert row["position"] == 3
    assert row["domain"] == "northside.example"
    assert row["source"] == "dataforseo"


async def test_ranked_keywords_normalizes_the_target_domain():
    seen = {}

    async def _capture(path, payload):
        seen["target"] = payload[0]["target"]
        return _ranked_body([])

    with (
        patch.object(dataforseo, "_post", _capture),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        await dataforseo.ranked_keywords("https://www.Northside.example/services/")
    assert seen["target"] == "northside.example"


async def test_ranked_keywords_skips_rows_with_no_keyword():
    body = _ranked_body([{"keyword_data": {"keyword": ""}}, {"nothing": 1}])
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=body)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, _errors = await dataforseo.ranked_keywords("x.example")
    assert rows == []


async def test_ranked_keywords_reports_failure_without_raising():
    with (
        patch.object(dataforseo, "_post", AsyncMock(return_value=None)),
        patch.object(dataforseo, "_auth_header", lambda: "Basic x"),
    ):
        rows, errors = await dataforseo.ranked_keywords("x.example")
    assert rows == []
    assert errors
