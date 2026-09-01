"""Regression tests for Phase 1–8 shared-memory continuity fixes."""

from app.services.content_audit import run_content_audit_plan
from app.services.content_queue import build_combined_priority_queue
from app.services.content_strategy import _suggested_path, build_priority_queue
from app.services.memory_packs import (
    slim_search_demand_memory,
    slim_seo_strategy_memory,
    slim_site_architecture_memory,
)


def test_strategy_queue_includes_phase5_topic_ideas():
    q = build_priority_queue(
        best=[],
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[],
        domain="clicktrends.com.au",
        topic_plan={
            "topic_ideas": [
                {"title": "Local SEO for Agencies", "keyword": "local seo for agencies", "intent": "commercial"},
                {"title": "Google Ads Management", "keyword": "google ads management melbourne", "intent": "transactional"},
            ]
        },
    )
    kws = {str(r.get("keyword") or "").lower() for r in q}
    assert "local seo for agencies" in kws
    assert "google ads management melbourne" in kws


def test_slim_search_demand_keeps_trend_avoid_and_cluster_kd():
    slim = slim_search_demand_memory(
        {
            "best_opportunities": [{"keyword": "seo", "volume": 1000, "difficulty": 40}],
            "trend_plays": [{"keyword": "ai seo", "volume": 200, "difficulty": 20}],
            "avoid": [{"keyword": "free seo 2019", "volume": 10, "difficulty": 90}],
            "clusters": [
                {
                    "name": "SEO",
                    "primary_keyword": "seo",
                    "est_traffic": 5000,
                    "avg_difficulty": 45,
                    "keywords": [{"keyword": "seo tools", "volume": 800, "difficulty": 50}],
                }
            ],
        }
    )
    assert slim.get("trend_plays")
    assert slim.get("avoid")
    c0 = (slim.get("clusters") or [])[0]
    assert c0.get("avg_difficulty") == 45
    assert c0.get("est_traffic") == 5000
    assert c0.get("total_volume") == 5000


def test_slim_search_demand_keeps_cleaning_audit_without_excluded_terms():
    slim = slim_search_demand_memory(
        {
            "services": ["SEO Services", "Lead Generation"],
            "cdd_keywords": ["local seo"],
            "best_opportunities": [{"keyword": "seo services", "volume": 1000}],
            "clusters": [{"name": "SEO", "primary_keyword": "seo services", "keywords": []}],
            "cluster_report": {
                "clusters": [{"name": "SEO", "primary_keyword": "seo services", "keywords": []}],
                "service_clusters": [
                    {
                        "service": "SEO Services",
                        "service_key": "seo services",
                        "seed_count": 1,
                        "keyword_count": 1,
                        "seeds": [{"seed_index": 1, "seed_label": "Seed 1", "seed": "seo", "keywords": []}],
                    }
                ],
            },
            "keyword_cleaning": {
                "input_count": 20,
                "kept_count": 12,
                "removed_count": 8,
                "removed_by_reason": {"no_business_evidence": 8},
                "sample": [{"keyword": "salesforce crm", "reason": "no_business_evidence"}],
            },
        }
    )
    assert slim["keyword_cleaning"]["removed_count"] == 8
    assert slim["services"] == ["SEO Services", "Lead Generation"]
    assert (slim.get("cluster_report") or {}).get("service_clusters")


def test_slim_strategy_keeps_clusters_and_difficulty():
    slim = slim_seo_strategy_memory(
        {
            "core_topics": [
                {
                    "pillar": "SEO",
                    "primary_keyword": "seo",
                    "clusters": [
                        {"name": "Local", "primary_keyword": "local seo"},
                        {"name": "Tech", "primary_keyword": "technical seo"},
                    ],
                }
            ],
            "priority_queue": [
                {
                    "title": "SEO Guide",
                    "keyword": "seo",
                    "volume": 1000,
                    "difficulty": 42,
                    "opportunity_score": 70,
                    "intent": "informational",
                    "priority": "Quick win",
                }
            ],
        }
    )
    core = (slim.get("core_topics") or [])[0]
    assert len(core.get("clusters") or []) == 2
    q0 = (slim.get("priority_queue") or [])[0]
    assert q0.get("difficulty") == 42
    assert q0.get("opportunity_score") == 70


def test_slim_ia_keeps_redirect_map_and_tree_depth():
    slim = slim_site_architecture_memory(
        {
            "target_url_tree": [
                {"url": "/seo/", "keyword": "seo", "parent": "/", "depth": 1, "type": "hub"}
            ],
            "redirect_map": [{"from": "/old-seo", "to": "/seo/", "status": 301}],
        }
    )
    assert (slim.get("redirect_map") or [])[0]["from"] == "/old-seo"
    node = (slim.get("target_url_tree") or [])[0]
    assert node.get("depth") == 1
    assert node.get("parent") == "/"


def test_suggested_path_not_all_blog():
    assert _suggested_path("buy seo software", "transactional", "landing").startswith(
        "/services/"
    )
    assert "/blog/" in _suggested_path("what is seo", "informational", "blog")
    assert "/guides/" in _suggested_path("seo checklist", "commercial", "guide")


def test_priority_queue_reads_slim_cluster_volume_keys():
    q = build_priority_queue(
        best=[],
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[
            {
                "primary_keyword": "local seo",
                "total_volume": 1200,
                "avg_difficulty": 33,
                "best_score": 66,
                "intent": "commercial",
            }
        ],
        domain="acme.example",
    )
    assert q
    assert q[0]["volume"] == 1200
    assert q[0]["difficulty"] == 33
    assert "/blog/" not in str(q[0].get("suggested_url") or "")


def test_combined_queue_refresh_beats_new():
    out = build_combined_priority_queue(
        content_audit={
            "inventory": [
                {
                    "path": "/guides/seo",
                    "keyword": "seo guide",
                    "disposition": "refresh",
                    "title": "Old guide",
                }
            ],
            "summary_counts": {"refresh": 1},
        },
        content_strategy={
            "priority_queue": [
                {
                    "title": "New SEO guide",
                    "keyword": "seo guide",
                    "priority": "Quick win",
                    "suggested_url": "/blog/seo-guide",
                }
            ],
            "pillars": [{"name": "SEO"}],
        },
    )
    actions = [r["action"] for r in out["combined_priority_queue"]]
    assert "refresh" in actions
    assert "create" not in actions  # same topic dropped


async def test_content_audit_blocks_empty_inventory():
    out = await run_content_audit_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        website={},
        site_architecture={},
        seo_strategy={},
        search_demand={},
    )
    assert out.get("blocked") is True
    assert out.get("inventory") == []
