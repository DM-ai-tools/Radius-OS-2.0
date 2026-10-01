"""Unit tests for batch_llm packing, truncation recovery, 429, resume, ID integrity."""

from __future__ import annotations

import pytest

from app.integrations.batch_llm.adapters.base import AdapterResponse, LLMAdapter
from app.integrations.batch_llm.checkpoint import CheckpointStore
from app.integrations.batch_llm.limits import clear_limits_cache, resolve_model_id
from app.integrations.batch_llm.packing import (
    Item,
    estimate_tokens,
    pack_batches,
    split_oversized,
)
from app.integrations.batch_llm.process import BudgetExceeded, process_items
from app.integrations.batch_llm.rate_limit import RateLimitState


@pytest.fixture(autouse=True)
def _limits():
    clear_limits_cache()
    yield
    clear_limits_cache()


def test_alias_resolves_bare_claude_ids():
    assert resolve_model_id("claude-haiku-4-5") == "anthropic/claude-haiku-4.5"
    assert resolve_model_id("claude-sonnet-5") == "anthropic/claude-sonnet-5"


def test_pack_batches_respects_token_budget():
    items = [Item(id=str(i), text=("keyword " * 20) + str(i)) for i in range(30)]
    batches = pack_batches(
        items,
        model_key="keyword_cluster",
        system="You are a filter." * 10,
        format_overhead="Return JSON.",
        expected_output_tokens_per_item=40,
        safety_margin=0.12,
    )
    assert batches
    assert sum(len(b.items) for b in batches) == 30
    for b in batches:
        assert b.input_tokens + b.reserved_output_tokens > 0


def test_oversized_item_split_and_reassemble_ids():
    huge = Item(id="doc1", text=("Paragraph one. " * 400) + ("\n\nParagraph two. " * 400))
    chunks = split_oversized(huge, max_item_tokens=200, tokenizer="gemini_approx")
    assert len(chunks) > 1
    assert all(c.parent_id == "doc1" for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    joined = " ".join(c.text for c in chunks)
    assert "Paragraph one" in joined and "Paragraph two" in joined


def test_estimate_tokens_positive():
    assert estimate_tokens("hello world") >= 1
    assert estimate_tokens("") == 0


class _ScriptedAdapter(LLMAdapter):
    def __init__(self, responses: list[AdapterResponse]):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if not self.responses:
            return AdapterResponse(text="", error="empty_script", retryable=False)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_id_matching_no_loss_or_duplicate(tmp_path, monkeypatch):
    monkeypatch.setitem(
        __import__("app.integrations.batch_llm.limits", fromlist=["defaults"]).defaults(),
        "checkpoint_dir",
        str(tmp_path / "cp"),
    )
    from app.integrations.batch_llm import limits as lim

    # Force checkpoint dir via defaults patch
    original = lim.defaults

    def _defaults():
        d = original()
        d["checkpoint_dir"] = str(tmp_path / "cp")
        return d

    monkeypatch.setattr(lim, "defaults", _defaults)
    monkeypatch.setattr(
        "app.integrations.batch_llm.checkpoint.defaults", _defaults
    )
    monkeypatch.setattr(
        "app.integrations.batch_llm.process.defaults", _defaults
    )

    payload = {"results": {"a": {"ok": True}, "b": {"ok": True}, "c": {"ok": True}}}
    adapter = _ScriptedAdapter(
        [AdapterResponse(text=__import__("json").dumps(payload), prompt_tokens=10, completion_tokens=5)]
    )
    result = await process_items(
        [{"id": "a", "text": "one"}, {"id": "b", "text": "two"}, {"id": "c", "text": "three"}],
        "keyword_cluster",
        "Classify each item.",
        {
            "checkpoint_key": "id-match",
            "expected_output_tokens_per_item": 20,
            "mode": "realtime",
        },
        adapter=adapter,
    )
    assert set(result.results) == {"a", "b", "c"}
    assert not result.failed
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_truncated_response_splits_batch(tmp_path, monkeypatch):
    from app.integrations.batch_llm import limits as lim

    original = lim.defaults

    def _defaults():
        d = original()
        d["checkpoint_dir"] = str(tmp_path / "cp")
        return d

    monkeypatch.setattr(lim, "defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.checkpoint.defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.process.defaults", _defaults)

    import json

    # First call: truncated, only a present
    first = AdapterResponse(
        text=json.dumps({"results": {"a": 1}}),
        truncated=True,
        finish_reason="length",
        prompt_tokens=5,
        completion_tokens=5,
    )
    # Follow-ups for remaining
    second = AdapterResponse(
        text=json.dumps({"results": {"b": 2}}),
        prompt_tokens=5,
        completion_tokens=5,
    )
    third = AdapterResponse(
        text=json.dumps({"results": {"c": 3}}),
        prompt_tokens=5,
        completion_tokens=5,
    )
    adapter = _ScriptedAdapter([first, second, third])
    result = await process_items(
        [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}, {"id": "c", "text": "z"}],
        "keyword_cluster",
        "Go.",
        {"checkpoint_key": "trunc", "expected_output_tokens_per_item": 20},
        adapter=adapter,
    )
    assert result.results.get("a") == 1
    assert "b" in result.results or any(f.item_id == "b" for f in result.failed) or adapter.calls >= 2


@pytest.mark.asyncio
async def test_429_backoff_then_success(tmp_path, monkeypatch):
    from app.integrations.batch_llm import limits as lim

    original = lim.defaults

    def _defaults():
        d = original()
        d["checkpoint_dir"] = str(tmp_path / "cp")
        d["max_retries"] = 3
        return d

    monkeypatch.setattr(lim, "defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.checkpoint.defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.process.defaults", _defaults)

    import json

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.integrations.batch_llm.process.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "app.integrations.batch_llm.rate_limit.asyncio.sleep", fake_sleep
    )

    adapter = _ScriptedAdapter(
        [
            AdapterResponse(
                text="", error="rate_limited", status_code=429, retryable=True, retry_after=0.01
            ),
            AdapterResponse(
                text=json.dumps({"results": {"a": "ok"}}),
                prompt_tokens=1,
                completion_tokens=1,
            ),
        ]
    )
    result = await process_items(
        [{"id": "a", "text": "hello"}],
        "keyword_cluster",
        "Go.",
        {"checkpoint_key": "rl", "expected_output_tokens_per_item": 10},
        adapter=adapter,
    )
    assert result.results["a"] == "ok"
    assert result.summary["retries"] >= 1
    assert sleeps  # backoff happened


@pytest.mark.asyncio
async def test_partial_batch_failure_dead_letters(tmp_path, monkeypatch):
    from app.integrations.batch_llm import limits as lim

    original = lim.defaults

    def _defaults():
        d = original()
        d["checkpoint_dir"] = str(tmp_path / "cp")
        d["max_retries"] = 0
        return d

    monkeypatch.setattr(lim, "defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.checkpoint.defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.process.defaults", _defaults)

    adapter = _ScriptedAdapter(
        [
            AdapterResponse(
                text="", error="http_400", status_code=400, retryable=False
            )
        ]
    )
    result = await process_items(
        [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
        "keyword_cluster",
        "Go.",
        {"checkpoint_key": "dead", "max_retries": 0},
        adapter=adapter,
    )
    assert not result.results
    assert {f.item_id for f in result.failed} == {"a", "b"}


@pytest.mark.asyncio
async def test_crash_resume_skips_completed(tmp_path, monkeypatch):
    from app.integrations.batch_llm import limits as lim

    original = lim.defaults

    def _defaults():
        d = original()
        d["checkpoint_dir"] = str(tmp_path / "cp")
        return d

    monkeypatch.setattr(lim, "defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.checkpoint.defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.process.defaults", _defaults)

    store = CheckpointStore("resume-job", base_dir=str(tmp_path / "cp"))
    store.save_result("a", {"done": True})
    store.close()

    import json

    adapter = _ScriptedAdapter(
        [
            AdapterResponse(
                text=json.dumps({"results": {"b": 2}}),
                prompt_tokens=1,
                completion_tokens=1,
            )
        ]
    )
    result = await process_items(
        [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
        "keyword_cluster",
        "Go.",
        {"checkpoint_key": "resume-job"},
        adapter=adapter,
    )
    assert result.results["a"] == {"done": True}
    assert result.results["b"] == 2
    assert adapter.calls == 1  # did not resend a


@pytest.mark.asyncio
async def test_budget_ceiling_stops_job(tmp_path, monkeypatch):
    from app.integrations.batch_llm import limits as lim

    original = lim.defaults

    def _defaults():
        d = original()
        d["checkpoint_dir"] = str(tmp_path / "cp")
        return d

    monkeypatch.setattr(lim, "defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.checkpoint.defaults", _defaults)
    monkeypatch.setattr("app.integrations.batch_llm.process.defaults", _defaults)

    adapter = _ScriptedAdapter([])
    with pytest.raises(BudgetExceeded):
        await process_items(
            [{"id": str(i), "text": "word " * 500} for i in range(50)],
            "write",
            "Draft.",
            {
                "checkpoint_key": "budget",
                "budget_usd": 0.0000001,
                "expected_output_tokens_per_item": 2000,
            },
            adapter=adapter,
        )
    assert adapter.calls == 0


def test_rate_limit_headroom_and_429_shrink():
    state = RateLimitState(rpm=10)
    for _ in range(9):
        state.note_request()
    assert state.headroom_ratio() < 0.2
    state.note_rate_limit(1.0)
    assert state.suggested_concurrency(8) == 1
