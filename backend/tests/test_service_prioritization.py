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
        services=["SEO", "PPC"],
        page_seeds=[
            {
                "term": "Local SEO",
                "page_type": "sub_service",
                "parent_segment": "seo",
                "path": "/services/seo/local-seo",
            },
        ],
    )
    seo = next(s for s in catalog if s["name"] == "SEO")
    assert any(sub["name"] == "Local SEO" for sub in seo["subservices"])


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


def test_merge_competitor_subservices():
    catalog = build_client_service_catalog(services=["SEO"])
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
    catalog = build_client_service_catalog(services=["SEO"])
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
    catalog = build_client_service_catalog(services=["SEO"])
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
        seo_strategy={},
        commercial={},
        website={},
    )
    urls = {node["url"]: node for node in blueprint["target_url_tree"]}
    assert urls["/content-marketing"]["page_type"] == "service"
    assert urls["/content-marketing"]["parent"] == "/"


def test_prioritization_pack_and_confirm():
    pack = build_prioritization_pack(
        client_name="Acme",
        services=["SEO", "PPC"],
        competitor_trees=[],
    )
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
        page_seeds=[],
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
