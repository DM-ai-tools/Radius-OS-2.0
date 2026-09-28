"""Per-service competitor links: validation, persistence shape, resolution."""

from __future__ import annotations

import pytest

from app.services.service_competitors import (
    InvalidCompetitorUrl,
    competitor_ids_for_service,
    detach_competitor_everywhere,
    normalize_domain,
    read_links,
    resolve_service_competitors,
    service_ids_in_catalog,
    set_service_competitors,
)

ROWS = [
    {"id": "c1", "name": "King Kong", "url": "https://kingkong.co", "source": "manual"},
    {"id": "c2", "name": "WebFX", "url": "https://www.webfx.com/", "source": "dataforseo"},
    {"id": "c3", "name": "Bad", "url": "not a domain", "source": "manual"},
]


# --- domain validation -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com", "example.com"),
        ("EXAMPLE.com", "example.com"),
        ("https://www.example.com/services/", "example.com"),
        ("http://example.co.uk?x=1", "example.co.uk"),
        ("example.com.", "example.com"),
        ("www.example.com", "example.com"),
        ("sub.example.com", "sub.example.com"),
        ("example.com/path", "example.com"),
    ],
)
def test_valid_domains_normalize(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", None, "not a domain", "localhost", "http://", "/services", "example", "-bad.com", "a..b"],
)
def test_invalid_domains_raise(raw):
    with pytest.raises(InvalidCompetitorUrl):
        normalize_domain(raw)


# --- link storage ----------------------------------------------------------


def test_setting_competitors_writes_under_service_prioritization():
    commercial, warnings = set_service_competitors(
        {}, service_id="seo", competitor_ids=["c1", "c2"]
    )
    assert commercial["service_prioritization"]["service_competitors"] == {"seo": ["c1", "c2"]}
    assert warnings == []


def test_setting_competitors_preserves_other_prioritization_keys():
    """That blob also holds the operator's pre-Phase-5 selection."""
    before = {
        "service_prioritization": {
            "selected_service_ids": ["seo"],
            "top_priority_service_id": "seo",
            "confirmed_at": "2026-09-01T00:00:00+00:00",
        }
    }
    after, _ = set_service_competitors(before, service_id="seo", competitor_ids=["c1"])
    prio = after["service_prioritization"]
    assert prio["selected_service_ids"] == ["seo"]
    assert prio["top_priority_service_id"] == "seo"
    assert prio["confirmed_at"] == "2026-09-01T00:00:00+00:00"
    assert prio["service_competitors"] == {"seo": ["c1"]}


def test_duplicate_competitors_are_collapsed_not_rejected():
    commercial, warnings = set_service_competitors(
        {}, service_id="seo", competitor_ids=["c1", "c1", "c2"]
    )
    assert competitor_ids_for_service(commercial, "seo") == ["c1", "c2"]
    assert any("Duplicate" in w for w in warnings)


def test_operator_order_is_preserved():
    commercial, _ = set_service_competitors(
        {}, service_id="seo", competitor_ids=["c2", "c1"]
    )
    assert competitor_ids_for_service(commercial, "seo") == ["c2", "c1"]


def test_an_empty_selection_removes_the_service_entry():
    commercial, _ = set_service_competitors({}, service_id="seo", competitor_ids=["c1"])
    commercial, _ = set_service_competitors(commercial, service_id="seo", competitor_ids=[])
    assert read_links(commercial) == {}


def test_competitors_from_another_client_are_dropped_with_a_warning():
    commercial, warnings = set_service_competitors(
        {}, service_id="seo", competitor_ids=["c1", "stolen"], known_ids={"c1", "c2"}
    )
    assert competitor_ids_for_service(commercial, "seo") == ["c1"]
    assert any("not on this client" in w for w in warnings)


def test_services_are_independent():
    commercial, _ = set_service_competitors({}, service_id="seo", competitor_ids=["c1"])
    commercial, _ = set_service_competitors(commercial, service_id="ppc", competitor_ids=["c2"])
    assert competitor_ids_for_service(commercial, "seo") == ["c1"]
    assert competitor_ids_for_service(commercial, "ppc") == ["c2"]


def test_a_service_with_no_link_has_no_competitors():
    assert competitor_ids_for_service({}, "anything") == []
    assert competitor_ids_for_service(None, "anything") == []


def test_a_blank_service_id_is_rejected():
    with pytest.raises(ValueError):
        set_service_competitors({}, service_id="  ", competitor_ids=["c1"])


def test_legacy_or_corrupt_link_blobs_do_not_raise():
    assert read_links({"service_prioritization": {"service_competitors": "nonsense"}}) == {}
    assert read_links({"service_prioritization": {"service_competitors": {"seo": "c1"}}}) == {}


def test_deleting_a_competitor_detaches_it_from_every_service():
    commercial, _ = set_service_competitors({}, service_id="seo", competitor_ids=["c1", "c2"])
    commercial, _ = set_service_competitors(commercial, service_id="ppc", competitor_ids=["c1"])
    commercial = detach_competitor_everywhere(commercial, "c1")
    assert competitor_ids_for_service(commercial, "seo") == ["c2"]
    assert competitor_ids_for_service(commercial, "ppc") == []


# --- catalog + resolution --------------------------------------------------


def test_catalog_ids_include_subservices():
    commercial = {
        "service_prioritization": {
            "service_catalog": [
                {"id": "seo", "name": "SEO", "subservices": [{"id": "local-seo", "name": "Local"}]},
                {"id": "ppc", "name": "PPC"},
            ]
        }
    }
    assert service_ids_in_catalog(commercial) == {"seo", "local-seo", "ppc"}


def test_resolution_returns_rows_in_operator_order_and_marks_them_confirmed():
    commercial, _ = set_service_competitors({}, service_id="seo", competitor_ids=["c2", "c1"])
    resolved = resolve_service_competitors(commercial, service_id="seo", competitor_rows=ROWS)
    assert [r["name"] for r in resolved] == ["WebFX", "King Kong"]
    assert all(r["confirmed"] is True for r in resolved)
    assert resolved[0]["domain"] == "webfx.com"
    assert resolved[0]["selected_for_service"] == "seo"


def test_resolution_skips_a_competitor_with_an_unusable_website():
    commercial, _ = set_service_competitors({}, service_id="seo", competitor_ids=["c3"])
    assert resolve_service_competitors(commercial, service_id="seo", competitor_rows=ROWS) == []


def test_resolution_skips_ids_that_no_longer_exist():
    commercial, _ = set_service_competitors({}, service_id="seo", competitor_ids=["gone"])
    assert resolve_service_competitors(commercial, service_id="seo", competitor_rows=ROWS) == []


def test_resolution_of_an_unlinked_service_is_empty():
    assert resolve_service_competitors({}, service_id="seo", competitor_rows=ROWS) == []
