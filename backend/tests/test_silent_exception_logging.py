"""AUDIT-011 / AUDIT-028: exception paths that used to fail completely
silently (`except Exception: pass` / bare `return None`, no logging) now log
a warning naming what failed. A real provider outage on any of these paths
used to be invisible in logs while the report quietly showed an empty
section — these pin that a failure is at least observable now."""

from __future__ import annotations

from app.services import content_strategy, create_content, technical_seo
from tests.test_create_content import _ready_brief


async def test_phase7_cwv_failure_is_logged(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        technical_seo.log, "warning", lambda event, **kw: events.append((event, kw))
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("PSI down")

    monkeypatch.setattr(technical_seo, "run_cwv_measurement", _boom)
    result = await technical_seo.run_technical_seo_plan(
        client_name="Acme", primary_url="https://acme.example"
    )
    assert result["core_web_vitals"] is None
    assert any(e == "technical_seo_cwv_failed" for e, _ in events)


async def test_phase7_broken_links_failure_is_logged(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        technical_seo.log, "warning", lambda event, **kw: events.append((event, kw))
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("crawler unreachable")

    monkeypatch.setattr(technical_seo, "check_broken_links", _boom)
    result = await technical_seo.run_technical_seo_plan(
        client_name="Acme", primary_url="https://acme.example"
    )
    assert result["seo_audit"] is not None or result is not None  # composite still returns
    assert any(e == "technical_seo_broken_links_failed" for e, _ in events)


async def test_content_strategy_llm_polish_failure_is_logged(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        content_strategy.log, "warning", lambda event, **kw: events.append((event, kw))
    )

    async def _boom(*a, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(content_strategy, "synthesize_json", _boom)
    monkeypatch.setattr(content_strategy, "synthesize_text", _boom)

    result = await content_strategy.run_content_strategy_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        industry="Retail",
        commercial={},
        marketing={},
        competitive={},
        website={},
        demand={},
        domain="acme.example",
    )
    assert isinstance(result, dict)
    logged = {e for e, _ in events}
    assert "content_strategy_summary_json_failed" in logged
    assert "content_strategy_summary_fallback_failed" in logged


async def test_create_content_llm_draft_failure_is_logged_and_falls_back(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        create_content.log, "warning", lambda event, **kw: events.append((event, kw))
    )

    async def _boom(*a, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(create_content, "synthesize_json", _boom)

    out = await create_content.write_one_page(
        brief=_ready_brief(), client_name="Acme", use_llm=True
    )
    assert out.get("ok") is True
    assert any(e == "create_content_draft_generation_failed" for e, _ in events)
