from app.services.chat_revisions import (
    apply_ops_to_summary,
    looks_like_revision,
    parse_revision_ops,
    resolve_target_agent,
)


def test_run_commands_are_not_revisions():
    assert not looks_like_revision("Run keyword research / search demand")
    assert not looks_like_revision("Refresh competitor scan")
    assert not looks_like_revision("Approve Search Demand")


def test_add_remove_keyword_is_revision():
    assert looks_like_revision("add keyword local seo melbourne")
    assert looks_like_revision('remove keyword "seo 2024" from the report')
    assert looks_like_revision("add 'google ads management' to the keyword list")


def test_parse_add_and_remove():
    ops = parse_revision_ops('add keyword local seo melbourne. remove keyword seo 2024')
    kinds = {(o["op"], o["value"].lower()) for o in ops}
    assert ("add", "local seo melbourne") in kinds
    assert ("remove", "seo 2024") in kinds


def test_apply_add_remove_on_search_demand():
    summary = {
        "best_opportunities": [{"keyword": "seo", "volume": 1000}],
        "topics": [{"primary_keyword": "seo", "pillar": "SEO"}],
    }
    out = apply_ops_to_summary(
        summary,
        [
            {"op": "add", "value": "local seo melbourne"},
            {"op": "remove", "value": "seo"},
        ],
        agent_key="search_demand",
    )
    kws = [str(r.get("keyword") or r.get("primary_keyword") or r).lower() for r in out["best_opportunities"]]
    assert "local seo melbourne" in kws
    assert "seo" not in kws
    assert "added local seo melbourne" in out["_revision_applied"]
    assert any("removed" in x for x in out["_revision_applied"])


def test_apply_add_on_strategy_queue():
    summary = {"priority_queue": [{"keyword": "brand seo", "title": "Brand SEO"}]}
    out = apply_ops_to_summary(
        summary,
        [{"op": "add", "value": "ppc management melbourne"}],
        agent_key="content_strategy",
    )
    kws = [str(r.get("keyword") or "").lower() for r in out["priority_queue"]]
    assert "ppc management melbourne" in kws


def test_resolve_agent_from_message_and_fallback():
    assert (
        resolve_target_agent(
            "add keyword x to search demand",
            active_agent_key="content_strategy",
            statuses={"search_demand_status": "complete"},
        )
        == "search_demand"
    )
    assert (
        resolve_target_agent(
            "remove keyword x",
            active_agent_key="search_demand",
            statuses={"search_demand_status": "pending_signoff"},
        )
        == "search_demand"
    )
