"""Phase 9 content-brief pre-flight and planning gates."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.content_brief import (
    author_standing,
    differentiation_angle,
    generate_briefs,
    preflight,
)
from app.services.create_content import draft_gate
from app.services.content_planning import run_content_planning_plan
from app.services.content_production import (
    parse_topic_selection,
    planning_gate_ok,
    run_content_production_plan,
)


def test_preflight_stops_when_existing_page_owns_intent():
    pre = preflight(
        keyword="share of search",
        suggested_url="/blog/share-of-search-new",
        action="create",
        content_audit={
            "inventory": [
                {
                    "path": "/blog/share-of-search",
                    "title": "Share of search",
                    "keyword": "share of search",
                    "disposition": "keep",
                }
            ]
        },
        website={},
        site_architecture={
            "target_url_tree": [
                {"path": "/blog/share-of-search-new", "parent": "/blog/", "type": "article"}
            ]
        },
        roadmap_row={"url": "/blog/share-of-search-new", "keyword": "share of search"},
        industry="marketing",
        marketing={},
    )
    assert pre["writer_ready"] is False
    assert pre["recommended_action"] == "stop_refresh_existing"
    assert pre["existing_page"]["band"] == "performing"


def test_preflight_refresh_when_audit_says_refresh():
    pre = preflight(
        keyword="crm software",
        suggested_url="/crm",
        action="create",
        content_audit={
            "inventory": [
                {"path": "/crm", "keyword": "crm software", "disposition": "refresh", "title": "CRM"}
            ]
        },
        website={},
        site_architecture={"target_url_tree": [{"path": "/crm", "parent": "/", "type": "service"}]},
        roadmap_row={"url": "/crm", "keyword": "crm software"},
        industry="saas",
        marketing={"author": "Alex Writer", "client_intake": {"author_credentials": "CRM consultant"}},
    )
    assert pre["recommended_action"] == "refresh"
    assert pre["writer_ready"] is True


def test_preflight_same_url_becomes_refresh_not_stop():
    pre = preflight(
        keyword="seo audit",
        suggested_url="/seo-audit",
        action="create",
        content_audit={
            "inventory": [
                {
                    "path": "/seo-audit",
                    "title": "SEO audit",
                    "keyword": "seo audit",
                    "disposition": "keep",
                }
            ]
        },
        website={},
        site_architecture={"target_url_tree": [{"path": "/seo-audit", "parent": "/", "type": "service"}]},
        roadmap_row={"url": "/seo-audit", "keyword": "seo audit", "action": "create"},
        industry="seo",
        marketing={},
        client_name="Click Trends",
    )
    assert pre["recommended_action"] == "refresh"
    assert pre["writer_ready"] is True
    assert pre["author"]["author"] == "Click Trends"


def test_author_from_primary_contact():
    standing = author_standing(
        "local seo",
        industry="marketing",
        marketing={"client_intake": {"primary_contact_name": "Jamie Owner"}},
        client_name="Click Trends",
    )
    assert standing["author"] == "Jamie Owner"
    assert standing["ymyl"] is False


def test_author_organization_fallback_non_ymyl():
    standing = author_standing("seo services", industry="marketing", marketing={}, client_name="Click Trends")
    assert standing["author"] == "Click Trends"
    assert standing["standing"] == "organization"


def test_ymyl_still_blocks_without_credentials():
    standing = author_standing(
        "best mortgage rates",
        industry="finance",
        marketing={"client_intake": {"primary_contact_name": "Pat Broker"}},
        client_name="Acme Finance",
    )
    assert standing["ymyl"] is True
    assert standing["standing"] == "unverified"


def test_differentiation_passes_create_content_gate():
    angle = differentiation_angle(
        client_name="Click Trends",
        keyword="seo audit",
        serp={
            "validated": True,
            "dominant_format": "guide",
            "organic": [{"title": "What is an SEO audit", "domain": "a.com"}],
        },
        marketing={
            "client_intake": {
                "positioning": "hands-on technical SEO for Australian SMBs",
                "geographic_focus": "Australia",
                "products": ["SEO audit"],
            }
        },
        industry="seo",
    )
    assert len(angle) >= 24
    assert not angle.lower().startswith("lead with")
    g = draft_gate(
        {
            "keyword": "seo audit",
            "url": "/seo-audit",
            "writer_ready": True,
            "action": "create",
            "title": "SEO audit",
            "differentiation": angle,
            "preflight": {"url": "/seo-audit", "parent": "/", "author": "Click Trends", "blockers": []},
            "required_coverage": {"outcomes": ["Decide next step"]},
            "outline": [{"title": "What it is"}],
        }
    )
    assert g["ok"] is True


@pytest.mark.asyncio
async def test_planning_blocked_without_strategy_queue():
    out = await run_content_planning_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        site_architecture_status="complete",
        site_architecture={"target_url_tree": [{"path": "/services/", "parent": "/"}]},
        seo_strategy={},
        seo_strategy_status="not_started",
    )
    assert out.get("blocked") is True
    assert "Content Strategy" in str(out.get("reason") or "")


@pytest.mark.asyncio
async def test_generate_briefs_has_coverage_not_word_count():
    serp = {
        "organic": [
            {
                "position": 1,
                "title": "What is share of search",
                "url": "https://a.com/x",
                "domain": "a.com",
            }
        ],
        "people_also_ask": ["What is share of search?"],
        "featured_snippet": None,
        "item_types": ["organic", "people_also_ask"],
        "validated": True,
    }
    with patch("app.services.content_brief.dataforseo.serp_advanced", new=AsyncMock(return_value=(serp, []))):
        with patch(
            "app.services.content_brief.synthesize_json",
            new=AsyncMock(
                return_value={
                    "differentiation": "Original campaign measurement framework with client-named metrics.",
                    "title_options": ["Share of search guide"],
                }
            ),
        ):
            pack = await generate_briefs(
                client_name="Reload Media",
                industry="marketing",
                marketing={"author": "Sam Editor", "client_intake": {"author_credentials": "Brand strategist"}},
                seo_strategy={
                    "priority_queue": [
                        {
                            "keyword": "share of search",
                            "title": "Share of search guide",
                            "suggested_url": "/blog/share-of-search",
                            "intent": "informational",
                            "priority": "Quick win",
                        }
                    ]
                },
                site_architecture={
                    "target_url_tree": [
                        {
                            "path": "/blog/share-of-search",
                            "parent": "/blog/",
                            "type": "article",
                            "keyword": "share of search",
                        }
                    ]
                },
                content_audit={"inventory": []},
                roadmap=[
                    {
                        "url": "/blog/share-of-search",
                        "path": "/blog/share-of-search",
                        "parent": "/blog/",
                        "keyword": "share of search",
                        "action": "create",
                        "wave": 1,
                    }
                ],
            )
    assert pack["brief_count"] == 1
    brief = pack["briefs"][0]
    assert "word_count" not in brief
    assert brief["required_coverage"]["outcomes"]
    assert brief["required_coverage"]["out_of_scope"]
    assert brief["preflight"]["url"]
    assert brief["serp"]["validated"] is True
    assert brief["faq"][0]["q"] == "What is share of search?"
    assert brief["writer_ready"] is True
    assert len(str(brief.get("differentiation") or "")) >= 24


@pytest.mark.asyncio
async def test_generate_briefs_writer_ready_without_named_person_or_llm_diff():
    serp = {
        "organic": [{"position": 1, "title": "SEO audit checklist", "url": "https://a.com/x", "domain": "a.com"}],
        "people_also_ask": [],
        "featured_snippet": None,
        "item_types": ["organic"],
        "validated": True,
    }
    with patch("app.services.content_brief.dataforseo.serp_advanced", new=AsyncMock(return_value=(serp, []))):
        with patch("app.services.content_brief.synthesize_json", new=AsyncMock(return_value={})):
            pack = await generate_briefs(
                client_name="Click Trends",
                industry="seo",
                marketing={},
                seo_strategy={},
                site_architecture={
                    "target_url_tree": [
                        {"path": "/seo-audit", "parent": "/", "type": "service", "keyword": "seo audit"}
                    ]
                },
                content_audit={"inventory": []},
                roadmap=[
                    {
                        "url": "/seo-audit",
                        "path": "/seo-audit",
                        "parent": "/",
                        "keyword": "seo audit",
                        "action": "create",
                        "wave": 1,
                    }
                ],
            )
    brief = pack["briefs"][0]
    assert brief["preflight"]["author"] == "Click Trends"
    assert len(str(brief["differentiation"])) >= 24
    assert brief["writer_ready"] is True
    assert draft_gate(brief)["ok"] is True


@pytest.mark.asyncio
async def test_production_drafts_only_one_writer_ready_page():
    planning = {
        "locked": True,
        "pages": [
            {
                "url_n": "/a",
                "url": "/a",
                "action": "create",
                "primary_keyword": "ready topic",
                "priority_rank": 1,
            },
            {
                "url_n": "/c",
                "url": "/c",
                "action": "create",
                "primary_keyword": "second topic",
                "priority_rank": 2,
            },
        ],
    }
    brief_pack = {
        "briefs": [
            {
                "keyword": "ready topic",
                "url": "/a",
                "writer_ready": True,
                "action": "create",
                "title": "Ready",
                "differentiation": "Unique client measurement method with named campaign results.",
                "outline": [{"title": "What it is"}],
                "required_coverage": {"outcomes": ["Decide next step"]},
                "preflight": {
                    "url": "/a",
                    "parent": "/",
                    "author": "Pat Writer",
                    "author_standing": "Strategist",
                    "blockers": [],
                },
                "faq": [],
            },
            {
                "keyword": "second topic",
                "url": "/c",
                "writer_ready": True,
                "action": "create",
                "title": "Second",
                "differentiation": "Second unique angle with proprietary checklist.",
                "outline": [{"title": "Overview"}],
                "required_coverage": {"outcomes": ["Pick a vendor"]},
                "preflight": {
                    "url": "/c",
                    "parent": "/",
                    "author": "Pat Writer",
                    "author_standing": "Strategist",
                    "blockers": [],
                },
                "faq": [],
            },
            {
                "keyword": "blocked topic",
                "url": "/b",
                "writer_ready": False,
                "preflight": {"blockers": ["Existing page owns this intent"]},
            },
        ],
        "skipped_new_urls": [],
    }
    assert planning_gate_ok("pending_signoff", planning) is True
    with patch("app.services.content_brief.generate_briefs", new=AsyncMock(return_value=brief_pack)):
        listed = await run_content_production_plan(
            client_name="Acme",
            primary_url="https://acme.example",
            content_planning_status="complete",
            content_planning=planning,
        )
    assert listed["draft_count"] == 0
    assert listed["awaiting_topic_selection"] is True
    assert len(listed.get("topic_choices") or []) == 2
    assert listed["topic_choices"][0]["keyword"] == "ready topic"

    with patch("app.services.create_content.synthesize_json", new=AsyncMock(return_value=None)):
        out = await run_content_production_plan(
            client_name="Acme",
            primary_url="https://acme.example",
            content_planning_status="complete",
            content_planning=planning,
            selected_keyword="ready topic",
            selected_url="/a",
            prior_briefs=brief_pack["briefs"],
        )
    assert out["draft_count"] == 1
    assert out["drafts"][0]["keyword"] == "ready topic"
    assert out["one_page_per_run"] is True
    assert out["translation"] == "excluded"
    assert "Review queue" in (out["drafts"][0].get("markdown") or "")
    assert "word_count" not in (out["briefs"][0] or {})
    assert len(out["held_briefs"]) >= 1
    assert len(out.get("queued_for_next_write") or []) >= 1


def test_parse_topic_selection():
    sel = parse_topic_selection("Write the full draft for: seo audit (/seo-audit)")
    assert sel == {"keyword": "seo audit", "url": "/seo-audit"}
    abs_sel = parse_topic_selection(
        "Write the full draft for: seo and web design company (https://www.clicktrends.com.au/seo)"
    )
    assert abs_sel
    assert abs_sel["keyword"] == "seo and web design company"
    assert "clicktrends.com.au/seo" in abs_sel["url"]
    assert parse_topic_selection("Run content production briefs and drafts") is None


@pytest.mark.asyncio
async def test_production_blocked_when_roadmap_unlocked():
    out = await run_content_production_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        content_planning_status="pending_signoff",
        content_planning={"locked": False, "lock_reason": "create /x missing title", "pages": []},
    )
    assert out.get("blocked") is True
    assert "unlocked" in str(out.get("reason") or "").lower()
