"""Skills always call Anthropic Claude Sonnet directly — never OpenRouter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import clear_settings_cache


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")  # even if set to openrouter…
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("SKILL_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("COMPETITOR_MODEL", "google/gemini-2.5-pro")
    monkeypatch.setenv("WRITE_MODEL", "openai/gpt-5.6-sol")
    monkeypatch.setenv("KEYWORD_CLUSTER_MODEL", "google/gemini-2.5-flash")
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.asyncio
async def test_skill_chat_uses_anthropic_even_when_provider_is_openrouter():
    from app.integrations import llm as llm_mod

    anthropic = AsyncMock(return_value='{"ok": true}')
    openrouter = AsyncMock(return_value='{"ok": true}')
    with (
        patch.object(llm_mod, "_anthropic_chat", anthropic),
        patch.object(llm_mod, "_openrouter_chat", openrouter),
    ):
        await llm_mod._skill_chat(system="sys", user="user")

    anthropic.assert_awaited_once()
    openrouter.assert_not_awaited()
    assert anthropic.await_args.kwargs["model"] == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_skill_chat_strips_openrouter_anthropic_prefix():
    from app.integrations import llm as llm_mod

    anthropic = AsyncMock(return_value="ok")
    with patch.object(llm_mod, "_anthropic_chat", anthropic):
        await llm_mod._skill_chat(
            system="sys",
            user="user",
            model="anthropic/claude-sonnet-5",
        )

    assert anthropic.await_args.kwargs["model"] == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_skill_chat_ignores_non_claude_model_overrides():
    """Competitor/write Gemini or GPT IDs must not be sent to Anthropic."""
    from app.integrations import llm as llm_mod

    anthropic = AsyncMock(return_value="ok")
    with patch.object(llm_mod, "_anthropic_chat", anthropic):
        await llm_mod._skill_chat(
            system="sys",
            user="user",
            model="google/gemini-2.5-pro",
        )

    assert anthropic.await_args.kwargs["model"] == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_keyword_validation_judge_uses_anthropic_skill_model():
    from app.services import keyword_validation_gate as gate

    skill = AsyncMock(return_value='{"verdicts":[]}')
    with patch.object(gate, "_skill_chat", skill):
        await gate._skill_judge("sys", "user")

    skill.assert_awaited_once()
    assert skill.await_args.kwargs["model"] == "claude-sonnet-5"


def test_keyword_validation_provider_ready_checks_anthropic():
    from app.services.keyword_validation_gate import _provider_ready

    assert _provider_ready() is True


def test_anthropic_message_text_skips_leading_thinking_block():
    """Sonnet 5 returns thinking then text — content[0].text is empty."""
    from types import SimpleNamespace

    from app.integrations.llm import _anthropic_message_text

    resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="...", text=""),
            SimpleNamespace(
                type="text",
                text='{"verdicts":[{"id":1,"verdict":"keep","score":0.9,"reason":"ok"}]}',
            ),
        ]
    )
    assert _anthropic_message_text(resp).startswith('{"verdicts"')


def test_anthropic_message_text_legacy_single_text_block():
    from types import SimpleNamespace

    from app.integrations.llm import _anthropic_message_text

    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="hello")])
    assert _anthropic_message_text(resp) == "hello"
