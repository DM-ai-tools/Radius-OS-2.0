"""search_intent and keywords_for_site — endpoints the client was missing.

Intent was previously inferred from regex heuristics over the keyword string.
DataForSEO returns a labelled intent with a probability, so that is what the
pipeline uses; nothing downstream may invent an intent label.

Paths, field names and limits verified against docs.dataforseo.com:
  POST /v3/dataforseo_labs/google/search_intent/live      (<=1000 keywords)
  POST /v3/dataforseo_labs/google/keywords_for_site/live  (limit <=1000)
"""

from __future__ import annotations

from unittest.mock import patch

from app.integrations import dataforseo


def _labs_body(items):
    return {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": items}]}]}


def _intent_item(keyword, label, prob, secondary=None):
    return {
        "keyword": keyword,
        "keyword_intent": {"label": label, "probability": prob},
        "secondary_keyword_intents": [
            {"label": s_label, "probability": s_prob} for s_label, s_prob in (secondary or [])
        ],
    }


# --- search_intent ---------------------------------------------------------


async def test_search_intent_returns_the_provider_label_and_probability():
    body = _labs_body(
        [
            _intent_item("emergency plumber melbourne", "transactional", 0.9812),
            _intent_item("how do drains block", "informational", 0.8734),
        ]
    )
    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", return_value=body
    ):
        rows, errors = await dataforseo.search_intent(
            ["emergency plumber melbourne", "how do drains block"],
            location_code=2036,
            language_code="en",
        )

    assert errors == []
    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["emergency plumber melbourne"]["intent"] == "transactional"
    assert by_kw["emergency plumber melbourne"]["intent_probability"] == 0.9812
    assert by_kw["how do drains block"]["intent"] == "informational"
    assert all(r["source"] == "dataforseo" for r in rows)


async def test_search_intent_carries_secondary_intents():
    body = _labs_body(
        [_intent_item("plumber cost", "commercial", 0.5211, [("informational", 0.3911)])]
    )
    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", return_value=body
    ):
        rows, _ = await dataforseo.search_intent(
            ["plumber cost"], location_code=2036, language_code="en"
        )
    assert rows[0]["secondary_intents"] == [
        {"label": "informational", "probability": 0.3911}
    ]


async def test_search_intent_batches_at_1000_keywords():
    keywords = [f"keyword number {i}" for i in range(2200)]
    sent: list[list[str]] = []

    async def fake_post(path, payload):
        assert path == "/dataforseo_labs/google/search_intent/live"
        batch = payload[0]["keywords"]
        sent.append(batch)
        return _labs_body([_intent_item(k, "commercial", 0.7) for k in batch])

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, errors = await dataforseo.search_intent(
            keywords, location_code=2036, language_code="en"
        )

    assert [len(b) for b in sent] == [1000, 1000, 200]
    assert len(rows) == 2200
    assert errors == []


async def test_search_intent_retries_without_locale_when_the_provider_rejects_it():
    """The documented field list omits location/language. Send them, but a
    locale rejection must not cost us the intent data."""
    calls: list[dict] = []

    async def fake_post(path, payload):
        calls.append(payload[0])
        if "location_code" in payload[0]:
            return {
                "status_code": 20000,
                "tasks": [
                    {
                        "status_code": 40501,
                        "status_message": "invalid field: location_code",
                        "result": None,
                    }
                ],
            }
        return _labs_body([_intent_item("plumber", "transactional", 0.9)])

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, _ = await dataforseo.search_intent(
            ["plumber"], location_code=2036, language_code="en"
        )

    assert len(calls) == 2
    assert "location_code" in calls[0]
    assert "location_code" not in calls[1]
    assert rows[0]["intent"] == "transactional"


async def test_search_intent_never_invents_a_label_when_the_provider_is_silent():
    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", return_value=_labs_body([{"keyword": "plumber"}])
    ):
        rows, _ = await dataforseo.search_intent(
            ["plumber"], location_code=2036, language_code="en"
        )
    assert rows[0]["intent"] is None
    assert rows[0]["intent_probability"] is None


async def test_search_intent_without_credentials_reports_unavailable():
    with patch.object(dataforseo, "_auth_header", return_value=None):
        rows, errors = await dataforseo.search_intent(
            ["plumber"], location_code=2036, language_code="en"
        )
    assert rows == []
    assert "dataforseo_unavailable" in errors


# --- keywords_for_site -----------------------------------------------------


async def test_keywords_for_site_requests_the_documented_path_and_fields():
    sent: list[tuple[str, dict]] = []

    async def fake_post(path, payload):
        sent.append((path, payload[0]))
        return _labs_body(
            [
                {
                    "keyword": "blocked drain melbourne",
                    "keyword_info": {"search_volume": 480, "cpc": 6.1, "competition": 0.42},
                }
            ]
        )

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, errors = await dataforseo.keywords_for_site(
            "https://www.example.com/services/",
            location_code=2036,
            language_code="en",
            limit=600,
        )

    path, sent_payload = sent[0]
    assert path == "/dataforseo_labs/google/keywords_for_site/live"
    assert sent_payload["target"] == "example.com"
    assert sent_payload["location_code"] == 2036
    assert sent_payload["language_code"] == "en"
    assert sent_payload["limit"] == 600
    assert errors == []
    assert rows[0]["keyword"] == "blocked drain melbourne"
    assert rows[0]["volume"] == 480


async def test_keywords_for_site_clamps_limit_to_1000():
    sent: list[dict] = []

    async def fake_post(path, payload):
        sent.append(payload[0])
        return _labs_body([])

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        await dataforseo.keywords_for_site(
            "example.com", location_code=2036, language_code="en", limit=9000
        )
    assert sent[0]["limit"] == 1000


async def test_keywords_for_site_pushes_min_volume_filter():
    sent: list[dict] = []

    async def fake_post(path, payload):
        sent.append(payload[0])
        return _labs_body([])

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        await dataforseo.keywords_for_site(
            "example.com", location_code=2036, language_code="en", min_volume=25
        )
    assert sent[0]["filters"] == ["keyword_info.search_volume", ">=", 25]


async def test_keywords_for_site_without_a_target_makes_no_call():
    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post"
    ) as post:
        rows, errors = await dataforseo.keywords_for_site(
            "  ", location_code=2036, language_code="en"
        )
    post.assert_not_called()
    assert rows == []


async def test_a_transient_failure_does_not_permanently_drop_the_locale():
    """A network blip returns no body at all. That is not the provider saying
    "location_code is invalid" — treating it as one would silently price every
    later batch against the wrong country."""
    calls: list[dict] = []

    async def fake_post(path, payload):
        calls.append(payload[0])
        if len(calls) == 1:
            return None  # transient: no body
        return _labs_body([_intent_item("plumber", "transactional", 0.9)])

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, _errors = await dataforseo.search_intent(
            ["plumber"], location_code=2036, language_code="en"
        )

    # It may retry, but every attempt must still carry the locale.
    assert all("location_code" in c for c in calls), calls
    assert rows and rows[0]["intent"] == "transactional"


async def test_locale_is_dropped_only_once_across_batches():
    """When the provider really does reject the locale field, later batches
    should not pay for the same rejection again."""
    keywords = [f"keyword number {i}" for i in range(1500)]
    calls: list[dict] = []

    async def fake_post(path, payload):
        calls.append(payload[0])
        if "location_code" in payload[0]:
            return {
                "status_code": 20000,
                "tasks": [
                    {
                        "status_code": 40501,
                        "status_message": "invalid field: location_code",
                        "result": None,
                    }
                ],
            }
        return _labs_body(
            [_intent_item(k, "commercial", 0.7) for k in payload[0]["keywords"]]
        )

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        rows, _ = await dataforseo.search_intent(
            keywords, location_code=2036, language_code="en"
        )

    # Batch 1 probes with locale then retries without; batch 2 skips the probe.
    with_locale = [c for c in calls if "location_code" in c]
    assert len(with_locale) == 1
    assert len(rows) == 1500
