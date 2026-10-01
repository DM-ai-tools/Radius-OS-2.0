"""The #1 priority service picked at the selection gate drives seeding order."""

from app.services.service_prioritization import (
    confirm_prioritization,
    prioritize_top_service_roots,
    top_priority_service_id,
)

SEED_TARGETS = {
    "hydro jetting": {"target": "Hydro Jetting", "target_type": "service"},
    "drain cleaning": {"target": "Drain Cleaning", "target_type": "service"},
    "blocked drain": {"target": "blocked drain", "target_type": "keyword"},
}


def test_top_priority_service_roots_are_expanded_first():
    roots = prioritize_top_service_roots(
        ["hydro jetting", "blocked drain", "drain cleaning"],
        SEED_TARGETS,
        {"top_priority_service_id": "drain cleaning"},
    )
    assert roots[0] == "drain cleaning"


def test_order_is_unchanged_without_a_top_priority():
    roots = ["hydro jetting", "blocked drain", "drain cleaning"]
    assert prioritize_top_service_roots(roots, SEED_TARGETS, {}) == roots


def test_order_is_unchanged_when_priority_service_has_no_root():
    roots = ["hydro jetting", "blocked drain"]
    assert (
        prioritize_top_service_roots(
            roots, SEED_TARGETS, {"top_priority_service_id": "gas fitting"}
        )
        == roots
    )


def test_priority_matches_on_service_label_too():
    roots = prioritize_top_service_roots(
        ["hydro jetting", "drain cleaning"],
        SEED_TARGETS,
        {"top_priority_service_id": "Drain Cleaning"},
    )
    assert roots[0] == "drain cleaning"


def test_confirm_keeps_the_top_priority_selection():
    confirmed = confirm_prioritization(
        {
            "selected_service_ids": ["drain-cleaning", "hydro-jetting"],
            "selected_subservice_ids": [],
            "primary_service_ids": ["drain-cleaning"],
            "top_priority_service_id": "drain-cleaning",
        }
    )
    assert confirmed["top_priority_service_id"] == "drain-cleaning"
    assert top_priority_service_id(confirmed) == "drain-cleaning"


def test_confirm_drops_a_top_priority_that_was_not_selected():
    """A #1 service nobody selected would silently steer seeding."""
    confirmed = confirm_prioritization(
        {
            "selected_service_ids": ["hydro-jetting"],
            "selected_subservice_ids": [],
            "primary_service_ids": [],
            "top_priority_service_id": "drain-cleaning",
        }
    )
    assert confirmed["top_priority_service_id"] is None


def test_confirmed_top_priority_is_also_a_primary_service():
    confirmed = confirm_prioritization(
        {
            "selected_service_ids": ["drain-cleaning"],
            "selected_subservice_ids": [],
            "primary_service_ids": [],
            "top_priority_service_id": "drain-cleaning",
        }
    )
    assert "drain-cleaning" in confirmed["primary_service_ids"]


def test_cap_keeps_every_selected_service_even_past_budget():
    from app.services.service_prioritization import (
        cap_seed_roots_preserving_selected_services,
    )

    services = [
        "SEO",
        "Google Ads",
        "Meta Ads",
        "Email Marketing",
        "Web Design",
        "Content Writing",
        "Local SEO",
        "Link Building",
        "CRO",
        "Analytics",
    ]
    roots = list(services) + [
        "seo audit checklist",
        "ppc landing page tips",
        "email subject lines",
    ]
    targets = {
        s.lower(): {"target": s, "target_type": "service"} for s in services
    }
    targets["seo audit checklist"] = {
        "target": "SEO",
        "target_type": "keyword",
    }
    targets["ppc landing page tips"] = {
        "target": "Google Ads",
        "target_type": "page",
    }
    targets["email subject lines"] = {
        "target": "Email Marketing",
        "target_type": "keyword",
    }

    capped = cap_seed_roots_preserving_selected_services(
        roots,
        targets,
        selected_services=services,
        max_seed_roots=8,
    )
    for svc in services:
        assert svc in capped
    # Soft budget still limits extras once services fill the list.
    assert "seo audit checklist" not in capped or len(capped) > 8
    assert len([r for r in capped if r in services]) == len(services)


def test_cap_fills_remaining_budget_with_non_service_roots():
    from app.services.service_prioritization import (
        cap_seed_roots_preserving_selected_services,
    )

    roots = ["SEO", "Google Ads", "seo checklist", "blog ideas", "extra page"]
    targets = {
        "seo": {"target": "SEO", "target_type": "service"},
        "google ads": {"target": "Google Ads", "target_type": "service"},
        "seo checklist": {"target": "SEO", "target_type": "keyword"},
        "blog ideas": {"target": "SEO", "target_type": "page"},
        "extra page": {"target": "Google Ads", "target_type": "page"},
    }
    capped = cap_seed_roots_preserving_selected_services(
        roots,
        targets,
        selected_services=["SEO", "Google Ads"],
        max_seed_roots=4,
    )
    assert capped[:2] == ["SEO", "Google Ads"]
    assert capped[2:] == ["seo checklist", "blog ideas"]
