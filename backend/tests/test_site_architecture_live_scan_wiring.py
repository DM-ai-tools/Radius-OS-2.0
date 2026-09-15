"""Phase 6b must use Phase 5's fresh live-site-scan pages for URL mapping
instead of Phase 3's website_situation_summary, which is already stale by
the time Phase 6b runs. This intercepts the actual build_final_url_map call
site.py makes to prove the fresh pages win, without driving the whole
(very large, network-touching) run_site_architecture_plan function."""

from __future__ import annotations

from app.services import site_architecture, url_mapping


async def _fake_click_depth(seed_url, *, page_cap=80):
    return {
        "urls_crawled": 1,
        "issues": [],
        "max_depth": 0,
        "pages_by_depth": {},
        "orphan_pages": [],
        "phantom_directories": [],
    }


def _demand_with_cluster(live_scan_pages=None):
    demand = {
        "cluster_report": {
            "clusters": [
                {
                    "name": "SEO Services",
                    "primary_keyword": "seo services",
                    "keywords": [{"keyword": "seo services", "volume": 500, "cpc": 12.5}],
                    "intent": "commercial",
                    "recommended_url": "/services/seo",
                }
            ]
        },
    }
    if live_scan_pages is not None:
        demand["live_site_scan"] = {"pages": live_scan_pages}
    return demand


async def test_build_final_url_map_receives_fresh_scan_pages_over_stale_website(monkeypatch):
    monkeypatch.setattr(site_architecture, "measure_click_depth", _fake_click_depth)

    captured: dict = {}

    def _fake_build_final_url_map(cluster_report, **kwargs):
        captured.update(kwargs)
        return {
            "final_url_map": [],
            "crawl_page_count": 0,
            "mapped_cluster_count": 0,
            "summary": {"optimize_existing": 0, "review_merge_redirect": 0, "create": 0,
                        "high_band": 0, "medium_band": 0, "low_band": 0},
        }

    monkeypatch.setattr(url_mapping, "build_final_url_map", _fake_build_final_url_map)

    fresh_pages = [
        {"url": "https://acme.example/services/seo", "path": "/services/seo", "title": "SEO Services"}
    ]
    stale_pages = [
        {"url": "https://acme.example/old-stale-page", "path": "/old-stale-page", "title": "Stale"}
    ]

    await site_architecture.run_site_architecture_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        domain="acme.example",
        demand=_demand_with_cluster(live_scan_pages=fresh_pages),
        seo_strategy={},
        commercial={},
        website={"pages": stale_pages},
    )

    assert captured["website"]["pages"] == fresh_pages


async def test_falls_back_to_stale_website_when_no_live_scan_present(monkeypatch):
    """Phase 5 hasn't run yet, or its scan came back empty — Phase 6b should
    still work off whatever website data it already had, not crash or wipe it."""
    monkeypatch.setattr(site_architecture, "measure_click_depth", _fake_click_depth)

    captured: dict = {}

    def _fake_build_final_url_map(cluster_report, **kwargs):
        captured.update(kwargs)
        return {
            "final_url_map": [],
            "crawl_page_count": 0,
            "mapped_cluster_count": 0,
            "summary": {"optimize_existing": 0, "review_merge_redirect": 0, "create": 0,
                        "high_band": 0, "medium_band": 0, "low_band": 0},
        }

    monkeypatch.setattr(url_mapping, "build_final_url_map", _fake_build_final_url_map)

    stale_pages = [
        {"url": "https://acme.example/old-stale-page", "path": "/old-stale-page", "title": "Stale"}
    ]

    await site_architecture.run_site_architecture_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        domain="acme.example",
        demand=_demand_with_cluster(live_scan_pages=None),
        seo_strategy={},
        commercial={},
        website={"pages": stale_pages},
    )

    assert any(
        (p.get("path") == "/old-stale-page" or "old-stale-page" in str(p.get("url") or ""))
        for p in (captured["website"].get("pages") or [])
    )
