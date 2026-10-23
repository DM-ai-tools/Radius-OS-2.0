"""Draft-writer fixes from the 2026-10-16 review of the AI Marketing preview.

The reviewed page was a "draft" built from the outline and template sentences after
every model call failed (Anthropic out of credit). These tests pin the fixes: no
template draft when the model fails, no competitor brand or article title in the
writer's instructions, "it" stays a word, no "Primary:" audience label in headings,
and no double full stop after the location.
"""

from __future__ import annotations

import pytest

import app.agents  # noqa: F401 — loads the agents package first (content_brief import order)
from app.services import create_content
from app.services.content_brief import differentiation_angle, summarize_serp
from app.services.create_content import (
    _differentiation,
    _normalize_acronyms,
    competitor_topic,
    write_one_page,
)
from app.services.headline_framework import _audience_label
from tests.test_create_content import _ready_brief

COMPETITOR_TITLE = "Best 20+ AI Marketing Tools to Grow Your Business - Digifix"


def test_it_is_a_word_not_an_acronym():
    text = "How it works, and why it matters for IT teams"
    assert _normalize_acronyms(text, "AI marketing", "AI Marketing") == text
    assert _normalize_acronyms("How it works", "IT support", "IT Support") == "How it works"
    assert _normalize_acronyms("ai marketing for seo", "AI marketing") == "AI marketing for SEO"


def test_competitor_titles_lose_their_brand():
    assert competitor_topic(COMPETITOR_TITLE) == "Best 20+ AI Marketing Tools to Grow Your Business"
    assert competitor_topic("AI Marketing Services | Sydney Agency | WebFX") == "AI Marketing Services"
    assert competitor_topic("What is SEO - The Complete Guide") == "What is SEO - The Complete Guide"
    serp = summarize_serp({"organic": [{"title": COMPETITOR_TITLE, "url": "https://digifix.example/x"}]})
    assert serp["coverage_floor"] == ["Best 20+ AI Marketing Tools to Grow Your Business"]


def test_differentiation_never_quotes_a_competitor_or_says_publishes_from():
    old = (
        "Click Trends publishes from hands-on practice with audits — not another restatement "
        f"of current service results such as “{COMPETITOR_TITLE}”."
    )
    cleaned = _differentiation({"differentiation": old})
    assert "Digifix" not in cleaned and "publishes from" not in cleaned
    assert "works from" in cleaned
    fresh = differentiation_angle(
        client_name="Click Trends",
        keyword="AI marketing",
        serp={"validated": True, "organic": [{"title": COMPETITOR_TITLE}], "dominant_format": "service"},
    )
    assert "Digifix" not in fresh and "publishes from" not in fresh


def test_audience_label_drops_the_primary_prefix():
    assert _audience_label("Primary: founders, owners, directors; Secondary: agencies", None) == "Founders"
    assert _audience_label("Target audience: clinic owners", None) == "Clinic Owners"
    assert _audience_label("Small business owners", None) == "Small Business Owners"


@pytest.mark.asyncio
async def test_a_failed_model_call_writes_nothing(monkeypatch):
    async def no_reply(*_a, **_kw):
        return None

    monkeypatch.setattr(create_content, "synthesize_json", no_reply)
    out = await write_one_page(brief=_ready_brief(), client_name="Acme", use_llm=True, generate_images=False)
    assert out["ok"] is False and out["refused"] is True
    assert "markdown" not in out
    assert "did not return a draft" in out["reason"]


@pytest.mark.asyncio
async def test_the_writer_prompt_has_no_competitor_brand_or_double_stop(monkeypatch):
    seen: dict = {}

    async def capture(system, user, **_kw):
        seen["user"] = user

    monkeypatch.setattr(create_content, "synthesize_json", capture)
    brief = _ready_brief(
        required_coverage={
            "outcomes": ["Decide"],
            "must_address": [COMPETITOR_TITLE],
            "must_name": [],
            "out_of_scope": [],
        },
        accuracy_grounding={
            "serp_competitors_to_beat": [COMPETITOR_TITLE],
            "competitive_notes": [COMPETITOR_TITLE],
        },
    )
    await write_one_page(
        brief=brief,
        client_name="Click Trends",
        use_llm=True,
        generate_images=False,
        location="Australia-wide; headquarters in Melbourne, Victoria.",
    )
    prompt = seen["user"]
    assert "Digifix" not in prompt
    assert "Best 20+ AI Marketing Tools to Grow Your Business" in prompt  # the topic stays
    assert "never name" in prompt
    assert "Victoria." not in prompt and "Victoria\n" in prompt


@pytest.mark.asyncio
async def test_a_cut_off_reply_is_refused_not_padded_with_template_text(monkeypatch):
    """A reply cut off after the opening used to be padded with template sections."""

    async def only_opening(*_a, **_kw):
        return {"opening": "Acme helps teams measure share of search with a method buyers can act on today."}

    monkeypatch.setattr(create_content, "synthesize_json", only_opening)
    out = await write_one_page(brief=_ready_brief(), client_name="Acme", use_llm=True, generate_images=False)
    assert out["refused"] is True and "incomplete" in out["reason"]
    assert "markdown" not in out


@pytest.mark.asyncio
async def test_an_unanswered_faq_is_left_out_of_a_model_written_page(monkeypatch):
    page = (
        "## What it is\n\nShare of search is the share of branded searches your brand earns in its "
        "category. It moves before market share does, which makes it a useful early signal for planning.\n\n"
        "## How we measure\n\nWe pull branded search volumes for you and named competitors every month, "
        "then track the share over a rolling year so seasonality does not mislead the trend. "
        "Each report names the searches counted, so your team can check every figure we show.\n\n"
        "## What you get\n\nA monthly share of search report, a short call to walk through it, and a "
        "plain note on what changed and why it matters for next month's plan.\n"
    )

    async def reply(*_a, **kw):
        assert kw.get("max_tokens") == create_content.DRAFT_MAX_TOKENS >= 20000
        return {
            "opening": "Acme measures share of search with a method buyers can check and act on.",
            "page_markdown": page,
            "faq": [{"q": "What is share of search?", "a": ""}],
            "cta": "Book a short call with Acme to set up your first share of search report.",
        }

    monkeypatch.setattr(create_content, "synthesize_json", reply)
    out = await write_one_page(brief=_ready_brief(), client_name="Acme", use_llm=True, generate_images=False)
    assert out["ok"] is True
    assert "AUTHOR INPUT REQUIRED" not in out["markdown"]
    assert "What is share of search?" not in out["markdown"]
    assert "rolling year" in out["markdown"]


def test_old_outline_headings_lose_the_audience_label():
    assert create_content._clean_heading("The Practical AI Marketing Path for Primary: Founders") == (
        "The Practical AI Marketing Path for Founders"
    )
    assert create_content._clean_heading("How it works") == "How it works"


def test_verify_markers_with_a_note_do_not_block_preview():
    from app.services.site_preview.checks import draft_problems
    from app.services.wp_content import PLACEHOLDER_MARKERS

    text = "It is used informally. [VERIFY: specific sourced definition] More text."
    assert not any("VERIFY" in p for p in draft_problems(text))
    assert any(m.lower() in text.lower() for m in PLACEHOLDER_MARKERS)


def test_accuracy_score_reads_the_page_not_the_editor_notes():
    brief = {
        "keyword": "AI Marketing",
        "accuracy_grounding": {
            "out_of_scope": ["Adjacent commercial pages owned by other URLs in the roadmap"],
            "geo": "Australia-wide, with priority metros Melbourne, Sydney; headquarters in Melbourne, Victoria",
        },
    }
    draft = {
        "title": "AI Marketing for Founders",
        "markdown": (
            "# AI Marketing for Founders\n\nClick Trends runs AI Marketing for teams in Melbourne.\n\n"
            "## Review queue\n\n- Out of scope: Adjacent commercial pages owned by other URLs in the roadmap\n"
        ),
    }
    acc = create_content.score_draft_accuracy(draft, brief, client_name="Click Trends")
    failed = [c["name"] for c in acc["checks"] if not c["ok"]]
    assert "out_of_scope" not in failed and "geo_grounding" not in failed
