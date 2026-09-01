"""Phase 7–12 gates and slim-memory smoke checks."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.content_brief import generate_briefs
from app.services.content_planning import build_roadmap, ia_gate_ok, run_content_planning_plan
from app.services.content_production import planning_gate_ok
from app.services.create_topic import funnel_balance, funnel_balance_warnings, funnel_from_intent
from app.services.on_page_seo import production_gate_ok, selected_on_page_rows
from app.services.publishing import on_page_gate_ok
from app.services.technical_seo import _ia_notes
from app.services.memory_packs import (
    slim_technical_seo_memory,
    slim_content_audit_memory,
    slim_content_planning_memory,
    slim_publishing_memory,
)


def test_content_planning_ia_gate_blocks_without_tree():
    assert ia_gate_ok("not_started", {}) is False
    assert ia_gate_ok("complete", {}) is True
    assert ia_gate_ok("pending_signoff", {"target_url_tree": [{"path": "/services/"}]}) is True


@pytest.mark.asyncio
async def test_content_planning_blocked_without_ia():
    out = await run_content_planning_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        site_architecture_status="not_started",
        site_architecture={},
        seo_strategy={"priority_queue": [{"keyword": "seo"}]},
    )
    assert out.get("blocked") is True
    assert "Site Architecture" in str(out.get("reason") or "")


def test_downstream_gates():
    assert planning_gate_ok("complete", {"locked": True, "pages": [{"url": "/a"}]}) is True
    assert planning_gate_ok("complete", {}) is False
    assert planning_gate_ok("not_started", {"roadmap": [{"url": "/a"}]}) is True
    assert planning_gate_ok("not_started", {}) is False
    assert planning_gate_ok("complete", {"locked": False}) is False
    assert planning_gate_ok("not_started", {"locked": True, "pages": [{"url_n": "/a"}]}) is True

    ok, src = production_gate_ok("complete", {}, {})
    assert ok and src == "content_production"
    ok, src = production_gate_ok("not_started", {}, {"roadmap": [{"path": "/x"}]})
    assert ok and src == "content_planning_fallback"
    ok, _ = production_gate_ok("not_started", {}, {})
    assert not ok

    assert on_page_gate_ok("complete", {}) is True
    assert on_page_gate_ok("not_started", {"pages": [{"url": "/a"}]}) is True
    assert on_page_gate_ok("not_started", {}) is False


def test_slim_memory_packs_trim():
    tech = slim_technical_seo_memory(
        {
            "score": 72,
            "executive_summary": "x" * 500,
            "priority_backlog": [{"issue": f"i{i}"} for i in range(20)],
            "broken_links": {"broken_count": 3},
        }
    )
    assert tech.get("_memory_slim") is True
    assert len(tech.get("priority_backlog") or []) <= 12

    audit = slim_content_audit_memory(
        {"inventory": [{"url": f"/p{i}", "disposition": "keep"} for i in range(30)]}
    )
    assert audit.get("_memory_slim") is True
    assert len(audit.get("inventory") or []) <= 20

    pub = slim_publishing_memory(
        {
            "publish_queue": [{"url": f"https://ex.com/{i}", "status": "simulated"} for i in range(25)],
            "checklist": ["a", "b", "c"],
        }
    )
    assert pub.get("_memory_slim") is True
    assert "chat" in str(pub.get("note") or "").lower() or pub.get("note")

    plan = slim_content_planning_memory(
        {
            "locked": True,
            "pages": [{"url_n": "/a", "primary_keyword": "seo", "action": "create"}],
            "excluded": [{"url_n": "/b", "reason": "strategy_only", "source_pack": "strategy"}],
        }
    )
    assert plan.get("locked") is True
    assert plan.get("pages")
    assert plan.get("roadmap")


# --- Phase 6a -> 9: funnel tag must not be dropped on the roadmap join --------------
#
# content_strategy.build_priority_queue tags every row TOFU/MOFU/BOFU (Architecture
# v1.9 funnel mapping). build_roadmap joins that row against site_architecture and
# content_audit — it used to copy every field EXCEPT funnel, so by the time Phase 10
# read the roadmap row the tag was silently gone.

def test_build_roadmap_carries_funnel_from_strategy_row():
    report = build_roadmap(
        {
            "priority_queue": [
                {
                    "keyword": "seo pricing",
                    "title": "SEO Pricing Guide",
                    "url": "/seo-pricing",
                    "intent": "transactional",
                    "funnel": "BOFU",
                    "priority_tier": "quick_win",
                }
            ]
        },
        {"target_url_tree": [{"url": "/seo-pricing", "path": "/seo-pricing", "type": "article"}]},
        {},
        client_name="Acme",
        primary_url="https://acme.example",
    )
    pages = report["pages"]
    assert len(pages) == 1
    assert pages[0]["funnel"] == "BOFU"


def test_build_roadmap_falls_back_to_architecture_funnel():
    report = build_roadmap(
        {"priority_queue": [{"keyword": "seo basics", "url": "/seo-basics"}]},
        {
            "target_url_tree": [
                {"url": "/seo-basics", "path": "/seo-basics", "type": "article", "funnel": "TOFU"}
            ]
        },
        {},
        client_name="Acme",
        primary_url="https://acme.example",
    )
    assert report["pages"][0]["funnel"] == "TOFU"


# --- Phase 6a -> 9: business_fit, angle, from_phase5_topic, competitor_domains -----
# also used to drop silently on the same join. business_fit was added to Phase 6a's
# priority_queue specifically so it would flow downstream; competitor_domains is
# read by Phase 10's brief pipeline (content_brief._rule_brief) under that exact key,
# but Phase 6a emits it as "beat_competitors" — both the rename and the Phase 9 carry
# were missing, so Phase 10's competitive analysis silently ran on an empty list.

def test_build_roadmap_carries_content_from_phase5_and_6a():
    report = build_roadmap(
        {
            "priority_queue": [
                {
                    "keyword": "seo pricing",
                    "url": "/seo-pricing",
                    "angle": "listicle",
                    "business_fit": {"score": 82, "reason": "core_service"},
                    "from_phase5_topic": True,
                    "beat_competitors": ["rival-a.com", "rival-b.com"],
                }
            ]
        },
        {"target_url_tree": [{"url": "/seo-pricing", "path": "/seo-pricing", "type": "article"}]},
        {},
        client_name="Acme",
        primary_url="https://acme.example",
    )
    row = report["pages"][0]
    assert row["angle"] == "listicle"
    assert row["business_fit"] == {"score": 82, "reason": "core_service"}
    assert row["from_phase5_topic"] is True
    assert row["competitor_domains"] == ["rival-a.com", "rival-b.com"]


def test_build_roadmap_from_phase5_topic_defaults_false():
    report = build_roadmap(
        {"priority_queue": [{"keyword": "seo basics", "url": "/seo-basics"}]},
        {"target_url_tree": [{"url": "/seo-basics", "path": "/seo-basics", "type": "article"}]},
        {},
        client_name="Acme",
        primary_url="https://acme.example",
    )
    row = report["pages"][0]
    assert row["from_phase5_topic"] is False
    assert row["competitor_domains"] == []


# --- Phase 10: every generated brief must carry a real funnel tag, plan reviewed ----
# for balance (v1.9 gap). MoFu is the layer most often missing — surface it by name.

def test_funnel_from_intent_maps_stages():
    assert funnel_from_intent("transactional", "seo pricing") == "BOFU"
    assert funnel_from_intent("commercial", "best seo tools") == "MOFU"
    assert funnel_from_intent("informational", "what is seo") == "TOFU"
    assert funnel_from_intent(None, "") == "TOFU"


def test_funnel_balance_warnings_flags_missing_mofu_by_name():
    counts = {"TOFU": 3, "MOFU": 0, "BOFU": 1}
    warnings = funnel_balance_warnings(counts)
    assert any("MoFu" in w for w in warnings)
    assert funnel_balance_warnings({"TOFU": 1, "MOFU": 1, "BOFU": 1}) == []
    assert funnel_balance_warnings({"TOFU": 0, "MOFU": 0, "BOFU": 0}) == []


@pytest.mark.asyncio
async def test_generate_briefs_guarantees_funnel_tag_and_reports_balance():
    """Roadmap row has no funnel (as Phase 9 used to emit before the fix) — the brief
    must still get a grounded TOFU/MOFU/BOFU tag, and the batch must report balance."""
    serp = {
        "organic": [{"position": 1, "title": "SEO pricing", "url": "https://a.com/x", "domain": "a.com"}],
        "people_also_ask": [],
        "featured_snippet": None,
        "item_types": ["organic"],
        "validated": True,
    }
    with patch("app.services.content_brief.dataforseo.serp_advanced", new=AsyncMock(return_value=(serp, []))):
        with patch("app.services.content_brief.synthesize_json", new=AsyncMock(return_value={})):
            pack = await generate_briefs(
                client_name="Acme",
                industry="marketing",
                seo_strategy={
                    "priority_queue": [
                        {
                            "keyword": "seo pricing",
                            "title": "SEO Pricing Guide",
                            "suggested_url": "/seo-pricing",
                            "intent": "transactional",
                        }
                    ]
                },
                site_architecture={
                    "target_url_tree": [
                        {"path": "/seo-pricing", "parent": "/", "type": "article", "keyword": "seo pricing"}
                    ]
                },
                content_audit={"inventory": []},
                roadmap=[
                    {
                        "url": "/seo-pricing",
                        "path": "/seo-pricing",
                        "keyword": "seo pricing",
                        "action": "create",
                        "wave": 1,
                        # deliberately no "funnel" key — simulates the pre-fix Phase 9 drop
                    }
                ],
            )
    assert pack["brief_count"] == 1
    brief = pack["briefs"][0]
    assert brief["funnel"] == "BOFU"  # derived from intent=transactional, no invented data
    assert "funnel_balance" in pack
    assert pack["funnel_balance"]["BOFU"] == 1
    assert any("MoFu" in w for w in pack["funnel_balance_warnings"])


def test_rule_brief_carries_angle_business_fit_and_reaches_competitor_pipeline():
    """item["competitor_domains"] (carried onto the Phase 9 row from Phase 6a's
    beat_competitors) must reach the cluster_stub fed into run_cluster_page_pipeline
    — before the fix, Phase 9 never carried the field, so this was always []."""
    from app.services.content_brief import _rule_brief, preflight

    item = {
        "keyword": "seo pricing",
        "url": "/seo-pricing",
        "path": "/seo-pricing",
        "action": "create",
        "angle": "comparison",
        "business_fit": {"score": 90, "reason": "core_service"},
        "competitor_domains": ["rival-a.com"],
    }
    pre = preflight(
        keyword="seo pricing",
        suggested_url="/seo-pricing",
        action="create",
        content_audit={"inventory": []},
        website={},
        site_architecture={
            "target_url_tree": [{"path": "/seo-pricing", "parent": "/", "type": "article"}]
        },
        roadmap_row=item,
        industry="marketing",
        marketing={},
        client_name="Acme",
    )
    serp = {"validated": False, "organic": [], "people_also_ask": [], "coverage_floor": []}

    with patch(
        "app.services.content_pipeline.run_cluster_page_pipeline",
        wraps=__import__("app.services.content_pipeline", fromlist=["run_cluster_page_pipeline"]).run_cluster_page_pipeline,
    ) as spy:
        brief = _rule_brief(
            item=item,
            pre=pre,
            serp=serp,
            client_name="Acme",
            audience=None,
            secondary=[],
            industry="marketing",
        )

    called_cluster = spy.call_args[0][0]
    assert called_cluster["competitor_domains"] == ["rival-a.com"]
    assert brief["angle"] == "comparison"
    assert brief["business_fit"] == {"score": 90, "reason": "core_service"}


# --- Phase 7: technical audit must ground its backlog in Phase 6b's IA pack ---------

def test_technical_seo_ia_notes_ground_in_site_architecture():
    actions, notes = _ia_notes(
        {
            "redirect_map": [{"from": "/old", "to": "/new", "status": 301}],
            "current_state": {"depth_4_plus": 5, "orphans": 2},
            "robots_facet_policy": "Block ?sort= and ?filter= params",
            "handoffs": [{"receiving": "Technical SEO", "item": "Fix faceted nav indexing"}],
        }
    )
    types = {a["type"] for a in actions}
    assert "redirect" in types
    assert "depth" in types
    assert "robots" in types
    assert any("5 URLs at click depth 4+" in n for n in notes)
    assert any("Fix faceted nav indexing" in n for n in notes)


def test_technical_seo_ia_notes_empty_without_architecture():
    actions, notes = _ia_notes({})
    assert actions == []
    assert notes == []


# --- Phase 11: on-page package is the single selected topic, never the full queue ---

def test_selected_on_page_rows_grounds_in_single_draft_and_keeps_funnel():
    production = {
        "briefs": [
            {"keyword": "seo pricing", "url": "/seo-pricing", "funnel": "BOFU", "title": "SEO Pricing"},
            {"keyword": "seo basics", "url": "/seo-basics", "funnel": "TOFU", "title": "SEO Basics"},
        ],
        "drafts": [{"keyword": "seo pricing", "url": "/seo-pricing", "title": "SEO Pricing Guide"}],
    }
    rows = selected_on_page_rows(production)
    assert len(rows) == 1
    assert rows[0]["keyword"] == "seo pricing"
    assert rows[0]["funnel"] == "BOFU"


def test_selected_on_page_rows_empty_without_selection():
    assert selected_on_page_rows({"briefs": [{"keyword": "x"}], "drafts": []}) == []
