"""Phase 2 tracking reports only what was actually checked (2026-10-19 cleanup).

Removed: cookie-consent and referral-exclusion rows that were "warning" for
every client without a check; four placeholder "secondary" conversions; a
twelve-metric historical baseline that was always empty; checklist rows for
manual platforms with nothing recorded; and the tracking blob technical SEO
received and discarded. Kept: the live tag checks, the primary conversion,
OAuth status, Discovery access notes, known changes, blockers and the gate.
"""

from __future__ import annotations

import inspect

from app.services.tracking_workflow import (
    build_t1_platforms,
    build_t2_audit,
    build_t3_conversions,
    build_t4_baseline,
    compute_tracking_blockers,
)

CHECKS = [
    {"element": "ga4_base_tag", "check_result": "pass", "detail": {"message": "GA4 tag detected."}},
    {"element": "gtm_container", "check_result": "warning", "detail": {"message": "No GTM."}},
    {"element": "cross_domain_tracking", "check_result": "warning", "detail": {"message": "No linker."}},
    {"element": "conversion_event", "check_result": "unverified", "detail": {"message": "Needs GA4 access."}},
]


def test_audit_lists_only_checks_that_ran():
    items = build_t2_audit(CHECKS)["items"]
    assert [i["element"] for i in items] == ["ga4_base_tag", "gtm_container", "cross_domain_tracking", "conversion_event"]
    assert not {"cookie_consent", "referral_exclusions"} & {i["element"] for i in items}


def test_conversions_show_only_the_checked_primary_conversion():
    events = build_t3_conversions(CHECKS)["events"]
    assert events == [{"name": "Primary conversion (e.g. purchase / lead)", "status": "unverified"}]


def test_baseline_says_nothing_was_collected_instead_of_empty_metrics():
    baseline = build_t4_baseline(has_ga4=True, has_gsc=False, display_name="Acme")
    assert "metrics" not in baseline
    assert baseline["status"] == "not_collected"
    assert "No historical traffic data was collected" in baseline["note"]
    assert "Connected: GA4." in baseline["note"]
    none = build_t4_baseline(has_ga4=False, has_gsc=False, display_name="Acme")
    assert "not connected" in none["note"]


def test_platforms_keep_oauth_rows_and_recorded_manual_ones_only():
    rows = build_t1_platforms({"ga4": True}, cdd_access={"hosting_access": "Client shares cPanel"})
    keys = [r["key"] for r in rows]
    assert keys == ["hosting", "search_console", "ga4", "gtm"]
    assert next(r for r in rows if r["key"] == "ga4")["status"] == "connected"
    assert next(r for r in rows if r["key"] == "hosting")["detail"] == "CDD: Client shares cPanel"
    assert "crm" not in keys and "call_tracking" not in keys and "cms" not in keys


def test_blockers_still_come_from_the_live_ga4_check():
    assert compute_tracking_blockers([{**CHECKS[0], "check_result": "fail"}])
    assert compute_tracking_blockers(CHECKS) == []


def test_technical_seo_no_longer_takes_the_unused_tracking_blob():
    from app.services.technical_seo import run_technical_seo_plan

    assert "tracking" not in inspect.signature(run_technical_seo_plan).parameters
