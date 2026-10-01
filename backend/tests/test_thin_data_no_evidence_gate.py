"""A client with no services/CDD/themes must not get an ungated keyword set."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services.keyword_relevance import (
    build_relevance_context,
    evaluate_keyword,
    filter_relevant_keywords,
)


def thin_ctx(seeds=None):
    return build_relevance_context(
        services=[],
        cdd_keywords=[],
        page_terms=[],
        themes=[],
        seeds=seeds or ["acme widgets"],
        brand_name="Acme",
        domain="acme.example",
    )


def test_no_evidence_no_longer_passes_arbitrary_keywords():
    keep, reason, _ = evaluate_keyword("anything goes here", thin_ctx())
    assert keep is False
    assert reason == "no_business_evidence"


def test_seed_shell_itself_is_kept_but_unverified():
    keep, reason, _ = evaluate_keyword(
        "acme widgets", thin_ctx(), match_class="exact", seed="acme widgets"
    )
    assert keep is True
    assert reason == "relevance_unverified"


def test_a_seed_in_the_context_is_kept_even_without_seed_lineage():
    keep, reason, _ = evaluate_keyword("acme widgets", thin_ctx())
    assert keep is True
    assert reason == "relevance_unverified"


def test_expansions_of_a_seed_are_not_kept_without_evidence():
    """Only the seed shell survives — its expansions are unverifiable."""
    keep, _reason, _ = evaluate_keyword(
        "acme widgets pricing guide",
        thin_ctx(),
        match_class="phrase",
        seed="acme widgets",
    )
    assert keep is False


def test_filter_marks_unverified_rows_and_blocks_commercial_targeting():
    rows = [
        {"keyword": "acme widgets", "match_class": "exact", "seed": "acme widgets"},
        {"keyword": "unrelated thing", "match_class": "broad", "seed": "acme widgets"},
    ]
    kept, excluded, _audit = filter_relevant_keywords(rows, thin_ctx())
    assert [r["keyword"] for r in kept] == ["acme widgets"]
    assert kept[0]["relevance_unverified"] is True
    assert kept[0]["commercial_target_eligible"] is False
    assert [r["keyword"] for r in excluded] == ["unrelated thing"]


def test_brief_cluster_members_skip_unverified_keywords():
    from app.services.content_brief import cluster_member_keywords

    members = cluster_member_keywords(
        {
            "primary_keyword": "acme widgets",
            "relevance_unverified": True,
            "keywords": [{"keyword": "acme widgets blue"}],
        }
    )
    assert members == []


def test_brief_cluster_members_keep_verified_keywords():
    from app.services.content_brief import cluster_member_keywords

    members = cluster_member_keywords(
        {
            "primary_keyword": "teeth whitening",
            "keywords": [{"keyword": "teeth whitening cost"}],
        }
    )
    assert "teeth whitening" in members
    assert "teeth whitening cost" in members


# --- agent level -----------------------------------------------------------


def _client():
    return SimpleNamespace(
        id=uuid4(),
        display_name="Acme",
        primary_url="https://acme.example",
        industry="widgets",
    )


def _thin_profile():
    return SimpleNamespace(
        discovery_status="complete",
        tracking_status="complete",
        website_status="complete",
        competitor_status="complete",
        search_demand_status="not_started",
        seo_strategy_status="not_started",
        site_architecture_status="not_started",
        technical_seo_status="not_started",
        content_audit_status="not_started",
        # Selection confirmed, but the client profile carries no services,
        # no CDD keywords and no website themes.
        commercial_scope={
            "service_prioritization": {
                "confirmed_at": "2026-09-18T00:00:00+00:00",
                "selected_service_ids": ["unknown"],
            }
        },
        marketing_context={},
        competitive_landscape_summary={},
        website_situation_summary={},
        search_demand_summary={},
    )


async def test_thin_data_client_is_blocked_before_any_keyword_expansion():
    from app.agents.search_demand import run_search_demand

    profile = _thin_profile()
    seeding = AsyncMock()

    with (
        patch("app.agents.search_demand.get_profile", AsyncMock(return_value=profile)),
        patch("app.agents.search_demand.load_skill", return_value=""),
        patch("app.agents.search_demand.load_skill_file", return_value=""),
        patch("app.agents.search_demand._load_cdd_fields", AsyncMock(return_value={})),
        patch(
            "app.agents.search_demand.resolve_competitors", AsyncMock(return_value=[])
        ),
        patch("app.agents.search_demand.run_multi_mode_seeding", seeding),
    ):
        events = await run_search_demand(
            AsyncMock(),
            client=_client(),
            session_id=uuid4(),
            user_id=uuid4(),
            message="Run keyword research",
        )

    seeding.assert_not_awaited()
    blocked = [
        e
        for e in events
        if (e.get("payload") or {}).get("event_type") == "agent_blocked"
    ]
    assert blocked, "a thin-data client must get a blocking card"
    reason = str(blocked[0]["payload"].get("reason") or "")
    assert "service" in reason.lower() or "cdd" in reason.lower()
    assert profile.search_demand_status != "complete"


async def test_thin_data_run_produces_no_expanded_keywords():
    from app.agents.search_demand import run_search_demand

    profile = _thin_profile()
    with (
        patch("app.agents.search_demand.get_profile", AsyncMock(return_value=profile)),
        patch("app.agents.search_demand.load_skill", return_value=""),
        patch("app.agents.search_demand.load_skill_file", return_value=""),
        patch("app.agents.search_demand._load_cdd_fields", AsyncMock(return_value={})),
        patch(
            "app.agents.search_demand.resolve_competitors", AsyncMock(return_value=[])
        ),
    ):
        events = await run_search_demand(
            AsyncMock(),
            client=_client(),
            session_id=uuid4(),
            user_id=uuid4(),
            message="Run keyword research",
        )

    cards = [e.get("payload") or {} for e in events if e.get("type") == "structured_card"]
    report = next(
        (c for c in cards if c.get("card_type") == "search_demand_report"), None
    )
    assert report is None, "no keyword report should be produced without evidence"


async def test_phase5_does_not_mutate_approved_phase3_website_pack():
    """Sitemap backfill must stay local — signed-off Phase 3 output is immutable."""
    from app.agents.search_demand import run_search_demand
    from app.services.site_sitemap import ensure_website_sitemap

    approved = {
        "primary_url": "https://acme.example",
        "signed_off_note": "operator approved",
        "crawl": {
            "pages_found": 2,
            "discovered_urls": [
                "https://acme.example/",
                "https://acme.example/services",
            ],
        },
    }
    # Prove the backfill would have added site_sitemap if written back.
    enriched = ensure_website_sitemap(dict(approved), primary_url="https://acme.example")
    assert enriched.get("site_sitemap"), "precondition: backfill rebuilds a sitemap"

    profile = _thin_profile()
    profile.website_situation_summary = approved
    # Exit immediately after the sitemap backfill (still past the write site).
    profile.discovery_status = "not_started"
    before = dict(approved)

    with (
        patch("app.agents.search_demand.get_profile", AsyncMock(return_value=profile)),
        patch("app.agents.search_demand.load_skill", return_value=""),
        patch("app.agents.search_demand.load_skill_file", return_value=""),
        patch("app.agents.search_demand._load_cdd_fields", AsyncMock(return_value={})),
    ):
        events = await run_search_demand(
            AsyncMock(),
            client=_client(),
            session_id=uuid4(),
            user_id=uuid4(),
            message="Run keyword research",
        )

    assert any(
        (e.get("payload") or {}).get("event_type") == "agent_blocked" for e in events
    )
    assert profile.website_situation_summary is approved
    assert profile.website_situation_summary == before
    assert "site_sitemap" not in profile.website_situation_summary


def test_unverified_clusters_never_reach_the_cdp():
    """The CDP cluster list feeds architecture, planning, briefs and publishing."""
    from app.services.keyword_clustering import clusters_for_cdp

    report = {
        "clusters": [
            {
                "name": "acme widgets",
                "primary_keyword": "acme widgets",
                "keywords": [
                    {"keyword": "acme widgets", "relevance_unverified": True},
                ],
            },
            {
                "name": "teeth whitening",
                "primary_keyword": "teeth whitening cost",
                "keywords": [{"keyword": "teeth whitening cost"}],
            },
        ]
    }
    names = [c["name"] for c in clusters_for_cdp(report)]
    assert names == ["teeth whitening"]


def test_partially_verified_cluster_still_reaches_the_cdp():
    from app.services.keyword_clustering import clusters_for_cdp

    report = {
        "clusters": [
            {
                "name": "mixed",
                "primary_keyword": "verified term",
                "keywords": [
                    {"keyword": "shell term", "relevance_unverified": True},
                    {"keyword": "verified term"},
                ],
            }
        ]
    }
    assert [c["name"] for c in clusters_for_cdp(report)] == ["mixed"]
