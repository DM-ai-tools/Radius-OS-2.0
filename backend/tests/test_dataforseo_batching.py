"""Batching, multi-task parsing and real cost capture for the DataForSEO client.

Three defects this locks down, all verified against docs.dataforseo.com:

1. search_volume capped its batch at 40 keywords with a `break`, and
   bulk_keyword_difficulty at 20 — then issued a single request and dropped
   every keyword past the cap on the floor. Both endpoints document a maximum
   of 1000 keywords per task. A 4,500-keyword pool got measured volume for 40
   keywords and difficulty for 20; everything else fell through to the
   fabricated-volume path.
2. _task_items read tasks[0] only, so any multi-task response lost every task
   after the first. That has to hold before anything batches.
3. Every response carries a `cost` field (top level and per task). The client
   discarded it, so api_usage_log recorded a flat 0.0025 guess for calls whose
   real cost varies by two orders of magnitude.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.integrations import dataforseo


def _keywords_data_body(rows, *, cost=None):
    """google_ads/search_volume: result IS the array of keyword objects."""
    task = {"status_code": 20000, "result": rows}
    if cost is not None:
        task["cost"] = cost
    body = {"status_code": 20000, "tasks": [task]}
    if cost is not None:
        body["cost"] = cost
    return body


def _labs_body(items, *, cost=None):
    task = {"status_code": 20000, "result": [{"items": items}]}
    if cost is not None:
        task["cost"] = cost
    body = {"status_code": 20000, "tasks": [task]}
    if cost is not None:
        body["cost"] = cost
    return body


def _volume_rows(keywords):
    return [{"keyword": k, "search_volume": 100, "cpc": 1.0, "competition": 0.5} for k in keywords]


# --- 2. multi-task parsing -------------------------------------------------


def test_task_items_merges_every_task_not_just_the_first():
    """A POST may carry several tasks; tasks[1:] were silently discarded."""
    body = {
        "status_code": 20000,
        "tasks": [
            {"status_code": 20000, "result": [{"keyword": "a", "search_volume": 1}]},
            {"status_code": 20000, "result": [{"keyword": "b", "search_volume": 2}]},
            {"status_code": 20000, "result": [{"keyword": "c", "search_volume": 3}]},
        ],
    }
    assert [i["keyword"] for i in dataforseo._task_items(body)] == ["a", "b", "c"]


def test_task_items_merges_labs_tasks():
    body = {
        "status_code": 20000,
        "tasks": [
            {"status_code": 20000, "result": [{"items": [{"keyword": "a"}]}]},
            {"status_code": 20000, "result": [{"items": [{"keyword": "b"}]}]},
        ],
    }
    assert [i["keyword"] for i in dataforseo._task_items(body)] == ["a", "b"]


def test_task_items_skips_a_failed_task_but_keeps_its_healthy_siblings():
    body = {
        "status_code": 20000,
        "tasks": [
            {"status_code": 40501, "status_message": "bad", "result": None},
            {"status_code": 20000, "result": [{"keyword": "survivor", "search_volume": 9}]},
        ],
    }
    assert [i["keyword"] for i in dataforseo._task_items(body)] == ["survivor"]


# --- 1. batching -----------------------------------------------------------


async def test_search_volume_batches_past_the_old_40_keyword_cap():
    """2,500 keywords => 3 requests of <=1000, and every keyword comes back."""
    keywords = [f"keyword number {i}" for i in range(2500)]
    sent: list[list[str]] = []

    async def fake_post(path, payload):
        batch = payload[0]["keywords"]
        sent.append(batch)
        return _keywords_data_body(_volume_rows(batch))

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, errors = await dataforseo.search_volume(
            keywords, location_code=2036, language_code="en"
        )

    assert [len(b) for b in sent] == [1000, 1000, 500]
    assert all(len(b) <= 1000 for b in sent)
    assert len(rows) == 2500
    assert errors == []


async def test_keyword_difficulty_batches_past_the_old_20_keyword_cap():
    keywords = [f"keyword number {i}" for i in range(1500)]
    sent: list[list[str]] = []

    async def fake_post(path, payload):
        batch = payload[0]["keywords"]
        sent.append(batch)
        return _labs_body([{"keyword": k, "keyword_difficulty": 30} for k in batch])

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, errors = await dataforseo.keyword_difficulty(
            keywords, location_code=2036, language_code="en"
        )

    assert [len(b) for b in sent] == [1000, 500]
    assert len(rows) == 1500
    assert errors == []


async def test_search_volume_keeps_healthy_batches_when_one_batch_fails():
    """A mid-pool failure must not discard the batches that succeeded."""
    keywords = [f"keyword number {i}" for i in range(2000)]
    calls = {"n": 0}

    async def fake_post(path, payload):
        calls["n"] += 1
        if calls["n"] == 2:
            return None
        return _keywords_data_body(_volume_rows(payload[0]["keywords"]))

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, errors = await dataforseo.search_volume(
            keywords, location_code=2036, language_code="en"
        )

    assert len(rows) == 1000
    assert any("volume" in e for e in errors)


async def test_search_volume_dedupes_before_batching():
    keywords = ["drain cleaning"] * 50 + ["blocked drain"] * 50
    sent: list[list[str]] = []

    async def fake_post(path, payload):
        sent.append(payload[0]["keywords"])
        return _keywords_data_body(_volume_rows(payload[0]["keywords"]))

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, _ = await dataforseo.search_volume(
            keywords, location_code=2036, language_code="en"
        )

    assert len(sent) == 1
    assert sorted(sent[0]) == ["blocked drain", "drain cleaning"]
    assert len(rows) == 2


# --- 3. real cost capture --------------------------------------------------


async def test_post_records_the_cost_field_from_the_response():
    """The provider tells us what the call cost. Log that, not a flat guess."""
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return _keywords_data_body(_volume_rows(["a"]), cost=0.0741)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch(
        "app.services.api_meter.record_api_call", new=fake_record
    ), patch.object(dataforseo.httpx, "AsyncClient", lambda **kw: _Client()):
        await dataforseo._post("/keywords_data/google_ads/search_volume/live", [{}])

    assert len(recorded) == 1
    assert Decimal(str(recorded[0]["estimated_cost_usd"])) == Decimal("0.0741")


async def test_post_omits_cost_when_the_response_has_none():
    """No cost field => leave it to the meter's estimate, never invent one."""
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return _keywords_data_body(_volume_rows(["a"]))

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch(
        "app.services.api_meter.record_api_call", new=fake_record
    ), patch.object(dataforseo.httpx, "AsyncClient", lambda **kw: _Client()):
        await dataforseo._post("/keywords_data/google_ads/search_volume/live", [{}])

    assert recorded[0].get("estimated_cost_usd") is None


# --- 4. push filtering to the provider -------------------------------------
#
# docs.dataforseo.com: the Labs keyword endpoints accept `limit` (max 1000),
# `filters` (max 8) and `order_by` (max 3 rules). The client asked for 100
# unsorted, unfiltered rows and did the rest in Python — paying for junk rows
# and capping the pool at a tenth of what one request can return.


async def _capture(fn, **kwargs):
    sent: list[dict] = []

    async def fake_post(path, payload):
        sent.append(payload[0])
        return _labs_body([])

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        await fn(**kwargs)
    return sent[0]


@pytest.mark.parametrize(
    "fn_name",
    ["related_keywords", "keyword_suggestions", "keyword_ideas"],
)
async def test_labs_expansion_honours_the_documented_1000_limit(fn_name):
    sent = await _capture(
        getattr(dataforseo, fn_name),
        seed="drain cleaning",
        location_code=2036,
        language_code="en",
        limit=800,
    )
    assert sent["limit"] == 800


@pytest.mark.parametrize(
    "fn_name",
    ["related_keywords", "keyword_suggestions", "keyword_ideas"],
)
async def test_labs_expansion_clamps_above_the_documented_maximum(fn_name):
    sent = await _capture(
        getattr(dataforseo, fn_name),
        seed="drain cleaning",
        location_code=2036,
        language_code="en",
        limit=5000,
    )
    assert sent["limit"] == 1000


@pytest.mark.parametrize(
    "fn_name",
    ["related_keywords", "keyword_suggestions", "keyword_ideas"],
)
async def test_labs_expansion_pushes_min_volume_filter_to_the_provider(fn_name):
    sent = await _capture(
        getattr(dataforseo, fn_name),
        seed="drain cleaning",
        location_code=2036,
        language_code="en",
        min_volume=10,
    )
    volume_field = (
        "keyword_data.keyword_info.search_volume"
        if fn_name == "related_keywords"
        else "keyword_info.search_volume"
    )
    assert sent["filters"] == [volume_field, ">=", 10]
    assert sent["order_by"] == [f"{volume_field},desc"]


@pytest.mark.parametrize(
    "fn_name",
    ["related_keywords", "keyword_suggestions", "keyword_ideas"],
)
async def test_labs_expansion_omits_filters_when_no_min_volume(fn_name):
    sent = await _capture(
        getattr(dataforseo, fn_name),
        seed="drain cleaning",
        location_code=2036,
        language_code="en",
    )
    volume_field = (
        "keyword_data.keyword_info.search_volume"
        if fn_name == "related_keywords"
        else "keyword_info.search_volume"
    )
    assert "filters" not in sent
    assert sent["order_by"] == [f"{volume_field},desc"]
