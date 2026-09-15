"""content_enhancement — pre-write brief strengthening."""

from __future__ import annotations

import pytest

from app.services.content_enhancement import (
    enhance_brief_for_writing,
    humanize_title,
    strengthen_differentiation,
)
from app.services.create_content import draft_gate, write_one_page


def test_humanize_title_from_slug():
    title = humanize_title(
        "what-is-channel-attribution-forecasting-keep-your-entire-team-updated",
        angle="what-why",
    )
    assert "Channel Attribution" in title
    assert "-" not in title
    assert title[0].isupper()


def test_humanize_title_from_path():
    title = humanize_title("/blog/cro-for-ecommerce-stores/")
    assert title == "CRO for Ecommerce Stores"


def test_strengthen_differentiation_from_angle():
    diff = strengthen_differentiation(
        {
            "keyword": "conversion rate optimisation",
            "differentiation": "",
            "angle": "pain-point",
            "serp": {"validated": False, "dominant_format": "guide"},
        },
        client_name="Click Trends",
        marketing={"positioning": "hands-on CRO for ecommerce"},
        industry="Digital marketing",
    )
    assert len(diff) >= 24
    assert "Click Trends" in diff
    assert "generic" in diff.lower() or "not another" in diff.lower()


def test_enhance_brief_resolves_author_and_differentiation():
    brief = {
        "keyword": "conversion-rate-optimisation-ecommerce",
        "title": "conversion-rate-optimisation-ecommerce",
        "writer_ready": False,
        "action": "create",
        "url": "/blog/cro-ecommerce/",
        "angle": "how-to",
        "differentiation": "",
        "preflight": {
            "url": "/blog/cro-ecommerce/",
            "parent": "/blog/",
            "blockers": ["Differentiation empty — create-content will refuse (scaled content risk)."],
        },
    }
    enhanced, applied = enhance_brief_for_writing(
        brief,
        client_name="Click Trends",
        marketing={"positioning": "CRO for Shopify stores"},
        industry="Ecommerce",
    )
    assert "title_humanized" in applied
    assert "differentiation_strengthened" in applied
    assert "author_resolved" in applied
    assert enhanced.get("writer_ready") is True
    assert enhanced["preflight"]["author"] == "Click Trends"
    assert len(str(enhanced.get("differentiation") or "")) >= 24
    gate = draft_gate(enhanced)
    assert gate["ok"] is True


@pytest.mark.asyncio
async def test_write_one_page_enhances_weak_brief():
    brief = {
        "keyword": "conversion-rate-optimisation-ecommerce",
        "title": "conversion-rate-optimisation-ecommerce",
        "writer_ready": False,
        "action": "create",
        "url": "/blog/cro-ecommerce/",
        "angle": "pain-point",
        "differentiation": "",
        "outline": [{"title": "What it involves", "notes": ["Definition"]}],
        "preflight": {
            "url": "/blog/cro-ecommerce/",
            "parent": "/blog/",
            "blockers": [],
        },
    }
    out = await write_one_page(
        brief=brief,
        client_name="Click Trends",
        use_llm=False,
        industry="Ecommerce",
        marketing={"positioning": "CRO for Shopify stores"},
    )
    assert out.get("ok") is True
    assert out.get("refused") is False
    assert out.get("enhancements")
    assert "Conversion Rate" in (out.get("title") or "")
    assert isinstance(out.get("accuracy"), dict)
    assert "score" in out["accuracy"]


def test_accuracy_grounding_pulls_cluster_and_serp():
    from app.services.content_enhancement import build_accuracy_grounding, enhance_brief_for_writing

    brief = {
        "keyword": "seo services melbourne",
        "title": "SEO Services Melbourne",
        "differentiation": "Click Trends delivers hands-on technical SEO with named crawl fixes, not generic agency checklists.",
        "competitive_notes": ["Missing pricing decision criteria"],
        "serp": {
            "validated": True,
            "organic": [{"title": "Best SEO Agencies in Melbourne 2024"}],
        },
        "required_coverage": {"must_address": [], "outcomes": ["Choose an SEO partner"], "must_name": ["Click Trends"]},
        "action": "create",
        "url": "/services/seo/",
        "preflight": {"url": "/services/seo/", "parent": "/services/", "author": "Click Trends", "blockers": []},
        "writer_ready": True,
    }
    grounding = build_accuracy_grounding(
        brief,
        client_name="Click Trends",
        location="Melbourne",
        search_demand={
            "clusters": [
                {
                    "primary_keyword": "seo services melbourne",
                    "keywords": ["seo services melbourne", "local seo melbourne", "technical seo audit"],
                }
            ]
        },
        marketing={"positioning": "hands-on technical SEO"},
    )
    assert grounding["geo"] == "Melbourne"
    assert "local seo melbourne" in grounding["related_keywords"]
    assert grounding["serp_competitors_to_beat"]
    assert any("invent" in r.lower() for r in grounding["rules"])

    enhanced, applied = enhance_brief_for_writing(
        brief,
        client_name="Click Trends",
        location="Melbourne",
        search_demand={
            "clusters": [
                {
                    "primary_keyword": "seo services melbourne",
                    "keywords": ["seo services melbourne", "local seo melbourne", "technical seo audit"],
                }
            ]
        },
        marketing={"positioning": "hands-on technical SEO"},
    )
    assert "accuracy_grounding" in applied
    assert "related_keywords_injected" in applied
    assert enhanced["secondary_keywords"]
    assert enhanced["accuracy_grounding"]["geo"] == "Melbourne"


def test_score_draft_accuracy_flags_missing_primary():
    from app.services.create_content import score_draft_accuracy

    score = score_draft_accuracy(
        {"title": "Something else", "markdown": "Generic advice with no brand."},
        {
            "keyword": "share of search",
            "accuracy_grounding": {
                "primary_keyword": "share of search",
                "related_keywords": ["brand search"],
                "must_name": ["Acme"],
                "out_of_scope": [],
                "geo": "Melbourne",
            },
        },
        client_name="Acme",
    )
    assert score["ok"] is False
    assert score["score"] < 70
    names = {c["name"]: c["ok"] for c in score["checks"]}
    assert names["primary_keyword"] is False
    assert names["client_named"] is False


def test_score_draft_accuracy_passes_grounded_draft():
    from app.services.create_content import score_draft_accuracy

    md = (
        "Share of search helps Acme teams in Melbourne compare brand search demand. "
        "Use brand search alongside share of voice. Studies show 87% of buyers convert — wait, [VERIFY]."
    )
    score = score_draft_accuracy(
        {"title": "Share of search", "markdown": md},
        {
            "keyword": "share of search",
            "accuracy_grounding": {
                "primary_keyword": "share of search",
                "related_keywords": ["brand search"],
                "must_name": ["Acme"],
                "out_of_scope": ["paid media mix modelling"],
                "geo": "Melbourne",
            },
        },
        client_name="Acme",
        related=["brand search"],
    )
    assert score["ok"] is True
    assert score["score"] >= 70
