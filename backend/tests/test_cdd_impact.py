"""Which phases a CDD change invalidates, directly and downstream."""

from __future__ import annotations

from app.services.agent_handoff import CONSUMES, PHASE_ORDER
from app.services.cdd_diff import diff_cdd
from app.services.cdd_impact import (
    CDD_FIELD_IMPACT,
    DEPENDENTS,
    StalenessState,
    directly_impacted_phases,
    evaluate_staleness,
    expand_downstream,
)


# --- the dependency graph ---------------------------------------------------


def test_dependents_is_the_inverse_of_consumes():
    for agent, deps in CONSUMES.items():
        for dep in deps:
            assert agent in DEPENDENTS[dep], f"{agent} consumes {dep} but is not its dependent"


def test_every_impact_target_is_a_real_phase():
    for field_key, phases in CDD_FIELD_IMPACT.items():
        for phase in phases:
            assert phase in PHASE_ORDER, f"{field_key} -> unknown phase {phase}"


def test_competitors_field_impacts_nothing_because_phase_4_owns_discovery():
    assert CDD_FIELD_IMPACT.get("competitors") == ()


# --- direct impact ----------------------------------------------------------


def test_business_keywords_change_hits_search_demand():
    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    assert "search_demand" in directly_impacted_phases(diff)


def test_industry_change_hits_competitor_and_search_demand():
    diff = diff_cdd({"inferred_industry": "Dental"}, {"inferred_industry": "Bakery"})
    impacted = directly_impacted_phases(diff)
    assert "competitor_market_agent" in impacted
    assert "search_demand" in impacted


def test_cosmetic_field_change_impacts_no_phase():
    diff = diff_cdd(
        {"public_reviews_summary": "good"}, {"public_reviews_summary": "great"}
    )
    assert directly_impacted_phases(diff) == set()


def test_removed_field_impacts_the_same_phases_as_a_change():
    diff = diff_cdd({"geographic_focus": "Melbourne"}, {})
    assert "search_demand" in directly_impacted_phases(diff)


def test_unknown_field_impacts_nothing():
    diff = diff_cdd({"some_new_field": "a"}, {"some_new_field": "b"})
    assert directly_impacted_phases(diff) == set()


# --- downstream propagation -------------------------------------------------


def test_downstream_of_search_demand_includes_the_whole_tail():
    downstream = expand_downstream({"search_demand"})
    assert "site_architecture" in downstream
    assert "content_strategy" in downstream
    assert "publishing" in downstream, "staleness must reach the end of the chain"


def test_downstream_includes_the_seed_phase_itself():
    assert "search_demand" in expand_downstream({"search_demand"})


def test_publishing_has_no_downstream_beyond_itself():
    assert expand_downstream({"publishing"}) == {"publishing"}


def test_discovery_change_reaches_everything():
    assert expand_downstream({"discovery_agent"}) >= set(PHASE_ORDER) - {"discovery_agent"} | {
        "discovery_agent"
    }


# --- staleness evaluation ---------------------------------------------------


def _states(**overrides):
    """phase -> recorded cdd version id."""
    base = {phase: "v1" for phase in PHASE_ORDER}
    base.update(overrides)
    return base


def test_phase_on_the_latest_version_is_fresh():
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v1",
        diff_by_version={},
    )
    assert report["search_demand"].state is StalenessState.FRESH


def test_completed_phase_on_an_old_impacted_version_is_stale_direct():
    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    entry = report["search_demand"]
    assert entry.state is StalenessState.STALE_DIRECT
    assert "business_keywords" in entry.fields


def test_downstream_phase_of_a_stale_phase_is_stale_upstream():
    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete", "site_architecture": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    entry = report["site_architecture"]
    assert entry.state is StalenessState.STALE_UPSTREAM
    assert "search_demand" in entry.upstream


def test_not_started_phase_is_never_stale():
    """Pending phases just pick up the latest CDD — nothing to approve."""
    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions={"search_demand": None},
        phase_statuses={"search_demand": "not_started"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    assert report["search_demand"].state is StalenessState.FRESH


def test_in_progress_phase_is_not_marked_stale_while_running():
    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "in_progress"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    assert report["search_demand"].state is StalenessState.RUNNING


def test_a_phase_that_finishes_on_an_old_version_becomes_stale():
    """The in-progress rule: let it finish, then mark it."""
    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(search_demand="v1"),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    assert report["search_demand"].state is StalenessState.STALE_DIRECT


def test_dismissal_clears_staleness_for_that_version():
    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
        dismissed_through={"search_demand": "v2"},
    )
    assert report["search_demand"].state is StalenessState.DISMISSED


def test_a_newer_version_reraises_a_dismissed_staleness():
    d2 = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    d3 = diff_cdd({"business_keywords": "seo, ppc"}, {"business_keywords": "seo, ppc, cro"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v3",
        diff_by_version={"v2": d2, "v3": d3},
        dismissed_through={"search_demand": "v2"},
    )
    assert report["search_demand"].state is StalenessState.STALE_DIRECT


def test_changes_accumulate_across_several_versions():
    """A phase two versions behind is judged on every diff since."""
    d2 = diff_cdd({"positioning": "a"}, {"positioning": "b"})  # no phase impact
    d3 = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v3",
        diff_by_version={"v2": d2, "v3": d3},
    )
    assert report["search_demand"].state is StalenessState.STALE_DIRECT


def test_unimpacted_completed_phase_on_an_old_version_stays_fresh():
    diff = diff_cdd({"public_reviews_summary": "a"}, {"public_reviews_summary": "b"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    assert report["search_demand"].state is StalenessState.FRESH


# --- gating -----------------------------------------------------------------


def test_blocking_upstream_reports_stale_dependencies():
    from app.services.cdd_impact import blocking_stale_upstream

    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete", "site_architecture": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    assert blocking_stale_upstream("site_architecture", report) == ["search_demand"]


def test_a_phase_is_not_blocked_by_its_own_staleness():
    """Redoing the stale phase itself is the fix — it must not block itself."""
    from app.services.cdd_impact import blocking_stale_upstream

    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
    )
    assert blocking_stale_upstream("search_demand", report) == []


def test_dismissed_upstream_does_not_block():
    from app.services.cdd_impact import blocking_stale_upstream

    diff = diff_cdd({"business_keywords": "seo"}, {"business_keywords": "seo, ppc"})
    report = evaluate_staleness(
        phase_versions=_states(),
        phase_statuses={"search_demand": "complete", "site_architecture": "complete"},
        latest_version_id="v2",
        diff_by_version={"v2": diff},
        dismissed_through={"search_demand": "v2"},
    )
    assert blocking_stale_upstream("site_architecture", report) == []
