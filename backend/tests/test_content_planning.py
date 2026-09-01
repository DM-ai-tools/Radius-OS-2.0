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


def test_architecture_tree_uses_strategy_queue_urls():
    from app.services.site_architecture import build_blueprint

    bp = build_blueprint(
        client_name="Acme",
        domain="acme.example",
        current_state={"urls_crawled": 0, "issues": []},
        demand={"clusters": []},
        seo_strategy={
            "priority_queue": [
                {
                    "keyword": "share of search",
                    "title": "Share of search",
                    "suggested_url": "https://acme.example/blog/share-of-search",
                    "cluster": "measurement",
                    "priority": "Quick win",
                }
            ]
        },
        commercial={},
        website={},
    )
    urls = {_pathish(n) for n in bp["target_url_tree"]}
    assert "/blog/share-of-search" in urls
    owners = bp.get("cluster_owners") or bp.get("cluster_ownership") or []
    assert any(str(o.get("cluster") or "").lower() == "measurement" for o in owners)


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
        seo_strategy={
            "priority_queue": [
                {
                    "url": "/blog/share-of-search",
                    "title": "Share of search",
                    "keyword": "share of search",
                    "priority_tier": "quick_win",
                }
            ]
        },
        seo_strategy_status="complete",
        website_status="not_started",
        website={},
        content_audit={},
        content_audit_status="not_started",
    )
    assert out.get("blocked") is not True
    assert out.get("locked") is True
    assert out["pages"][0]["action"] == "create"
