"""Phase 9: service refresh admission + blog create cap."""

from __future__ import annotations

from app.services.content_planning import (
    MAX_BLOG_CREATES,
    build_roadmap,
    prune_blog_creates,
)


def _strategy(*rows: dict) -> dict:
    return {"priority_queue": list(rows)}


def _arch(tree: list[dict], **extra) -> dict:
    pack: dict = {"target_url_tree": tree}
    pack.update(extra)
    return pack


def _audit(*rows: dict) -> dict:
    return {"dispositions": list(rows), "inventory": list(rows)}


def test_live_service_pages_enter_as_refresh_even_without_strategy():
    report = build_roadmap(
        _strategy(),
        _arch(
            [
                {
                    "url": "/services/seo",
                    "parent": "/services",
                    "depth": 2,
                    "page_type": "service",
                    "keyword": "seo services",
                    "title": "SEO Services",
                }
            ]
        ),
        _audit(
            {
                "url": "/services/seo",
                "disposition": "KEEP",
                "keyword": "seo services",
                "reason": "still ranking",
            }
        ),
    )
    assert len(report["pages"]) == 1
    row = report["pages"][0]
    assert row["url_n"] == "/services/seo"
    assert row["action"] == "refresh"
    assert row["content_type_label"] == "service page" or row["page_type"] == "service"


def test_blog_creates_capped_at_two():
    blogs = [
        {
            "url": f"/blog/topic-{i}",
            "title": f"Topic {i}",
            "keyword": f"topic {i}",
            "priority_tier": "fill_in",
            "from_phase5_topic": True,
            "trend": "rising",
            "volume": 400,
            "bucket": "trend",
            "opportunity_score": 60,
        }
        for i in range(1, 8)
    ]
    tree = [
        {
            "url": f"/blog/topic-{i}",
            "parent": "/blog",
            "depth": 2,
            "page_type": "blog",
        }
        for i in range(1, 8)
    ]
    report = build_roadmap(_strategy(*blogs), _arch(tree), {})
    creates = [r for r in report["pages"] if r.get("action") == "create"]
    assert len(creates) == MAX_BLOG_CREATES
    assert len(creates) >= 2
    assert any("blog_create_cap" in str(e.get("reason") or "") for e in report["excluded"])


def test_prune_blog_creates_keeps_services_and_refreshes():
    pages = [
        {
            "url_n": "/services/seo",
            "action": "refresh",
            "page_type": "service",
            "content_type_label": "service page",
            "priority_tier": "big_bet",
        },
        {
            "url_n": "/blog/a",
            "action": "create",
            "page_type": "blog",
            "content_type_label": "blog",
            "priority_tier": "fill_in",
            "from_phase5_topic": True,
            "priority_score": 10,
        },
        {
            "url_n": "/blog/b",
            "action": "create",
            "page_type": "blog",
            "content_type_label": "blog",
            "priority_tier": "fill_in",
            "from_phase5_topic": True,
            "priority_score": 9,
        },
        {
            "url_n": "/blog/c",
            "action": "create",
            "page_type": "blog",
            "content_type_label": "blog",
            "priority_tier": "fill_in",
            "priority_score": 1,
        },
    ]
    kept, dropped = prune_blog_creates(pages, max_creates=2)
    assert len([r for r in kept if r["action"] == "create"]) == 2
    assert any(r["url_n"] == "/services/seo" for r in kept)
    assert len(dropped) == 1
    assert dropped[0]["url_n"] == "/blog/c"


def test_commercial_route_to_existing_service_is_refresh_not_create():
    report = build_roadmap(
        _strategy(
            {
                "url": "/blog/seo-company",
                "title": "SEO Company Guide",
                "keyword": "seo services",
                "intent": "commercial",
                "volume": 200,
                "priority_tier": "big_bet",
            }
        ),
        _arch(
            [
                {
                    "url": "/services/seo",
                    "parent": "/services",
                    "depth": 2,
                    "page_type": "service",
                    "keyword": "seo services",
                    "title": "SEO Services",
                },
                {
                    "url": "/blog/seo-company",
                    "parent": "/blog",
                    "depth": 2,
                    "page_type": "blog",
                },
            ]
        ),
        _audit({"url": "/services/seo", "disposition": "OPTIMISE", "keyword": "seo services"}),
    )
    service_rows = [r for r in report["pages"] if "/services/seo" in str(r.get("url_n"))]
    assert service_rows
    assert all(r["action"] == "refresh" for r in service_rows)


def _selected(*services: dict) -> dict:
    return {"selected_services": list(services)}


def test_only_selected_service_pages_are_optimized():
    report = build_roadmap(
        _strategy(),
        _arch(
            [
                {
                    "url": "/services/email-marketing",
                    "page_type": "service",
                    "title": "Email Marketing",
                    "keyword": "email marketing",
                    "service_id": "email marketing",
                },
                {
                    "url": "/services/seo",
                    "page_type": "service",
                    "title": "SEO Services",
                    "keyword": "seo services",
                    "service_id": "seo",
                },
            ],
            **_selected(
                {
                    "id": "email marketing",
                    "name": "Email Marketing",
                    "url": "/email-marketing",
                    "subservices": [],
                }
            ),
        ),
        _audit(
            {"url": "/services/email-marketing", "disposition": "KEEP"},
            {"url": "/services/seo", "disposition": "KEEP"},
        ),
    )
    urls = {r["url_n"] for r in report["pages"]}
    refresh = [r for r in report["pages"] if r["action"] == "refresh"]
    assert "/services/email-marketing" in urls
    assert all("/services/seo" not in str(r.get("url_n")) for r in refresh)
    assert any("unselected_service" in str(e.get("reason") or "") for e in report["excluded"])
    invented = [
        r
        for r in report["pages"]
        if r["action"] == "create" and r.get("supports_service_id") == "email marketing"
    ]
    assert invented == []


def test_selected_services_get_cluster_pages_instead_of_a_fixed_set():
    report = build_roadmap(
        _strategy(),
        _arch(
            [],
            **_selected(
                {"id": "email marketing", "name": "Email Marketing", "subservices": []},
                {"id": "ppc", "name": "PPC", "subservices": []},
            ),
        ),
        {},
        search_demand={
            "cluster_report": {
                "clusters": [
                    {"primary_keyword": "email marketing pricing", "intent": "transactional", "funnel": "BOFU"},
                    {"primary_keyword": "what is ppc", "intent": "informational", "funnel": "TOFU"},
                ]
            }
        },
    )
    keywords = {str(r.get("keyword") or "").lower() for r in report["pages"]}
    assert "email marketing pricing" in keywords
    assert "what is ppc" in keywords
    assert "email marketing guide" not in keywords
    assert "ppc pricing" not in keywords
    assert "ppc faq" not in keywords
    refresh = [r for r in report["pages"] if r["action"] == "refresh"]
    assert {r["supports_service_id"] for r in refresh} == {"email marketing", "ppc"}


def test_search_demand_selection_drives_the_blog_floor():
    report = build_roadmap(
        _strategy(),
        _arch(
            [
                {
                    "url": "/services/cro",
                    "page_type": "service",
                    "title": "CRO",
                    "keyword": "cro",
                    "service_id": "cro",
                }
            ]
        ),
        {},
        search_demand={
            "selected_services": [
                {"id": "cro", "name": "CRO", "url": "/services/cro", "subservices": []}
            ]
        },
    )
    assert any(r["url_n"] == "/services/cro" and r["action"] == "refresh" for r in report["pages"])
    assert report["summary"]["blogs_per_selected_service"]["CRO"] == 0


def test_selected_service_blogs_are_not_cut_by_the_global_cap():
    blogs = [
        {
            "url": f"/blog/email-idea-{i}",
            "title": f"Email Marketing idea {i}",
            "keyword": f"email marketing idea {i}",
            "priority_tier": "fill_in",
            "from_phase5_topic": True,
            "supports_service_id": "email marketing",
            "trend": "rising",
            "volume": 400,
            "bucket": "trend",
            "opportunity_score": 60,
        }
        for i in range(1, 5)
    ]
    tree = [
        {
            "url": f"/blog/email-idea-{i}",
            "parent": "/email-marketing",
            "depth": 2,
            "page_type": "blog",
            "supports_service_id": "email marketing",
        }
        for i in range(1, 5)
    ]
    report = build_roadmap(
        _strategy(*blogs),
        _arch(
            tree,
            **_selected({"id": "email marketing", "name": "Email Marketing", "subservices": []}),
        ),
        {},
    )
    email_blogs = [
        r
        for r in report["pages"]
        if r["action"] == "create" and r.get("supports_service_id") == "email marketing"
    ]
    assert len(email_blogs) >= 4
    assert not any(
        str(e.get("url_n") or "").startswith("/blog/email-idea-") for e in report["excluded"]
    )
