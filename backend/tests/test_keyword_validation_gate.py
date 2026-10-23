"""Per-keyword LLM relevance gate — judges each keyword, fails closed.

The stage this replaces skipped the LLM entirely for most seeds and kept
everything on provider error. Every test here asserts the opposite: a keyword is
either explicitly judged, or explicitly quarantined. Nothing is silently kept.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import keyword_validation_gate as gate
from app.services.keyword_validation_gate import validate_keywords

@pytest.fixture(autouse=True)
def _no_shared_cache(monkeypatch):
    """Isolate from any live Redis.

    Without this, a verdict cached by one test is served to the next and the
    gate makes no call at all — the tests would pass for the wrong reason. The
    caching test opts back in by patching these itself.
    """

    async def _get_many(keys):
        return {}

    async def _set_many(values, ttl):
        return None

    monkeypatch.setattr(gate, "cache_get_many", _get_many)
    monkeypatch.setattr(gate, "cache_set_many", _set_many)


CONTEXT = dict(
    client_name="Northside Dental",
    primary_url="https://northsidedental.com.au",
    industry="Dental",
    services=["Teeth Whitening", "Dental Implants"],
    cdd_keywords=["dentist melbourne"],
    geography="Melbourne",
    competitor_names=["SmileCo Dental"],
    competitor_domains=["smileco.com.au"],
    client_id="client-1",
)


def rows(*keywords: str, target: str = "Teeth Whitening") -> list[dict[str, Any]]:
    return [
        {"keyword": kw, "seed": target.lower(), "target": target, "target_type": "service",
         "match_class": "broad", "volume": 100}
        for kw in keywords
    ]


def _caller(verdicts: dict[str, dict[str, Any]], *, calls: list | None = None):
    """Fake LLM returning canned verdicts keyed by keyword."""

    async def _call(system: str, user: str) -> dict[str, Any]:
        if calls is not None:
            calls.append((system, user))
        sent = [k for k in verdicts if k in user]
        return {"verdicts": [{"keyword": k, **verdicts[k]} for k in sent]}

    return _call


def _quarantined(row: dict[str, Any]) -> bool:
    return (
        row.get("relevance_unverified") is True
        and row.get("commercial_target_eligible") is False
    )


# --- verdict handling ------------------------------------------------------


async def test_keep_above_threshold_survives_and_drop_is_dropped():
    caller = _caller(
        {
            "teeth whitening cost": {"verdict": "keep", "score": 0.9, "reason": "buyer"},
            "free dental jobs": {"verdict": "drop", "score": 0.02, "reason": "job seeker"},
        }
    )
    kept, dropped, audit = await validate_keywords(
        rows("teeth whitening cost", "free dental jobs"), caller=caller, **CONTEXT
    )
    assert [r["keyword"] for r in kept] == ["teeth whitening cost"]
    assert [r["keyword"] for r in dropped] == ["free dental jobs"]
    assert not _quarantined(kept[0])
    assert audit["judged"] == 2


async def test_keep_below_threshold_is_dropped():
    caller = _caller({"maybe relevant": {"verdict": "keep", "score": 0.3, "reason": "weak"}})
    kept, dropped, _audit = await validate_keywords(
        rows("maybe relevant"), caller=caller, **CONTEXT
    )
    assert kept == []
    assert dropped[0]["keyword"] == "maybe relevant"


async def test_review_verdict_is_kept_but_quarantined():
    caller = _caller({"whitening strips": {"verdict": "review", "score": 0.5, "reason": "unclear"}})
    kept, dropped, _audit = await validate_keywords(
        rows("whitening strips"), caller=caller, **CONTEXT
    )
    assert dropped == []
    assert _quarantined(kept[0])


async def test_dropped_rows_carry_the_model_reason_for_logging():
    caller = _caller({"free dental jobs": {"verdict": "drop", "score": 0.0, "reason": "job seeker, not a patient"}})
    _kept, dropped, _audit = await validate_keywords(
        rows("free dental jobs"), caller=caller, **CONTEXT
    )
    assert "job seeker" in dropped[0]["relevance_drop_reason"]


# --- failure matrix: every path quarantines, none silently keeps -----------


async def test_no_provider_quarantines_everything(monkeypatch):
    monkeypatch.setattr(gate, "_provider_ready", lambda: False)
    kept, dropped, audit = await validate_keywords(rows("teeth whitening cost"), **CONTEXT)
    assert dropped == []
    assert _quarantined(kept[0])
    assert audit["quarantined"] == 1
    assert audit["api_calls"] == 0


async def test_caller_raising_quarantines_that_batch():
    async def _boom(system: str, user: str):
        raise RuntimeError("provider exploded")

    kept, dropped, audit = await validate_keywords(
        rows("a keyword", "b keyword"), caller=_boom, **CONTEXT
    )
    assert dropped == []
    assert all(_quarantined(r) for r in kept)
    assert audit["quarantined"] == 2


async def test_caller_timeout_quarantines_that_batch():
    import asyncio

    async def _hang(system: str, user: str):
        await asyncio.sleep(5)
        return {}

    kept, dropped, _audit = await validate_keywords(
        rows("a keyword"), caller=_hang, call_timeout=0.1, **CONTEXT
    )
    assert dropped == []
    assert _quarantined(kept[0])


async def test_malformed_response_quarantines_that_batch():
    async def _junk(system: str, user: str):
        return {"nonsense": True}

    kept, dropped, _audit = await validate_keywords(
        rows("a keyword"), caller=_junk, **CONTEXT
    )
    assert dropped == []
    assert _quarantined(kept[0])


async def test_none_response_quarantines_that_batch():
    async def _none(system: str, user: str):
        return None

    kept, _dropped, _audit = await validate_keywords(rows("a keyword"), caller=_none, **CONTEXT)
    assert _quarantined(kept[0])


async def test_omitted_keyword_is_quarantined_not_kept():
    caller = _caller({"judged keyword": {"verdict": "keep", "score": 0.9, "reason": "ok"}})
    kept, _dropped, audit = await validate_keywords(
        rows("judged keyword", "forgotten keyword"), caller=caller, **CONTEXT
    )
    by_kw = {r["keyword"]: r for r in kept}
    assert not _quarantined(by_kw["judged keyword"])
    assert _quarantined(by_kw["forgotten keyword"])
    assert audit["quarantined"] == 1


async def test_invented_keyword_verdict_is_discarded():
    """The model must not be able to add keywords that were never sent."""

    async def _inventive(system: str, user: str):
        return {
            "verdicts": [
                {"keyword": "real keyword", "verdict": "keep", "score": 0.9, "reason": "ok"},
                {"keyword": "hallucinated keyword", "verdict": "keep", "score": 0.99, "reason": "nope"},
            ]
        }

    kept, _dropped, _audit = await validate_keywords(
        rows("real keyword"), caller=_inventive, **CONTEXT
    )
    assert [r["keyword"] for r in kept] == ["real keyword"]


async def test_all_drop_response_is_treated_as_suspect_and_quarantined():
    """A model that rejects an entire batch has usually failed, not judged."""

    async def _all_drop(system: str, user: str):
        return {
            "verdicts": [
                {"keyword": kw, "verdict": "drop", "score": 0.0, "reason": "no"}
                for kw in ("alpha keyword", "beta keyword", "gamma keyword", "delta keyword")
            ]
        }

    kept, dropped, _audit = await validate_keywords(
        rows("alpha keyword", "beta keyword", "gamma keyword", "delta keyword"),
        caller=_all_drop,
        **CONTEXT,
    )
    assert dropped == []
    assert all(_quarantined(r) for r in kept)


# --- batching, caps, caching ----------------------------------------------


async def test_keywords_are_batched_not_sent_one_per_call():
    calls: list = []
    caller = _caller(
        {f"keyword number {i}": {"verdict": "keep", "score": 0.9, "reason": "ok"} for i in range(45)},
        calls=calls,
    )
    await validate_keywords(
        rows(*[f"keyword number {i}" for i in range(45)]),
        caller=caller,
        batch_size=20,
        **CONTEXT,
    )
    assert len(calls) == 3, "45 keywords at batch size 20 must be 3 calls, not 45"


async def test_deadline_stops_new_judgements_so_the_report_can_save():
    async def judge(system, user, batch_size=0):
        raise AssertionError("judge should not run after the deadline")

    kept, dropped, audit = await validate_keywords(
        [{"keyword": "email marketing strategy", "service": "Email Marketing", "volume": 20}],
        client_id="client-deadline",
        services=["Email Marketing"],
        caller=judge,
        enabled=True,
        deadline=0,
    )
    assert dropped == []
    assert kept[0]["relevance_unjudged_cause"] == "time_budget"
    assert audit["api_calls"] == 0
    assert audit["quarantined_by_cause"]["time_budget"] == 1


async def test_max_calls_cap_quarantines_the_remainder():
    calls: list = []
    caller = _caller(
        {f"keyword number {i}": {"verdict": "keep", "score": 0.9, "reason": "ok"} for i in range(45)},
        calls=calls,
    )
    kept, _dropped, audit = await validate_keywords(
        rows(*[f"keyword number {i}" for i in range(45)]),
        caller=caller,
        batch_size=20,
        max_calls=1,
        **CONTEXT,
    )
    assert len(calls) == 1
    assert audit["quarantined"] == 25
    assert sum(1 for r in kept if _quarantined(r)) == 25


async def test_each_service_is_judged_against_its_own_service_context():
    calls: list = []
    caller = _caller(
        {
            "teeth whitening cost": {"verdict": "keep", "score": 0.9, "reason": "ok"},
            "dental implants price": {"verdict": "keep", "score": 0.9, "reason": "ok"},
        },
        calls=calls,
    )
    batch = rows("teeth whitening cost", target="Teeth Whitening") + rows(
        "dental implants price", target="Dental Implants"
    )
    await validate_keywords(batch, caller=caller, **CONTEXT)
    assert len(calls) == 2, "batches must not mix services"
    services_in_prompts = {
        "Teeth Whitening" in user or "Dental Implants" in user for _system, user in calls
    }
    assert services_in_prompts == {True}


async def test_prompt_carries_service_cdd_and_competitor_context():
    calls: list = []
    caller = _caller({"teeth whitening cost": {"verdict": "keep", "score": 0.9, "reason": "ok"}}, calls=calls)
    await validate_keywords(rows("teeth whitening cost"), caller=caller, **CONTEXT)
    _system, user = calls[0]
    assert "Teeth Whitening" in user
    assert "dentist melbourne" in user
    assert "SmileCo Dental" in user
    assert "Melbourne" in user


async def test_cached_verdicts_skip_the_llm(monkeypatch):
    calls: list = []
    caller = _caller({"teeth whitening cost": {"verdict": "keep", "score": 0.9, "reason": "ok"}}, calls=calls)

    cached: dict[str, Any] = {}

    async def _get_many(keys):
        return {k: cached[k] for k in keys if k in cached}

    async def _set_many(values, ttl):
        cached.update(values)

    monkeypatch.setattr(gate, "cache_get_many", _get_many)
    monkeypatch.setattr(gate, "cache_set_many", _set_many)

    await validate_keywords(rows("teeth whitening cost"), caller=caller, **CONTEXT)
    assert len(calls) == 1
    assert cached, "verdict should have been cached"

    kept, _dropped, audit = await validate_keywords(
        rows("teeth whitening cost"), caller=caller, **CONTEXT
    )
    assert len(calls) == 1, "second run must be served from cache"
    assert audit["cached"] == 1
    assert not _quarantined(kept[0])


async def test_empty_input_makes_no_calls():
    calls: list = []
    caller = _caller({}, calls=calls)
    kept, dropped, audit = await validate_keywords([], caller=caller, **CONTEXT)
    assert (kept, dropped) == ([], [])
    assert calls == []
    assert audit["api_calls"] == 0


@pytest.mark.parametrize("disabled_value", [False])
async def test_gate_can_be_disabled_by_settings(monkeypatch, disabled_value):
    """Disabled means pass through untouched — not quarantine everything."""
    calls: list = []
    caller = _caller({}, calls=calls)
    kept, dropped, audit = await validate_keywords(
        rows("teeth whitening cost"), caller=caller, enabled=disabled_value, **CONTEXT
    )
    assert [r["keyword"] for r in kept] == ["teeth whitening cost"]
    assert dropped == []
    assert calls == []
    assert audit["skipped"] == "disabled"


async def test_haiku_screen_keep_and_drop_are_final(monkeypatch):
    from app.config import clear_settings_cache

    monkeypatch.setenv("SKILL_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("KEYWORD_SCREEN_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("KEYWORD_SCREEN_ENABLED", "true")
    clear_settings_cache()
    monkeypatch.setattr(gate, "keyword_analysis_ready", lambda: True)
    calls: list[tuple[str, str]] = []

    async def chat(*, system, user, model, max_tokens, allow_fallback=True):
        calls.append((model, user))
        if model == "claude-haiku-4-5":
            assert allow_fallback is False
            return (
                '{"r":['
                '["teeth whitening cost","k","c","whitening"],'
                '["free dental jobs","d","n","dental jobs","job advert"]'
                "]}"
            )
        raise AssertionError("Sonnet must not see a batch Haiku already decided")

    monkeypatch.setattr(gate, "keyword_analysis_chat", chat)
    kept, dropped, audit = await validate_keywords(
        rows("teeth whitening cost", "free dental jobs"), **CONTEXT
    )
    assert [r["keyword"] for r in kept] == ["teeth whitening cost"]
    assert dropped[0]["keyword"] == "free dental jobs"
    assert dropped[0]["relevance_drop_reason"] == "screen: job advert"
    assert audit["screen_dropped"] == 1
    assert audit["screen_kept"] == 1
    assert audit["api_calls"] == 1
    assert [model for model, _user in calls] == ["claude-haiku-4-5"]


async def test_an_unsure_screen_row_is_the_only_one_sonnet_sees(monkeypatch):
    from app.config import clear_settings_cache

    monkeypatch.setenv("SKILL_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("KEYWORD_SCREEN_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("KEYWORD_SCREEN_ENABLED", "true")
    clear_settings_cache()
    monkeypatch.setattr(gate, "keyword_analysis_ready", lambda: True)
    sonnet_users: list[str] = []

    async def chat(*, system, user, model, max_tokens, allow_fallback=True):
        if model == "claude-haiku-4-5":
            return (
                '{"r":['
                '["teeth whitening cost","k","c","whitening"],'
                '["seo consultant","u","c","consultant"]'
                "]}"
            )
        sonnet_users.append(user)
        return '{"r":[["seo consultant","k","c","consultant"]]}'

    monkeypatch.setattr(gate, "keyword_analysis_chat", chat)
    kept, dropped, audit = await validate_keywords(
        rows("teeth whitening cost", "seo consultant"), **CONTEXT
    )
    assert dropped == []
    assert {r["keyword"] for r in kept} == {"teeth whitening cost", "seo consultant"}
    assert audit["screen_kept"] == 1
    assert len(sonnet_users) == 1
    assert "seo consultant" in sonnet_users[0]
    assert "teeth whitening cost" not in sonnet_users[0]


async def test_a_failed_screen_sends_the_whole_batch_to_sonnet(monkeypatch):
    from app.config import clear_settings_cache

    monkeypatch.setenv("SKILL_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("KEYWORD_SCREEN_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("KEYWORD_SCREEN_ENABLED", "true")
    clear_settings_cache()
    monkeypatch.setattr(gate, "keyword_analysis_ready", lambda: True)
    sonnet_users: list[str] = []

    async def chat(*, system, user, model, max_tokens, allow_fallback=True):
        if model == "claude-haiku-4-5":
            return None
        sonnet_users.append(user)
        return (
            '{"r":['
            '["teeth whitening cost","k","c","whitening"],'
            '["seo consultant","k","c","consultant"]'
            "]}"
        )

    monkeypatch.setattr(gate, "keyword_analysis_chat", chat)
    kept, dropped, audit = await validate_keywords(
        rows("teeth whitening cost", "seo consultant"), **CONTEXT
    )
    assert dropped == []
    assert {r["keyword"] for r in kept} == {"teeth whitening cost", "seo consultant"}
    assert audit["screen_dropped"] == 0
    assert len(sonnet_users) == 1
    assert "teeth whitening cost" in sonnet_users[0]
    assert "seo consultant" in sonnet_users[0]
