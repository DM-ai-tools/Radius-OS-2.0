"""create-content gates — one page, differentiation + author required."""

from __future__ import annotations

import pytest

from app.services.content_strategy import build_priority_queue
from app.services.create_content import (
    ANGLE_GUIDANCE,
    _is_instructional,
    angle_guidance,
    collect_image_specs,
    draft_gate,
    related_keywords,
    refuse_batch,
    resolve_page_type,
    select_one_brief,
    write_one_page,
)


def _ready_brief(**over: object) -> dict:
    base = {
        "keyword": "share of search",
        "url": "/blog/share-of-search",
        "writer_ready": True,
        "action": "create",
        "title": "Share of search",
        "differentiation": "Original measurement method from client campaigns with named metrics.",
        "preflight": {
            "url": "/blog/share-of-search",
            "parent": "/blog",
            "author": "Jane Strategist",
            "author_standing": "10 years brand measurement",
            "blockers": [],
        },
        "required_coverage": {
            "outcomes": ["Decide how to measure share of search"],
            "must_address": ["Definition"],
            "must_name": ["Acme"],
            "out_of_scope": ["Paid media mix modelling"],
        },
        "outline": [{"title": "What it is", "notes": ["Definition"]}, {"title": "How we measure", "notes": ["Our method"]}],
        "faq": [{"q": "What is share of search?"}],
        "meta_description": "How to measure share of search without vanity metrics.",
    }
    base.update(over)
    return base


def test_refuse_without_brief():
    g = draft_gate(None)
    assert g["ok"] is False
    assert g["route_to"] == "content_brief"


def test_refuse_empty_differentiation():
    g = draft_gate(_ready_brief(differentiation=""))
    assert g["ok"] is False
    assert "Differentiation" in (g["reason"] or "")


def test_refuse_missing_author():
    b = _ready_brief()
    b["preflight"] = {**b["preflight"], "author": None}  # type: ignore[misc]
    g = draft_gate(b)
    assert g["ok"] is False
    assert "author" in (g["reason"] or "").lower()


def test_refuse_batch():
    r = refuse_batch(20)
    assert r["refused"] is True
    assert "one page" in r["reason"].lower()


def test_select_one_brief_queues_rest():
    a = _ready_brief(keyword="a", url="/a")
    b = _ready_brief(keyword="b", url="/b")
    chosen, held = select_one_brief([a, b])
    assert chosen and chosen["keyword"] == "a"
    assert any("one page" in str(h.get("reason") or "").lower() for h in held)


@pytest.mark.asyncio
async def test_write_one_page_includes_review_queue():
    out = await write_one_page(brief=_ready_brief(), client_name="Acme", use_llm=False)
    assert out.get("ok") is True
    assert out.get("status") == "draft_pending_review"
    assert "Review queue" in (out.get("markdown") or "")
    assert "Named reviewer" in (out.get("markdown") or "")
    assert "word_count" not in out
    assert out.get("translation") == "excluded"
    md = (out.get("markdown") or "").lower()
    assert "share of search" in md
    assert "**cta:**" not in md
    assert "people who searched" not in md
    assert out.get("page_type") == "article"


def test_strategy_queue_includes_image_suggestions():
    q = build_priority_queue(
        best=[
            {
                "keyword": "local seo",
                "volume": 100,
                "difficulty": 20,
                "opportunity_score": 70,
                "intent": "commercial",
            }
        ],
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[],
        domain="acme.example",
        industry="SEO",
        location="Melbourne",
    )
    assert q
    suggestions = q[0].get("image_suggestions") or []
    assert len(suggestions) >= 2
    assert suggestions[0]["role"] == "hero"
    assert "local seo" in suggestions[0]["prompt"].lower()
    assert "melbourne" in suggestions[0]["prompt"].lower()


def test_collect_image_specs_from_strategy():
    specs = collect_image_specs(
        _ready_brief(),
        seo_strategy={
            "priority_queue": [
                {
                    "keyword": "share of search",
                    "image_suggestions": [
                        {
                            "role": "hero",
                            "prompt": "Editorial photo of a brand team measuring share of search on a wall of charts",
                        },
                        {
                            "role": "supporting",
                            "prompt": "Diagram of share of search calculation steps for a brand team",
                        },
                        {
                            "role": "supporting",
                            "prompt": "Before and after outcomes visual for share of search reporting in a war room",
                        },
                    ],
                }
            ]
        },
        client_name="Acme",
    )
    assert len(specs) == 3
    assert specs[0]["role"] == "hero"
    assert "share of search" in specs[0]["prompt"].lower()


def test_collect_image_specs_scales_with_outline():
    brief = {
        **_ready_brief(),
        "outline": [
            {"title": "What it is"},
            {"title": "How it works"},
            {"title": "Common mistakes"},
            {"title": "Implementation steps"},
            {"title": "Measuring results"},
            {"title": "FAQ"},
        ],
    }
    specs = collect_image_specs(brief, client_name="Acme", industry="SEO", location="Melbourne")
    assert len(specs) >= 3
    assert specs[0]["role"] == "hero"
    assert len(specs) <= 12


def test_collect_image_specs_honours_explicit_count():
    brief = {
        **_ready_brief(),
        "image_requirements": {
            "count": 4,
            "hero": "Editorial hero photo about share of search measurement for brands",
            "supporting": [
                "Diagram one for share of search inputs",
                "Diagram two for share of search outputs",
                "Diagram three for share of search reporting cadence",
            ],
        },
    }
    specs = collect_image_specs(brief, client_name="Acme")
    assert len(specs) == 4
    assert specs[0]["role"] == "hero"


@pytest.mark.asyncio
async def test_write_one_page_embeds_strategy_figures():
    out = await write_one_page(
        brief=_ready_brief(),
        client_name="Acme",
        use_llm=False,
        seo_strategy={
            "priority_queue": [
                {
                    "keyword": "share of search",
                    "image_suggestions": [
                        {
                            "role": "hero",
                            "prompt": "Editorial photo of share of search measurement in a brand war room",
                        }
                    ],
                }
            ]
        },
    )
    md = out.get("markdown") or ""
    assert "FIGURE" in md
    assert "share of search" in md.lower()
    assert out.get("images")
    assert out["images"][0]["role"] == "hero"


def test_related_keywords_prioritize_assigned():
    primary, related = related_keywords(
        _ready_brief(
            keyword="seo company",
            secondary_keywords=["local seo", "seo services", "seo company"],
        ),
        search_demand={
            "clusters": [
                {
                    "primary_keyword": "seo company",
                    "keywords": ["seo agency melbourne", "website seo", "seo company"],
                }
            ]
        },
    )
    assert primary == "seo company"
    assert "seo company" not in related
    assert "local seo" in related
    assert "seo agency melbourne" in related
    assert "website seo" in related


@pytest.mark.asyncio
async def test_write_one_page_weaves_related_keywords():
    out = await write_one_page(
        brief=_ready_brief(
            secondary_keywords=["share of voice", "brand search demand"],
        ),
        client_name="Acme",
        use_llm=False,
    )
    md = (out.get("markdown") or "").lower()
    assert "primary keyword" in md
    assert "share of search" in md
    assert "share of voice" in md
    assert "brand search demand" in md
    assert out.get("related_keywords") == ["share of voice", "brand search demand"]


def test_instructional_outline_is_detected():
    assert _is_instructional(
        "Cover this section so the reader reaches the briefed outcome for SEO Web Design Services."
    )
    assert not _is_instructional(
        "SEO-focused web design starts with crawlable templates, not a visual mock that ignores indexation."
    )


@pytest.mark.asyncio
async def test_write_one_page_does_not_stamp_placeholders_on_every_section():
    out = await write_one_page(
        brief=_ready_brief(
            outline=[
                {
                    "title": "What makes the latest approach work",
                    "notes": [
                        "Cover this section so the reader reaches the briefed outcome for share of search"
                    ],
                },
                {"title": "How we measure", "notes": ["Definition"]},
            ],
            faq=[{"q": "What is share of search?"}],
        ),
        client_name="Acme",
        use_llm=False,
    )
    md = out.get("markdown") or ""
    assert "briefed outcome" not in md.lower()
    assert "cover this section" not in md.lower()
    assert md.lower().count("[verify]") == 0
    assert md.lower().count("[author input required") == 0
    assert "What is share of search?" in md
    assert "[VERIFY] — answer only from verified knowledge" not in md
    assert "Named reviewer" in md
    assert "First-hand placeholders: none" in md


def test_resolve_page_type_prefers_ia_and_url():
    assert (
        resolve_page_type(
            {
                "keyword": "seo services",
                "url": "/services/seo/",
                "content_type": "article",
                "preflight": {"page_type": "service", "url": "/services/seo/"},
            }
        )
        == "service"
    )
    assert resolve_page_type({"keyword": "crm vs spreadsheets", "url": "/compare/crm-vs-spreadsheets"}) == "comparison"
    assert resolve_page_type({"keyword": "how to measure seo", "url": "/guides/measure-seo"}) == "guide"


@pytest.mark.asyncio
async def test_service_page_draft_reads_as_publishable_service_copy():
    out = await write_one_page(
        brief=_ready_brief(
            keyword="seo services melbourne",
            title="SEO services in Melbourne",
            url="/services/seo/",
            content_type="article",
            search_intent="commercial",
            preflight={
                "url": "/services/seo/",
                "parent": "/services/",
                "page_type": "service",
                "author": "Jane Strategist",
                "author_standing": "10 years brand measurement",
                "blockers": [],
            },
            outline=[
                {"title": "What seo services melbourne involves", "notes": ["Definition"]},
                {"title": "How to choose / apply", "notes": ["Steps"]},
            ],
        ),
        client_name="Click Trends",
        use_llm=False,
        industry="SEO",
        location="Melbourne",
        marketing={"geographic_focus": "Melbourne", "positioning": "hands-on technical SEO for SMBs"},
    )
    md = out.get("markdown") or ""
    assert out.get("page_type") == "service"
    assert "Page type**: service" in md or "**Page type**: service" in md
    assert "Who seo services melbourne is for" in md or "What is included" in md
    assert "**CTA:**" not in md
    assert "people who searched" not in md.lower()
    assert "Click Trends" in md
    assert "Melbourne" in md
    assert md.lower().count("\n\n") >= 8


@pytest.mark.parametrize(
    ("page_type", "url"),
    [
        ("service", "/seo/"),
        ("landing", "/pricing/seo/"),
        ("guide", "/guides/measure-seo/"),
        ("comparison", "/compare/seo-vs-ppc/"),
    ],
)
@pytest.mark.asyncio
async def test_existing_page_types_reach_actual_drafting_flow(page_type: str, url: str):
    brief = _ready_brief(
        keyword=f"{page_type} seo",
        title=f"{page_type.title()} SEO",
        url=url,
        content_type=page_type,
        outline=[],
        preflight={
            "url": url,
            "parent": "/",
            "page_type": page_type,
            "author": "Jane Strategist",
            "author_standing": "10 years brand measurement",
            "blockers": [],
        },
    )
    out = await write_one_page(brief=brief, client_name="Acme", use_llm=False)
    assert out.get("ok") is True
    assert out.get("status") == "draft_pending_review"
    assert out.get("page_type") == page_type
    assert out.get("markdown")


@pytest.mark.asyncio
async def test_tool_page_draft_is_component_spec_not_article_fallback():
    url = "/tools/marketing-roi-calculator/"
    brief = _ready_brief(
        keyword="marketing roi calculator",
        title="Marketing ROI Calculator",
        url=url,
        content_type="tool",
        outline=[],
        preflight={
            "url": url,
            "parent": "/tools/",
            "page_type": "tool",
            "author": "Jane Strategist",
            "author_standing": "10 years brand measurement",
            "blockers": [],
        },
    )
    out = await write_one_page(brief=brief, client_name="Acme", use_llm=False)
    markdown = out.get("markdown") or ""
    assert out.get("ok") is True
    assert out.get("page_type") == "tool"
    assert "INTERACTIVE COMPONENT REQUIRED" in markdown
    assert "Inputs and validation" in markdown
    assert "PRODUCT CONFIRMATION REQUIRED" in markdown


# --- Angle awareness ------------------------------------------------------------
# Phase 5 (create_topic._ANGLE_CYCLE) assigns one of 13 angles to every topic so a
# batch of "article" pages doesn't collapse into the same generic explainer. That
# signal used to reach the brief and die there — the writer never read it, so a
# "pain-point" topic and a "trends" topic on the same page_type got an identical
# opening. These pin that the angle now actually shapes the draft.

def test_angle_guidance_covers_every_phase5_angle():
    phase5_angles = (
        "pain-point", "how-to", "mistakes", "what-why", "why-failing", "listicle",
        "comparison", "never-again", "case-study", "trends", "beginner", "advanced",
        "templates",
    )
    for angle in phase5_angles:
        assert angle in ANGLE_GUIDANCE, f"missing guidance for angle {angle}"
        guidance = angle_guidance(angle, kw="seo")
        assert guidance and "seo" in guidance.lower()


def test_angle_guidance_returns_none_for_unknown_or_empty():
    assert angle_guidance(None, kw="seo") is None
    assert angle_guidance("", kw="seo") is None
    assert angle_guidance("not-a-real-angle", kw="seo") is None


@pytest.mark.asyncio
async def test_write_one_page_opening_reflects_angle():
    pain_point = await write_one_page(
        brief=_ready_brief(angle="pain-point"), client_name="Acme", use_llm=False
    )
    trends = await write_one_page(
        brief=_ready_brief(angle="trends"), client_name="Acme", use_llm=False
    )
    pp_md = pain_point.get("markdown") or ""
    tr_md = trends.get("markdown") or ""
    # Same brief, same page_type — only the angle differs — so the two openings
    # must not be identical (the bug this pins: angle was silently discarded).
    assert "frustrating" in pp_md.lower()
    assert "changing" in tr_md.lower()
    assert pp_md != tr_md


@pytest.mark.asyncio
async def test_write_one_page_surfaces_angle_and_business_fit():
    out = await write_one_page(
        brief=_ready_brief(
            angle="how-to",
            business_fit={"score": 88, "reason": "core_service"},
        ),
        client_name="Acme",
        use_llm=False,
    )
    md = out.get("markdown") or ""
    assert out.get("angle") == "how-to"
    assert out.get("business_fit") == {"score": 88, "reason": "core_service"}
    assert "Angle**: how-to" in md
    assert "Business fit" in md
    assert "core_service" in md


@pytest.mark.asyncio
async def test_write_one_page_without_angle_still_works():
    """No angle in the brief (older data, or a phase that doesn't set one) must not
    break the draft — it just falls back to the generic opening."""
    out = await write_one_page(brief=_ready_brief(), client_name="Acme", use_llm=False)
    assert out.get("ok") is True
    assert out.get("angle") is None


# --- Ready-to-publish display: title + meta description must be structured fields,
# not just text buried inside the markdown "Draft metadata" section — the frontend
# renders a search-result preview from these two fields directly.

@pytest.mark.asyncio
async def test_write_one_page_exposes_meta_description_as_top_level_field():
    out = await write_one_page(brief=_ready_brief(), client_name="Acme", use_llm=False)
    assert out.get("meta_description") == "How to measure share of search without vanity metrics."
    # Still present in the markdown for the plain-text/export view too.
    assert "Meta description" in (out.get("markdown") or "")


@pytest.mark.asyncio
async def test_write_one_page_meta_description_empty_when_brief_has_none():
    out = await write_one_page(
        brief=_ready_brief(meta_description=""), client_name="Acme", use_llm=False
    )
    assert out.get("meta_description") == ""
