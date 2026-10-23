"""Phase 5 consumes competitor page keywords: harvest at the gate, cluster after."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.agents.search_demand import (
    KEYWORD_DEPTH_PRESETS,
    filter_merged_to_seed_pool,
    max_seed_roots_for_pool_target,
    stored_competitor_page_intel,
)
from app.services.competitor_keyword_intel import (
    merge_competitor_rows_into_cluster_input,
    refresh_gap_flags,
)

COMPETITOR_ROW = {
    "keyword": "drain cleaning",
    "volume": 720,
    "source": "competitor_page",
    "match_class": "broad",
    "intent": "commercial",
    "funnel": "mid",
    "gap_flag": True,
    "competitor_domain": "rival.example",
    "from_competitor_page": True,
}


def _client():
    return SimpleNamespace(
        id=uuid4(),
        display_name="Northside Plumbing",
        primary_url="https://northside.example",
        industry="plumbing",
    )


def _profile(**overrides):
    base = {
        "discovery_status": "complete",
        "tracking_status": "complete",
        "website_status": "complete",
        "competitor_status": "complete",
        "search_demand_status": "not_started",
        "seo_strategy_status": "not_started",
        "site_architecture_status": "not_started",
        "technical_seo_status": "not_started",
        "content_audit_status": "not_started",
        "commercial_scope": {
            "business_keywords": "drain cleaning",
            "products": "Drain Cleaning, Hydro Jetting",
        },
        "marketing_context": {},
        "competitive_landscape_summary": {"competitors": [{"name": "Rival", "url": "https://rival.example"}]},
        "website_situation_summary": {},
        "search_demand_summary": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_seed_pool_filter_keeps_competitor_keywords_after_metric_merge():
    """merge_keyword_metrics rebuilds rows and drops `source`, so identity has to
    come from the harvested keyword set, not the merged row."""
    merged = [
        {"keyword": "drain cleaning", "sources": ["ahrefs"], "gap_flag": False},
        {"keyword": "unrelated term", "sources": ["dataforseo"], "gap_flag": False},
    ]
    kept = filter_merged_to_seed_pool(
        merged,
        seed_kw_set={"blocked drain"},
        competitor_kw_set={"drain cleaning"},
    )
    assert [r["keyword"] for r in kept] == ["drain cleaning"]


def test_seed_pool_filter_keeps_seeded_and_gap_rows():
    merged = [
        {"keyword": "blocked drain", "gap_flag": False},
        {"keyword": "competitor gap term", "gap_flag": True},
        {"keyword": "noise", "gap_flag": False},
    ]
    kept = filter_merged_to_seed_pool(
        merged, seed_kw_set={"blocked drain"}, competitor_kw_set=set()
    )
    assert {r["keyword"] for r in kept} == {"blocked drain", "competitor gap term"}


def test_seed_pool_filter_is_a_no_op_without_a_seed_pool():
    merged = [{"keyword": "anything", "gap_flag": False}]
    assert filter_merged_to_seed_pool(
        merged, seed_kw_set=set(), competitor_kw_set=set()
    ) == merged


def test_depth_presets_raise_the_seed_root_cap():
    """Depth must move the seed-root cap, or picking 'Deep' changes nothing real."""
    focused = max_seed_roots_for_pool_target(1500)
    standard = max_seed_roots_for_pool_target(4500)
    deep = max_seed_roots_for_pool_target(8000)
    assert focused < standard < deep
    assert standard == 16
    assert deep == 24


def test_seed_root_cap_snaps_an_arbitrary_target_to_a_preset():
    assert max_seed_roots_for_pool_target(4400) == 16
    assert max_seed_roots_for_pool_target(9000) == 24
    assert max_seed_roots_for_pool_target(0) == 6


def test_depth_presets_are_offered_with_labels_and_targets():
    ids = {p["id"] for p in KEYWORD_DEPTH_PRESETS}
    assert ids == {"focused", "standard", "deep"}
    for preset in KEYWORD_DEPTH_PRESETS:
        assert preset["label"]
        assert preset["keyword_pool_target"] > 0
        assert preset["max_seed_roots"] > 0


def test_stored_intel_reads_back_harvested_keywords():
    intel = stored_competitor_page_intel(
        {
            "competitor_keyword_intel": {
                "keywords": [COMPETITOR_ROW],
                "snapshots": [{"domain": "rival.example"}],
                "stats": {"qualified": 1},
                "collected_at": "2026-09-18T00:00:00+00:00",
            }
        }
    )
    assert intel["keywords"] == [COMPETITOR_ROW]
    assert intel["snapshots"] == [{"domain": "rival.example"}]
    assert intel["collected_at"] == "2026-09-18T00:00:00+00:00"


def test_stored_intel_is_empty_when_nothing_harvested():
    assert stored_competitor_page_intel({})["keywords"] == []
    assert stored_competitor_page_intel({"competitor_keyword_intel": None})["snapshots"] == []


def test_stored_intel_ignores_malformed_rows():
    intel = stored_competitor_page_intel(
        {"competitor_keyword_intel": {"keywords": ["oops", COMPETITOR_ROW]}}
    )
    assert intel["keywords"] == [COMPETITOR_ROW]


def test_merge_appends_competitor_rows_not_already_clustered():
    cluster_input = [{"keyword": "blocked drain", "source": "semrush"}]
    merged = merge_competitor_rows_into_cluster_input(cluster_input, [COMPETITOR_ROW])
    assert [r["keyword"] for r in merged] == ["blocked drain", "drain cleaning"]


def test_merge_does_not_duplicate_an_existing_keyword():
    cluster_input = [{"keyword": "Drain Cleaning", "source": "semrush"}]
    merged = merge_competitor_rows_into_cluster_input(cluster_input, [COMPETITOR_ROW])
    assert len(merged) == 1
    assert merged[0]["source"] == "semrush"


def test_merge_leaves_input_untouched_when_no_competitor_rows():
    cluster_input = [{"keyword": "blocked drain"}]
    merged = merge_competitor_rows_into_cluster_input(cluster_input, [])
    assert merged == cluster_input


def test_refresh_gap_flags_marks_keywords_the_client_already_ranks_for():
    rows = refresh_gap_flags([dict(COMPETITOR_ROW)], {"drain cleaning": 7})
    assert rows[0]["gap_flag"] is False
    assert rows[0]["client_position"] == 7


def test_refresh_gap_flags_keeps_gap_when_client_is_absent():
    rows = refresh_gap_flags([dict(COMPETITOR_ROW)], {"something else": 3})
    assert rows[0]["gap_flag"] is True


async def test_service_selection_turn_is_fast_gate_without_harvest():
    """Stage 1 only shows the service card — no competitor crawl/harvest."""
    from app.agents.search_demand import run_search_demand

    profile = _profile()
    harvest = AsyncMock(
        return_value={
            "keywords": [COMPETITOR_ROW],
            "snapshots": [{"name": "Rival", "domain": "rival.example", "pages": []}],
            "stats": {"candidates": 12, "qualified": 1, "error": None},
        }
    )
    trees = AsyncMock(return_value=[])

    with (
        patch("app.agents.search_demand.get_profile", AsyncMock(return_value=profile)),
        patch("app.agents.search_demand.load_skill", return_value=""),
        patch("app.agents.search_demand.load_skill_file", return_value=""),
        patch(
            "app.agents.search_demand._load_cdd_fields",
            AsyncMock(
                return_value={
                    "business_keywords": "drain cleaning",
                    "products": "Drain Cleaning, Hydro Jetting",
                }
            ),
        ),
        patch(
            "app.agents.search_demand.resolve_competitors",
            AsyncMock(
                return_value=[
                    {
                        "name": "Rival",
                        "url": "https://rival.example",
                        "domain": "rival.example",
                    }
                ]
            ),
        ),
        patch(
            "app.services.service_prioritization.discover_competitor_service_trees",
            trees,
        ),
        patch(
            "app.services.competitor_keyword_intel.collect_competitor_page_keywords",
            harvest,
        ),
    ):
        events = await run_search_demand(
            AsyncMock(),
            client=_client(),
            session_id=uuid4(),
            user_id=uuid4(),
            message="Run keyword research",
        )

    harvest.assert_not_awaited()
    trees.assert_not_awaited()
    cards = [
        e["payload"] for e in events if e.get("type") == "structured_card"
    ]
    gate = next(c for c in cards if c.get("card_type") == "service_prioritization")
    assert gate["competitor_page_keywords"] == []
    assert gate["competitor_page_stats"]["deferred"] is True
    assert profile.search_demand_status == "awaiting_service_selection"
    assert "competitor_keyword_intel" not in (profile.commercial_scope or {})


def test_scope_competitor_keywords_to_selected_services():
    from app.agents.search_demand import scope_competitor_keywords_to_services

    rows = [
        COMPETITOR_ROW,
        {
            "keyword": "seo audit brisbane",
            "volume": 100,
            "source": "competitor_page",
            "from_competitor_page": True,
        },
        {"keyword": "", "volume": 1},
    ]
    kept = scope_competitor_keywords_to_services(
        rows, ["Drain Cleaning", "Hydro Jetting"]
    )
    assert [r["keyword"] for r in kept] == ["drain cleaning"]


def test_scope_competitor_keywords_keeps_all_without_services():
    from app.agents.search_demand import scope_competitor_keywords_to_services

    rows = [COMPETITOR_ROW, {"keyword": "anything else", "volume": 10}]
    assert scope_competitor_keywords_to_services(rows, []) == rows
