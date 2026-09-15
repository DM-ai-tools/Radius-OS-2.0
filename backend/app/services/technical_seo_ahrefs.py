"""Economical Ahrefs Site Audit ingestion for Phase 7 Technical SEO.

API cost strategy (per audit):
  1. GET /site-audit/projects — FREE — resolve project + health score
  2. GET /site-audit/issues — 50 units — full issue inventory with counts
  3. GET /site-audit/page-explorer — 50 units per 1000 pages — normalized pages
  4. GET /site-audit/page-explorer?issue_id=… — 50 units each — sample URLs for
     top N priority issues only (default 3)
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.integrations import ahrefs
from app.logging_config import get_logger
from app.services.technical_seo_normalize import normalize_ahrefs_page
from app.services.technical_seo_rules import (
    compute_category_scores,
    issues_from_ahrefs,
    issues_from_pages,
    merge_issues,
    overall_score,
    severity_summary,
)
from app.services.technical_seo_schemas import TechnicalSEOPage
from app.services.technical_seo_companion import duplicate_groups_from_pages

log = get_logger("technical_seo_ahrefs")


async def fetch_ahrefs_technical_seo(
    primary_url: str,
    *,
    project_id: int | None = None,
    date_compared: str | None = None,
) -> dict[str, Any]:
    """Fetch and normalize Ahrefs Site Audit data. Never fabricates metrics."""
    settings = get_settings()
    errors: list[str] = []
    api_calls = 0

    if not settings.ahrefs_api_key or settings.use_mock_providers:
        return {
            "available": False,
            "audit_state": "FAILED",
            "errors": ["ahrefs_unavailable"],
            "reason": "Ahrefs API key missing or mock providers enabled.",
        }

    resolved_id = project_id or settings.ahrefs_site_audit_project_id
    log.info("audit_started", url=primary_url, project_id=resolved_id)

    project, proj_errors = await ahrefs.resolve_site_audit_project(
        primary_url, project_id=resolved_id
    )
    errors.extend(proj_errors)
    if not project:
        if "ahrefs_insufficient_plan" in errors:
            reason = (
                "Ahrefs Site Audit API is not included in this Ahrefs plan "
                "(API returned Insufficient plan). Upgrade the Ahrefs plan for Site Audit API "
                "access, or continue with the crawl-based audit fallback."
            )
        elif any(e in errors for e in ("ahrefs_forbidden", "ahrefs_site_audit_projects_failed")):
            reason = (
                "Ahrefs Site Audit projects API failed (auth/plan/permission). "
                "Check the API key scope, or continue with the crawl-based audit fallback."
            )
        else:
            reason = (
                "No Ahrefs Site Audit project found for this domain. "
                "Create a project in Ahrefs with verified ownership, or set "
                "AHREFS_SITE_AUDIT_PROJECT_ID."
            )
        return {
            "available": False,
            "audit_state": "FAILED",
            "errors": errors,
            "reason": reason,
        }

    pid = int(project.get("project_id") or project.get("id") or resolved_id or 0)
    if not pid:
        return {
            "available": False,
            "audit_state": "FAILED",
            "errors": errors + ["ahrefs_project_id_missing"],
            "reason": "Could not determine Ahrefs project_id.",
        }

    health_score = project.get("health_score")
    if health_score is not None:
        try:
            health_score = int(health_score)
        except (TypeError, ValueError):
            health_score = None

    crawl_date = project.get("date")
    total_urls = project.get("total")
    urls_with_errors = project.get("urls_with_errors")

    issues_raw, issue_errors = await ahrefs.site_audit_issues(
        pid, date_compared=date_compared
    )
    api_calls += 1
    errors.extend(issue_errors)
    log.info("ahrefs_response", endpoint="issues", count=len(issues_raw))

    # Paginated page inventory — skip when limit is 0 (issues-only fast path).
    page_limit = int(settings.ahrefs_site_audit_page_limit or 0)
    pages: list[TechnicalSEOPage] = []
    if page_limit > 0:
        offset = 0
        batch = min(1000, page_limit)
        while len(pages) < page_limit:
            chunk, page_errors = await ahrefs.site_audit_page_explorer(
                pid, limit=batch, offset=offset
            )
            api_calls += 1
            errors.extend(page_errors)
            if not chunk:
                break
            for row in chunk:
                norm = normalize_ahrefs_page(row)
                if norm:
                    pages.append(norm)
            if len(chunk) < batch:
                break
            offset += batch
            if offset >= page_limit:
                break

    log.info("normalization_completed", pages=len(pages))

    ahrefs_issues = issues_from_ahrefs(issues_raw)
    supplemental = issues_from_pages(pages) if pages else []
    all_issues = merge_issues(ahrefs_issues, supplemental)

    # Sample URLs for top priority Ahrefs issues only (50 units each) — in parallel.
    sample_limit = settings.ahrefs_site_audit_issue_sample_limit
    by_id = {i.ahrefs_issue_id: i for i in all_issues if i.ahrefs_issue_id}
    candidates = [i for i in all_issues if i.ahrefs_issue_id][:sample_limit]

    async def _sample_issue(issue: Any) -> None:
        nonlocal api_calls
        ahrefs_id = issue.ahrefs_issue_id
        if not ahrefs_id:
            return
        rows, samp_errors = await ahrefs.site_audit_page_explorer(
            pid, issue_id=ahrefs_id, limit=25, offset=0, select="url"
        )
        api_calls += 1
        errors.extend(samp_errors)
        urls = [str(r.get("url")).strip() for r in rows if r.get("url")][:25]
        issue.affected_urls = urls
        if ahrefs_id in by_id:
            by_id[ahrefs_id].affected_urls = urls

    if candidates:
        await asyncio.gather(*[_sample_issue(issue) for issue in candidates])

    category_scores = compute_category_scores(
        all_issues, pages_available=bool(pages), ahrefs_health_score=health_score
    )
    score = overall_score(category_scores, ahrefs_health_score=health_score)
    sev = severity_summary(all_issues)
    dup_titles, dup_meta = duplicate_groups_from_pages(pages)

    domain = urlparse(primary_url if "://" in primary_url else f"https://{primary_url}").netloc

    comparison: dict[str, Any] | None = None
    if date_compared and issues_raw:
        comparison = _build_comparison(issues_raw)

    log.info(
        "audit_completed",
        project_id=pid,
        score=score,
        issues=len(all_issues),
        api_calls=api_calls,
    )

    return {
        "available": True,
        "audit_state": "COMPLETED",
        "provider": "ahrefs_site_audit",
        "project_id": pid,
        "project_name": project.get("project_name"),
        "target_url": project.get("target_url") or primary_url,
        "crawl_date": crawl_date,
        "health_score": health_score,
        "total_urls": total_urls,
        "urls_with_errors": urls_with_errors,
        "domain": domain,
        "score": score,
        "overall_score": score,
        "category_scores": category_scores,
        "severity_summary": sev,
        "issues": [i.to_dict() for i in all_issues],
        "pages_fetched": len(pages),
        "sample_urls": [p.url for p in pages[:8]],
        "page_inventory": [
            {
                "url": p.url,
                "status_code": p.status_code,
                "indexable": p.indexable,
                "depth": p.depth,
                "internal_link_count": p.internal_link_count,
                "title": p.title,
                "meta_description": p.meta_description,
            }
            for p in pages
        ],
        "duplicate_titles": dup_titles,
        "duplicate_meta_descriptions": dup_meta,
        "api_calls_estimate": api_calls,
        "api_units_estimate": max(0, (api_calls - 1) * 50),  # projects is free
        "errors": errors,
        "audit_comparison": comparison,
        "data_sources": {
            "ahrefs_issues": {"status": "available", "count": len(issues_raw)},
            "ahrefs_pages": {
                "status": "available" if pages else "NOT_AVAILABLE",
                "count": len(pages),
                "reason": None if pages else "No pages returned from page-explorer.",
            },
            "core_web_vitals": {
                "status": "NOT_AVAILABLE",
                "reason": "CWV is measured via PageSpeed Insights, not Ahrefs Site Audit.",
            },
        },
    }


def _build_comparison(issues_raw: list[dict[str, Any]]) -> dict[str, Any]:
    new_issues: list[str] = []
    resolved: list[str] = []
    for row in issues_raw:
        name = str(row.get("name") or "")
        issue_id = str(row.get("issue_id") or "")
        if int(row.get("new") or 0) > 0:
            new_issues.append(name or issue_id)
        if int(row.get("removed") or 0) > 0:
            resolved.append(name or issue_id)
    return {
        "new_issues": new_issues[:20],
        "resolved_issues": resolved[:20],
        "new_count": len(new_issues),
        "resolved_count": len(resolved),
    }
