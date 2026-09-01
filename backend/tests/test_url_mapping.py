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
    assert tax["level"] == 3
    assert tax["l1_category"] == "Services"
    assert tax["l2_subcategory"] == "Local Seo"
    assert tax["l3_subsubcategory"] == "Melbourne"
    assert tax["l4_attribution"] is None


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
    assert out["status"] in ("Optimize Existing", "Review / Merge / Redirect")
    assert out["primary_keyword"] == "local seo melbourne"
    assert out["search_volume"] == 1200
    assert out["cpc"] == 4.5
    assert out["combined_cluster_volume"] == 2000
    assert "seo agency melbourne" in out["secondary_keywords_sheet"]
    assert out["l1_category"] == "Services"
    assert out["l2_subcategory"] == "Local Seo Melbourne"
    assert out["priority"] in ("High", "Medium", "Low")
    assert out["in_scope"] == "Yes"
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
    assert out["status"] == "Create New"
    assert out["combined_cluster_volume"] == 5900
    assert out["l1_category"] == "Blog"
    assert out["in_scope"] == "Yes"


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


def test_in_scope_review_when_weak_relevance():
    cluster = _cluster()
    cluster["keywords"][0]["relevance_reason"] = "gap_only"
    out = map_cluster_to_url(cluster, crawled_pages=_crawl())
    assert out["in_scope"] == "Review"


def test_build_final_url_map_includes_all_sheet_columns():
    report = build_final_url_map(
        {"clusters": [_cluster()]},
        website={"pages": _crawl()},
        commercial={"products_for_promotion": ["local seo melbourne"]},
    )
    row = report["final_url_map"][0]
    for col in SHEET_COLUMNS:
        assert col in row, f"missing sheet column on final_url_map: {col}"
