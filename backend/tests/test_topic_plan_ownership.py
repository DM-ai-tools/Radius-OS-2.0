"""Phase 6 must own its refinements instead of writing into Phase 5's column.

Phase 5 drafts topic_plan; Phase 6 stamps URL-map actions onto it. Phase 6 used
to write the stamped copy back into search_demand_summary — a column Phase 5
fully replaces on its next run, so the refinement was silently discarded. The
stamped plan already lives in site_architecture_summary; consumers just have to
read it from its owner.
"""

from __future__ import annotations

from app.services.create_topic import resolve_topic_plan

DRAFT = {
    "topic_ideas": [
        {"title": "Drain cleaning guide", "keyword": "drain cleaning"},
    ]
}

STAMPED = {
    "topic_ideas": [
        {
            "title": "Drain cleaning guide",
            "keyword": "drain cleaning",
            "url_action": "CREATE",
            "suggested_url": "/blog/drain-cleaning-guide/",
        }
    ]
}


def test_phase_6_stamped_plan_wins_over_the_phase_5_draft():
    plan = resolve_topic_plan(
        demand={"topic_plan": DRAFT}, architecture={"topic_plan": STAMPED}
    )
    assert plan["topic_ideas"][0]["url_action"] == "CREATE"


def test_falls_back_to_the_draft_before_phase_6_has_run():
    plan = resolve_topic_plan(demand={"topic_plan": DRAFT}, architecture={})
    assert plan == DRAFT


def test_falls_back_to_the_draft_when_phase_6_has_no_plan():
    plan = resolve_topic_plan(
        demand={"topic_plan": DRAFT}, architecture={"target_url_tree": []}
    )
    assert plan == DRAFT


def test_empty_phase_6_plan_does_not_shadow_a_real_draft():
    plan = resolve_topic_plan(
        demand={"topic_plan": DRAFT}, architecture={"topic_plan": {"topic_ideas": []}}
    )
    assert plan == DRAFT


def test_returns_empty_when_neither_phase_has_a_plan():
    assert resolve_topic_plan(demand={}, architecture={}) == {}


def test_handles_missing_and_non_dict_inputs():
    assert resolve_topic_plan(demand=None, architecture=None) == {}
    assert resolve_topic_plan(demand={"topic_plan": "junk"}, architecture={}) == {}


def test_resolved_topics_follow_the_resolved_plan():
    from app.services.create_topic import resolve_topics

    topics = resolve_topics(
        demand={"topics": [{"title": "stale"}], "topic_plan": DRAFT},
        architecture={"topics": [{"title": "stamped"}], "topic_plan": STAMPED},
    )
    assert topics[0]["title"] == "stamped"


def test_resolved_topics_fall_back_to_phase_5():
    from app.services.create_topic import resolve_topics

    topics = resolve_topics(demand={"topics": [{"title": "draft"}]}, architecture={})
    assert topics[0]["title"] == "draft"


def test_phase_6_agent_no_longer_writes_into_phase_5s_column():
    import pathlib

    src = pathlib.Path("app/agents/site_architecture.py").read_text(encoding="utf-8")
    assert "profile.search_demand_summary =" not in src
