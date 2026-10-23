"""Four QA layers that every agent report is scored on.

Contract checks the deliverable is shaped correctly. Honesty checks metrics were
not invented. Scope checks the work stays on this client's services. Judgement
is the semantic review. A layer with no checks is reported as skipped.
"""

from __future__ import annotations

from app.services.phase_validation.schema import CheckResult, CheckStatus

QA_LAYERS: tuple[tuple[str, str, str], ...] = (
    ("contract", "Contract", "The deliverable has the fields this phase must produce."),
    ("honesty", "Honesty", "Numbers and statuses come from a real measurement, not a guess."),
    ("scope", "Scope", "The work stays on this client's services and market."),
    ("judgement", "Judgement", "A reviewer reads the output against the phase purpose."),
)

_CONTRACT_IDS = {
    "output_present",
    "ia_tree_nodes",
    "publishing_mode",
    "audit_dispositions",
    "planning_lock_integrity",
    "production_one_page",
    "on_page_one_page",
    "search_demand_topics",
    "skip_validation",
    "unknown_phase",
}

_HONESTY_IDS = {
    "qa_metric_honesty",
    "tracking_honesty",
    "technical_seo_honesty",
    "technical_seo_cwv",
    "publishing_verify",
}

_SCOPE_IDS = {
    "qa_service_scope",
    "discovery_identity",
    "website_inventory",
    "competitor_peers",
    "search_demand_competitor_seeds",
    "search_demand_product_context",
    "strategy_phase5_carry",
    "planning_action_fit",
    "production_action_fit",
    "production_sub_service_scope",
}


def layer_id_for(check: CheckResult) -> str:
    if check.layer in {row[0] for row in QA_LAYERS}:
        return check.layer
    if check.source == "semantic" or check.check_id.startswith("semantic"):
        return "judgement"
    check_id = check.check_id.split(":", 1)[0]
    if check_id in _HONESTY_IDS or "honesty" in check.check_id or "cwv" in check.check_id:
        return "honesty"
    if check_id in _SCOPE_IDS:
        return "scope"
    if check_id in _CONTRACT_IDS or check_id == "required_key":
        return "contract"
    if check.parameter in {"honesty_anti_hallucination"}:
        return "honesty"
    if check.parameter in {
        "service_product_alignment",
        "industry_alignment",
        "domain_alignment",
        "geographic_market_alignment",
        "relevance",
    }:
        return "scope"
    return "contract"


def stamp_layers(checks: list[CheckResult]) -> None:
    for check in checks:
        check.layer = layer_id_for(check)


def build_layer_report(checks: list[CheckResult]) -> list[dict]:
    stamp_layers(checks)
    report: list[dict] = []
    for layer_id, label, purpose in QA_LAYERS:
        rows = [c for c in checks if c.layer == layer_id]
        failed = sum(1 for c in rows if c.status == CheckStatus.FAILED.value)
        warnings = sum(1 for c in rows if c.status == CheckStatus.WARNING.value)
        passed = sum(1 for c in rows if c.status == CheckStatus.PASSED.value)
        if not rows or all(c.status == CheckStatus.SKIPPED.value for c in rows):
            status = "skipped"
        elif failed:
            status = "failed"
        elif warnings:
            status = "warning"
        else:
            status = "passed"
        report.append(
            {
                "id": layer_id,
                "label": label,
                "purpose": purpose,
                "status": status,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
            }
        )
    return report
