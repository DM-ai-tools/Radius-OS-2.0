"""Tests for BW workbook row builders."""

from __future__ import annotations

from app.services.bw_workbook import (
    build_category_url_mapping_rows,
    build_combined_workbook_pack,
    build_search_demand_analysis_rows,
    build_tofu_mofu_content_strategy_rows,
    export_workbook_xlsx,
)


def test_category_url_mapping_live_and_new():
    rows = build_category_url_mapping_rows(
        [
            {
                "level": 1,
                "l1_category": "Mattresses",
                "primary_keyword": "queen mattress",
                "action": "OPTIMIZE_EXISTING",
                "current_url": "/product-category/mattresses/queen/",
                "search_volume": 22200,
                "cpc": 1.24,
                "combined_cluster_volume": 35520,
                "page_type": "Hub Page",
                "score_band": "HIGH",
                "in_scope": "YES",
            },
            {
                "level": 2,
                "l1_category": "Mattresses",
                "l2_subcategory": "Foam",
                "primary_keyword": "foam mattress",
                "action": "CREATE",
                "proposed_url": "/mattresses/foam-mattress/",
                "search_volume": 9900,
                "page_type": "Collection",
                "score_band": "MEDIUM",
            },
        ],
        include_section_headers=True,
    )
    data_rows = [r for r in rows if r.get("row_type") == "data"]
    assert any(r["status"] == "LIVE" for r in data_rows)
    assert any(r["status"] == "NEW" for r in data_rows)
    assert any(r["priority"].startswith("P") for r in data_rows)


def test_search_demand_analysis_sorted_by_volume():
    rows = build_search_demand_analysis_rows(
        [
            {"keyword": "sofa bed", "volume": 60500, "funnel": "BOFU", "cpc": 0.95},
            {"keyword": "bookcase", "volume": 9900, "funnel": "BOFU"},
        ],
        url_map=[
            {
                "primary_keyword": "bookcase",
                "proposed_url": "/accessories/bookcase/",
                "action": "CREATE",
            }
        ],
    )
    assert rows[0]["keyword"] == "sofa bed"
    assert rows[1]["target_url"] == "/accessories/bookcase/"
    assert rows[1]["status"] == "NEW"


def test_tofu_mofu_filters_bofu():
    rows = build_tofu_mofu_content_strategy_rows(
        [
            {
                "title": "Mattress Buying Guide",
                "keyword": "mattress buying guide",
                "funnel": "TOFU",
                "content_type": "Buying Guide",
                "volume": 2400,
            },
            {
                "title": "Queen Mattress",
                "keyword": "queen mattress",
                "funnel": "BOFU",
                "content_type": "Product",
            },
        ]
    )
    assert len(rows) == 1
    assert rows[0]["funnel_stage"] == "TOFU"


def test_export_workbook_xlsx_bytes():
    pack = build_combined_workbook_pack(
        client_name="Test Co",
        search_demand={"keyword_dataset": [{"keyword": "test", "volume": 100, "funnel": "BOFU"}]},
        site_architecture={
            "url_map_report": {
                "final_url_map": [
                    {
                        "level": 1,
                        "l1_category": "Test",
                        "primary_keyword": "test",
                        "action": "CREATE",
                        "proposed_url": "/test/",
                        "page_type": "Collection",
                    }
                ]
            }
        },
        content_strategy={
            "priority_queue": [
                {
                    "title": "Guide",
                    "keyword": "guide kw",
                    "funnel": "MOFU",
                    "content_type": "Comparison",
                }
            ]
        },
    )
    blob = export_workbook_xlsx(pack)
    assert blob[:2] == b"PK"
