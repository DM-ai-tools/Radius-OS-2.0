"""Phase 6 must not read Phase 7's output.

site_architecture sits before content_strategy, but used to read
profile.seo_strategy_summary — empty on a first pass, populated on a re-run.
That made the URL tree depend on how many times the pipeline had been round-
tripped: run Phase 6 again after Phase 7 and you got a different site
architecture. Phase 9 already joins the strategy queue itself, so Phase 6 has
no reason to pre-bake it.
"""

from __future__ import annotations

import inspect

from app.services import site_architecture as sa
from app.services.agent_handoff import CONSUMES, PHASE_ORDER
from app.services.site_architecture import build_blueprint

DEMAND = {
    "clusters": [
        {
            "name": "drain cleaning",
            "primary_keyword": "drain cleaning melbourne",
            "keyword_count": 4,
            "total_volume": 900,
            "intent": "commercial",
        }
    ],
    "services": ["Drain Cleaning"],
    "competitor_names": ["Rival Plumbing"],
    "competitor_domains": ["rival.example"],
}

STRATEGY_PACK = {
    "core_topics": [{"name": "Drain care hub", "primary_keyword": "drain care"}],
    "priority_queue": [
        {"keyword": "emergency drain repair", "suggested_url": "/emergency-drain-repair/"},
        {"keyword": "blocked drain cost", "suggested_url": "/blocked-drain-cost/"},
    ],
    "competitor_names": ["Ghost Competitor"],
}


def _blueprint(**extra):
    return build_blueprint(
        client_name="Northside Plumbing",
        domain="northside.example",
        current_state={"urls_crawled": 0, "issues": []},
        demand=DEMAND,
        commercial={"products": "Drain Cleaning"},
        website={},
        **extra,
    )


def test_blueprint_no_longer_accepts_a_later_phases_pack():
    """Structural guarantee: the parameter is gone, so the cycle cannot return."""
    assert "seo_strategy" not in inspect.signature(build_blueprint).parameters


def test_url_tree_is_identical_whether_or_not_phase_7_has_run():
    first_pass = _blueprint()
    # A re-run after Phase 7 would previously have injected strategy nodes here.
    assert first_pass["target_url_tree"] == _blueprint()["target_url_tree"]


def test_strategy_queue_urls_do_not_leak_into_the_tree():
    tree = _blueprint()["target_url_tree"]
    urls = {str(n.get("url") or n.get("path") or "") for n in tree}
    assert not any("emergency-drain-repair" in u for u in urls)


def test_agent_does_not_read_the_strategy_summary():
    import pathlib

    src = pathlib.Path("app/agents/site_architecture.py").read_text(encoding="utf-8")
    assert "profile.seo_strategy_summary" not in src


def test_phase_6_still_precedes_phase_7_and_declares_only_earlier_phases():
    pos = {p: i for i, p in enumerate(PHASE_ORDER)}
    assert pos["site_architecture"] < pos["content_strategy"]
    for dep in CONSUMES["site_architecture"]:
        assert pos[dep] < pos["site_architecture"], f"{dep} runs after site_architecture"


def test_strategy_queue_helper_is_gone():
    """It existed only to serve the cycle."""
    assert not hasattr(sa, "_nodes_from_strategy_queue")


# --- Phase 7 contributes the nodes instead, in declared order ---------------


def test_merge_adds_strategy_pages_to_the_tree():
    from app.services.site_architecture import merge_strategy_nodes_into_tree

    arch = {
        "target_url_tree": [
            {"url": "/meta-ads", "parent": "/", "depth": 1, "page_type": "service"}
        ]
    }
    strategy = {
        "priority_queue": [
            {
                "keyword": "instagram ads guide",
                "title": "Instagram Ads guide",
                "suggested_url": "/blog/instagram-ads-guide/",
                "content_type": "blog",
            }
        ]
    }
    out = merge_strategy_nodes_into_tree(arch, strategy)
    urls = {str(n.get("url") or n.get("path") or "") for n in out["target_url_tree"]}
    assert any("instagram-ads-guide" in u for u in urls)
    assert out["strategy_nodes_added"] == 1


def test_merge_is_idempotent_and_never_overwrites_phase_6_mapping():
    from app.services.site_architecture import merge_strategy_nodes_into_tree

    arch = {
        "target_url_tree": [
            {"url": "/blog/instagram-ads-guide", "page_type": "article", "from_phase6": True}
        ]
    }
    strategy = {
        "priority_queue": [
            {"keyword": "instagram ads guide", "suggested_url": "/blog/instagram-ads-guide/"}
        ]
    }
    once = merge_strategy_nodes_into_tree(arch, strategy)
    twice = merge_strategy_nodes_into_tree(once, strategy)
    assert len(once["target_url_tree"]) == 1
    assert twice["target_url_tree"] == once["target_url_tree"]
    assert once["target_url_tree"][0]["from_phase6"] is True


def test_merge_does_not_mutate_the_input():
    from app.services.site_architecture import merge_strategy_nodes_into_tree

    arch = {"target_url_tree": [{"url": "/a"}]}
    merge_strategy_nodes_into_tree(
        arch, {"priority_queue": [{"keyword": "b", "suggested_url": "/b/"}]}
    )
    assert len(arch["target_url_tree"]) == 1


def test_merge_is_a_noop_without_a_queue():
    from app.services.site_architecture import merge_strategy_nodes_into_tree

    arch = {"target_url_tree": [{"url": "/a"}]}
    assert merge_strategy_nodes_into_tree(arch, {}) is arch


def test_phase_7_agent_contributes_nodes_to_the_architecture_tree():
    import pathlib

    src = pathlib.Path("app/agents/content_strategy.py").read_text(encoding="utf-8")
    assert "merge_strategy_nodes_into_tree" in src
