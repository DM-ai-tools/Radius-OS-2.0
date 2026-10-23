"""Phase 7 — Technical SEO composite audit service.

Consumes Phase 6 site-architecture shared memory and composes the crawl-based
technical-seo audit, a direct page sample, broken links, CWV, lightweight
rendering/hreflang surface checks, internal linking snapshot, and GSC sitemap signals
(when OAuth connected).
"""

from __future__ import annotations

import asyncio
import re
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
    "seo-sitemap",
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
    previous_summary: dict[str, Any] | None = None,
    gsc_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose technical audit + SEO themes + broken links + IA handoffs.

    Technical SEO does not use Ahrefs (their decision, 2026-10-18): the crawl audit,
    the page sample, PageSpeed, broken links and Search Console are the sources.
    """
    ia = dict(site_architecture or {})
    ia_actions, ia_notes = _ia_notes(ia)

    from app.config import get_settings
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

    settings = get_settings()
    previous_summary = dict(previous_summary or {})

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

    async def _fetch_cwv(strategy: str = "mobile") -> dict[str, Any]:
        try:
            return await run_cwv_measurement(primary_url, display_name=client_name, strategy=strategy)
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_cwv_failed", url=primary_url, strategy=strategy, error=str(exc))
            return {}

    async def _fetch_rendering() -> dict[str, Any]:
        # Every audit: the mobile-vs-desktop fetch is two requests and feeds Mobile parity.
        try:
            return await run_rendering_snapshot(primary_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_rendering_failed", url=primary_url, error=str(exc))
            return {"status": "NOT_AVAILABLE"}

    async def _fetch_sample() -> dict[str, Any]:
        if settings.use_mock_providers:
            return {"status": "NOT_AVAILABLE", "pages": [], "note": "Mock providers: page sample skipped."}
        from app.integrations import firecrawl
        from app.services.technical_seo_sample import run_page_sample

        async def _rendered(url: str) -> str:
            # Browser-rendered DOM with <head>, for raw-vs-rendered and blocked pages.
            if not settings.firecrawl_api_key:
                return ""
            res = await firecrawl.scrape_page(url, formats=["rawHtml"], full_page=True)
            return str(res.get("raw_html") or res.get("html") or "") if res.get("available") else ""

        async def _fallback(url: str) -> str:
            return await _rendered(url)

        try:
            return await run_page_sample(
                primary_url,
                candidate_urls=_candidate_urls(dict(website or {}), ia),
                rendered_fetch=_rendered,
                html_fallback=_fallback,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("technical_seo_sample_failed", url=primary_url, error=str(exc))
            return {"status": "NOT_AVAILABLE", "pages": [], "note": f"Page sample failed: {type(exc).__name__}"}

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

    # CWV (mobile, then desktop), the page sample, the mobile/desktop fetch and the
    # crawl providers all run together.
    async def _fetch_cwv_both() -> tuple[dict[str, Any], dict[str, Any]]:
        # One after the other: two PageSpeed calls at once trip its anonymous quota (429).
        mobile = await _fetch_cwv("mobile")
        desktop = await _fetch_cwv("desktop")
        return mobile, desktop

    (cwv, cwv_desktop), sample, rendering, tech, broken, seo, hreflang = await asyncio.gather(
        _fetch_cwv_both(),
        _fetch_sample(),
        _fetch_rendering(),
        _fetch_tech(),
        _fetch_broken(),
        _fetch_seo(),
        _fetch_hreflang([]),
    )
    providers_used: list[str] = []

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
    if sample.get("status") == "available":
        providers_used.append("page_sample")
    if (cwv_desktop or {}).get("field") or (cwv_desktop or {}).get("lab"):
        providers_used.append("cwv_desktop")
    if hreflang.get("status") == "available":
        providers_used.append("hreflang_surface")

    gsc = dict(gsc_data or {})
    if gsc.get("status") == "available":
        providers_used.append("search_console")

    sections = merge_sections({}, dict(tech.get("sections") or {}))
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

    internal_linking = build_internal_linking_snapshot(site_architecture=ia, pages=None)
    if internal_linking.get("status") == "available":
        providers_used.append("internal_linking_snapshot")

    duplicate_titles = seo.get("duplicate_titles") or []
    duplicate_meta = seo.get("duplicate_meta_descriptions") or []

    backlog: list[dict[str, Any]] = []
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
    # Broken links appear once, as an Internal linking issue with example URLs (below);
    # per-link rows repeated it and were filed under the wrong group.
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
    sitemap_audit = tech.get("sitemap_audit") if isinstance(tech.get("sitemap_audit"), dict) else None
    if sitemap_audit is None and not settings.use_mock_providers:
        from app.services.sitemap_audit import fetch_sitemap_audit

        try:
            sitemap_audit = await fetch_sitemap_audit(primary_url)
        except Exception:  # noqa: BLE001
            sitemap_audit = None
    if sitemap_audit and sitemap_audit.get("status") == "available":
        from app.services.sitemap_audit import apply_known_page_status, checklist_issues

        pages_for_status = list((seo or {}).get("pages") or [])
        apply_known_page_status(sitemap_audit, pages_for_status)
        if gsc.get("status") == "available":
            sitemap_audit["gsc_note"] = (
                f"Search Console: {gsc.get('sitemap_count', 0)} submitted sitemap(s), "
                f"{len(gsc.get('issues') or [])} indexation signal(s)."
            )
        all_issues.extend(enrich_issues_with_confidence(checklist_issues(sitemap_audit)))
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

    from app.services import technical_seo_report as report

    seo_pages = [p for p in (seo or {}).get("pages") or [] if isinstance(p, dict)]
    onpage_rows, onpage_table = report.onpage_checks(
        sample, seo_pages=seo_pages, duplicate_titles=duplicate_titles, duplicate_meta=duplicate_meta
    )
    canonical_rows, canonical_table, duplicate_clusters = report.canonical_checks(
        sample,
        gsc=gsc,
        sitemap_urls=[str(u.get("loc") if isinstance(u, dict) else u) for u in (sitemap_audit or {}).get("urls") or []],
        duplicate_titles=duplicate_titles,
    )
    js_rows, js_table = report.js_checks(sample)
    perf_rows, perf_metrics, perf_weight = report.performance_checks(cwv, cwv_desktop, sample)
    sd_rows, sd_table, rich_results = report.structured_data_checks(sample)
    security_rows, security_headers = report.security_checks(sample)
    url_rows = report.url_structure_checks(sample)
    checks = (
        onpage_rows + report.indexation_checks(sample) + url_rows + canonical_rows + js_rows + perf_rows
        + report.mobile_checks(sample, cwv, rendering) + sd_rows + security_rows
    )
    sample_issues = report.issues_from_checks(checks)
    measured_fields = {(r["group"], r["field"]) for r in checks}
    extra_issues: list[dict[str, Any]] = []
    # Crawl-audit fixes become issues unless a direct check already covers that field.
    for fix in tech.get("priority_fixes") or []:
        if not isinstance(fix, dict) or not fix.get("issue"):
            continue
        group, fld = report.infer_group(str(fix.get("issue")))
        if (group, fld) in measured_fields:
            continue
        extra_issues.append({
            "rule_id": "CRAWL_" + re.sub(r"[^A-Z0-9]+", "_", str(fix["issue"]).upper())[:40],
            "category": group, "field": fld, "title": fix["issue"], "severity": fix.get("priority") or "Medium",
            "affected_urls": [], "affected_url_count": 1, "recommended_action": fix.get("fix") or "",
            "source": "crawl_audit", "status": "open",
        })
    internal_broken = [
        u for u in (str((x.get("broken_url") or x.get("url")) if isinstance(x, dict) else x) for x in broken.get("internal") or [])
        # Cloudflare email obfuscation links resolve only with JavaScript; not broken.
        if "/cdn-cgi/l/email-protection" not in u
    ]
    real_broken = [
        u for u in (str((x.get("broken_url") or x.get("url")) if isinstance(x, dict) else x) for x in (broken.get("internal") or []) + (broken.get("external") or []))
        if "/cdn-cgi/l/email-protection" not in u
    ]
    if real_broken or (int(broken.get("broken_count") or 0) and not (broken.get("internal") or broken.get("external"))):
        extra_issues.append({
            "rule_id": "BROKEN_LINKS", "category": "Internal linking", "field": "broken links",
            "title": f"{len(real_broken) or broken.get('broken_count')} broken link(s) found", "severity": "High",
            "affected_urls": (internal_broken or real_broken)[:25],
            "affected_url_count": len(real_broken) or int(broken.get("broken_count") or 0),
            "recommended_action": "Fix or redirect each broken link; update the linking page.", "source": "broken_links",
            "status": "open",
        })
    all_issues = enrich_issues_with_confidence(list(all_issues) + sample_issues + extra_issues)

    issues_by_category = cluster_issues_by_category(all_issues)
    suggestion_items = build_suggestion_items(all_issues)

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
            "status": "covered_in_this_report" if seo else "handoff",
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

    score = tech.get("score")
    if score is None and findings_by_theme:
        scores = [t["score"] for t in findings_by_theme if isinstance(t.get("score"), (int, float))]
        score = round(sum(scores) / len(scores)) if scores else None

    audit_coverage = build_audit_coverage(
        issues=all_issues if isinstance(all_issues, list) else [],
        pages_available=bool((seo or {}).get("pages_audited")),
        gsc_available=gsc.get("status") == "available",
        cwv_available=bool(cwv_field),
        rendering_available=rendering.get("status") == "available",
        sitemap_available=bool(
            (sitemap_audit or {}).get("status") == "available" or gsc.get("sitemap_count")
        ),
        # Only when a homepage was actually read (crawl audit or the page sample).
        homepage_checked=bool(
            tech.get("fetch_source")
            or any(p.get("parsed") for p in sample.get("pages") or [])
            or (seo or {}).get("pages")
        ),
        sitemap_audit=sitemap_audit if (sitemap_audit or {}).get("status") == "available" else None,
        explicit=report.field_statuses(checks),
    )
    group_score_map = report.group_scores(all_issues, audit_coverage)
    from app.services.technical_seo_rules import overall_score as _overall

    # Overall score: the mean of the measured groups (the scorecard's own numbers).
    score = _overall(group_score_map, ahrefs_health_score=None) if any(
        v.get("score") is not None for v in group_score_map.values()
    ) else score
    scorecard_rows, scorecard_line = report.scorecard(group_score_map)
    full_backlog = report.build_backlog(all_issues, backlog)
    by_severity = sorted(full_backlog, key=lambda b: (report.SEVERITY_ORDER.get(str(b["priority"]), 9), -int(b["affected"] or 0)))
    quick = report.quick_wins(by_severity)
    coverage_notes = _coverage_notes(
        sample=sample, seo=seo, gsc=gsc, cwv=cwv, cwv_desktop=cwv_desktop, rendering=rendering,
    )
    audit_groups = report.build_groups(
        issues=all_issues, coverage=audit_coverage, scores=group_score_map, checks=checks, coverage_notes=coverage_notes
    )

    severity = "ok"
    broken_n = len(real_broken) if real_broken or broken.get("internal") or broken.get("external") else int(broken.get("broken_count") or 0)
    if (score is not None and score < 60) or broken_n > 5:
        severity = "critical"
    elif (score is not None and score < 75) or broken_n > 0:
        severity = "warning"

    domain = urlparse(primary_url if "://" in primary_url else f"https://{primary_url}").netloc
    cwv_summary = (
        f"CWV (field): LCP {cwv_field.get('lcp_ms')}ms, INP {cwv_field.get('inp_ms')}ms, "
        f"CLS {cwv_field.get('cls')} — "
        + ("passes" if cwv.get("passes_core_web_vitals") else "does not pass")
        + " the assessment. "
        if cwv_field
        else "CWV not measured this pass (see Not Measured). "
    )
    executive_summary = (
        f"Phase 7 technical composite for {client_name} ({domain}): "
        f"score {score if score is not None else '—'}, "
        f"{broken_n} broken links, "
        f"{len(ia_actions)} IA handoffs from Phase 6. "
        f"{(audit_coverage.get('summary') or {}).get('coverage_pct', 0)}% of the checklist measured this pass. "
        + (
            "JS rendering risk flagged — treat HTML-only findings as provisional until rendering-audit. "
            if js_risk
            else ""
        )
        + cwv_summary
        + (
            f"Rendering snapshot: {'parity OK' if rendering.get('parity_ok') else 'parity gaps detected'}. "
            # Only when both fetches got a real page; a challenge page proves nothing.
            if rendering.get("status") == "available" and rendering.get("desktop_status") == 200
            and rendering.get("mobile_status") == 200 and (rendering.get("desktop_links") or 0) > 0
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
    audit_comparison = score_comparison

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
        "ahrefs_site_audit": None,
        "issues": all_issues,
        "issues_by_category": issues_by_category,
        "audit_coverage": audit_coverage,
        "suggestion_items": suggestion_items,
        "category_scores": group_score_map,
        "severity_summary": _severity_counts(all_issues),
        "audit_state": "COMPLETED",
        "data_sources": _merged_data_sources(rendering, hreflang, cwv_field, gsc),
        "audit_comparison": audit_comparison,
        "rendering_audit": rendering if rendering.get("status") == "available" else None,
        "hreflang_check": hreflang if hreflang.get("status") == "available" else None,
        "internal_linking": internal_linking,
        "gsc_indexation": gsc if gsc.get("status") == "available" else None,
        "sitemap_audit": sitemap_audit if (sitemap_audit or {}).get("status") == "available" else None,
        "seo_audit": {
            "score": seo.get("score") or seo.get("overall_score"),
            "pages_audited": (
                seo.get("pages_audited")
                or seo.get("pages_found")
                or seo.get("pages_analyzed")
            ),
            "issue_count": len(all_issues) or len(seo.get("issues") or seo.get("findings") or []),
            "critical": seo.get("critical") or [],
            "warnings": seo.get("warnings") or [],
            "duplicate_titles": duplicate_titles,
            "duplicate_meta_descriptions": duplicate_meta,
        }
        if seo
        else None,
        "broken_links": broken,
        "broken_link_count": broken.get("broken_count"),
        "priority_backlog": full_backlog,
        "priority_fixes": full_backlog,
        "quick_wins": quick,
        "scorecard": scorecard_rows,
        "scorecard_line": scorecard_line,
        "audit_groups": audit_groups,
        "audit_checks": checks,
        "coverage_mode": "crawl",
        "coverage_pct": (audit_coverage.get("summary") or {}).get("coverage_pct"),
        "coverage_notes": coverage_notes,
        "coverage_needs": _coverage_needs(cwv=cwv, sample=sample, settings=settings),
        "on_page_audit": {"pages": onpage_table},
        "url_structure_audit": {"checks": [c for c in checks if c["group"] == "URL structure"], "sample_url": (sample.get("variants") or {}).get("sample_url")},
        "canonical_audit": {"pages": canonical_table, "duplicate_clusters": duplicate_clusters},
        "js_rendering_audit": {"comparison": js_table, "rendered_available": bool(sample.get("rendered_available"))},
        "performance_audit": {"metrics": perf_metrics, "page_weight": perf_weight,
                              "mobile_field": bool((cwv or {}).get("field")), "desktop_field": bool((cwv_desktop or {}).get("field"))},
        "structured_data_audit": {"pages": sd_table, "rich_results_eligible": rich_results},
        "security_audit": {"headers": security_headers, "headers_measured": bool(sample.get("headers_measured")),
                           "wordpress": sample.get("wordpress") or {}},
        "page_sample": {"status": sample.get("status"), "pages": len(sample.get("pages") or []),
                        "fetched_via": sample.get("fetched_via"), "headers_measured": bool(sample.get("headers_measured")),
                        "blocked_pages": sum(1 for p in sample.get("pages") or [] if p.get("blocked"))},
        "core_web_vitals_desktop": cwv_desktop or None,
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
            + "Sources: crawl audit, direct page sample, PageSpeed, broken links (no Ahrefs). "
            + (
                "CWV measured this pass via PageSpeed Insights. "
                if cwv_field
                else "CWV not measured this pass (handoff to cwv-measurement). "
            )
            + "Lightweight rendering + hreflang surface checks included; full rendering-audit / hreflang-validator for deep passes."
        ),
    }


def _candidate_urls(website: dict[str, Any], ia: dict[str, Any]) -> list[str]:
    """Live pages worth sampling: crawled 200 pages first, then the IA's current URLs."""
    urls: list[str] = []
    pages = ((website.get("site_sitemap") or {}).get("pages")) or []
    ranked = sorted(
        (p for p in pages if isinstance(p, dict)),
        key=lambda p: (str(p.get("status")) != "200", int(p.get("depth") or 9)),
    )
    for p in ranked:
        u = p.get("url") or p.get("canonical_url")
        if u:
            urls.append(str(u))
    for node in ia.get("target_url_tree") or []:
        if isinstance(node, dict) and str(node.get("url") or "").startswith("http"):
            urls.append(str(node["url"]))
    return urls[:60]


def _severity_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Info": 0}
    for issue in issues:
        sev = str(issue.get("severity") or "Medium")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def _coverage_notes(
    *,
    sample: dict[str, Any],
    seo: dict[str, Any],
    gsc: dict[str, Any],
    cwv: dict[str, Any] | None,
    cwv_desktop: dict[str, Any] | None,
    rendering: dict[str, Any],
) -> dict[str, str]:
    """One line per group on where its data came from, and why coverage is reduced."""
    pages = sample.get("pages") or []
    blocked = sum(1 for p in pages if p.get("blocked"))
    n = sum(1 for p in pages if p.get("parsed"))
    crawl_n = (seo or {}).get("pages_audited") or (seo or {}).get("pages_analyzed") or 0
    base = (
        f"Checked a direct sample of {n} page(s)"
        + (f" and the crawl audit of {crawl_n} page(s)" if crawl_n else "")
        + "."
    )
    if blocked:
        via_firecrawl = sum(1 for p in pages if p.get("blocked") and p.get("html_source") == "firecrawl")
        base += (
            f" {blocked} sampled page(s) blocked this server"
            + (f"; {via_firecrawl} were read through Firecrawl instead." if via_firecrawl
               else "; their HTML could not be read (no Firecrawl copy).")
        )
    gsc_on = gsc.get("status") == "available"
    psi = "PageSpeed Insights " + ("mobile and desktop" if (cwv_desktop or {}).get("lab") or (cwv_desktop or {}).get("field") else "mobile")
    field_note = "real-user field data" if (cwv or {}).get("field") else "lab data only (no Chrome field data for this URL)"
    notes = {g: base for g in (
        "Crawlability", "Indexation", "URL structure", "Canonicalisation", "Sitemap", "Internal linking",
        "On-page technical SEO", "JavaScript/rendering", "Performance", "Mobile", "Structured data", "Security",
    )}
    notes["Indexation"] = base + (" Search Console indexation data included." if gsc_on
                                  else " Search Console not connected: indexed/excluded and soft-404 counts are not measured.")
    notes["Canonicalisation"] = base + ("" if gsc_on else " Google-selected canonicals need Search Console.")
    notes["Performance"] = f"{psi}: {field_note}, plus HTML weight from the direct sample." if (cwv or {}).get("lab") or (cwv or {}).get("field") else base + " PageSpeed Insights returned no data this pass."
    notes["Mobile"] = base + (" Mobile and desktop fetches compared." if rendering.get("status") == "available" else "")
    notes["JavaScript/rendering"] = base + (
        " Raw HTML compared with a browser-rendered copy (Firecrawl)." if sample.get("rendered_available")
        else " No browser-rendered copy (Firecrawl unavailable): raw HTML checked for app-shell signs only."
    )
    notes["Security"] = base + ("" if sample.get("headers_measured")
                                else " The site blocked this server's request, so response headers were not read.")
    notes["Structured data"] = base + " JSON-LD and microdata parsed on the sampled pages."
    return notes


def _coverage_needs(*, cwv: dict[str, Any] | None, sample: dict[str, Any], settings: Any) -> dict[str, str]:
    """Group -> the specific fix that would complete it, when this pass shows the cause."""
    needs: dict[str, str] = {}
    error = str((cwv or {}).get("error") or "")
    if "429" in error and not settings.pagespeed_api_key:
        needs["Performance"] = "Set PAGESPEED_API_KEY: PageSpeed's shared anonymous quota answered 429 this pass."
    elif error:
        needs["Performance"] = f"PageSpeed Insights failed this pass ({error[:80]}); re-run."
    if not settings.firecrawl_api_key:
        needs["JavaScript/rendering"] = "Set FIRECRAWL_API_KEY for a browser-rendered copy to compare with raw HTML."
    if (sample.get("pages") or []) and not sample.get("headers_measured"):
        needs["Security"] = (
            "Response headers: the site blocked this server; allow-list it or check headers from a browser."
            + ("" if settings.firecrawl_api_key else " Set FIRECRAWL_API_KEY to read blocked pages' HTML.")
        )
    return needs


def _merged_data_sources(
    rendering: dict[str, Any],
    hreflang: dict[str, Any],
    cwv_field: dict[str, Any] | None,
    gsc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {}
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
