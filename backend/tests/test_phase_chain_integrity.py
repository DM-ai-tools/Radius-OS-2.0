"""Regressions for defects that only surfaced running Phases 1-13 end to end.

Each of these silently broke the downstream chain while every phase still reported
"complete", so they are pinned here rather than left to an E2E run to rediscover.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Client, ClientDigitalProfile, FindingsLedger
from app.services.content_brief import find_existing_intent
from app.services.content_planning import build_roadmap
from app.services.content_strategy import build_authority_map, build_priority_queue
from app.services.review import approve_phase_batch
from tests.conftest import make_user


# --- Phase 6 crash: call site passed industry/location the signature rejected -------

def test_build_authority_map_accepts_industry_and_location():
    """content_strategy calls this with industry/location; a TypeError here killed
    Phases 6-13 outright (every downstream phase blocked on the missing pack)."""
    core = build_authority_map(
        pillars=[{"name": "SEO Services", "primary_keyword": "seo services", "intent": "transactional"}],
        report_clusters=[],
        topic_plan=None,
        industry="Digital Marketing",
        location="Melbourne",
    )
    assert core and core[0]["primary_keyword"] == "seo services"


def test_authority_map_uses_phase5_topic_plan_when_no_pillars():
    core = build_authority_map(
        pillars=[],
        report_clusters=[],
        topic_plan={
            "topic_ideas": [
                {"title": "SEO for Dentists", "keyword": "seo for dentists", "intent": "commercial"},
                {"title": "Local SEO Checklist", "keyword": "local seo checklist", "intent": "informational"},
            ]
        },
        industry="Digital Marketing",
        location="Melbourne",
    )
    assert core
    assert core[0]["primary_keyword"] == "seo for dentists"
    assert any(
        (c.get("primary_keyword") == "local seo checklist")
        for c in (core[0].get("clusters") or [])
    )


# --- Phase 5 -> 6: approved topic headlines were regenerated into generic titles ---

def test_priority_queue_carries_phase5_topic_titles():
    """Create Topic headlines must survive into the Phase 6 queue.

    The ideas' keywords also sit in the volume buckets (the topics were generated
    from them), so the old 'skip if keyword already seen' guard dropped every idea
    and the queue re-derived a generic 'What Is X?' title for each one.
    """
    best = [
        {"keyword": "lead generation", "volume": 900, "difficulty": 20, "opportunity_score": 80},
        {"keyword": "b2b lead generation", "volume": 500, "difficulty": 25, "opportunity_score": 70},
    ]
    topic_plan = {
        "topic_ideas": [
            {
                "title": "Struggling With Leads Going Cold? Here's The Fix",
                "keyword": "lead generation",
                "intent": "informational",
            },
            {
                "title": "Why Your B2B Lead Generation Isn't Working",
                "keyword": "b2b lead generation",
                "intent": "informational",
            },
        ]
    }
    queue = build_priority_queue(
        best=best,
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[],
        domain="example.com",
        topic_plan=topic_plan,
    )
    by_kw = {row["keyword"]: row for row in queue}
    for idea in topic_plan["topic_ideas"]:
        row = by_kw[idea["keyword"]]
        assert row["title"] == idea["title"], (
            f"Phase 6 rewrote the approved headline for {idea['keyword']!r}"
        )
        assert row["from_phase5_topic"] is True


def test_priority_queue_uses_phase5_topics_as_spine_not_best_opportunities():
    """High-volume best_opportunities must not displace approved Phase 5 topics."""
    topic_plan = {
        "topic_ideas": [
            {
                "title": "What Is Conversion Rate Optimisation? A Clear Guide",
                "keyword": "conversion rate optimisation",
                "secondary_keywords": ["cro agency"],
                "intent": "informational",
            },
            {
                "title": "Best Digital Marketing Agency Australia Strategies",
                "keyword": "digital marketing agency australia",
                "intent": "commercial",
            },
        ]
    }
    best = [
        {
            "keyword": "meta ads library",
            "volume": 18100,
            "difficulty": 12,
            "opportunity_score": 81,
        },
        {
            "keyword": "conversion rate optimisation",
            "volume": 4400,
            "difficulty": 25,
            "opportunity_score": 70,
        },
    ]
    queue = build_priority_queue(
        best=best,
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[],
        domain="clicktrends.com.au",
        topic_plan=topic_plan,
    )
    assert queue[0]["keyword"] == "conversion rate optimisation"
    assert queue[0]["title"].startswith("What Is Conversion Rate")
    assert queue[0]["from_phase5_topic"] is True
    assert queue[0]["volume"] == 4400
    assert "cro agency" in queue[0]["secondary_keywords"]
    assert queue[1]["keyword"] == "digital marketing agency australia"
    # Unrelated high-volume best rows may appear only as fill-ins after the spine.
    fill_kws = [r["keyword"] for r in queue if not r.get("from_phase5_topic")]
    assert "meta ads library" in fill_kws
    assert fill_kws.index("meta ads library") >= 0
    assert queue.index(next(r for r in queue if r["keyword"] == "meta ads library")) > 1


def test_priority_queue_still_titles_keywords_phase5_never_covered():
    """Keywords with no Create Topic idea must still get a generated title."""
    queue = build_priority_queue(
        best=[{"keyword": "seo audit", "volume": 300, "opportunity_score": 60}],
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[],
        domain="example.com",
        topic_plan={"topic_ideas": []},
    )
    row = next(r for r in queue if r["keyword"] == "seo audit")
    assert row["title"] and row["title"].lower() != "seo audit"
    assert row["from_phase5_topic"] is False


# --- Phase 9 join: same topic, different URL was dropped from BOTH sides -----------

def _strategy_row(url, kw):
    return {"url": url, "keyword": kw, "title": kw.title(), "opportunity_score": 70}


def _arch_node(url, kw, parent="/blog", depth=2):
    return {"url": url, "keyword": kw, "parent": parent, "depth": depth, "type": "article"}


def test_roadmap_reconciles_url_conflict_onto_architecture():
    """Phase 6 slugs /guides/x, Phase 6b slugs /blog/x for the same keyword. A URL-only
    join dropped both and reported two gaps; the page must survive on the architecture
    URL (the documented url_conflict_winner)."""
    out = build_roadmap(
        {"priority_queue": [_strategy_row("/guides/local-seo-pricing", "local seo pricing")]},
        {"target_url_tree": [_arch_node("/blog/local-seo-pricing", "local seo pricing")]},
        {},
    )
    pages = out.get("pages") or out.get("roadmap") or []
    assert len(pages) == 1, out.get("excluded")
    assert pages[0]["url_n"] == "/blog/local-seo-pricing"
    assert "url_reconciled" in (pages[0].get("flags") or [])
    assert not out.get("excluded")


def test_roadmap_does_not_double_plan_one_keyword():
    """Reconciliation must not hand a keyword a second URL — two URLs on one keyword is
    the cannibalisation this phase exists to prevent."""
    out = build_roadmap(
        {
            "priority_queue": [
                _strategy_row("/blog/seo-services", "seo services"),
                _strategy_row("/guides/seo-services", "seo services"),
            ]
        },
        {
            "target_url_tree": [
                _arch_node("/blog/seo-services", "seo services"),
                _arch_node("/services/seo-services", "seo services", parent="/services"),
            ]
        },
        {},
    )
    pages = out.get("pages") or out.get("roadmap") or []
    keywords = [p.get("primary_keyword") or p.get("keyword") for p in pages]
    assert keywords.count("seo services") == 1, pages


def test_roadmap_still_reports_genuine_gaps():
    """Guard against the fix over-matching: unrelated rows must still be excluded."""
    out = build_roadmap(
        {"priority_queue": [_strategy_row("/guides/alpha", "alpha topic")]},
        {"target_url_tree": [_arch_node("/blog/beta", "beta topic")]},
        {},
    )
    assert not (out.get("pages") or out.get("roadmap"))
    reasons = {e.get("source_pack") for e in out.get("excluded") or []}
    assert reasons == {"strategy", "architecture"}


# --- Phase 10 targeting: "/" and parent hubs claimed every planned page -----------

def test_homepage_does_not_own_every_intent():
    """"/" is a substring of every URL. Substring matching made the homepage the
    existing owner of every planned page, retargeting briefs/on-page/publish at "/"."""
    hit = find_existing_intent(
        "local seo pricing",
        content_audit={},
        website={"sample_urls": ["https://ex.com/"]},
        suggested_url="/blog/local-seo-pricing",
    )
    assert hit is None


def test_parent_hub_does_not_own_child_page():
    """/blog is not the owner of /blog/local-seo-pricing — treating it as one collapsed
    every new child page onto the hub."""
    hit = find_existing_intent(
        "local seo pricing",
        content_audit={},
        website={"sample_urls": ["https://ex.com/blog"]},
        suggested_url="/blog/local-seo-pricing",
    )
    assert hit is None


def test_exact_existing_page_is_still_matched():
    hit = find_existing_intent(
        "local seo pricing",
        content_audit={},
        website={"sample_urls": ["https://ex.com/blog/local-seo-pricing/"]},
        suggested_url="/blog/local-seo-pricing",
    )
    assert hit is not None
    assert hit["path"].rstrip("/") == "/blog/local-seo-pricing"


# --- Approving a blocked/empty phase marked it complete ---------------------------

@pytest.mark.asyncio
async def test_approving_phase_with_no_findings_is_rejected(db_session):
    """A blocked run produces no findings. Approving it used to flip the phase to
    "complete", telling downstream phases an empty pack was ready."""
    _user, _token = await make_user(db_session, "head_of_department", "hod-empty@test.com")
    user = (await db_session.execute(select(type(_user)).where(type(_user).id == _user.id))).scalar_one()

    client = Client(
        legal_name="Empty Co", display_name="Empty Co",
        primary_url="https://example.com", industry="Test", tier="B", status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await approve_phase_batch(
            db_session, user=user, client_id=client.id,
            agent_key="content_planning", action="approve",
        )
    assert exc.value.status_code == 400

    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    assert profile.content_planning_status == "not_started"
    assert not (
        await db_session.execute(
            select(FindingsLedger).where(FindingsLedger.client_id == client.id)
        )
    ).scalars().all()
