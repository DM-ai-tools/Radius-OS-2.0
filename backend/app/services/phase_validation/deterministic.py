"""Deterministic validation — schema, required fields, join integrity, honesty heuristics.

These checks must not depend on an LLM.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.services.phase_validation.criteria import PhaseCriteria, get_criteria
from app.services.phase_validation.schema import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
)

_ALLOWED_AUDIT_DISPOSITIONS = {
    "keep",
    "refresh",
    "optimise",
    "optimize",
    "retitle",
    "consolidate",
    "noindex",
    "delete_candidate",
    "delete-candidate",
    "improve",
}

_PUBLISH_MODES = {"preview", "draft", "publish"}


def run_deterministic_checks(
    *,
    agent_key: str,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    criteria = get_criteria(agent_key)
    if not criteria:
        return [
            CheckResult(
                check_id="unknown_phase",
                parameter="phase_objective_compliance",
                category="phase_objective_compliance",
                status=CheckStatus.WARNING.value,
                severity=CheckSeverity.WARNING.value,
                message=f"No phase criteria registered for `{agent_key}`; semantic-only validation.",
                source="deterministic",
            )
        ]

    checks: list[CheckResult] = []
    for check_id in criteria.deterministic_checks:
        fn = _CHECK_FNS.get(check_id)
        if not fn:
            continue
        checks.extend(
            fn(
                criteria=criteria,
                output=output,
                company_ctx=company_ctx,
                prior=prior,
            )
        )
    return checks


def _pass(check_id: str, parameter: str, message: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        parameter=parameter,
        category=parameter,
        status=CheckStatus.PASSED.value,
        severity=CheckSeverity.INFO.value,
        message=message,
        source="deterministic",
    )


def _fail(
    check_id: str,
    parameter: str,
    message: str,
    *,
    severity: str = CheckSeverity.MAJOR.value,
    evidence: str | None = None,
    correction: str | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        parameter=parameter,
        category=parameter,
        status=CheckStatus.FAILED.value,
        severity=severity,
        message=message,
        evidence=evidence,
        recommended_correction=correction,
        source="deterministic",
    )


def _warn(check_id: str, parameter: str, message: str, *, evidence: str | None = None) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        parameter=parameter,
        category=parameter,
        status=CheckStatus.WARNING.value,
        severity=CheckSeverity.WARNING.value,
        message=message,
        evidence=evidence,
        source="deterministic",
    )


def _check_output_present(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = company_ctx, prior
    if output.get("blocked"):
        return [
            _pass(
                "output_present",
                "phase_objective_compliance",
                "Phase correctly reported blocked/incomplete upstream — not treated as deliverable.",
            )
        ]
    # Empty card after a successful-looking run
    meaningful = {
        k: v
        for k, v in output.items()
        if k
        not in (
            "card_type",
            "title",
            "agent_key",
            "generated_at",
            "source",
            "actions",
            "required_role",
        )
        and v not in (None, "", [], {})
    }
    if not meaningful:
        return [
            _fail(
                "output_present",
                "completeness",
                f"{criteria.phase_label} produced an empty output payload.",
                severity=CheckSeverity.CRITICAL.value,
                correction="Re-run the phase after verifying upstream packs and provider access.",
            )
        ]
    return [
        _pass(
            "output_present",
            "completeness",
            f"Output payload present with {len(meaningful)} substantive fields.",
        )
    ]


def _check_required_keys(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = company_ctx, prior
    if output.get("blocked"):
        return []
    results: list[CheckResult] = []
    for key in criteria.required_keys:
        val = output.get(key)
        # content_planning uses pages or roadmap
        if key == "pages" and not val:
            val = output.get("roadmap")
        if val in (None, "", [], {}):
            results.append(
                _fail(
                    f"required_key:{key}",
                    "completeness",
                    f"Required field `{key}` is missing or empty.",
                    severity=CheckSeverity.MAJOR.value,
                    correction=f"Regenerate so `{key}` is populated per phase contract.",
                )
            )
        else:
            results.append(
                _pass(
                    f"required_key:{key}",
                    "schema_structure",
                    f"Required field `{key}` is present.",
                )
            )
    return results


def _check_discovery_identity(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, prior
    results: list[CheckResult] = []
    name = str(company_ctx.get("display_name") or "").strip().lower()
    url = str(company_ctx.get("primary_url") or "").strip().lower()
    domain = urlparse(url if "://" in url else f"https://{url}").netloc.replace("www.", "")

    blob = str(output).lower()
    if name and len(name) >= 3 and name not in blob and domain and domain not in blob:
        # Discovery cards may use field structures — soft fail if completely absent
        commercial = output.get("commercial_scope") if isinstance(output.get("commercial_scope"), dict) else {}
        marketing = output.get("marketing_context") if isinstance(output.get("marketing_context"), dict) else {}
        if not commercial and not marketing and "pre_research" not in str(output.get("card_type", "")):
            results.append(
                _warn(
                    "discovery_identity",
                    "company_alignment",
                    "Could not confirm company name/URL appear in discovery output.",
                    evidence=f"expected name≈{name!r} or domain≈{domain!r}",
                )
            )
        else:
            results.append(
                _pass(
                    "discovery_identity",
                    "company_alignment",
                    "Discovery output includes commercial/marketing structures for this client.",
                )
            )
    else:
        results.append(
            _pass(
                "discovery_identity",
                "company_alignment",
                "Company identity signals present in discovery output.",
            )
        )
    return results


def _check_tracking_honesty(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    results: list[CheckResult] = []
    text = str(output).lower()
    if "connected" in text and "pending" not in text and "fail" not in text:
        # Soft: only warn if it claims all systems connected with no detail
        if "system_access" not in output and "platforms" not in output and "audits" not in output:
            results.append(
                _warn(
                    "tracking_honesty",
                    "honesty_anti_hallucination",
                    "Tracking output claims connectivity without structured platform statuses.",
                )
            )
        else:
            results.append(
                _pass(
                    "tracking_honesty",
                    "honesty_anti_hallucination",
                    "Tracking output includes structured access fields.",
                )
            )
    else:
        results.append(
            _pass(
                "tracking_honesty",
                "honesty_anti_hallucination",
                "No overconfident all-connected claim detected.",
            )
        )
    return results


_INVENTORY_URL_KEYS = (
    "sample_urls",
    "discovered_urls",
    "pages",
    "status_samples",
    "page_hierarchy",
    "crawled_pages",
)
_INVENTORY_COUNT_KEYS = ("pages_found", "indexable", "pages_analyzed", "pages_crawled")


def _url_from_inventory_item(item: Any) -> str | None:
    if isinstance(item, str):
        raw = item.strip()
        if raw.startswith(("http://", "https://", "/")):
            return raw
        return None
    if isinstance(item, dict):
        raw = item.get("url") or item.get("path") or item.get("page")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _collect_urls_from_mapping(obj: dict[str, Any], into: list[str]) -> None:
    for key in _INVENTORY_URL_KEYS:
        val = obj.get(key)
        if not isinstance(val, list):
            continue
        for item in val[:80]:
            url = _url_from_inventory_item(item)
            if url:
                into.append(url)


def _inventory_containers(output: dict[str, Any]) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = [output]
    tabs = output.get("tabs")
    if isinstance(tabs, dict):
        for tab in tabs.values():
            if isinstance(tab, dict):
                containers.append(tab)
                summary = tab.get("summary")
                if isinstance(summary, dict):
                    containers.append(summary)
    for nest_key in ("technical", "seo_audit", "crawl", "summary"):
        nest = output.get(nest_key)
        if isinstance(nest, dict) and nest not in containers:
            containers.append(nest)
            inner = nest.get("summary")
            if isinstance(inner, dict) and inner not in containers:
                containers.append(inner)
    return containers


def collect_website_inventory_urls(output: dict[str, Any]) -> list[str]:
    """URLs from top-level fields, crawl samples, SEO pages, or nested audit tabs."""
    urls: list[str] = []
    for container in _inventory_containers(output):
        _collect_urls_from_mapping(container, urls)
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def website_inventory_page_count(output: dict[str, Any]) -> int | None:
    for container in _inventory_containers(output):
        for key in _INVENTORY_COUNT_KEYS:
            val = container.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)) and val:
                return int(val)
    return None


def _website_error_signal(output: dict[str, Any]) -> str | None:
    for container in _inventory_containers(output):
        for key in ("error", "crawl_error", "provider_errors"):
            val = container.get(key)
            if val not in (None, "", [], {}):
                return str(val)[:300]
    return None


def _check_website_inventory(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, prior
    if output.get("blocked"):
        return []
    samples = collect_website_inventory_urls(output)
    pages_found = website_inventory_page_count(output)
    client_host = urlparse(
        str(company_ctx.get("primary_url") or "")
    ).netloc.replace("www.", "").lower()

    if not samples and not pages_found:
        evidence = _website_error_signal(output)
        if evidence:
            return [
                _warn(
                    "website_inventory",
                    "completeness",
                    "Website audit reported crawl/provider errors with empty inventory.",
                    evidence=evidence,
                )
            ]
        return [
            _fail(
                "website_inventory",
                "completeness",
                "Website situation has no sample URLs / page counts.",
                severity=CheckSeverity.MAJOR.value,
                correction="Re-run crawl or surface the crawl failure explicitly.",
            )
        ]

    off = []
    if client_host:
        for u in samples[:30]:
            host = urlparse(u).netloc.replace("www.", "").lower()
            if host and client_host not in host and host not in client_host:
                off.append(u)
    if off:
        return [
            _fail(
                "website_inventory",
                "company_alignment",
                "Inventory includes URLs outside the client domain.",
                severity=CheckSeverity.CRITICAL.value,
                evidence=str(off[:5]),
                correction="Restrict inventory to the client's primary domain.",
            )
        ]
    return [
        _pass(
            "website_inventory",
            "completeness",
            "Website inventory signals present and domain-aligned.",
        )
    ]


def _check_competitor_peers(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, prior
    peers = (
        output.get("competitors")
        or output.get("scorecards")
        or output.get("rankings")
        or output.get("tier_results")
        or []
    )
    if isinstance(peers, dict):
        peers = peers.get("items") or list(peers.values())
    if not peers:
        if output.get("excluded_tier5") or output.get("client_baseline"):
            return [
                _warn(
                    "competitor_peers",
                    "completeness",
                    "Competitor landscape has baseline/exclusions but no scored peers.",
                )
            ]
        return [
            _fail(
                "competitor_peers",
                "completeness",
                "No competitor peers present in landscape output.",
                severity=CheckSeverity.MAJOR.value,
            )
        ]
    industry = str(company_ctx.get("industry") or "").lower()
    if industry and len(industry) >= 4:
        # Light heuristic only — semantic layer does industry judgment
        return [
            _pass(
                "competitor_peers",
                "completeness",
                f"Found {len(peers) if isinstance(peers, list) else 'structured'} competitor entries for industry={industry!r}.",
            )
        ]
    return [
        _pass(
            "competitor_peers",
            "completeness",
            "Competitor peer list present.",
        )
    ]


def _check_search_demand_relevance_shell(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, prior
    results: list[CheckResult] = []
    products = company_ctx.get("products_services")
    classification = output.get("sitemap_classification") or (
        (output.get("cluster_report") or {}).get("sitemap_classification")
    )
    topic_plan = output.get("topic_plan") or output.get("topics") or []
    has_clusters = bool(
        (output.get("cluster_report") or {}).get("clusters") or output.get("clusters")
    )
    if classification:
        results.append(
            _pass(
                "search_demand_topics",
                "completeness",
                "sitemap cluster classification present (existing vs new topics).",
            )
        )
    elif topic_plan or has_clusters:
        results.append(
            _pass(
                "search_demand_topics",
                "completeness",
                "topic_plan/topics or clusters present.",
            )
        )
    else:
        results.append(
            _fail(
                "search_demand_topics",
                "completeness",
                "Search demand missing sitemap classification / topic_plan / clusters.",
                severity=CheckSeverity.MAJOR.value,
            )
        )

    # Competitor brands as seeds — if avoid list empty and known competitors appear as seeds
    seeds = output.get("seed_keywords") or []
    competitors = company_ctx.get("known_competitors") or []
    comp_names: list[str] = []
    if isinstance(competitors, list):
        for c in competitors:
            if isinstance(c, str):
                comp_names.append(c.lower())
            elif isinstance(c, dict):
                comp_names.append(str(c.get("name") or c.get("domain") or "").lower())
    bad_seeds = []
    for s in seeds if isinstance(seeds, list) else []:
        sl = str(s).lower()
        for cn in comp_names:
            if cn and len(cn) >= 4 and cn in sl:
                bad_seeds.append(s)
    if bad_seeds:
        results.append(
            _fail(
                "search_demand_competitor_seeds",
                "relevance",
                "Competitor brand names appear in seed keywords.",
                severity=CheckSeverity.MAJOR.value,
                evidence=str(bad_seeds[:5]),
                correction="Move competitor brands to avoid/blocklist; do not seed on them.",
            )
        )

    if products and topic_plan:
        results.append(
            _pass(
                "search_demand_product_context",
                "service_product_alignment",
                "Company products available for semantic relevance checks.",
            )
        )
    return results


def _check_strategy_phase5_carry(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx
    demand = prior.get("search_demand_summary") or {}
    topic_plan = demand.get("topic_plan") or demand.get("topics") or []
    titles: set[str] = set()
    if isinstance(topic_plan, list):
        for t in topic_plan:
            if isinstance(t, dict):
                title = str(t.get("title") or t.get("name") or "").strip().lower()
            else:
                title = str(t).strip().lower()
            if title:
                titles.add(title)
    elif isinstance(topic_plan, dict):
        for t in topic_plan.get("topics") or []:
            if isinstance(t, dict):
                title = str(t.get("title") or "").strip().lower()
                if title:
                    titles.add(title)

    queue = output.get("priority_queue") or []
    if not isinstance(queue, list) or not queue:
        return [
            _fail(
                "strategy_queue",
                "completeness",
                "Content strategy priority_queue is missing or empty.",
                severity=CheckSeverity.MAJOR.value,
            )
        ]

    if not titles:
        return [
            _warn(
                "strategy_phase5_carry",
                "prior_phase_consistency",
                "No Phase 5 topic titles available to verify carry-through.",
            )
        ]

    carried = 0
    for item in queue:
        if not isinstance(item, dict):
            continue
        t = str(item.get("title") or "").strip().lower()
        if item.get("from_phase5_topic") or t in titles:
            carried += 1
    if carried == 0 and titles:
        return [
            _fail(
                "strategy_phase5_carry",
                "prior_phase_consistency",
                "Priority queue does not retain Phase 5 topic titles.",
                severity=CheckSeverity.MAJOR.value,
                evidence=f"phase5_titles_sample={list(titles)[:5]}",
                correction="Preserve Phase 5 topic headlines in the priority queue.",
            )
        ]
    return [
        _pass(
            "strategy_phase5_carry",
            "prior_phase_consistency",
            f"Priority queue retains {carried} Phase 5-linked items.",
        )
    ]


def _check_ia_tree_nodes(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    tree = output.get("target_url_tree") or []
    if not isinstance(tree, list) or not tree:
        return [
            _fail(
                "ia_tree_nodes",
                "completeness",
                "target_url_tree missing or empty.",
                severity=CheckSeverity.CRITICAL.value,
                correction="Produce a URL tree with url, parent, and depth on every node.",
            )
        ]
    bad = []
    for node in tree:
        if not isinstance(node, dict):
            bad.append(node)
            continue
        loc = node.get("url") or node.get("path")
        depth = node.get("depth")
        if depth is None and loc:
            # Infer folder/click depth from path when builders omit explicit depth.
            depth = max(0, str(loc).strip("/").count("/")) if str(loc).strip("/") else 0
            node["depth"] = depth
        if not loc or depth is None:
            bad.append(node.get("url") or node.get("path") or node)
            continue
        if not node.get("url"):
            node["url"] = loc
    if bad:
        return [
            _fail(
                "ia_tree_nodes",
                "schema_structure",
                "Some IA tree nodes lack url and/or depth.",
                severity=CheckSeverity.MAJOR.value,
                evidence=str(bad[:5]),
            )
        ]
    return [
        _pass(
            "ia_tree_nodes",
            "schema_structure",
            f"IA tree has {len(tree)} nodes with url/depth.",
        )
    ]


def _check_technical_seo_honesty(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    results: list[CheckResult] = []
    not_measured = output.get("not_measured") or []
    # If CWV numeric scores appear without measurement note — warn/fail
    text = str(output).lower()
    claims_cwv = bool(re.search(r"\b(lcp|inp|cls)\b.{0,20}\d", text))
    if claims_cwv and not not_measured:
        # Allow if explicit cwv section says measured
        cwv = output.get("cwv") or output.get("core_web_vitals")
        if not cwv:
            results.append(
                _fail(
                    "technical_seo_cwv",
                    "honesty_anti_hallucination",
                    "CWV-like numeric claims without a measured CWV section or not_measured list.",
                    severity=CheckSeverity.MAJOR.value,
                    correction="Mark CWV as Not Measured or attach real lab/field measurements.",
                )
            )
        else:
            results.append(
                _pass(
                    "technical_seo_cwv",
                    "honesty_anti_hallucination",
                    "CWV section present alongside metrics.",
                )
            )
    else:
        results.append(
            _pass(
                "technical_seo_cwv",
                "honesty_anti_hallucination",
                "No unsupported CWV numeric claims detected.",
            )
        )
    return results


def _check_audit_dispositions(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    inventory = output.get("inventory") or []
    if not isinstance(inventory, list) or not inventory:
        return [
            _fail(
                "audit_inventory",
                "completeness",
                "Content audit inventory is empty.",
                severity=CheckSeverity.MAJOR.value,
            )
        ]
    bad = []
    for row in inventory:
        if not isinstance(row, dict):
            continue
        disp = str(row.get("disposition") or row.get("action") or "").strip().lower()
        if disp and disp not in _ALLOWED_AUDIT_DISPOSITIONS:
            bad.append(disp)
    if bad:
        return [
            _fail(
                "audit_dispositions",
                "schema_structure",
                "Inventory contains unknown dispositions.",
                severity=CheckSeverity.MINOR.value,
                evidence=str(sorted(set(bad))[:10]),
            )
        ]
    return [
        _pass(
            "audit_dispositions",
            "schema_structure",
            f"Inventory has {len(inventory)} rows with allowed dispositions.",
        )
    ]


def _check_planning_lock_integrity(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx
    results: list[CheckResult] = []
    pages = output.get("pages") or output.get("roadmap") or []
    if not isinstance(pages, list):
        pages = []
    keywords = []
    for p in pages:
        if isinstance(p, dict):
            kw = str(p.get("primary_keyword") or p.get("keyword") or "").strip().lower()
            if kw:
                keywords.append(kw)
    dupes = sorted({k for k in keywords if keywords.count(k) > 1})
    if dupes:
        results.append(
            _fail(
                "planning_unique_keywords",
                "prior_phase_consistency",
                "Roadmap assigns the same primary keyword to multiple pages.",
                severity=CheckSeverity.MAJOR.value,
                evidence=str(dupes[:5]),
                correction="Enforce one keyword → one URL.",
            )
        )
    else:
        results.append(
            _pass(
                "planning_unique_keywords",
                "prior_phase_consistency",
                "No duplicate primary keywords in roadmap.",
            )
        )

    ia = prior.get("site_architecture_summary") or {}
    tree = ia.get("target_url_tree") or []
    ia_urls = {
        str(n.get("url") or "").rstrip("/").lower()
        for n in tree
        if isinstance(n, dict) and n.get("url")
    }
    if ia_urls and pages:
        reconciled_ok = True
        for p in pages:
            if not isinstance(p, dict):
                continue
            if p.get("url_reconciled") is False:
                reconciled_ok = False
        if not reconciled_ok:
            results.append(
                _warn(
                    "planning_url_reconcile",
                    "prior_phase_consistency",
                    "Some roadmap rows were not URL-reconciled to architecture.",
                )
            )
        else:
            results.append(
                _pass(
                    "planning_url_reconcile",
                    "prior_phase_consistency",
                    "Roadmap URL reconciliation flags look consistent.",
                )
            )
    return results


def _check_planning_action_fit(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    pages = output.get("pages") or output.get("roadmap") or []
    if not isinstance(pages, list) or not pages:
        return []
    bad: list[str] = []
    overlap_risk: list[str] = []
    for row in pages:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").lower()
        if action != "create":
            continue
        basis = str(row.get("decision_basis") or "").strip().lower()
        check = str(row.get("existing_content_check") or "").strip().lower()
        justified = bool(str(row.get("new_content_justification") or "").strip())
        allowed_basis = {
            "new_content_gap",
            "strategy_gap",
            "url_map_create",
            # Older planner stamps — accept when create is still a true gap.
            "audit_disposition",
        }
        if check not in {"no_match", "audit_not_available"}:
            bad.append(str(row.get("url_n") or row.get("url") or "?"))
        elif basis not in allowed_basis and not justified:
            bad.append(str(row.get("url_n") or row.get("url") or "?"))
        if row.get("existing_content_evidence_url"):
            overlap_risk.append(str(row.get("url_n") or row.get("url") or "?"))
    if overlap_risk:
        return [
            _fail(
                "planning_action_fit",
                "phase_objective_compliance",
                "Roadmap creates new pages even though existing pages were matched for the same intent.",
                severity=CheckSeverity.MAJOR.value,
                evidence=str(overlap_risk[:5]),
                correction="Switch those rows to optimize/update/consolidate existing content.",
            )
        ]
    if bad:
        return [
            _fail(
                "planning_action_fit",
                "phase_objective_compliance",
                "Some create actions are missing explicit new-content justification/evidence.",
                severity=CheckSeverity.MAJOR.value,
                evidence=str(bad[:5]),
                correction="Run existing-content check first, then justify create only for true gaps.",
            )
        ]
    return [
        _pass(
            "planning_action_fit",
            "phase_objective_compliance",
            "Planning action choices include create-vs-optimize evidence.",
        )
    ]


def _check_production_one_page(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    drafts = output.get("drafts") or []
    if isinstance(drafts, list) and len(drafts) > 1 and output.get("one_page_per_run") is not False:
        return [
            _fail(
                "production_one_page",
                "phase_objective_compliance",
                f"Production returned {len(drafts)} drafts; phase allows at most one full draft per run.",
                severity=CheckSeverity.MAJOR.value,
                correction="Write a single selected page draft per run.",
            )
        ]
    planning = prior.get("content_planning_summary") or {}
    if planning and planning.get("locked") is False and (output.get("drafts") or output.get("briefs")):
        return [
            _fail(
                "production_requires_lock",
                "prior_phase_consistency",
                "Production ran against an unlocked content planning roadmap.",
                severity=CheckSeverity.CRITICAL.value,
                correction="Lock content planning before briefing/drafting.",
            )
        ]
    return [
        _pass(
            "production_one_page",
            "phase_objective_compliance",
            "Production one-page / lock constraints satisfied.",
        )
    ]


def _check_production_action_fit(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    briefs = output.get("briefs") or []
    if not isinstance(briefs, list):
        briefs = []
    conflicts: list[str] = []
    for b in briefs:
        if not isinstance(b, dict):
            continue
        action = str(b.get("action") or "").lower()
        if action != "create":
            continue
        pre = b.get("preflight") if isinstance(b.get("preflight"), dict) else {}
        existing = str(pre.get("existing_page_for_intent") or "").strip().lower()
        if existing and not existing.startswith("none found"):
            conflicts.append(str(b.get("url") or b.get("path") or b.get("keyword") or "?"))
    if conflicts:
        return [
            _fail(
                "production_action_fit",
                "prior_phase_consistency",
                "Production still carries create briefs for intents that already map to existing pages.",
                severity=CheckSeverity.MAJOR.value,
                evidence=str(conflicts[:5]),
                correction="Convert those briefs to optimize/update/rewrite existing pages.",
            )
        ]
    return [
        _pass(
            "production_action_fit",
            "prior_phase_consistency",
            "Production briefs respect existing-content-first action decisions.",
        )
    ]


def _check_on_page_one_page(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    pages = output.get("pages") or output.get("queue") or []
    if isinstance(pages, list) and len(pages) > 1 and output.get("one_page_only") is not False:
        return [
            _fail(
                "on_page_one_page",
                "phase_objective_compliance",
                f"On-page package contains {len(pages)} pages; expected one selected page.",
                severity=CheckSeverity.MAJOR.value,
            )
        ]
    return [
        _pass(
            "on_page_one_page",
            "phase_objective_compliance",
            "On-page package scoped appropriately.",
        )
    ]


def _check_publishing_mode(
    *,
    criteria: PhaseCriteria,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    _ = criteria, company_ctx, prior
    mode = str(output.get("mode") or "").strip().lower()
    if mode and mode not in _PUBLISH_MODES:
        return [
            _fail(
                "publishing_mode",
                "schema_structure",
                f"Invalid publishing mode `{mode}`.",
                severity=CheckSeverity.MAJOR.value,
                correction="Use preview, draft, or publish.",
            )
        ]
    if mode in ("draft", "publish"):
        verification = output.get("verification") or {}
        if isinstance(verification, dict) and verification.get("verified") is True:
            return [
                _pass(
                    "publishing_verify",
                    "honesty_anti_hallucination",
                    "Write mode includes verified=true.",
                )
            ]
        if output.get("claimed_success") and not (isinstance(verification, dict) and verification.get("verified")):
            return [
                _fail(
                    "publishing_verify",
                    "honesty_anti_hallucination",
                    "Publish success claimed without verification.verified.",
                    severity=CheckSeverity.CRITICAL.value,
                )
            ]
        return [
            _warn(
                "publishing_verify",
                "honesty_anti_hallucination",
                "Write mode without explicit verification.verified — confirm before treating as live.",
            )
        ]
    return [
        _pass(
            "publishing_mode",
            "schema_structure",
            f"Publishing mode `{mode or 'unspecified'}` acceptable for preview-oriented runs.",
        )
    ]


_CHECK_FNS = {
    "output_present": _check_output_present,
    "required_keys": _check_required_keys,
    "discovery_identity": _check_discovery_identity,
    "tracking_honesty": _check_tracking_honesty,
    "website_inventory": _check_website_inventory,
    "competitor_peers": _check_competitor_peers,
    "search_demand_relevance_shell": _check_search_demand_relevance_shell,
    "strategy_phase5_carry": _check_strategy_phase5_carry,
    "ia_tree_nodes": _check_ia_tree_nodes,
    "technical_seo_honesty": _check_technical_seo_honesty,
    "audit_dispositions": _check_audit_dispositions,
    "planning_lock_integrity": _check_planning_lock_integrity,
    "planning_action_fit": _check_planning_action_fit,
    "production_one_page": _check_production_one_page,
    "production_action_fit": _check_production_action_fit,
    "on_page_one_page": _check_on_page_one_page,
    "publishing_mode": _check_publishing_mode,
}
