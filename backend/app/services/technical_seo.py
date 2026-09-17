"""Phase 7 — Technical SEO composite audit service.

Consumes Phase 6 site-architecture shared memory and composes Ahrefs Site Audit
(when configured), crawl-based technical-seo audit, broken links, CWV, lightweight
rendering/hreflang surface checks, internal linking snapshot, and GSC sitemap signals
(when OAuth connected).
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from app.integrations.providers import (
    check_broken_links,
    run_cwv_measurement,
    run_seo_audit,
    run_technical_seo_audit,
)
from app.logging_config import get_logger

log = get_logger("technical_seo")


PHASE7_SKILL_DIRS = (
    "technical-seo-audit",
    "seo-audit",
    "broken-link-checker",
    "rendering-audit",
    "cwv-measurement",
    "hreflang-validator",
)


def phase7_skill_contracts() -> dict[str, int]:
    """Confirm Phase 7 skill markdown is loadable (chars loaded per skill)."""
    from app.agents.prompts import load_skill, load_skill_file

    out: dict[str, int] = {}
    for dirname in PHASE7_SKILL_DIRS:
        body = load_skill_file(dirname)
        out[dirname] = len(body or "")
    # Entry agent key alias
    out["technical_seo"] = len(load_skill("technical_seo") or "")
    return out


def _detect_js_rendering_risk(website: dict[str, Any], tech: dict[str, Any]) -> bool:
    blob = " ".join(
        str(x)
        for x in (
            website.get("stack"),
            website.get("tech_stack"),
            website.get("framework"),
            tech.get("notes"),
            tech.get("framework"),
        )
        if x
    ).lower()
    markers = (
        "react",
        "vue",
        "angular",
        "next.js",
        "nextjs",
        "nuxt",
        "svelte",
        "spa",
        "client-side",
        "csr",
    )
    return any(m in blob for m in markers)


def _ia_notes(site_architecture: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Pull redirect/depth/robots handoffs from IA slim or full pack."""
    actions: list[dict[str, Any]] = []
    notes: list[str] = []
    redirect_map = site_architecture.get("redirect_map") or []
    if isinstance(redirect_map, list):
        for row in redirect_map[:20]:
            if not isinstance(row, dict):
                continue
            actions.append(
                {
                    "type": "redirect",
                    "from": row.get("from") or row.get("source"),
                    "to": row.get("to") or row.get("target"),
                    "status": row.get("status") or 301,
                }
            )
    cs = site_architecture.get("current_state") if isinstance(site_architecture.get("current_state"), dict) else {}
    depth_n = cs.get("depth_4_plus")
    if depth_n:
        notes.append(f"{depth_n} URLs at click depth 4+ — prioritize internal-link remediation.")
        actions.append(
            {
                "type": "depth",
                "issue": f"{depth_n} pages at depth 4+",
                "fix": "Add hub links / flatten hierarchy per IA blueprint",
                "priority": "High",
            }
        )
    orphans = cs.get("orphans")
    if orphans:
        notes.append(f"{orphans} orphan candidates from IA crawl.")
    robots = site_architecture.get("robots_facet_policy") or site_architecture.get("faceted_navigation")
    if robots:
        notes.append(str(robots)[:200])
        actions.append(
            {
                "type": "robots",
                "issue": "Facet / robots policy from IA",
                "fix": str(robots)[:160],
                "priority": "Medium",
            }
        )
    for item in site_architecture.get("handoffs") or []:
        if isinstance(item, dict) and "Technical SEO" in str(item.get("receiving") or ""):
            notes.append(str(item.get("item") or "")[:160])
    return actions, notes


async def _rule_based_fallback(url: str, display_name: str) -> dict[str, Any]:
    """Minimal crawl-based technical snapshot when providers fail."""
    from app.integrations.web_fetch import fetch_url

    seed = url if "://" in url else f"https://{url}"
    domain = urlparse(seed).netloc or display_name
    findings: list[str] = []
    score = 70
    try:
        fetched = await fetch_url(seed)
        status = int(fetched.get("status_code") or 0)
        html = (fetched.get("text") or "").lower()
        if status != 200:
            findings.append(f"Homepage returned {status}")
            score -= 15
        if "robots" not in html and status == 200:
            findings.append("Could not confirm robots meta on homepage HTML sample")
        if "canonical" not in html:
            findings.append("No rel=canonical detected in homepage sample")
            score -= 5
        if seed.startswith("http://"):
            findings.append("Primary URL is HTTP — prefer HTTPS")
            score -= 10
        else:
            findings.append("HTTPS primary URL")
    except Exception as exc:  # noqa: BLE001
        findings.append(f"Crawl failed: {str(exc)[:120]}")
        score = 40
    return {
        "site": domain,
        "display_name": display_name,
        "score": max(0, min(100, score)),
        "sections": {
            "crawlability": {"score": score, "findings": findings or ["Rule-based crawl complete"]},
        },
        "priority_fixes": [
            {
                "priority": "High" if score < 60 else "Medium",
                "issue": findings[0] if findings else "Complete full technical crawl",
                "fix": "Re-run with live providers or expand crawl coverage",
            }
        ],
        "source": "rule_based_crawl",
    }


async def run_technical_seo_plan(
    *,
    client_name: str,
    primary_url: str,
    site_architecture: dict[str, Any] | None = None,
    website: dict[str, Any] | None = None,
    tracking: dict[str, Any] | None = None,
    ahrefs_project_id: int | None = None,
    previous_summary: dict[str, Any] | None = None,
    gsc_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose technical audit + SEO themes + broken links + IA handoffs.

    When Ahrefs Site Audit is configured, uses economical API flow:
    projects (free) → issues (50u) → page-explorer (50u/1k pages) → sample URLs for top issues.
    Falls back to crawl providers when Ahrefs project is unavailable.
    """
    _ = tracking
    ia = dict(site_architecture or {})
    ia_actions, ia_notes = _ia_notes(ia)

    from app.config import get_settings
    from app.services.technical_seo_ahrefs import fetch_ahrefs_technical_seo
    from app.services.technical_seo_checklist import build_audit_coverage
    from app.services.technical_seo_companion import (
        build_internal_linking_snapshot,
        build_score_comparison,
        build_suggestion_items,
        cluster_issues_by_category,
        enrich_issues_with_confidence,
        merge_sections,
        run_hreflang_surface_check,
        run_rendering_snapshot,
    )
    from app.services.technical_seo_schemas import TechnicalSEOPage

    settings = get_settings()
    previous_summary = dict(previous_summary or {})
    prev_crawl = None
    if isinstance(previous_summary.get("ahrefs_site_audit"), dict):
        prev_crawl = previous_summary["ahrefs_site_audit"].get("crawl_date")

    async def _fetch_ahrefs() -> dict[str, Any] | None:
        if not settings.ahrefs_api_key or settings.use_mock_providers:
            return None
        try:
            return await fetch_ahrefs_technical_seo(
                primary_url,
                project_id=ahrefs_project_id or settings.ahrefs_site_audit_project_id,
                date_compared=prev_crawl,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_ahrefs_failed", url=primary_url, error=str(exc))
            return None

    async def _fetch_tech() -> dict[str, Any]:
        try:
            out = await run_technical_seo_audit(primary_url, display_name=client_name)
            out["_provider"] = "technical_seo_audit"
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_audit_failed_falling_back", url=primary_url, error=str(exc))
            out = await _rule_based_fallback(primary_url, client_name)
            out["_provider"] = "rule_based_crawl"
            return out

    async def _fetch_cwv() -> dict[str, Any]:
        try:
            return await run_cwv_measurement(primary_url, display_name=client_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_cwv_failed", url=primary_url, error=str(exc))
            return {}

    async def _fetch_rendering() -> dict[str, Any]:
        if not _detect_js_rendering_risk(dict(website or {}), {}):
            return {
                "status": "SKIPPED",
                "note": "Skipped — no JS/SPA stack signals; run rendering-audit if needed.",
            }
        try:
            return await run_rendering_snapshot(primary_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_rendering_failed", url=primary_url, error=str(exc))
            return {"status": "NOT_AVAILABLE"}

    async def _fetch_broken() -> dict[str, Any]:
        try:
            return await check_broken_links(primary_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_broken_links_failed", url=primary_url, error=str(exc))
            return {"broken_count": 0, "internal": [], "external": [], "quick_fixes": []}

    async def _fetch_seo() -> dict[str, Any]:
        try:
            return await run_seo_audit(
                primary_url,
                display_name=client_name,
                max_pages=settings.technical_seo_seo_audit_max_pages,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_seo_audit_failed", url=primary_url, error=str(exc))
            return {}

    async def _fetch_hreflang(sample_urls: list[str]) -> dict[str, Any]:
        try:
            out = await run_hreflang_surface_check(primary_url, sample_urls=sample_urls)
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_hreflang_failed", url=primary_url, error=str(exc))
            return {"status": "NOT_AVAILABLE"}

    # Phase 1 — Ahrefs issues + CWV only (skip redundant homepage crawl until we know Ahrefs failed).
    ahrefs_data, cwv = await asyncio.gather(_fetch_ahrefs(), _fetch_cwv())

    use_ahrefs = bool(ahrefs_data and ahrefs_data.get("available"))
    seo: dict[str, Any] = {}
    broken: dict[str, Any] = {"broken_count": 0, "internal": [], "external": [], "quick_fixes": []}
    providers_used: list[str] = []

    sample_urls = (ahrefs_data or {}).get("sample_urls") or []

    if use_ahrefs:
        providers_used.append("ahrefs_site_audit")
        tech = {
            "score": ahrefs_data.get("score"),
            "sections": {},
            "priority_fixes": [],
            "source": "ahrefs_site_audit",
        }
        rendering, hreflang = await asyncio.gather(
            _fetch_rendering(),
            _fetch_hreflang(sample_urls),
        )
    else:
        tech, broken, seo, rendering, hreflang = await asyncio.gather(
            _fetch_tech(),
            _fetch_broken(),
            _fetch_seo(),
            _fetch_rendering(),
            _fetch_hreflang(sample_urls),
        )

    tech = dict(tech or {})
    tech_provider = str(tech.pop("_provider", "") or tech.get("source") or "technical_seo_audit")
    if tech_provider not in providers_used:
        providers_used.append(tech_provider)
    if seo:
        providers_used.append("seo_audit")
    if broken.get("broken_count") or broken.get("internal") or broken.get("external"):
        providers_used.append("broken_links")
    if cwv.get("field_data_available") or cwv.get("lab"):
        providers_used.append("cwv_measurement")
    if rendering.get("status") == "available":
        providers_used.append("rendering_snapshot")
    if hreflang.get("status") == "available":
        providers_used.append("hreflang_surface")

    gsc = dict(gsc_data or {})
    if gsc.get("status") == "available":
        providers_used.append("search_console")

    ahrefs_sections: dict[str, Any] = {}
    if use_ahrefs and ahrefs_data:
        cat_scores = ahrefs_data.get("category_scores") or {}
        for cat, payload in cat_scores.items():
            if not isinstance(payload, dict) or payload.get("status") == "NOT_AVAILABLE":
                continue
            ahrefs_sections[cat.lower().replace(" ", "_")] = {
                "score": payload.get("score"),
                "findings": [
                    f"{int(payload.get('issue_count') or 0)} issue(s) in {cat}"
                    if payload.get("issue_count")
                    else f"No issues detected in {cat}"
                ],
            }

    sections = merge_sections(ahrefs_sections, dict(tech.get("sections") or {}))
    cwv_field = cwv.get("field") if isinstance(cwv, dict) else None
    if cwv_field:
        cwv_findings = [
            f"LCP {cwv_field.get('lcp_ms')}ms ({cwv_field.get('lcp_category')})",
            f"INP {cwv_field.get('inp_ms')}ms ({cwv_field.get('inp_category')})",
            f"CLS {cwv_field.get('cls')} ({cwv_field.get('cls_category')})",
        ]
        if cwv.get("passes_core_web_vitals") is False:
            cwv_findings.append("Fails Core Web Vitals assessment (field data)")
        elif cwv.get("passes_core_web_vitals") is True:
            cwv_findings.append("Passes Core Web Vitals assessment (field data)")
        cats = [cwv_field.get(k) for k in ("lcp_category", "inp_category", "cls_category")]
        cwv_score = (
            round(100 * sum(1 for c in cats if c == "FAST") / len(cats)) if all(cats) else None
        )
        sections["performance"] = {"score": cwv_score, "findings": cwv_findings}
    findings_by_theme: list[dict[str, Any]] = []
    for theme, payload in sections.items():
        if not isinstance(payload, dict):
            continue
        findings = list(payload.get("findings") or [])
        findings_by_theme.append(
            {
                "theme": theme,
                "score": payload.get("score"),
                "findings": findings,
                "finding_count": len(findings),
            }
        )

    # Cluster SEO audit issues by theme if present
    for issue in seo.get("issues") or seo.get("findings") or []:
        if not isinstance(issue, dict):
            continue
        theme = str(issue.get("theme") or issue.get("category") or "seo_audit")
        findings_by_theme.append(
            {
                "theme": theme,
                "score": issue.get("score"),
                "findings": [issue.get("issue") or issue.get("message") or str(issue)],
                "finding_count": 1,
            }
        )

    page_inventory: list[TechnicalSEOPage] = []
    if use_ahrefs and ahrefs_data:
        for row in ahrefs_data.get("page_inventory") or []:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            page_inventory.append(
                TechnicalSEOPage(
                    url=str(row["url"]),
                    status_code=row.get("status_code"),
                    indexable=row.get("indexable"),
                    depth=row.get("depth"),
                    internal_link_count=row.get("internal_link_count"),
                    title=row.get("title"),
                    meta_description=row.get("meta_description"),
                    source="ahrefs",
                )
            )

    internal_linking = build_internal_linking_snapshot(
        site_architecture=ia,
        pages=page_inventory or None,
    )
    if internal_linking.get("status") == "available":
        providers_used.append("internal_linking_snapshot")

    duplicate_titles = seo.get("duplicate_titles") or []
    duplicate_meta = seo.get("duplicate_meta_descriptions") or []
    if use_ahrefs and ahrefs_data:
        duplicate_titles = ahrefs_data.get("duplicate_titles") or duplicate_titles
        duplicate_meta = ahrefs_data.get("duplicate_meta_descriptions") or duplicate_meta

    backlog: list[dict[str, Any]] = []
    if use_ahrefs and ahrefs_data:
        for issue in ahrefs_data.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            backlog.append(
                {
                    "priority": issue.get("severity") or "Medium",
                    "issue": issue.get("title"),
                    "fix": issue.get("recommended_action"),
                    "source": issue.get("source") or "ahrefs",
                    "rule_id": issue.get("rule_id"),
                    "category": issue.get("category"),
                    "affected_url_count": issue.get("affected_url_count"),
                }
            )
    for fix in tech.get("priority_fixes") or []:
        if isinstance(fix, dict):
            backlog.append(
                {
                    "priority": fix.get("priority") or "Medium",
                    "issue": fix.get("issue"),
                    "fix": fix.get("fix"),
                    "source": "technical_seo",
                }
            )
    for action in ia_actions:
        if action.get("type") in ("depth", "robots", "redirect"):
            backlog.append(
                {
                    "priority": action.get("priority") or ("High" if action.get("type") == "redirect" else "Medium"),
                    "issue": action.get("issue")
                    or f"{action.get('type')}: {action.get('from')} → {action.get('to')}",
                    "fix": action.get("fix")
                    or f"Implement {action.get('status') or 301} redirect / policy",
                    "source": "site_architecture",
                }
            )
    for qf in (broken.get("quick_fixes") or [])[:6]:
        backlog.append(
            {
                "priority": "High",
                "issue": str(qf),
                "fix": "Fix or redirect broken link",
                "source": "broken_links",
            }
        )
    for link in (internal_linking.get("link_suggestions") or [])[:6]:
        if isinstance(link, dict):
            backlog.append(
                {
                    "priority": link.get("priority") or "Medium",
                    "issue": f"Internal link: {link.get('to')}",
                    "fix": str(link.get("reason") or "Add internal link"),
                    "source": "internal_linking",
                    "category": "Internal Linking",
                }
            )

    all_issues: list[dict[str, Any]] = []
    if use_ahrefs and ahrefs_data:
        all_issues = enrich_issues_with_confidence(list(ahrefs_data.get("issues") or []))
    if gsc.get("status") == "available":
        all_issues.extend(enrich_issues_with_confidence(list(gsc.get("issues") or [])))

    for dup in duplicate_titles[:8]:
        backlog.append(
            {
                "priority": "Medium",
                "issue": f"Duplicate title “{dup.get('value')}” on {dup.get('count')} pages",
                "fix": "Give each page its own title — duplicates split ranking signal and CTR",
                "source": "seo_audit",
            }
        )
    for dup in duplicate_meta[:8]:
        backlog.append(
            {
                "priority": "Low",
                "issue": f"Duplicate meta description on {dup.get('count')} pages",
                "fix": "Write a unique meta description per page (still not a ranking factor, but hurts CTR)",
                "source": "seo_audit",
            }
        )
    for issue in gsc.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        backlog.append(
            {
                "priority": issue.get("severity") or "Medium",
                "issue": issue.get("title"),
                "fix": issue.get("recommended_action"),
                "source": "gsc",
                "rule_id": issue.get("rule_id"),
                "category": issue.get("category"),
            }
        )

    priority_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    backlog.sort(key=lambda b: priority_order.get(str(b.get("priority") or "Medium"), 9))

    issues_by_category = cluster_issues_by_category(all_issues)
    suggestion_items = build_suggestion_items(all_issues)

    score = tech.get("score")
    if use_ahrefs and ahrefs_data and ahrefs_data.get("score") is not None:
        score = ahrefs_data.get("score")
    elif score is None and findings_by_theme:
        scores = [t["score"] for t in findings_by_theme if isinstance(t.get("score"), (int, float))]
        score = round(sum(scores) / len(scores)) if scores else None

    severity = "ok"
    if (score is not None and score < 60) or int(broken.get("broken_count") or 0) > 5:
        severity = "critical"
    elif (score is not None and score < 75) or int(broken.get("broken_count") or 0) > 0:
        severity = "warning"

    website = dict(website or {})
    js_risk = _detect_js_rendering_risk(website, tech)
    not_measured: list[dict[str, str]] = []

    if not cwv_field:
        not_measured.append(
            {
                "item": "Core Web Vitals field data (LCP / INP / CLS)",
                "requires": "cwv-measurement",
                "note": (cwv.get("note") or cwv.get("error") or "cwv-measurement did not return data this pass.")
                if isinstance(cwv, dict)
                else "cwv-measurement did not run this pass.",
            },
        )

    if rendering.get("status") != "available":
        not_measured.append(
            {
                "item": "JS rendered vs raw HTML + mobile parity (full)",
                "requires": "rendering-audit",
                "note": "Lightweight UA snapshot only — run rendering-audit for CSR/SPA sites.",
            }
        )
    elif rendering.get("parity_issues"):
        not_measured.append(
            {
                "item": "Full rendering verification",
                "requires": "rendering-audit",
                "note": "; ".join(rendering.get("parity_issues") or [])[:200],
            }
        )

    if hreflang.get("hreflang_found") and hreflang.get("issue_count", 0) > 0:
        not_measured.append(
            {
                "item": "hreflang reciprocity matrix",
                "requires": "hreflang-validator",
                "note": f"{hreflang.get('issue_count')} surface issue(s) — run full hreflang-validator.",
            }
        )
    elif not hreflang.get("hreflang_found"):
        not_measured.append(
            {
                "item": "hreflang cluster validation",
                "requires": "hreflang-validator",
                "note": "No hreflang on sample — skip unless multi-locale site.",
            }
        )

    specialist_routing = [
        {
            "finding_area": "Crawl/index, status codes, security, pagination remediation",
            "route_to": "technical-seo",
            "status": "covered_in_this_report",
        },
        {
            "finding_area": "GSC sitemap / indexation signals",
            "route_to": "search-console",
            "status": (
                "measured_in_this_report"
                if gsc.get("status") == "available"
                else "from_phase_2"
            ),
        },
        {
            "finding_area": "Broken links / redirect chains",
            "route_to": "broken-links",
            "status": "covered_in_this_report",
        },
        {
            "finding_area": "First-pass meta/headings/images triage",
            "route_to": "seo-audit",
            "status": "covered_in_this_report" if (seo or use_ahrefs) else "handoff",
        },
        {
            "finding_area": "Real CWV numbers, crawl budget, logs",
            "route_to": "cwv-measurement",
            "status": "measured_in_this_report" if cwv_field else "handoff",
        },
        {
            "finding_area": "JS-rendered content, mobile parity",
            "route_to": "rendering-audit",
            "status": (
                "measured_in_this_report"
                if rendering.get("status") == "available" and rendering.get("parity_ok")
                else "partial_in_this_report"
                if rendering.get("status") == "available"
                else "handoff"
            ),
        },
        {
            "finding_area": "hreflang correctness",
            "route_to": "hreflang-validator",
            "status": (
                "partial_in_this_report"
                if hreflang.get("hreflang_found")
                else "handoff"
            ),
        },
        {
            "finding_area": "Orphan/deep pages, hub links",
            "route_to": "internal-linking",
            "status": (
                "partial_in_this_report"
                if internal_linking.get("link_suggestions")
                else "from_phase_6"
            ),
        },
        {
            "finding_area": "URL structure, click-depth remedy, faceting, migrations",
            "route_to": "site-architecture",
            "status": "from_phase_6",
        },
    ]

    domain = urlparse(primary_url if "://" in primary_url else f"https://{primary_url}").netloc
    cwv_summary = (
        f"CWV (field): LCP {cwv_field.get('lcp_ms')}ms, INP {cwv_field.get('inp_ms')}ms, "
        f"CLS {cwv_field.get('cls')} — "
        + ("passes" if cwv.get("passes_core_web_vitals") else "does not pass")
        + " the assessment. "
        if cwv_field
        else "CWV not measured this pass (see Not Measured). "
    )
    ahrefs_line = ""
    if use_ahrefs and ahrefs_data:
        ahrefs_line = (
            f"Ahrefs Site Audit: health {ahrefs_data.get('health_score')}, "
            f"{len(ahrefs_data.get('issues') or [])} issues, "
            f"{ahrefs_data.get('pages_fetched') or 0} pages sampled. "
        )
    elif ahrefs_data and not ahrefs_data.get("available"):
        ahrefs_line = f"Ahrefs unavailable ({ahrefs_data.get('reason', 'unknown')}); crawl fallback used. "

    executive_summary = (
        f"Phase 7 technical composite for {client_name} ({domain}): "
        f"score {score if score is not None else '—'}, "
        f"{int(broken.get('broken_count') or 0)} broken links, "
        f"{len(ia_actions)} IA handoffs from Phase 6. "
        + ahrefs_line
        + (
            "JS rendering risk flagged — treat HTML-only findings as provisional until rendering-audit. "
            if js_risk
            else ""
        )
        + cwv_summary
        + (
            f"Rendering snapshot: {'parity OK' if rendering.get('parity_ok') else 'parity gaps detected'}. "
            if rendering.get("status") == "available"
            else ""
        )
        + (
            f"GSC: {gsc.get('sitemap_count', 0)} sitemap(s), {len(gsc.get('issues') or [])} indexation signal(s). "
            if gsc.get("status") == "available"
            else ""
        )
        + (
            f"Hreflang surface: {hreflang.get('pages_with_hreflang', 0)} page(s) with annotations. "
            if hreflang.get("hreflang_found")
            else ""
        )
    )

    score_comparison = build_score_comparison(
        int(score) if score is not None else None,
        previous_summary if previous_summary else None,
    )
    audit_comparison = (ahrefs_data or {}).get("audit_comparison") if use_ahrefs else None
    if score_comparison and audit_comparison:
        audit_comparison = {**audit_comparison, **score_comparison}
    elif score_comparison:
        audit_comparison = score_comparison

    audit_coverage = build_audit_coverage(
        issues=all_issues if isinstance(all_issues, list) else [],
        pages_available=bool(
            (ahrefs_data or {}).get("pages_fetched") or (seo or {}).get("pages_audited")
        ),
        gsc_available=gsc.get("status") == "available",
        cwv_available=bool(cwv_field),
        rendering_available=rendering.get("status") == "available",
        sitemap_available=bool(gsc.get("sitemap_count")),
        homepage_checked=bool(seo or tech),
    )

    return {
        "client_name": client_name,
        "primary_url": primary_url,
        "site": domain or primary_url,
        "score": score,
        "overall_score": score,
        "severity": severity,
        "executive_summary": executive_summary,
        "sections": sections,
        "findings_by_theme": findings_by_theme,
        "technical_sections": sections,
        "ahrefs_site_audit": ahrefs_data if use_ahrefs else (
            {"available": False, "reason": (ahrefs_data or {}).get("reason")}
            if ahrefs_data
            else None
        ),
        "issues": all_issues if all_issues else (ahrefs_data or {}).get("issues") if use_ahrefs else [],
        "issues_by_category": issues_by_category,
        "audit_coverage": audit_coverage,
        "suggestion_items": suggestion_items,
        "category_scores": (ahrefs_data or {}).get("category_scores") if use_ahrefs else {},
        "severity_summary": (ahrefs_data or {}).get("severity_summary") if use_ahrefs else {},
        "audit_state": (ahrefs_data or {}).get("audit_state") if use_ahrefs else "COMPLETED",
        "data_sources": _merged_data_sources(ahrefs_data, rendering, hreflang, cwv_field, gsc),
        "audit_comparison": audit_comparison,
        "rendering_audit": rendering if rendering.get("status") == "available" else None,
        "hreflang_check": hreflang if hreflang.get("status") == "available" else None,
        "internal_linking": internal_linking,
        "gsc_indexation": gsc if gsc.get("status") == "available" else None,
        "seo_audit": {
            "score": seo.get("score") or seo.get("overall_score"),
            "pages_audited": (
                seo.get("pages_audited")
                or seo.get("pages_found")
                or seo.get("pages_analyzed")
                or (ahrefs_data or {}).get("pages_fetched")
            ),
            "issue_count": len(all_issues) or len(seo.get("issues") or seo.get("findings") or []),
            "critical": seo.get("critical") or [],
            "warnings": seo.get("warnings") or [],
            "duplicate_titles": duplicate_titles,
            "duplicate_meta_descriptions": duplicate_meta,
        }
        if (seo or use_ahrefs)
        else None,
        "broken_links": broken,
        "broken_link_count": broken.get("broken_count"),
        "priority_backlog": backlog[:25],
        "priority_fixes": backlog[:25],
        "ia_actions": ia_actions,
        "ia_notes": ia_notes,
        "phase6_connected": bool(ia or ia_actions or ia_notes),
        "upstream_phase": "site_architecture",
        "core_web_vitals": cwv or None,
        "not_measured": not_measured,
        "specialist_routing": specialist_routing,
        "js_rendering_risk": js_risk,
        "skills_loaded": phase7_skill_contracts(),
        "skills_used": list(PHASE7_SKILL_DIRS)
        + [p for p in providers_used if p not in PHASE7_SKILL_DIRS],
        "providers_used": providers_used,
        "source": "+".join(providers_used) or "technical_seo",
        "note": (
            "Phase 7 entry report — connected to Phase 6 IA memory. "
            + (
                "Ahrefs Site Audit is the primary data source. "
                if use_ahrefs
                else "Crawl-based providers used (Ahrefs Site Audit not available). "
            )
            + (
                "CWV measured this pass via PageSpeed Insights. "
                if cwv_field
                else "CWV not measured this pass (handoff to cwv-measurement). "
            )
            + "Lightweight rendering + hreflang surface checks included; full rendering-audit / hreflang-validator for deep passes."
        ),
    }


def _merged_data_sources(
    ahrefs_data: dict[str, Any] | None,
    rendering: dict[str, Any],
    hreflang: dict[str, Any],
    cwv_field: dict[str, Any] | None,
    gsc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict((ahrefs_data or {}).get("data_sources") or {})
    base["rendering_snapshot"] = {
        "status": rendering.get("status", "NOT_AVAILABLE"),
        "reason": rendering.get("note"),
    }
    base["hreflang_surface"] = {
        "status": hreflang.get("status", "NOT_AVAILABLE"),
        "reason": hreflang.get("note"),
    }
    base["core_web_vitals"] = {
        "status": "available" if cwv_field else "NOT_AVAILABLE",
        "reason": None if cwv_field else "PageSpeed field data unavailable this pass.",
    }
    gsc = dict(gsc or {})
    base["search_console"] = {
        "status": gsc.get("status", "NOT_AVAILABLE"),
        "reason": gsc.get("note"),
        "property": gsc.get("property"),
    }
    return base
