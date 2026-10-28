"""URL mapping pipeline — cluster → crawl → score → final URL map."""

from app.services.url_mapping import (
    _taxonomy_levels,
    action_from_band,
    band_from_score,
    build_final_url_map,
    build_url_candidates,
    collect_crawl_pages,
    map_cluster_to_url,
    score_url_candidate,
    url_n,
)

# All 17 URL Mapping sheet columns the frontend/PDF/DOCX exports expect.
SHEET_COLUMNS = (
    "level",
    "l1_category",
    "l2_subcategory",
    "l3_subsubcategory",
    "l4_attribution",
    "current_url",
    "proposed_url",
    "status",
    "primary_keyword",
    "search_volume",
    "cpc",
    "secondary_keywords_sheet",
    "combined_cluster_volume",
    "page_type",
    "priority",
    "notes",
    "est_products",
    "in_scope",
)


def _cluster():
    return {
        "name": "Local SEO Melbourne",
        "primary_keyword": "local seo melbourne",
        "intent": "commercial",
        "content_type": "service",
        "best_score": 72,
        "est_traffic": 900,
        "recommended_url": "/blog/local-seo-melbourne",
        "keywords": [
            {
                "keyword": "local seo melbourne",
                "role": "Primary",
                "volume": 1200,
                "cpc": 4.5,
                "relevance_reason": "seed_exact",
            },
            {
                "keyword": "seo agency melbourne",
                "role": "Secondary",
                "volume": 800,
                "cpc": 3.2,
            },
        ],
    }


def _crawl():
    return [
        {
            "url": "/services/local-seo-melbourne",
            "path": "/services/local-seo-melbourne",
            "title": "Local SEO Melbourne Services",
            "metrics": {"clicks": 120, "impressions": 2400, "position": 8},
        },
        {
            "url": "/blog/marketing-tips",
            "path": "/blog/marketing-tips",
            "title": "Marketing Tips for SMBs",
        },
    ]


def test_collect_crawl_pages_dedupes():
    pages = collect_crawl_pages(
        website={"pages": [{"url": "/services/a", "title": "A"}, {"url": "/services/a", "title": "A2"}]},
        content_audit={"inventory": [{"url": "/about", "title": "About"}]},
    )
    paths = {p["path"] for p in pages}
    assert "/services/a" in paths
    assert "/about" in paths
    assert len(paths) == 2


def test_build_url_candidates_prefers_keyword_overlap():
    cands = build_url_candidates(
        primary_keyword="local seo melbourne",
        crawled_pages=_crawl(),
        suggested_url="/blog/local-seo-melbourne",
    )
    paths = [c["path"] for c in cands]
    assert "/services/local-seo-melbourne" in paths


def test_score_url_candidate_ranks_service_page_high():
    cluster = _cluster()
    page = _crawl()[0]
    scored = score_url_candidate(
        page,
        cluster=cluster,
        primary_keyword="local seo melbourne",
        secondary_keywords=["seo agency melbourne"],
        cluster_intent="commercial",
        commercial={"products_for_promotion": ["local seo melbourne"]},
    )
    assert scored["url_score"] >= 50
    assert scored["score_breakdown"]["semantic_match"] > 0


def test_band_and_action_mapping():
    assert band_from_score(70) == "HIGH"
    assert band_from_score(45) == "MEDIUM"
    assert band_from_score(20) == "LOW"
    assert action_from_band("HIGH", has_existing_page=True) == "OPTIMIZE_EXISTING"
    assert action_from_band("MEDIUM", has_existing_page=True) == "REVIEW_MERGE_REDIRECT"
    assert action_from_band("LOW", has_existing_page=False) == "CREATE"
    assert action_from_band("LOW", has_existing_page=True) == "REVIEW_MERGE_REDIRECT"


def test_map_cluster_to_url_optimize_existing():
    out = map_cluster_to_url(
        _cluster(),
        crawled_pages=_crawl(),
        serp_summary={
            "validated": True,
            "organic": [{"position": 3, "url": "/services/local-seo-melbourne", "domain": "client.com"}],
        },
        commercial={"products_for_promotion": ["local seo melbourne"]},
    )
    assert out["action"] in ("OPTIMIZE_EXISTING", "REVIEW_MERGE_REDIRECT")
    assert out["selected_url"] == "/services/local-seo-melbourne"
    assert out["url_score"] >= 35
    assert out["url_candidates"]


def test_build_final_url_map_batch():
    report = build_final_url_map(
        {"clusters": [_cluster()]},
        website={"pages": _crawl()},
        commercial={"products_for_promotion": ["local seo melbourne"]},
        client_domain="client.com",
    )
    assert report["mapped_cluster_count"] == 1
    assert report["final_url_map"][0]["primary_keyword"] == "local seo melbourne"
    assert report["summary"]["optimize_existing"] + report["summary"]["review_merge_redirect"] >= 1


def test_url_n_normalizes():
    assert url_n("https://ex.com/Services/Local-SEO/") == "/services/local-seo"


def test_taxonomy_levels_from_path():
    tax = _taxonomy_levels("/services/local-seo/melbourne")
    assert tax["level"] == 2
    assert tax["l1_category"] == "Local Seo"
    assert tax["l2_subcategory"] == "Melbourne"
    assert tax["l3_subsubcategory"] is None
    assert tax["l4_attribution"] is None


def test_taxonomy_skips_structural_folders():
    hub = _taxonomy_levels("/product-category/accessories/")
    assert hub["level"] == 1
    assert hub["l1_category"] == "Accessories"
    assert hub["l2_subcategory"] is None
    nested = _taxonomy_levels("/product-category/beds-furniture/bed-frames/")
    assert nested["level"] == 2
    assert nested["l1_category"] == "Beds Furniture"
    assert nested["l2_subcategory"] == "Bed Frames"
    landing = _taxonomy_levels("/locations/adjustable-beds-brisbane/")
    assert landing["sheet_kind"] == "landing"
    assert landing["l1_category"] == "Adjustable Beds Brisbane"
    brand = _taxonomy_levels("/brands/ergoadjust/")
    assert brand["sheet_kind"] == "brand"
    assert brand["l1_category"] == "Ergoadjust"


def test_map_cluster_sheet_columns_optimize_existing():
    out = map_cluster_to_url(
        _cluster(),
        crawled_pages=_crawl(),
        serp_summary={"validated": True, "dominant_format": "service"},
        commercial={
            "products_for_promotion": ["local seo melbourne", "seo agency melbourne"],
        },
    )
    for col in SHEET_COLUMNS:
        assert col in out, f"missing sheet column: {col}"

    assert out["current_url"] == "/services/local-seo-melbourne"
    assert out["proposed_url"] is None
    assert out["status"] == "LIVE"
    assert out["primary_keyword"] == "local seo melbourne"
    assert out["search_volume"] == 1200
    assert out["cpc"] == 4.5
    assert out["combined_cluster_volume"] == 2000
    assert out["secondary_keywords_sheet"] == ["seo agency melbourne"]
    assert out["l1_category"] == "Local Seo Melbourne"
    assert out["l2_subcategory"] is None
    assert out["l4_attribution"] is None
    assert out["page_type"] == "Hub Page"
    assert out["priority"] == "P0"
    assert out["ia_page_type"]
    assert out["in_scope"] == "YES"
    assert out["est_products"] == 2
    assert isinstance(out["notes"], str) and out["notes"]


def test_map_cluster_sheet_columns_create_new():
    cluster = {
        "name": "CRM Software Guide",
        "primary_keyword": "best crm software",
        "intent": "informational",
        "keywords": [
            {"keyword": "best crm software", "role": "Primary", "volume": 5000, "cpc": 6.1},
            {"keyword": "crm tools comparison", "role": "Secondary", "volume": 900},
        ],
    }
    out = map_cluster_to_url(
        cluster,
        crawled_pages=[],
        serp_summary={"validated": False},
    )
    assert out["action"] == "CREATE"
    assert out["current_url"] is None
    assert out["proposed_url"] == "/blog/best-crm-software"
    assert out["status"] == "NEW"
    assert out["combined_cluster_volume"] == 5900
    assert out["secondary_keywords_sheet"] == ["crm tools comparison"]
    assert out["l1_category"] == "Best Crm Software"
    assert out["page_type"] == "Hub Page"
    # Priority weighs opportunity too: a new hub with 5,900/mo of demand is P0.
    assert out["priority"] == "P0"
    assert "5,900/mo" in out["priority_reason"]
    assert out["in_scope"] == "YES"


def test_map_cluster_low_score_existing_page_uses_current_url():
    """Weak match but real crawl page → Current URL, not Proposed."""
    cluster = {
        "name": "Marketing Tips",
        "primary_keyword": "marketing tips",
        "intent": "informational",
        "keywords": [{"keyword": "marketing tips", "role": "Primary", "volume": 200}],
    }
    crawl = [
        {
            "url": "/blog/marketing-tips",
            "path": "/blog/marketing-tips",
            "title": "Marketing Tips for SMBs",
        },
    ]
    out = map_cluster_to_url(cluster, crawled_pages=crawl)
    assert out["current_url"] == "/blog/marketing-tips"
    assert out["proposed_url"] is None
    assert out["action"] in ("OPTIMIZE_EXISTING", "REVIEW_MERGE_REDIRECT")


def test_secondary_keywords_are_the_cluster_not_a_modifier_pattern():
    """The mapping step takes the cluster's own measured keywords and invents none.

    Rows left short are topped up afterwards, in Phase 6, with variants that have a
    measured volume (tests/test_secondary_keywords.py); a place variant is never built.
    """
    cluster = {
        "name": "Bookcase",
        "primary_keyword": "bookcase",
        "keywords": [
            {"keyword": "bookcase", "role": "Primary", "volume": 9900},
            {"keyword": "bookcase with doors", "role": "Secondary", "volume": 400},
        ],
    }
    out = map_cluster_to_url(
        cluster,
        crawled_pages=[],
        commercial={"geography": "Brisbane, Australia"},
    )
    assert out["secondary_keywords_sheet"] == ["bookcase with doors"]
    assert out["combined_cluster_volume"] == 10300
    local = map_cluster_to_url(
        {
            "name": "Bedroom Furniture Brisbane",
            "primary_keyword": "bedroom furniture brisbane",
            "keywords": [{"keyword": "bedroom furniture brisbane", "volume": 210}],
        },
        crawled_pages=[],
        commercial={"geography": "Brisbane, Australia"},
    )
    assert local["secondary_keywords_sheet"] == []
    assert local["combined_cluster_volume"] == 210


def test_pillar_becomes_the_l1_category():
    report = build_final_url_map(
        {"clusters": [_cluster()]},
        website={"pages": _crawl()},
        pillar_structure=[{"pillar": "SEO", "clusters": [{"name": "Local SEO Melbourne"}]}],
    )
    row = report["final_url_map"][0]
    assert row["l1_category"] == "SEO"
    assert row["l2_subcategory"] == "Local SEO Melbourne"
    assert row["l3_subsubcategory"] is None
    assert row["l4_attribution"] is None
    assert row["page_type"] == "Landing Page"
    assert row["status"] == "LIVE"
    assert row["priority"] == "P0"
    assert row["priority_reason"] == "Landing Page +2, live +0.5, 2,000/mo +3, HIGH match +1 = 6.5 -> P0"
    assert row["pillar_cluster_id"] == row["cluster_id"] and not report["link_warnings"]


def test_in_scope_review_when_weak_relevance():
    cluster = _cluster()
    cluster["keywords"][0]["relevance_reason"] = "gap_only"
    out = map_cluster_to_url(cluster, crawled_pages=_crawl())
    assert out["in_scope"] == "REVIEW"


def test_rejected_clusters_are_not_url_mapped():
    kept = _cluster()
    dropped = {
        "name": "American Marketing Association Code of Ethics",
        "primary_keyword": "american marketing association code of ethics",
        "intent": "informational",
        "cluster_sense_verdict": "drop",
        "keywords": [
            {"keyword": "american marketing association code of ethics", "role": "Primary", "volume": 90}
        ],
    }
    report = build_final_url_map({"clusters": [kept, dropped]})
    primaries = [row["primary_keyword"] for row in report["final_url_map"]]
    assert primaries == ["local seo melbourne"]
    assert report["final_url_map"][0]["secondary_keywords"]


def test_build_final_url_map_includes_all_sheet_columns():
    report = build_final_url_map(
        {"clusters": [_cluster()]},
        website={"pages": _crawl()},
        commercial={"products_for_promotion": ["local seo melbourne"]},
    )
    row = report["final_url_map"][0]
    for col in SHEET_COLUMNS:
        assert col in row, f"missing sheet column on final_url_map: {col}"
