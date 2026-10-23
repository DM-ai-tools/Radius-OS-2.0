"""Comparison drafts use rival-page facts and refuse when none were retrieved."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.content_benchmark.fetch import PageContent
from app.services.content_production import run_content_production_plan
from app.services.content_signals.skills import collect_skill
from app.services.create_content import write_one_page
from tests.test_content_brief import FAKE_DRAFT_REPLY


def _brief(page_type: str, keyword: str, url: str) -> dict:
    return {
        "keyword": keyword,
        "url": url,
        "writer_ready": True,
        "action": "create",
        "title": keyword.title(),
        "page_type": page_type,
        "differentiation": "A client-owned method with named steps and a fixed owner.",
        "preflight": {
            "url": url,
            "parent": "/",
            "page_type": page_type,
            "author": "Pat Writer",
            "author_standing": "Senior strategist",
            "blockers": [],
        },
        "required_coverage": {
            "outcomes": ["Pick an option"],
            "must_address": ["Scope"],
            "must_name": ["Acme"],
            "out_of_scope": [],
        },
        "outline": [{"title": "Quick verdict"}],
        "faq": [],
    }


def _planning(brief: dict) -> dict:
    return {
        "locked": True,
        "pages": [
            {
                "url": brief["url"],
                "keyword": brief["keyword"],
                "title": brief["title"],
                "page_type": brief["page_type"],
                "action": "create",
            }
        ],
    }


def _live_page() -> PageContent:
    body = ("Scope is listed as a retained team. " * 40) + "UNIQUE_TAIL_NOT_IN_PROMPT"
    return PageContent(
        competitor_id="c1",
        name="Northwind",
        domain="northwind.example",
        url="https://northwind.example/seo",
        source="live",
        title="Northwind SEO retainers",
        h1s=["SEO retainers for operators"],
        h2s=["What the retainer includes"],
        body_text=body,
        depth_available=True,
    )


@pytest.mark.asyncio
async def test_comparison_prompt_includes_rival_facts_and_skill():
    captured: dict[str, str] = {}
    page = _live_page()

    async def _capture(system, user, **_kwargs):
        captured["system"] = system
        captured["user"] = user
        return {
            "opening": "Acme is the better fit when the work needs a named owner.",
            "page_markdown": "Acme discloses that it is one of the options on this page. " * 12,
            "sections": [
                {
                    "title": "Quick verdict",
                    "body": "Choose Acme when you want a named owner. Northwind lists a retained team.",
                }
            ],
            "faq": [],
            "cta": "Book a consultation to compare the two scopes.",
        }

    skill = collect_skill(page_type="comparison")
    context = {
        "keyword": "seo comparison",
        "page_type": "comparison",
        "signal_order": ["skill"],
        "signals": {"skill": skill.to_dict()},
        "missing": [],
        "conflicts": [],
    }
    with patch("app.services.create_content.synthesize_json", new=AsyncMock(side_effect=_capture)):
        draft = await write_one_page(
            brief=_brief("comparison", "seo comparison", "/compare/seo"),
            client_name="Acme",
            use_llm=True,
            generate_images=False,
            comparison_pages=[page],
            content_context=context,
        )

    assert draft["ok"] is True
    assert "Northwind SEO retainers" in captured["user"]
    assert "What the retainer includes" in captured["user"]
    assert "Scope is listed as a retained team." in captured["user"]
    assert "UNIQUE_TAIL_NOT_IN_PROMPT" not in captured["user"]
    assert "COMPARISON EVIDENCE" in captured["user"]
    assert "Never invent pricing" in captured["system"]
    assert "No product/service overlap found" in captured["system"]
    assert "do not put rival brand names in the H1" in captured["user"]
    record = draft["comparison_evidence"]["rivals"][0]
    assert record["name"] == "Northwind"
    assert record["url"] == "https://northwind.example/seo"
    assert record["source"] == "live"
    assert "body" not in record["missing"]
    assert "UNIQUE_TAIL_NOT_IN_PROMPT" not in str(draft["comparison_evidence"])


@pytest.mark.asyncio
async def test_comparison_without_usable_rivals_does_not_call_the_writer():
    writer = AsyncMock()
    failed = PageContent(
        competitor_id="c1",
        name="Northwind",
        domain="northwind.example",
        url="https://northwind.example",
        source="live",
        error="empty_response",
    )
    brief = _brief("comparison", "seo comparison", "/compare/seo")
    planning = _planning(brief)
    with (
        patch(
            "app.services.content_benchmark.fetch.gather_competitor_content",
            new=AsyncMock(return_value=[failed]),
        ) as gather,
        patch("app.services.create_content.synthesize_json", new=writer),
    ):
        out = await run_content_production_plan(
            client_name="Acme",
            primary_url="https://acme.example",
            content_planning_status="complete",
            content_planning=planning,
            selected_keyword="seo comparison",
            prior_briefs=[brief],
            competitors=[{"id": "c1", "name": "Northwind", "url": "https://northwind.example"}],
        )
    assert gather.await_count == 1
    assert len(gather.await_args.args[0]) == 1
    writer.assert_not_awaited()
    assert out["draft_count"] == 0
    assert out["write_refusal"]["refused"] is True
    assert "no competitor page" in out["write_refusal"]["reason"].lower()


@pytest.mark.asyncio
async def test_service_page_does_not_fetch_rival_pages():
    brief = _brief("service", "seo services", "/services/seo")
    planning = _planning(brief)
    with (
        patch(
            "app.services.content_benchmark.fetch.gather_competitor_content",
            new=AsyncMock(return_value=[]),
        ) as gather,
        patch(
            "app.services.create_content.synthesize_json",
            new=AsyncMock(return_value=FAKE_DRAFT_REPLY),
        ),
    ):
        out = await run_content_production_plan(
            client_name="Acme",
            primary_url="https://acme.example",
            content_planning_status="complete",
            content_planning=planning,
            selected_keyword="seo services",
            prior_briefs=[brief],
            competitors=[{"id": "c1", "name": "Northwind", "url": "https://northwind.example"}],
        )
    gather.assert_not_awaited()
    assert out["draft_count"] == 1
    assert "comparison_evidence" not in out["drafts"][0]
