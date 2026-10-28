"""Phase 9 roadmap merge — join/lock, no judgement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.content_planning import build_roadmap, url_n, validate_report


def _strategy(*rows: dict) -> dict:
    return {"priority_queue": list(rows)}


def _arch(tree: list[dict], owners: list[dict] | None = None) -> dict:
    pack: dict = {"target_url_tree": tree}
    if owners:
        pack["cluster_owners"] = owners
    return pack


def _audit(*rows: dict) -> dict:
    return {"dispositions": list(rows)}


def test_url_map_page_plan_keeps_the_decided_url():
    from app.services.content_strategy import work_rows_from_url_map

    rows = work_rows_from_url_map(
        [
            {
                "l1_category": "SEO",
                "page_job": "optimize_service_page",
                "primary_keyword": "search engine optimisation",
                "secondary_keywords": ["seo"],
                "current_url": "/seo",
                "action": "OPTIMIZE_EXISTING",
                "search_volume": 8100,
            },
            {
                "l1_category": "SEO",
                "page_job": "draft_guide",
                "primary_keyword": "what is seo",
                "page_type": "Guide",
                "proposed_url": "/what-is-seo",
                "action": "CREATE",
                "search_volume": 1900,
            },
        ],
        "clicktrends.com.au",
    )
    by_keyword = {row["primary_keyword"]: row for row in rows}
    assert by_keyword["search engine optimisation"]["action"] == "optimize"
    assert by_keyword["search engine optimisation"]["url"] == "/seo"
    assert by_keyword["search engine optimisation"]["secondary_keywords"] == ["seo"]
    assert by_keyword["what is seo"]["action"] == "create"
    assert by_keyword["what is seo"]["url"] == "/what-is-seo"
    assert by_keyword["search engine optimisation"]["title"] == "A Clear Path To Search Engine Optimisation"
    assert by_keyword["what is seo"]["title"] == "What Is SEO? A Clear Guide"


def test_page_plan_tree_uses_api_page_types():
    from app.schemas.client import SiteArchitectureSummary
    from app.services.content_strategy import attach_page_plan

    attached = attach_page_plan(
        {
            "url_map_report": {
                "final_url_map": [
                    {
                        "page_job": "optimize_service_page",
                        "page_type": "Hub Page",
                        "primary_keyword": "search engine optimisation",
                        "current_url": "/seo",
                        "action": "OPTIMIZE_EXISTING",
                    },
                    {
                        "page_job": "draft_guide",
                        "page_type": "Guide",
                        "primary_keyword": "what is seo",
                        "proposed_url": "/what-is-seo",
                        "action": "CREATE",
                    },
                    {
                        "page_job": "related_service_page",
                        "page_type": "Collection",
                        "primary_keyword": "instagram marketing services",
                        "proposed_url": "/social-media-marketing/instagram-marketing-services",
                        "action": "CREATE",
                    },
                ]
            }
        }
    )
    SiteArchitectureSummary.model_validate(attached)
    kinds = {node["url"]: node["page_type"] for node in attached["target_url_tree"]}
    assert kinds["/seo"] == "hub"
    assert kinds["/what-is-seo"] == "guide"
    assert kinds["/social-media-marketing/instagram-marketing-services"] == "spoke"


def test_url_tree_is_the_url_map_nested_under_each_hub():
    from app.schemas.client import SiteArchitectureSummary
    from app.services.content_strategy import attach_page_plan

    attached = attach_page_plan(
        {
            "target_url_tree": [
                {"url": "/seo/ai-seo", "path": "/seo/ai-seo", "page_type": "subservice", "title": "AI SEO", "depth": 2, "parent": "/seo"},
                {"url": "/search-engine-optimisation-company", "path": "/search-engine-optimisation-company", "page_type": "hub", "title": "seo company", "depth": 1, "parent": "/"},
            ],
            "url_map_report": {
                "final_url_map": [
                    {
                        "page_type": "Hub Page",
                        "l1_category": "SEO",
                        "primary_keyword": "seo",
                        "current_url": "/seo",
                    },
                    {
                        "page_type": "Guide",
                        "l1_category": "SEO",
                        "primary_keyword": "what is seo",
                        "proposed_url": "/what-is-seo",
                    },
                    {
                        "page_type": "Hub Page",
                        "l1_category": "Email Marketing",
                        "primary_keyword": "email marketing",
                        "current_url": "/email-marketing",
                    },
                ]
            },
        }
    )
    SiteArchitectureSummary.model_validate(attached)
    by_url = {node["url"]: node for node in attached["target_url_tree"]}
    assert set(by_url) == {"/seo", "/what-is-seo", "/email-marketing"}
    assert by_url["/what-is-seo"]["parent"] == "/seo"
    assert by_url["/what-is-seo"]["title"] == "what is seo"
    assert by_url["/seo"]["title"] == "SEO"
    assert by_url["/seo"]["parent"] == "/"
    owners = {row["canonical_owner_url"]: row for row in attached["cluster_ownership"]}
    assert set(owners) == set(by_url)
    assert owners["/what-is-seo"]["cluster"] == "what is seo"
    assert owners["/seo"]["cluster"] == "seo"
    assert attached["cluster_owners"] == attached["cluster_ownership"]


def test_proposed_page_joins_the_roadmap_at_that_url():
    report = build_roadmap(
        _strategy(
            {
                "url": "/what-is-seo",
                "title": "What Is Seo",
                "keyword": "what is seo",
                "action": "create",
                "priority_tier": "big_bet",
            }
        ),
        {
            "target_url_tree": [],
            "url_map_report": {
                "final_url_map": [
                    {
                        "page_job": "draft_guide",
                        "primary_keyword": "what is seo",
                        "action": "CREATE",
                        "proposed_url": "/what-is-seo",
                        "selected_url": "/what-is-seo",
                    }
                ]
            },
        },
        {},
    )
    assert any(page.get("url_n") == "/what-is-seo" for page in report["pages"])


def test_url_n_normalises_path():
    assert url_n("https://ex.com/Blog/Share-Of-Search/") == "/blog/share-of-search"
    assert url_n("/services") == "/services"
    assert url_n("/") == "/"


def test_missing_audit_is_create_and_locks():
    report = build_roadmap(
        _strategy(
            {
                "url": "/blog/share-of-search",
                "title": "Share of search guide",
                "keyword": "share of search",
                "cluster": "measurement",
                "priority_tier": "quick_win",
            }
        ),
        _arch(
            [
                {
                    "url": "/blog/share-of-search",
                    "parent": "/blog",
                    "depth": 2,
                    "page_type": "article",
                    "breadcrumb": ["Home", "Blog", "Share of search"],
                    "indexable": True,
                }
            ],
            [{"cluster": "measurement", "owner_url": "/blog/share-of-search"}],
        ),
        {},
    )
    assert report["locked"] is True
    assert report["pages"][0]["action"] == "create"
    assert report["pages"][0]["disposition"] is None
    assert report["summary"]["by_action"]["create"] == 1


def test_keep_maps_to_no_action():
    report = build_roadmap(
        _strategy(
            {
                "url": "/about",
                "title": "About",
                "keyword": "about us",
                "priority": "Fill-in",
            }
        ),
        _arch([{"url": "/about", "parent": "/", "depth": 1, "page_type": "utility"}]),
        _audit({"url": "/about", "disposition": "KEEP", "reason": "still converting"}),
    )
    row = report["pages"][0]
    assert row["action"] == "no_action"
    assert row["disposition"] == "KEEP"
    assert row["disposition_reason"] == "still converting"
    assert report["locked"] is True


def test_refresh_optmise_retitle_map_to_refresh():
    report = build_roadmap(
        _strategy(
            {"url": "/crm", "title": "CRM", "keyword": "crm software", "priority": "Quick win"},
            {"url": "/seo", "title": "SEO", "keyword": "seo services", "priority": "Big bet"},
            {"url": "/blog/old", "title": "Old", "keyword": "old title", "priority": "Fill-in"},
        ),
        _arch(
            [
                {"url": "/crm", "parent": "/", "depth": 1, "page_type": "service"},
                {"url": "/seo", "parent": "/", "depth": 1, "page_type": "service"},
                {"url": "/blog/old", "parent": "/blog", "depth": 2, "page_type": "article"},
            ]
        ),
        _audit(
            {"url": "/crm", "disposition": "REFRESH"},
            {"url": "/seo", "disposition": "optimise"},
            {"url": "/blog/old", "disposition": "RETITLE"},
        ),
    )
    by_url = {r["url_n"]: r for r in report["pages"]}
    assert by_url["/crm"]["action"] == "refresh"
    assert by_url["/seo"]["action"] == "refresh"
    assert by_url["/seo"]["disposition"] == "OPTIMISE"
    assert by_url["/blog/old"]["action"] == "refresh"


def test_strategy_only_goes_to_excluded():
    report = build_roadmap(
        _strategy(
            {
                "url": "/planned-only",
                "title": "Planned",
                "keyword": "planned topic",
                "priority_tier": "quick_win",
            }
        ),
        _arch([]),
        {},
    )
    assert report["pages"] == []
    assert report["excluded"][0]["source_pack"] == "strategy"
    assert "strategy_only" in report["excluded"][0]["reason"]
    assert report["locked"] is True  # no merged rows to invalidate


def test_architecture_only_goes_to_excluded():
    report = build_roadmap(
        _strategy(),
        _arch([{"url": "/orphan", "parent": "/", "depth": 1, "page_type": "article"}]),
        {},
    )
    assert report["pages"] == []
    assert report["excluded"][0]["source_pack"] == "architecture"
    assert "architecture_only" in report["excluded"][0]["reason"]


def test_optimize_existing_from_url_map_enters_roadmap():
    """OPTIMIZE_EXISTING architecture rows must appear as refresh — not excluded."""
    report = build_roadmap(
        _strategy(),
        {
            "target_url_tree": [],
            "final_url_map": [
                {
                    "cluster": "google ads",
                    "primary_keyword": "google ads management",
                    "action": "OPTIMIZE_EXISTING",
                    "selected_url": "/google-ads",
                    "dedicated_url": True,
                    "url_score": 78,
                    "score_band": "HIGH",
                }
            ],
        },
        {},
    )
    assert len(report["pages"]) == 1
    row = report["pages"][0]
    assert row["url_n"] == "/google-ads"
    assert row["action"] == "refresh"
    assert row["primary_keyword"] == "google ads management"
    assert any("architecture_optimize_admitted" in str(r.get("reconcile_note") or "") for r in report["pages"])
    assert not any(
        e.get("url_n") == "/google-ads" and "architecture_only" in str(e.get("reason") or "")
        for e in report["excluded"]
    )


def test_architecture_refresh_disposition_enters_roadmap():
    report = build_roadmap(
        _strategy(),
        _arch(
            [
                {
                    "url": "/email-marketing",
                    "parent": "/",
                    "depth": 1,
                    "page_type": "service",
                    "keyword": "email marketing",
                    "title": "Email Marketing",
                }
            ]
        ),
        _audit(
            {
                "url": "/email-marketing",
                "disposition": "REFRESH",
                "reason": "thin copy",
                "keyword": "email marketing",
            }
        ),
    )
    assert len(report["pages"]) == 1
    assert report["pages"][0]["action"] == "refresh"
    assert report["pages"][0]["url_n"] == "/email-marketing"


def test_create_without_title_or_keyword_does_not_lock():
    report = build_roadmap(
        _strategy({"url": "/x", "priority_tier": "quick_win"}),
        _arch([{"url": "/x", "parent": "/", "depth": 1, "page_type": "article"}]),
        {},
    )
    assert report["locked"] is False
    assert "primary_keyword" in (report.get("lock_reason") or "")

def test_flags_do_not_block_lock_by_default():
    report = build_roadmap(
        _strategy(
            {
                "url": "/deep/page",
                "title": "Deep",
                "keyword": "deep topic",
                "cluster": "measurement",
                "priority_tier": "quick_win",
            }
        ),
        _arch(
            [
                {
                    "url": "/deep/page",
                    "parent": "/a/b/c",
                    "depth": 5,
                    "page_type": "article",
                    "indexable": False,
                    "breadcrumb": ["Home", "Deep"],
                }
            ],
            [{"cluster": "measurement", "owner_url": "/cluster-owner"}],
        ),
        {},
    )
    row = report["pages"][0]
    assert row["cannibal_conflict"] is True
    assert "depth_gt_4" in row["flags"]
    assert "non_indexable_active" in row["flags"]
    assert "breadcrumb_mismatch" in row["flags"]
    assert report["locked"] is True


def test_strict_flags_and_excluded_block_lock():
    report = build_roadmap(
        _strategy(
            {
                "url": "/a",
                "title": "A",
                "keyword": "a topic",
                "cluster": "x",
                "priority_tier": "quick_win",
            },
            {"url": "/only-strategy", "title": "S", "keyword": "s topic"},
        ),
        _arch(
            [{"url": "/a", "parent": "/", "depth": 5, "page_type": "article", "indexable": True}],
            [{"cluster": "x", "owner_url": "/other"}],
        ),
        {},
        strict=True,
    )
    assert report["locked"] is False
    reason = report.get("lock_reason") or ""
    assert "flag" in reason or "excluded" in reason


def test_refresh_ranks_before_create_in_same_tier():
    report = build_roadmap(
        _strategy(
            {"url": "/new", "title": "New", "keyword": "new topic", "priority": "Quick win", "opportunity_score": 99},
            {"url": "/old", "title": "Old", "keyword": "old topic", "priority": "Quick win", "opportunity_score": 10},
        ),
        _arch(
            [
                {"url": "/new", "parent": "/", "depth": 1, "page_type": "article"},
                {"url": "/old", "parent": "/", "depth": 1, "page_type": "article"},
            ]
        ),
        _audit({"url": "/old", "disposition": "REFRESH"}),
    )
    assert report["pages"][0]["url_n"] == "/old"
    assert report["pages"][0]["action"] == "refresh"
    assert report["pages"][1]["action"] == "create"
    assert report["pages"][0]["priority_rank"] == 1


def test_declining_blog_is_dropped_and_rising_blog_stays():
    from app.services.content_planning import apply_blog_virality_gate

    pages = [
        {
            "url_n": "/blog/rising-topic",
            "action": "create",
            "page_type": "blog",
            "keyword": "meta ads trends",
            "title": "Meta ads trends",
            "trend": "rising",
            "volume": 800,
            "bucket": "trend",
            "opportunity_score": 62,
        },
        {
            "url_n": "/blog/fading-topic",
            "action": "create",
            "page_type": "blog",
            "keyword": "old meta ads",
            "title": "Old meta ads",
            "trend": "declining",
            "volume": 40,
            "bucket": "avoid",
        },
        {
            "url_n": "/meta-ads",
            "action": "refresh",
            "page_type": "service",
            "keyword": "meta ads",
            "title": "Meta Ads",
        },
    ]
    kept, dropped = apply_blog_virality_gate(pages)
    urls = [row["url_n"] for row in kept]
    assert "/blog/rising-topic" in urls
    assert "/meta-ads" in urls
    assert "/blog/fading-topic" not in urls
    assert dropped[0]["source_pack"] == "virality_check"
    assert kept[0]["virality_pass"] is True or any(row.get("virality_pass") for row in kept)


def test_selected_service_blog_requires_a_trending_keyword():
    from app.services.content_planning import ensure_selected_service_coverage

    service = {"id": "meta ads", "name": "Meta Ads", "url": "/meta-ads"}
    without = ensure_selected_service_coverage([], [service], {})
    assert {row.get("page_type") for row in without} == {"service"}

    with_trend = ensure_selected_service_coverage(
        [],
        [service],
        {
            "trend_plays": [
                {
                    "keyword": "meta ads trends 2026",
                    "trend": "rising",
                    "volume": 900,
                    "bucket": "trend",
                    "opportunity_score": 70,
                }
            ]
        },
    )
    blogs = [row for row in with_trend if row.get("page_type") == "blog"]
    assert len(blogs) == 1
    assert blogs[0]["keyword"] == "meta ads trends 2026"
    assert blogs[0]["virality_pass"] is True


def test_pages_follow_pillar_blog_subpillar_blog_order():
    from app.services.content_planning import order_pages_by_hierarchy

    pages = [
        {
            "url_n": "/meta-ads/instagram",
            "page_type": "subservice",
            "supports_service_id": "meta ads",
            "priority_tier": "quick_win",
            "priority_score": 99,
            "action": "create",
        },
        {
            "url_n": "/blog/instagram-tips",
            "page_type": "blog",
            "supports_service_id": "meta ads",
            "supports_subservice_id": "instagram",
            "parent_url_n": "/meta-ads/instagram",
            "priority_tier": "quick_win",
            "priority_score": 90,
            "action": "create",
        },
        {
            "url_n": "/blog/meta-ads-guide",
            "page_type": "blog",
            "supports_service_id": "meta ads",
            "priority_tier": "fill_in",
            "priority_score": 5,
            "action": "create",
        },
        {
            "url_n": "/meta-ads",
            "page_type": "service",
            "supports_service_id": "meta ads",
            "priority_tier": "big_bet",
            "priority_score": 20,
            "action": "refresh",
        },
    ]
    ordered = order_pages_by_hierarchy(pages)
    assert [row["url_n"] for row in ordered] == [
        "/meta-ads",
        "/blog/meta-ads-guide",
        "/meta-ads/instagram",
        "/blog/instagram-tips",
    ]
    assert [row["hierarchy_role"] for row in ordered] == [
        "pillar_page",
        "pillar_blogs",
        "subpillar_page",
        "subpillar_blogs",
    ]


def test_validate_report_check_roundtrip(tmp_path: Path):
    report = build_roadmap(
        _strategy({"url": "/p", "title": "P", "keyword": "p keyword", "priority_tier": "fill_in"}),
        _arch([{"url": "/p", "parent": "/", "depth": 1, "page_type": "hub"}]),
        {},
    )
    dest = tmp_path / "content_planning_report.json"
    dest.write_text(json.dumps(report), encoding="utf-8")
    stored = json.loads(dest.read_text(encoding="utf-8"))
    checked = validate_report(stored)
    assert checked["ok"] is True
    assert checked["locked"] is True


def test_tool_page_type_locks_and_reaches_calendar_roadmap():
    report = build_roadmap(
        _strategy(
            {
                "url": "/tools/roi-calculator",
                "title": "ROI Calculator",
                "keyword": "roi calculator",
                "content_type": "tool",
                "priority_tier": "quick_win",
            }
        ),
        _arch(
            [
                {
                    "url": "/tools/roi-calculator",
                    "parent": "/tools",
                    "depth": 2,
                    "page_type": "tool",
                }
            ]
        ),
        {},
    )
    assert report["locked"] is True
    assert report["pages"][0]["page_type"] == "tool"
    assert report["pages"][0]["content_type_label"] == "tool"


def test_architecture_tree_is_built_from_clusters_not_the_strategy_queue():
    """Phase 6 runs BEFORE Phase 7, so it cannot use Phase 7's priority queue.

    It used to read profile.seo_strategy_summary, which is empty on a first pass
    and populated on a re-run — the tree therefore changed depending on how many
    times the pipeline had been round-tripped. Phase 9 joins the strategy queue
    itself (see _strategy_rows), so nothing downstream loses the information.
    """
    from app.services.site_architecture import build_blueprint

    bp = build_blueprint(
        client_name="Acme",
        domain="acme.example",
        current_state={"urls_crawled": 0, "issues": []},
        demand={
            "clusters": [
                {
                    "name": "measurement",
                    "primary_keyword": "share of search",
                    "keyword_count": 3,
                    "total_volume": 400,
                    "intent": "informational",
                }
            ]
        },
        commercial={},
        website={},
    )
    urls = {_pathish(n) for n in bp["target_url_tree"]}
    assert urls, "clusters alone must still produce a tree"
    owners = bp.get("cluster_owners") or bp.get("cluster_ownership") or []
    assert any(str(o.get("cluster") or "").lower() == "measurement" for o in owners)


def test_blueprint_builds_service_subservice_blog_chain():
    from app.services.site_architecture import build_blueprint

    catalog = [
        {
            "id": "meta ads",
            "name": "Meta Ads",
            "source": "cdd",
            "subservices": [
                {
                    "id": "instagram ads",
                    "name": "Instagram Ads",
                    "source": "competitor",
                }
            ],
        }
    ]
    bp = build_blueprint(
        client_name="Acme",
        domain="acme.example",
        current_state={"urls_crawled": 0, "issues": []},
        demand={
            "services": ["Meta Ads"],
            "service_catalog": catalog,
            "service_prioritization": {
                "selected_service_ids": ["meta ads"],
                "selected_subservice_ids": ["instagram ads"],
                "service_catalog": catalog,
            },
        },
        commercial={},
        website={},
    )
    by_url = {_pathish(node): node for node in bp["target_url_tree"]}
    # Phase 6 owns the service / sub-service mapping…
    assert by_url["/meta-ads"]["page_type"] == "service"
    assert by_url["/meta-ads"]["parent"] == "/"
    assert by_url["/meta-ads/instagram-ads"]["page_type"] == "subservice"
    assert by_url["/meta-ads/instagram-ads"]["parent"] == "/meta-ads/"
    # …and the supporting blog page arrives from Phase 7's content calendar,
    # which runs after Phase 6. Phase 6 reaching forward for it only ever
    # worked on a re-run.
    assert "/blog/instagram-ads-guide" not in by_url

    from app.services.site_architecture import merge_strategy_nodes_into_tree

    enriched = merge_strategy_nodes_into_tree(
        bp,
        {
            "priority_queue": [
                {
                    "keyword": "instagram ads guide",
                    "title": "Instagram Ads guide",
                    "suggested_url": "/blog/instagram-ads-guide/",
                    "content_type": "blog",
                }
            ]
        },
    )
    after = {_pathish(node): node for node in enriched["target_url_tree"]}
    assert after["/blog/instagram-ads-guide"]["page_type"] == "blog"


def test_roadmap_routes_commercial_service_and_parents_supporting_blog():
    report = build_roadmap(
        _strategy(
            {
                "url": "/blog/social-media-marketing",
                "title": "Social media marketing",
                "keyword": "social media marketing",
                "intent": "commercial",
                "volume": 90,
                "priority_tier": "quick_win",
            },
            {
                "url": "/blog/instagram-ads-guide",
                "title": "Instagram Ads guide",
                "keyword": "instagram ads guide",
                "intent": "informational",
                "priority_tier": "fill_in",
            },
        ),
        _arch(
            [
                {
                    "url": "/social-media-marketing",
                    "title": "Social Media Marketing",
                    "keyword": "social media marketing",
                    "service_id": "social media marketing",
                    "parent": "/",
                    "depth": 1,
                    "page_type": "service",
                },
                {
                    "url": "/meta-ads",
                    "title": "Meta Ads",
                    "service_id": "meta ads",
                    "parent": "/",
                    "depth": 1,
                    "page_type": "service",
                },
                {
                    "url": "/meta-ads/instagram-ads",
                    "title": "Instagram Ads",
                    "service_id": "meta ads",
                    "subservice_id": "instagram ads",
                    "parent": "/meta-ads",
                    "depth": 2,
                    "page_type": "subservice",
                },
                {
                    "url": "/blog/social-media-marketing",
                    "parent": "/blog",
                    "depth": 2,
                    "page_type": "blog",
                },
                {
                    "url": "/blog/instagram-ads-guide",
                    "parent": "/blog",
                    "depth": 2,
                    "page_type": "blog",
                },
            ]
        ),
        {},
    )
    by_keyword = {row["primary_keyword"]: row for row in report["pages"]}
    commercial = by_keyword["social media marketing"]
    assert commercial["url_n"] == "/social-media-marketing"
    assert commercial["page_type"] == "service"
    assert commercial["parent"] == "/"

    blog = by_keyword["instagram ads guide"]
    assert blog["page_type"] == "blog"
    assert blog["parent_url_n"] == "/meta-ads/instagram-ads"
    assert blog["supports_subservice_id"] == "instagram ads"


def test_geo_commercial_variants_do_not_collapse_onto_service_url():
    """Geo variants keep distinct URLs; only the head service term collapses."""
    report = build_roadmap(
        _strategy(
            {
                "url": "/guides/google-ads-management-sydney",
                "title": "Google Ads Management Sydney",
                "keyword": "google ads management sydney",
                "intent": "commercial",
                "volume": 210,
                "priority_tier": "quick_win",
            },
            {
                "url": "/guides/google-ads-management-near-me",
                "title": "Google Ads Management Near Me",
                "keyword": "google ads management near me",
                "intent": "commercial",
                "volume": 30,
                "priority_tier": "quick_win",
            },
            {
                "url": "/google-ads-management",
                "title": "Google Ads Management",
                "keyword": "google ads management",
                "intent": "commercial",
                "volume": 400,
                "priority_tier": "big_bet",
            },
        ),
        _arch(
            [
                {
                    "url": "/google-ads-management",
                    "title": "Google Ads Management",
                    "keyword": "google ads management",
                    "parent": "/",
                    "depth": 1,
                    "page_type": "service",
                },
                {
                    "url": "/blog/google-ads-management-sydney",
                    "keyword": "google ads management sydney",
                    "parent": "/blog",
                    "depth": 2,
                    "page_type": "article",
                },
                {
                    "url": "/blog/google-ads-management-near-me",
                    "keyword": "google ads management near me",
                    "parent": "/blog",
                    "depth": 2,
                    "page_type": "article",
                },
            ]
        ),
        {},
    )
    assert report["locked"] is True
    urls = [r["url_n"] for r in report["pages"]]
    assert urls.count("/google-ads-management") == 1
    assert "/blog/google-ads-management-sydney" in urls
    assert "/blog/google-ads-management-near-me" in urls
    by_kw = {r["primary_keyword"]: r for r in report["pages"]}
    assert by_kw["google ads management sydney"]["supports_service_id"] or by_kw[
        "google ads management sydney"
    ].get("parent_url_n") == "/google-ads-management"


def test_strategy_queue_mirrors_are_deduped():
    strategy = {
        "priority_queue": [
            {
                "url": "/blog/a",
                "title": "A",
                "keyword": "topic a",
                "intent": "informational",
                "volume": 100,
                "priority_tier": "quick_win",
            }
        ],
        "combined_priority_queue": [{"title": "A", "keyword": "topic a"}],
        "priority_pages": [{"url": "/blog/a", "keyword": "topic a", "title": "A"}],
    }
    report = build_roadmap(
        strategy,
        _arch([{"url": "/blog/a", "parent": "/blog", "depth": 2, "page_type": "article"}]),
        {},
    )
    assert len(report["pages"]) == 1
    assert report["locked"] is True


def _pathish(n: dict) -> str:
    from app.services.content_planning import url_n

    return url_n(str(n.get("url") or n.get("path") or ""))


@pytest.mark.asyncio
async def test_planning_blocks_established_site_without_audit():
    from app.services.content_planning import run_content_planning_plan

    out = await run_content_planning_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        site_architecture_status="complete",
        site_architecture={"target_url_tree": [{"path": "/a", "parent": "/"}]},
        seo_strategy={"priority_queue": [{"url": "/a", "keyword": "a", "title": "A"}]},
        seo_strategy_status="complete",
        website_status="complete",
        website={"sample_urls": ["/a"]},
        content_audit={},
        content_audit_status="not_started",
    )
    assert out.get("blocked") is True
    assert "Audit" in str(out.get("reason") or "")


@pytest.mark.asyncio
async def test_new_site_planning_allows_missing_audit():
    from app.services.content_planning import run_content_planning_plan

    out = await run_content_planning_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        site_architecture_status="complete",
        site_architecture={
            "target_url_tree": [
                {"url": "/blog/share-of-search", "parent": "/blog", "depth": 2, "page_type": "article"}
            ]
        },
        seo_strategy={"priority_queue": [{"url": "/blog/share-of-search", "keyword": "share of search", "title": "Share of search"}]},
        seo_strategy_status="complete",
        website_status="not_started",
        website={},
        content_audit={},
        content_audit_status="not_started",
    )
    assert out.get("blocked") is not True
    assert out.get("locked") is True
    assert out["pages"][0]["action"] == "create"
