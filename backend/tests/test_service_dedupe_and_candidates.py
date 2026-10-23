"""Duplicate service names merge; top-level pages surface as opt-in candidates.

Seen in stored runs:
  * Click Trends listed "Web Development" and "Website Development" as two services.
  * ARG Finance's /bridging-finance, /refinancing, /land-banking and /rentvesting
    pages are real offerings, but the conservative page classifier files them as
    plain pages, so they never reached the service selection card.
"""

import pytest

from app.agents.search_demand import _primary_client_services, _products_list
from app.services.service_prioritization import (
    build_client_service_catalog,
    build_prioritization_pack,
    confirm_prioritization,
    default_selection,
    merge_competitor_subservices,
    service_identity_key,
)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Web Development", "Website Development"),
        ("Website Development", "website  development"),
        ("SEO", "SEO Services"),
        ("Email Marketing", "email marketing services"),
        ("Home Loans", "Home loan"),
    ],
)
def test_same_offering_gets_the_same_identity_key(a, b):
    assert service_identity_key(a) == service_identity_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Web Development", "Web Design"),
        ("SEO", "Local SEO"),
        ("Google Ads", "Meta Ads"),
        ("Google Ads", "Google Ads/PPC Management"),
        ("Business Loans", "Commercial Loans"),
    ],
)
def test_different_offerings_keep_different_keys(a, b):
    assert service_identity_key(a) != service_identity_key(b)


def test_identity_key_never_collapses_a_name_to_nothing():
    assert service_identity_key("Services") == "services"
    assert service_identity_key("") == ""


def test_products_list_drops_equivalent_names_and_keeps_the_first_spelling():
    out = _products_list(
        {"products": ["Web Development", "SEO", "Email Marketing"]},
        {"products_for_promotion": ["Website Development", "SEO Services"]},
        {},
    )
    assert out == ["Web Development", "SEO", "Email Marketing"]


def test_primary_services_card_list_drops_equivalent_names():
    out = _primary_client_services(
        {"products": ["Web Development", "Website Development", "Meta Ads"]}, {}, {}
    )
    assert out == ["Web Development", "Meta Ads"]


def test_competitor_service_with_a_variant_name_attaches_to_the_clients_service():
    catalog = [
        {"id": "web development", "name": "Web Development", "source": "website", "subservices": []}
    ]
    tree = {
        "name": "Rival",
        "competitor_id": "rival",
        "services": [
            {
                "id": "website-development",
                "name": "Website Development",
                "subservices": [{"id": "shopify", "name": "Shopify"}],
            }
        ],
    }
    out = merge_competitor_subservices(catalog, competitor_tree=tree, competitor_trees=[tree])
    assert [s["name"] for s in out] == ["Web Development"]  # no duplicate candidate row
    assert [s["name"] for s in out[0]["subservices"]] == ["Shopify"]


# --- page candidates ---------------------------------------------------------


def _site(*paths: str, service_paths: tuple[str, ...] = ()) -> dict:
    rows = [
        {"title": p, "path": p, "url": f"https://x.com{p}", "page_type": "page", "cluster": "other"}
        for p in paths
    ]
    rows += [
        {"title": p, "path": p, "url": f"https://x.com{p}", "page_type": "service", "cluster": "services"}
        for p in service_paths
    ]
    return {"page_hierarchy": rows}


def test_top_level_pages_become_unselected_candidate_services():
    catalog = build_client_service_catalog(
        services=[],
        website=_site("/bridging-finance", "/refinancing", "/land-banking", "/rentvesting"),
        page_seeds=[],
    )
    assert [c["name"] for c in catalog] == [
        "Bridging Finance",
        "Land Banking",
        "Refinancing",
        "Rentvesting",
    ]
    for row in catalog:
        assert row["candidate_new_service"] is True
        assert row["source"] == "website_page"
        assert row["selected"] is False
        assert row["priority_tier"] is None
        assert row["path"].startswith("/")


def test_non_service_pages_are_not_offered_as_candidates():
    catalog = build_client_service_catalog(
        services=[],
        website=_site(
            "/about-us",
            "/contact",
            "/careers",
            "/awards",
            "/privacy-policy",
            "/terms-and-conditions",
            "/comparison_rate_calculator",
            "/coronavirus-crisis",
            "/faq",
            "/testimonials",
            "/login",
            "/borrow-now/apply",  # two segments: not a top-level page
        ),
        page_seeds=[],
    )
    assert catalog == []


def test_a_page_already_a_service_is_not_also_a_candidate():
    page_seeds = [{"term": "/refinancing", "page_type": "service", "path": "/refinancing"}]
    catalog = build_client_service_catalog(
        services=[],
        website=_site("/refinancing", "/land-banking"),
        page_seeds=page_seeds,
    )
    by_name = {c["name"]: c for c in catalog}
    assert by_name["Refinancing"].get("candidate_new_service") is not True
    assert by_name["Land Banking"]["candidate_new_service"] is True
    assert len(catalog) == 2


def test_a_candidate_equivalent_to_an_existing_service_is_dropped():
    page_seeds = [{"term": "x", "page_type": "service", "path": "/web-development"}]
    catalog = build_client_service_catalog(
        services=[], website=_site("/website-development"), page_seeds=page_seeds
    )
    assert [c["name"] for c in catalog] == ["Web Development"]


def test_candidates_are_opt_in_and_never_selected_by_default():
    catalog = build_client_service_catalog(
        services=[], website=_site("/bridging-finance"), page_seeds=[]
    )
    assert default_selection(catalog)["selected_service_ids"] == []


def test_adopting_a_page_candidate_records_where_it_came_from():
    pack = build_prioritization_pack(
        client_name="ARG",
        services=[],
        website=_site("/bridging-finance"),
        page_seeds=[],
    )
    out = confirm_prioritization(
        {
            "selected_service_ids": ["bridging finance"],
            "adopt_competitor_service_ids": ["bridging finance"],
            "service_catalog": pack["service_catalog"],
        }
    )
    row = out["service_catalog"][0]
    assert row["source"] == "adopted"
    assert row["adopted_from"] == "website_page"
    assert out["adopt_competitor_service_ids"] == ["bridging finance"]
