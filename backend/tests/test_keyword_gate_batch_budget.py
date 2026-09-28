"""The relevance gate must be able to answer for a whole batch.

Measured on a real Click Trends Phase 5 run (2026-09-24): of 283 keywords,
15 were kept, 90 dropped and **178 quarantined** — 174 of those with cause
`error:JSONDecodeError`. The gate was not filtering, it was crashing, so
`ai girlfriend`, `ai clothing remover`, `google ai` and `free email services`
all reached the pool for a B2B email-marketing agency (flagged unverified by
the fail-closed path, but unjudged).

Cause: `keyword_validation_batch_size` is 200 while
`keyword_relevance_max_tokens` is 2048. One verdict row
(`keyword`/`verdict`/`score`/`reason`) costs roughly 30-40 tokens, so 200 rows
need ~7000 — the model's reply is cut off mid-array and the whole batch is lost.

Three separate guarantees here: the configured batch must fit the token budget,
the budget scales when a caller asks for a bigger batch, and a truncated reply
still yields the verdicts that did arrive instead of discarding all 200.
"""

from __future__ import annotations

from app.config import get_settings
from app.services.keyword_validation_gate import (
    TOKENS_PER_VERDICT,
    _parse_verdicts,
    all_drop_is_suspect,
    tokens_for_batch,
)


def test_the_default_batch_fits_the_default_token_budget():
    settings = get_settings()
    needed = settings.keyword_validation_batch_size * TOKENS_PER_VERDICT
    assert needed <= settings.keyword_relevance_max_tokens, (
        f"batch of {settings.keyword_validation_batch_size} needs ~{needed} tokens "
        f"but the budget is {settings.keyword_relevance_max_tokens}"
    )


def test_token_budget_scales_with_batch_size():
    small = tokens_for_batch(20)
    large = tokens_for_batch(200)
    assert large > small
    assert large >= 200 * TOKENS_PER_VERDICT


def test_token_budget_never_drops_below_the_configured_floor():
    settings = get_settings()
    assert tokens_for_batch(1) >= settings.keyword_relevance_max_tokens


def test_a_truncated_reply_still_yields_the_verdicts_that_arrived():
    """The model was cut off mid-array. Salvage the complete rows — losing all
    200 verdicts to one missing bracket is what produced 174 quarantines."""
    sent = {1: "email automation", 2: "ai girlfriend", 3: "drip campaign"}
    truncated = (
        '{"verdicts": ['
        '{"keyword": "email automation", "verdict": "keep", "score": 0.93, "reason": "core service"},'
        '{"keyword": "ai girlfriend", "verdict": "drop", "score": 0.01, "reason": "consumer toy"},'
        '{"keyword": "drip campai'
    )
    verdicts = _parse_verdicts(truncated, sent)
    assert "email automation" in verdicts
    assert "ai girlfriend" in verdicts
    assert verdicts["email automation"].verdict == "keep"
    assert verdicts["ai girlfriend"].verdict == "drop"
    # The cut-off row is simply absent — it gets quarantined, not guessed.
    assert "drip campaign" not in verdicts


def test_a_truncated_bare_array_is_also_salvaged():
    sent = {1: "email automation", 2: "spicy ai"}
    truncated = (
        '[{"keyword": "email automation", "verdict": "keep", "score": 0.9, "reason": "core"},'
        '{"keyword": "spicy ai", "verdict": "drop", "score": 0.0, "reason": "irrelevant"},'
        '{"keyword": "half'
    )
    verdicts = _parse_verdicts(truncated, sent)
    assert set(verdicts) == {"email automation", "spicy ai"}


def test_salvage_still_refuses_keywords_that_were_never_sent():
    """Truncation tolerance must not become a hole in the invented-keyword rule."""
    sent = {1: "email automation"}
    truncated = (
        '{"verdicts": ['
        '{"keyword": "email automation", "verdict": "keep", "score": 0.9, "reason": "core"},'
        '{"keyword": "crypto casino", "verdict": "keep", "score": 0.95, "reason": "invented"},'
    )
    verdicts = _parse_verdicts(truncated, sent)
    assert set(verdicts) == {"email automation"}


def test_well_formed_payloads_are_unaffected():
    sent = {1: "email automation"}
    payload = {
        "verdicts": [
            {"keyword": "email automation", "verdict": "keep", "score": 0.9, "reason": "core"}
        ]
    }
    verdicts = _parse_verdicts(payload, sent)
    assert verdicts["email automation"].verdict == "keep"


def test_unsalvageable_junk_yields_nothing_rather_than_raising():
    sent = {1: "email automation"}
    assert _parse_verdicts("not json at all", sent) == {}
    assert _parse_verdicts("", sent) == {}
    assert _parse_verdicts(None, sent) == {}


# --- the salvage path must actually be reachable ---------------------------


async def test_a_raising_json_parser_does_not_bypass_salvage():
    """`_parse_json_content` raises JSONDecodeError on a truncated reply rather
    than returning None, so the salvage branch was never reached and the batch
    was quarantined as `error:JSONDecodeError` — 25 keywords on a live run."""
    from unittest.mock import patch

    from app.services import keyword_validation_gate as gate

    truncated = (
        '{"keywords": ['
        '{"keyword": "email automation", "relevant": true, "reason": "core service"},'
        '{"keyword": "ai girlfriend", "relevant": false, "reason": "consumer toy"},'
        '{"keyword": "half a ro'
    )

    async def fake_chat(**kwargs):
        return truncated

    with patch.object(gate, "_skill_chat", fake_chat):
        payload = await gate._skill_judge("sys", "user", batch_size=3)

    assert payload is not None, "a truncated reply must still come back for salvage"
    verdicts = gate._parse_verdicts(payload, {1: "email automation", 2: "ai girlfriend"})
    assert set(verdicts) == {"email automation", "ai girlfriend"}


# --- an all-drop batch can be the right answer ----------------------------


def test_an_all_drop_batch_with_real_reasons_is_trusted():
    """On a junk-heavy pool a whole batch genuinely is irrelevant. The guard
    exists to catch a confused model, and quarantining 63 correctly-dropped
    keywords just leaves them in the pool as "unverified" instead."""
    rows = [
        {"keyword": "ai girlfriend", "verdict": "drop", "reason": "consumer toy, not marketing"},
        {"keyword": "spicy ai", "verdict": "drop", "reason": "adult content"},
        {"keyword": "undetected ai", "verdict": "drop", "reason": "student plagiarism tool"},
        {"keyword": "ai music generator", "verdict": "drop", "reason": "media generation"},
        {"keyword": "free email services", "verdict": "drop", "reason": "consumer mailbox"},
    ]
    assert all_drop_is_suspect(rows) is False


def test_an_all_drop_batch_with_no_reasons_is_still_suspect():
    rows = [{"keyword": f"kw {i}", "verdict": "drop", "reason": ""} for i in range(6)]
    assert all_drop_is_suspect(rows) is True


def test_an_all_drop_batch_with_one_repeated_reason_is_suspect():
    """A model echoing the same string for every row is not judging."""
    rows = [
        {"keyword": f"kw {i}", "verdict": "drop", "reason": "not relevant"} for i in range(6)
    ]
    assert all_drop_is_suspect(rows) is True


def test_a_tiny_all_drop_batch_is_just_a_verdict_not_a_glitch():
    """The guard is about a WHOLE batch coming back empty-handed. One or two
    drops carries no signal about model confusion, and treating them as suspect
    would quarantine ordinary single-keyword rejections."""
    rows = [{"keyword": "kw", "verdict": "drop", "reason": "a real specific reason"}]
    assert all_drop_is_suspect(rows) is False
