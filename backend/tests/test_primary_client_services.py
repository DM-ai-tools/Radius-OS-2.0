"""Primary client services for the prioritization card."""

from app.agents.search_demand import _primary_client_services, _products_list


def test_primary_services_prefer_products_over_promotion_union():
    commercial = {
        "products": ["Drain Cleaning", "Hydro Jetting"],
        "products_for_promotion": [
            "Drain Cleaning",
            "Hydro Jetting",
            "Camera Inspection",
            "Water Heater Repair",
            "Emergency Plumbing",
            "Slab Leak Detection",
        ],
    }
    primary = _primary_client_services(commercial, {}, {})
    full = _products_list(commercial, {}, {})
    assert primary == ["Drain Cleaning", "Hydro Jetting"]
    assert len(full) > len(primary)


def test_primary_services_fall_back_to_promotion_when_products_empty():
    commercial = {"products_for_promotion": ["SEO", "PPC"]}
    assert _primary_client_services(commercial, {}, {}) == ["SEO", "PPC"]


def test_page_seeds_name_the_website_page_not_the_cdd_term():
    from app.agents.search_demand import _page_target_seeds

    seeds = _page_target_seeds(
        {
            "page_hierarchy": [
                {
                    "page_type": "service",
                    "cluster": "services",
                    "title": "Drain Cleaning",
                    "path": "/services/drain-cleaning",
                    "cdd_terms": ["21/09/2026 (draft)"],
                },
                {
                    "page_type": "core_service",
                    "cluster": "service_hub",
                    "title": "Web Design",
                    "path": "/web-design",
                },
            ]
        }
    )
    by_path = {row["path"]: row for row in seeds}
    assert by_path["/services/drain-cleaning"]["term"] == "Drain Cleaning"
    assert by_path["/services/drain-cleaning"]["page_type"] == "service"
    assert by_path["/web-design"]["term"] == "Web Design"
    assert by_path["/web-design"]["page_type"] == "service"
