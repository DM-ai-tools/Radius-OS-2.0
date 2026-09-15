"""Phase 8 — theme clustering (Architecture v1.9, Step 08).

Audit output must be grouped by theme, not just listed by URL — reusing Phase 5's
clusters / Phase 6a's pillars rather than inventing a new taxonomy. See
app/agents/existing-content-audit/SKILL.md and TR_SEO_Architecture_v1_9.
"""

from __future__ import annotations

import pytest

from app.services.content_audit import (
    _group_by_theme,
    _theme_lookup,
    run_content_audit_plan,
)


def test_theme_lookup_prefers_phase6a_pillar_over_phase5_cluster():
    lookup = _theme_lookup(
        search_demand={
            "cluster_report": {
                "clusters": [
                    {
                        "name": "SEO Basics",
                        "primary_keyword": "what is seo",
                        "keywords": [{"keyword": "what is seo"}, {"keyword": "seo basics"}],
                    }
                ]
            }
        },
        seo_strategy={
            "core_topics": [
                {
                    "pillar": "SEO Fundamentals",
                    "clusters": [
                        {
                            "name": "SEO Basics",
                            "primary_keyword": "what is seo",
                            "keywords": [{"keyword": "what is seo"}, {"keyword": "seo basics"}],
                        }
                    ],
                }
            ]
        },
    )
    assert lookup["what is seo"] == "SEO Fundamentals"
    assert lookup["seo basics"] == "SEO Fundamentals"


def test_theme_lookup_falls_back_to_phase5_cluster_without_pillars():
    lookup = _theme_lookup(
        search_demand={
            "cluster_report": {
                "clusters": [
                    {"name": "SEO Tools", "primary_keyword": "seo tools", "keywords": []}
                ]
            }
        },
        seo_strategy={},
    )
    assert lookup["seo tools"] == "SEO Tools"


def test_group_by_theme_counts_dispositions_and_caps_sample_urls():
    inventory = [
        {"path": f"/p{i}", "title": f"Page {i}", "theme": "SEO Basics", "disposition": "keep"}
        for i in range(15)
    ] + [
        {"path": "/legal", "title": "Legal", "theme": "Uncategorized", "disposition": "keep"},
    ]
    themes = _group_by_theme(inventory)
    seo_basics = next(t for t in themes if t["theme"] == "SEO Basics")
    assert seo_basics["url_count"] == 15
    assert seo_basics["dispositions"]["keep"] == 15
    assert len(seo_basics["urls"]) == 10
    assert seo_basics["urls_truncated"] is True

    uncategorized = next(t for t in themes if t["theme"] == "Uncategorized")
    assert uncategorized["url_count"] == 1
    assert "urls_truncated" not in uncategorized

    # Largest theme first.
    assert themes[0]["theme"] == "SEO Basics"


@pytest.mark.asyncio
async def test_run_content_audit_plan_groups_pages_by_phase5_cluster():
    result = await run_content_audit_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        website={"sample_urls": ["/what-is-seo", "/seo-tools", "/legal/privacy"]},
        site_architecture={
            "target_url_tree": [
                {"path": "/what-is-seo", "type": "article", "keyword": "what is seo"},
                {"path": "/seo-tools", "type": "article", "keyword": "seo tools"},
                {"path": "/legal/privacy", "type": "utility", "keyword": None},
            ]
        },
        seo_strategy={},
        search_demand={
            "cluster_report": {
                "clusters": [
                    {
                        "name": "SEO Fundamentals",
                        "primary_keyword": "what is seo",
                        "keywords": [{"keyword": "what is seo"}],
                    },
                    {
                        "name": "SEO Tools",
                        "primary_keyword": "seo tools",
                        "keywords": [{"keyword": "seo tools"}],
                    },
                ]
            }
        },
    )
    assert not result.get("blocked")
    themes_by_name = {t["theme"]: t for t in result["themes"]}
    assert "SEO Fundamentals" in themes_by_name
    assert "SEO Tools" in themes_by_name
    assert "Uncategorized" in themes_by_name  # /legal/privacy has no keyword match

    by_path = {i["path"]: i for i in result["inventory"]}
    assert by_path["/what-is-seo"]["theme"] == "SEO Fundamentals"
    assert by_path["/seo-tools"]["theme"] == "SEO Tools"
    assert by_path["/legal/privacy"]["theme"] == "Uncategorized"

    # Full page-by-page detail is kept, not truncated to a fixed cap.
    assert len(result["inventory"]) == 3


@pytest.mark.asyncio
async def test_run_content_audit_plan_keeps_full_inventory_past_old_80_cap():
    """Regression: inventory used to be silently sliced to 80 rows, which also
    broke Phase 9/10 disposition lookups for any URL past the cut. Architecture
    v1.9 requires grouping (themes), not truncation."""
    urls = [f"/page-{i}" for i in range(95)]
    result = await run_content_audit_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        website={"sample_urls": urls},
        site_architecture={},
        seo_strategy={},
        search_demand={},
    )
    assert len(result["inventory"]) == 95
