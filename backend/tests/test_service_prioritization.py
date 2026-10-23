"""Service prioritization — pre Phase 5 selection."""

from app.services.service_prioritization import (
    _paths_to_service_nodes,
    apply_service_selection,
    build_client_service_catalog,
    build_prioritization_pack,
    confirm_prioritization,
    default_selection,
    expand_vertical_subservice_seeds,
    ia_nodes_from_competitor_tree,
    is_prioritization_confirmed,
    merge_competitor_subservices,
)


def test_paths_to_service_nodes():
    nodes = _paths_to_service_nodes(
        [
            "https://ex.com/services/seo",
            "https://ex.com/services/seo/local-seo",
            "https://ex.com/services/ppc/google-ads",
            "https://ex.com/blog/tips",
        ]
    )
    assert len(nodes) == 2
    seo = next(n for n in nodes if n["id"] == "seo")
    assert any(s["id"] == "local-seo" for s in seo["subservices"])


def test_build_client_service_catalog_with_subservices():
    catalog = build_client_service_catalog(
        services=["21/09/2026 (draft)"],
        page_seeds=[
            {"term": "SEO", "page_type": "service", "path": "/services/seo"},
            {"term": "PPC", "page_type": "service", "path": "/services/ppc"},
            {
                "term": "Local SEO",
                "page_type": "sub_service",
                "parent_segment": "seo",
                "path": "/services/seo/local-seo",
            },
        ],
    )
    assert {s["name"] for s in catalog} == {"SEO", "PPC"}
    assert all(s["source"] == "website" for s in catalog)
    seo = next(s for s in catalog if s["name"] == "SEO")
    assert any(sub["name"] == "Local SEO" for sub in seo["subservices"])


def test_catalog_uses_website_pages_not_cdd_names():
    catalog = build_client_service_catalog(
        services=["21/09/2026 (draft)", "SEO"],
        page_seeds=[
            {
                "term": "Technical SEO",
                "page_type": "service",
                "path": "/services/technical-seo",
            },
            {
                "term": "Web Design",
                "page_type": "core_service",
                "path": "/services/web-design",
            },
            {
                "term": "Local SEO",
                "page_type": "sub_service",
                "parent_segment": "technical-seo",
                "path": "/services/technical-seo/local-seo",
            },
        ],
    )
    names = {s["name"] for s in catalog}
    assert names == {"Technical SEO", "Web Design"}
    tech = next(s for s in catalog if s["name"] == "Technical SEO")
    assert tech["source"] == "website"
    assert any(sub["name"] == "Local SEO" for sub in tech["subservices"])


def test_catalog_collapses_location_copies_and_drops_articles():
    catalog = build_client_service_catalog(
        services=[],
        page_seeds=[
            {
                "term": "AI SEO Services Adelaide | Clicktrends - Click Trends",
                "page_type": "service",
                "path": "/ai-seo-services-adelaide/",
            },
            {
                "term": "AI Search Optimization Services - Click Trends",
                "page_type": "service",
                "path": "/ai-seo-services-geelong/",
            },
            {
                "term": "SEO Services Melbourne | Award-Winning SEO Agency | ClickTrends",
                "page_type": "service",
                "path": "/seo-services-melbourne/",
            },
            {
                "term": "what are the advantages of hiring shopify development services",
                "page_type": "service",
                "path": "/what-are-the-advantages-of-hiring-shopify-development-services/",
            },
            {
                "term": "Magento Development for Business Expansion | Click Trends",
                "page_type": "service",
                "path": "/how-magento-development-services-help-with-business-expansion/",
            },
            {
                "term": "services aeo geo 2",
                "page_type": "service",
                "path": "/services-aeo-geo-2/",
            },
            {
                "term": "/services-aeo-geo/",
                "page_type": "service",
                "path": "/services-aeo-geo/",
            },
            {
                "term": "Shopify Development Services in Melbourne | Click Trends",
                "page_type": "service",
                "path": "/best-shopify-development-services-melbourne/",
            },
            {
                "term": "Conversion Rate Optimisation Services | Click Trends",
                "page_type": "service",
                "path": "/conversion-rate-optimisation-services/",
            },
            {
                "term": "Conversion Rate Optimization Services - Click Trends",
                "page_type": "service",
                "path": "/conversion-rate-optimization/",
            },
        ],
    )
    names = {row["name"] for row in catalog}
    assert "AI SEO" in names
    assert "SEO" in names
    assert "Shopify Development" in names
    assert "AEO GEO" in names
    assert any(name.startswith("Conversion Rate Optimi") for name in names)
    assert not any("adelaide" in name.lower() or "geelong" in name.lower() or "melbourne" in name.lower() for name in names)
    assert not any(name.lower().startswith("what ") or name.lower().startswith("how ") for name in names)
    assert sum(1 for name in names if name.startswith("Conversion Rate")) == 1
    assert sum(1 for name in names if name == "AI SEO") == 1
    assert sum(1 for name in names if name == "AEO GEO") == 1


def test_website_pages_fill_catalog_only_when_cdd_empty():
    catalog = build_client_service_catalog(
        services=[],
        page_seeds=[
            {
                "term": "Drain Cleaning",
                "page_type": "service",
                "path": "/services/drain-cleaning",
            },
        ],
    )
    assert len(catalog) == 1
    assert catalog[0]["name"] == "Drain Cleaning"
    assert catalog[0]["source"] == "website"


def test_apply_service_selection_filters_services():
    services = ["SEO", "PPC", "Web Design"]
    targets = {
        "seo": {"target": "SEO", "target_type": "service"},
        "ppc": {"target": "PPC", "target_type": "service"},
        "web design": {"target": "Web Design", "target_type": "service"},
        "local seo": {
            "target": "Local SEO",
            "target_type": "sub_service",
            "parent_segment": "seo",
        },
    }
    filtered_services, filtered_targets = apply_service_selection(
        services,
        targets,
        {"selected_service_ids": ["seo"], "selected_subservice_ids": []},
    )
    assert filtered_services == ["SEO"]
    assert "ppc" not in filtered_targets
    assert "local seo" in filtered_targets


def test_apply_service_selection_drops_unselected_keyword_and_page_seeds():
    """SEO / email themes must not expand when the user only picked Meta Ads."""
    from app.services.service_prioritization import term_matches_selected_services

    services = ["Meta Ads", "Google Ads/PPC Management", "SEO", "Email Marketing"]
    targets = {
        "meta ads": {"target": "Meta Ads", "target_type": "service"},
        "google ads/ppc management": {
            "target": "Google Ads/PPC Management",
            "target_type": "service",
        },
        "seo": {"target": "SEO", "target_type": "service"},
        "local seo melbourne": {
            "target": "Local SEO Melbourne",
            "target_type": "keyword",
        },
        "seo marketing melbourne": {
            "target": "seo marketing melbourne",
            "target_type": "keyword",
        },
        "email marketing agency": {
            "target": "email marketing agency",
            "target_type": "keyword",
        },
        "meta ads melbourne": {
            "target": "Meta Ads Melbourne",
            "target_type": "keyword",
        },
        "google ads consultant": {
            "target": "google ads consultant",
            "target_type": "page",
        },
    }
    filtered_services, filtered_targets = apply_service_selection(
        services,
        targets,
        {
            "selected_service_ids": ["meta ads", "google ads/ppc management"],
            "selected_subservice_ids": [],
            "confirmed_at": "2026-01-01T00:00:00Z",
        },
    )
    assert set(filtered_services) == {"Meta Ads", "Google Ads/PPC Management"}
    assert "seo" not in filtered_targets
    assert "local seo melbourne" not in filtered_targets
    assert "seo marketing melbourne" not in filtered_targets
    assert "email marketing agency" not in filtered_targets
    assert "meta ads melbourne" in filtered_targets
    assert "google ads consultant" in filtered_targets
    assert term_matches_selected_services("meta ads pricing", ["Meta Ads"])
    assert not term_matches_selected_services(
        "seo marketing melbourne", ["Meta Ads", "Google Ads/PPC Management"]
    )


def test_term_matches_does_not_map_google_ads_to_meta_ads():
    """Shared 'ads' must not treat Google Ads as Meta Ads (or vice versa)."""
    from app.services.service_prioritization import term_matches_selected_services

    assert term_matches_selected_services("meta ads", ["Meta Ads"])
    assert term_matches_selected_services("facebook ads melbourne", ["Meta Ads"])
    assert term_matches_selected_services("instagram ads agency", ["Meta Ads"])
    assert not term_matches_selected_services("google ads", ["Meta Ads"])
    assert not term_matches_selected_services(
        "google ads ppc management", ["Meta Ads"]
    )
    assert not term_matches_selected_services("google ads consultant", ["Meta Ads"])
    assert term_matches_selected_services(
        "google ads", ["Google Ads/PPC Management"]
    )
    assert not term_matches_selected_services("meta ads", ["Google Ads/PPC Management"])


def test_apply_service_selection_drops_google_when_only_meta_selected():
    from app.services.service_prioritization import apply_service_selection

    services = ["Meta Ads", "Google Ads/PPC Management", "SEO"]
    targets = {
        "meta ads": {"target": "Meta Ads", "target_type": "service"},
        "google ads/ppc management": {
            "target": "Google Ads/PPC Management",
            "target_type": "service",
        },
        "google ads consultant": {
            "target": "google ads consultant",
            "target_type": "page",
        },
        "facebook ads agency": {
            "target": "facebook ads agency",
            "target_type": "keyword",
        },
    }
    filtered_services, filtered_targets = apply_service_selection(
        services,
        targets,
        {
            "selected_service_ids": ["meta ads"],
            "selected_subservice_ids": [],
            "confirmed_at": "2026-01-01T00:00:00Z",
        },
    )
    assert filtered_services == ["Meta Ads"]
    assert "meta ads" in filtered_targets
    assert "facebook ads agency" in filtered_targets
    assert "google ads/ppc management" not in filtered_targets
    assert "google ads consultant" not in filtered_targets


def test_apply_service_selection_respects_slash_vs_space_ids():
    """Catalog ids keep '/', cleaned seeds drop them — selection must still stick."""
    from app.services.service_prioritization import apply_service_selection

    services = ["Google Ads/PPC Management", "Meta Ads"]
    targets = {
        "google ads ppc management": {
            "target": "Google Ads/PPC Management",
            "target_type": "service",
        },
        "meta ads": {"target": "Meta Ads", "target_type": "service"},
    }
    filtered_services, filtered_targets = apply_service_selection(
        services,
        targets,
        {
            "selected_service_ids": ["google ads/ppc management"],
            "service_catalog": [
                {"id": "google ads/ppc management", "name": "Google Ads/PPC Management"},
                {"id": "meta ads", "name": "Meta Ads"},
            ],
            "confirmed_at": "2026-01-01T00:00:00Z",
        },
    )
    assert filtered_services == ["Google Ads/PPC Management"]
    assert "google ads ppc management" in filtered_targets
    assert "meta ads" not in filtered_targets


def test_apply_service_selection_never_falls_back_to_all_services():
    """Unknown selection ids must not silently expand every CDD service."""
    from app.services.service_prioritization import apply_service_selection

    services = ["Meta Ads", "SEO", "Email Marketing"]
    targets = {
        "meta ads": {"target": "Meta Ads", "target_type": "service"},
        "seo": {"target": "SEO", "target_type": "service"},
    }
    filtered_services, filtered_targets = apply_service_selection(
        services,
        targets,
        {
            "selected_service_ids": ["totally-unknown-service"],
            "confirmed_at": "2026-01-01T00:00:00Z",
        },
    )
    assert filtered_services == []
    assert filtered_targets == {}


def test_apply_service_selection_uses_catalog_name_when_products_differ():
    from app.services.service_prioritization import apply_service_selection

    filtered_services, filtered_targets = apply_service_selection(
        ["Email Marketing"],  # products list missing Meta
        {"email marketing": {"target": "Email Marketing", "target_type": "service"}},
        {
            "selected_service_ids": ["meta ads"],
            "service_catalog": [
                {"id": "meta ads", "name": "Meta Ads"},
                {"id": "email marketing", "name": "Email Marketing"},
            ],
            "confirmed_at": "2026-01-01T00:00:00Z",
        },
    )
    assert filtered_services == ["Meta Ads"]
    assert "email marketing" not in filtered_targets


def test_merge_competitor_subservices():
    catalog = build_client_service_catalog(
        services=["SEO"],
        page_seeds=[{"term": "SEO", "page_type": "service", "path": "/services/seo"}],
    )
    tree = {
        "competitor_id": "comp",
        "name": "Comp",
        "services": [
            {
                "id": "seo",
                "name": "Seo",
                "subservices": [{"id": "local-seo", "name": "Local Seo"}],
            }
        ],
    }
    out = merge_competitor_subservices(catalog, competitor_tree=tree, adopt_service_ids=["seo"])
    seo = out[0]
    assert any(s.get("source") == "competitor" for s in seo["subservices"])


def test_merge_competitor_subservices_uses_all_trees():
    catalog = build_client_service_catalog(
        services=["SEO"],
        page_seeds=[{"term": "SEO", "page_type": "service", "path": "/services/seo"}],
    )
    trees = [
        {
            "competitor_id": "one",
            "name": "One",
            "services": [{"name": "SEO", "subservices": [{"id": "local-seo", "name": "Local SEO"}]}],
        },
        {
            "competitor_id": "two",
            "name": "Two",
            "services": [{"name": "SEO", "subservices": [{"id": "technical-seo", "name": "Technical SEO"}]}],
        },
    ]
    out = merge_competitor_subservices(catalog, competitor_tree=None, competitor_trees=trees)
    subs = out[0]["subservices"]
    assert {s["id"] for s in subs} >= {"local-seo", "technical-seo"}


def test_consensus_service_candidate_is_opt_in_and_can_be_adopted():
    catalog = build_client_service_catalog(
        services=["SEO"],
        page_seeds=[{"term": "SEO", "page_type": "service", "path": "/services/seo"}],
    )
    trees = [
        {
            "competitor_id": "one",
            "name": "One",
            "services": [
                {
                    "id": "content-marketing",
                    "name": "Content Marketing",
                    "subservices": [{"id": "content-strategy", "name": "Content Strategy"}],
                },
                {"id": "cro", "name": "CRO", "subservices": []},
            ],
        },
        {
            "competitor_id": "two",
            "name": "Two",
            "services": [
                {
                    "id": "content-marketing",
                    "name": "Content Marketing",
                    "subservices": [{"id": "blog-content", "name": "Blog Content"}],
                }
            ],
        },
    ]
    merged = merge_competitor_subservices(
        catalog,
        competitor_tree=None,
        competitor_trees=trees,
    )
    assert not any(s["name"] == "CRO" for s in merged)
    candidate = next(s for s in merged if s["name"] == "Content Marketing")
    assert candidate["source"] == "competitor"
    assert candidate["candidate_new_service"] is True
    assert candidate["selected"] is False
    assert candidate["priority_tier"] is None
    assert candidate["competitor_count"] == 2

    defaults = default_selection(merged)
    assert "content-marketing" not in defaults["selected_service_ids"]

    confirmed = confirm_prioritization(
        {
            "selected_service_ids": ["seo", "content-marketing"],
            "selected_subservice_ids": ["content-strategy", "blog-content"],
            "primary_service_ids": ["seo"],
            "adopt_competitor_service_ids": ["content-marketing"],
            "service_catalog": merged,
        }
    )
    promoted = next(
        s for s in confirmed["service_catalog"] if s["id"] == "content-marketing"
    )
    assert promoted["source"] == "adopted"
    assert promoted["candidate_new_service"] is False
    assert promoted["selected"] is True
    assert confirmed["adopt_competitor_service_ids"] == ["content-marketing"]

    from app.services.site_architecture import build_blueprint

    blueprint = build_blueprint(
        client_name="Acme",
        domain="acme.example",
        current_state={"urls_crawled": 0, "issues": []},
        demand={
            "services": ["SEO", "Content Marketing"],
            "service_catalog": confirmed["service_catalog"],
            "service_prioritization": confirmed,
        },
        commercial={},
        website={},
    )
    urls = {node["url"]: node for node in blueprint["target_url_tree"]}
    assert urls["/content-marketing"]["page_type"] == "service"
    assert urls["/content-marketing"]["parent"] == "/"


def test_industry_list_includes_a_service_offered_by_one_competitor_and_a_manual_add():
    pack = build_prioritization_pack(
        client_name="Acme",
        services=[],
        page_seeds=[{"term": "SEO", "page_type": "service", "path": "/services/seo"}],
        competitor_trees=[
            {
                "competitor_id": "one",
                "name": "One",
                "services": [{"id": "cro", "name": "CRO", "subservices": []}],
            }
        ],
        existing={
            "service_catalog": [
                {"id": "email marketing", "name": "Email Marketing", "source": "manual"}
            ]
        },
    )
    by_name = {row["name"]: row for row in pack["service_catalog"]}
    assert by_name["SEO"]["source"] == "website"
    assert by_name["CRO"]["source"] == "competitor"
    assert by_name["CRO"]["candidate_new_service"] is True
    assert by_name["Email Marketing"]["source"] == "manual"
    assert "cro" not in pack["defaults"]["selected_service_ids"]
    assert "email marketing" in pack["defaults"]["selected_service_ids"]


def test_prioritization_pack_and_confirm():
    pack = build_prioritization_pack(
        client_name="Acme",
        services=["21/09/2026 (draft)"],
        page_seeds=[
            {"term": "SEO", "page_type": "service", "path": "/services/seo"},
            {"term": "PPC", "page_type": "service", "path": "/services/ppc"},
        ],
        competitor_trees=[],
    )
    assert {row["name"] for row in pack["service_catalog"]} == {"SEO", "PPC"}
    assert pack["service_catalog"]
    assert pack["defaults"]["selected_service_ids"]
    confirmed = confirm_prioritization(
        {
            "selected_service_ids": ["seo"],
            "selected_subservice_ids": [],
            "primary_service_ids": ["seo"],
        }
    )
    assert is_prioritization_confirmed(confirmed)


def test_expand_vertical_subservice_seeds_adds_missing_roots():
    catalog = build_client_service_catalog(
        services=["SEO"],
        page_seeds=[{"term": "SEO", "page_type": "service", "path": "/services/seo"}],
    )
    catalog[0]["subservices"] = [{"id": "technical-seo", "name": "Technical SEO"}]
    roots, targets = expand_vertical_subservice_seeds(
        ["SEO"],
        {"seo": {"target": "SEO", "target_type": "service"}},
        prioritization={
            "confirmed_at": "2026-01-01T00:00:00Z",
            "selected_service_ids": ["seo"],
            "selected_subservice_ids": ["technical-seo"],
        },
        service_catalog=catalog,
    )
    assert "Technical SEO" in roots
    assert "technical seo" in targets
    assert targets["technical seo"]["target_type"] == "seed"
    assert targets["technical seo"]["target"] == "SEO"
    assert targets["technical seo"]["parent_segment"] == "SEO"


def test_ia_nodes_from_competitor_tree():
    tree = {
        "name": "Comp",
        "services": [
            {
                "id": "seo",
                "name": "SEO",
                "subservices": [{"id": "local-seo", "name": "Local SEO"}],
            }
        ],
    }
    nodes = ia_nodes_from_competitor_tree(
        tree,
        domain="example.com",
        selected_service_ids=["seo"],
    )
    assert len(nodes) == 2
    by_url = {n["url"]: n for n in nodes}
    assert by_url["/seo"]["page_type"] == "service"
    assert by_url["/seo"]["parent"] == "/"
    assert by_url["/seo/local-seo"]["page_type"] == "subservice"
    assert by_url["/seo/local-seo"]["parent"] == "/seo/"
