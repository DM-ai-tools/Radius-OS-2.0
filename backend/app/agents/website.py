"""Phase 3 — Website Situation Agent per website-situation-agent skill."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm import extract_domain
from app.integrations.providers import (
    check_broken_links,
    crawl_site,
    optimize_on_page,
    pull_backlinks,
    run_seo_audit,
    run_technical_seo_audit,
)
from app.ml.scoring import detect_traffic_anomalies, spam_risk_score
from app.models import (
    AgentJob,
    BacklinkSnapshot,
    Client,
    ClientDigitalProfile,
    FindingsLedger,
    WebsiteAudit,
)
from app.services.audit import log_event
from app.services.readiness import recompute_readiness
from app.skills import load_skill


def _parse_scope(message: str) -> set[str]:
    """Return which sub-steps to run."""
    lowered = message.lower()
    # Technical SEO first — "technical SEO audit" must not match generic seo_audit
    tech_intent = any(
        k in lowered
        for k in (
            "technical seo",
            "tech seo",
            "core web vitals",
            "site speed",
            "page speed",
            "crawlability",
            "indexation",
            "robots.txt",
            "sitemap check",
            "sitemap audit",
            "render blocking",
            "mobile-friendly",
            "mobile friendly",
        )
    )
    if tech_intent and "situation" not in lowered and "full" not in lowered:
        return {"technical_seo"}
    # Comprehensive SEO audit (distinct from narrower technical SEO)
    seo_audit_intent = any(
        k in lowered
        for k in (
            "seo audit",
            "audit seo",
            "seo health",
            "find seo issues",
            "check my site's seo",
            "check my sites seo",
            "check site seo",
            "comprehensive seo",
            "full seo audit",
        )
    ) or (
        "site audit" in lowered
        and "technical" not in lowered
        and "situation" not in lowered
    )
    if seo_audit_intent and "situation" not in lowered and "full website" not in lowered:
        return {"seo_audit"}
    on_page_intent = any(
        k in lowered
        for k in (
            "on-page",
            "on page",
            "optimize this page",
            "optimize meta",
            "meta tags",
            "improve seo for",
            "improve rankings",
            "onpage",
        )
    )
    if on_page_intent and "situation" not in lowered and "full" not in lowered:
        return {"on_page"}
    broken_intent = any(
        k in lowered
        for k in (
            "broken link",
            "dead link",
            "dead url",
            "fix 404",
            "404s",
            "link checker",
            "link audit",
        )
    )
    if broken_intent and "situation" not in lowered and "full" not in lowered:
        return {"broken_links"}
    if "crawl" in lowered and "backlink" not in lowered and "anomal" not in lowered:
        if "situation" not in lowered and "full" not in lowered:
            return {"crawl", "broken_links"}
    if ("backlink" in lowered or "anomal" in lowered) and "crawl" not in lowered:
        if "situation" not in lowered:
            return {"backlink", "anomaly"}
    # Full website situation: crawl + per-page SEO audit + authority + anomalies
    return {"crawl", "seo_audit", "backlink", "anomaly", "broken_links"}


async def run_website(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("website_situation_agent")
    _ = load_skill("broken_link_checker")
    _ = load_skill("on_page_seo")
    _ = load_skill("technical_seo_audit")
    _ = load_skill("seo_audit")
    _ = user_id
    events: list[dict] = []
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    profile.website_status = "in_progress"
    scope = _parse_scope(message)

    tabs: dict = {
        "technical": {"status": "not_run_this_session"},
        "authority": {"status": "not_run_this_session"},
        "anomalies": {"status": "not_run_this_session"},
    }
    severities = {"technical": "info", "authority": "info", "anomalies": "info"}
    audit_ids: dict[str, str] = {}
    audits_for_ledger: list[WebsiteAudit] = []
    broken_report: dict | None = None
    on_page_report: dict | None = None
    tech_report: dict | None = None
    seo_audit_report: dict | None = None

    crawl: dict = {}
    crawl_audit: WebsiteAudit | None = None

    if "seo_audit" in scope:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Comprehensive SEO audit for {client.primary_url} — "
                    "discovering all site pages (sitemap + crawl), then checking "
                    "meta, headings, images, schema, and mobile signals on each…"
                ),
            }
        )
        seo_audit_report = await run_seo_audit(
            client.primary_url, display_name=client.display_name
        )
        sa = WebsiteAudit(
            client_id=client.id,
            audit_type="seo_audit",
            summary=seo_audit_report,
            severity=seo_audit_report.get("severity", "info"),
            status="pending",
        )
        db.add(sa)
        await db.flush()
        audit_ids["seo_audit"] = str(sa.id)
        audits_for_ledger.append(sa)
        tabs["technical"] = {
            "seo_audit_score": seo_audit_report.get("overall_score"),
            "pages_analyzed": seo_audit_report.get("pages_analyzed"),
            "note": "Standalone comprehensive SEO audit — Authority/Anomalies not run this session.",
        }
        severities["technical"] = seo_audit_report.get("severity", "info")

    if "technical_seo" in scope:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Technical SEO audit for {client.primary_url} — "
                    "crawlability, indexation, CWV, mobile, security, schema…"
                ),
            }
        )
        tech_report = await run_technical_seo_audit(
            client.primary_url, display_name=client.display_name
        )
        tech_audit = WebsiteAudit(
            client_id=client.id,
            audit_type="technical_seo",
            summary=tech_report,
            severity=tech_report.get("severity", "info"),
            status="pending",
        )
        db.add(tech_audit)
        await db.flush()
        audit_ids["technical_seo"] = str(tech_audit.id)
        audits_for_ledger.append(tech_audit)
        tabs["technical"] = {
            "technical_seo_score": tech_report.get("score"),
            "site": tech_report.get("site"),
            "note": "Standalone technical SEO audit — Authority/Anomalies not run this session.",
        }
        severities["technical"] = tech_report.get("severity", "info")

    if "on_page" in scope:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"On-Page SEO specialist analyzing {client.primary_url} "
                    "(title, meta, headings, schema, internal links)…"
                ),
            }
        )
        on_page_report = await optimize_on_page(
            client.primary_url,
            display_name=client.display_name,
            message=message,
        )
        op_audit = WebsiteAudit(
            client_id=client.id,
            audit_type="on_page_seo",
            summary=on_page_report,
            severity=on_page_report.get("severity", "info"),
            status="pending",
        )
        db.add(op_audit)
        await db.flush()
        audit_ids["on_page_seo"] = str(op_audit.id)
        audits_for_ledger.append(op_audit)
        tabs["technical"] = {
            "on_page_summary": {
                "target_keyword": on_page_report.get("target_keyword"),
                "current_score": on_page_report.get("current_score"),
                "optimized_score": on_page_report.get("optimized_score"),
            },
            "note": "Standalone on-page optimization — Authority/Anomalies not run this session.",
        }
        severities["technical"] = on_page_report.get("severity", "info")

    if "broken_links" in scope and "crawl" not in scope and "on_page" not in scope:
        # Dedicated broken-link-checker skill path
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Broken Link Checker scanning {client.primary_url} for 404s, "
                    "dead external links, and redirect chains…"
                ),
            }
        )
        broken_report = await check_broken_links(client.primary_url)
        bl_audit = WebsiteAudit(
            client_id=client.id,
            audit_type="broken_links",
            summary=broken_report,
            severity=broken_report.get("severity", "warning"),
            status="pending",
        )
        db.add(bl_audit)
        await db.flush()
        audit_ids["broken_links"] = str(bl_audit.id)
        audits_for_ledger.append(bl_audit)
        tabs["technical"] = {
            "broken_link_summary": {
                "pages_scanned": broken_report.get("pages_scanned"),
                "broken_count": broken_report.get("broken_count"),
                "redirect_chain_count": broken_report.get("redirect_chain_count"),
            },
            "note": "Standalone broken-link audit — Authority/Anomalies not run this session.",
        }
        severities["technical"] = broken_report.get("severity", "warning")

    if "crawl" in scope:
        job = AgentJob(
            session_id=session_id,
            agent_key="website_situation_agent",
            job_type="site_crawl",
            status="running",
            attempt_count=1,
        )
        db.add(job)
        await db.flush()

        events.append(
            {
                "type": "system_notice",
                "content": f"Site crawl started for {client.primary_url} — this will take a moment…",
            }
        )
        events.append(
            {
                "type": "job_progress",
                "payload": {
                    "job_id": str(job.id),
                    "job_type": "site_crawl",
                    "status": "running",
                    "message": f"Crawling {extract_domain(client.primary_url)}…",
                },
            }
        )

        # Reuse multi-page SEO audit inventory when already available (avoids a second research pass)
        if seo_audit_report and (seo_audit_report.get("pages") or []):
            sa_pages = seo_audit_report.get("pages") or []
            crawl = {
                "pages_found": len(sa_pages),
                "indexable": sum(
                    1 for p in sa_pages if int(p.get("status") or 0) == 200
                ),
                "redirects": 0,
                "redirect_chains": 0,
                "canonical_issues": sum(
                    1
                    for p in sa_pages
                    if any(
                        "canonical" in str(i.get("issue") or "").lower()
                        for i in (p.get("opportunities") or [])
                    )
                ),
                "broken_links": sum(
                    1 for p in sa_pages if int(p.get("status") or 0) >= 400
                ),
                "notable_changes": [],
                "severity": seo_audit_report.get("severity", "info"),
                "status_samples": [
                    {
                        "url": p.get("url"),
                        "status": p.get("status"),
                        "title": p.get("title"),
                    }
                    for p in sa_pages[:40]
                ],
                "discovered_urls": [p.get("url") for p in sa_pages if p.get("url")],
                "note": (
                    f"Page inventory from SEO audit of {len(sa_pages)} pages "
                    "(title, meta, H1, images, canonical per URL)."
                ),
            }
        else:
            crawl = await crawl_site(client.primary_url)
        job.status = "succeeded"
        job.completed_at = datetime.now(timezone.utc)
        job.result_ref = {"pages_found": crawl["pages_found"]}

        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Crawl progress: {crawl.get('pages_found', 0)} pages found, "
                    f"{crawl.get('canonical_issues', 0)} canonical issues, "
                    f"{crawl.get('redirect_chains', 0)} redirect chains."
                    + (
                        f" Note: {crawl['note']}"
                        if crawl.get("note")
                        else ""
                    )
                    + (
                        f" Error: {crawl['error']}"
                        if crawl.get("error")
                        else ""
                    )
                ),
            }
        )

        crawl_audit = WebsiteAudit(
            client_id=client.id,
            audit_type="crawl_technical",
            summary=crawl,
            severity=crawl.get("severity", "info"),
            linked_job_id=job.id,
            status="pending",
        )
        db.add(crawl_audit)
        await db.flush()
        tabs["technical"] = crawl
        severities["technical"] = crawl_audit.severity
        audit_ids["crawl_technical"] = str(crawl_audit.id)
        audits_for_ledger.append(crawl_audit)

        if "broken_links" in scope and broken_report is None:
            events.append(
                {
                    "type": "system_notice",
                    "content": "Running Broken Link Checker (404s, dead externals, redirect chains)…",
                }
            )
            broken_report = await check_broken_links(client.primary_url)
            link_audit = WebsiteAudit(
                client_id=client.id,
                audit_type="broken_links",
                summary=broken_report,
                severity=broken_report.get("severity", "warning"),
                status="pending",
            )
            db.add(link_audit)
            await db.flush()
            audit_ids["broken_links"] = str(link_audit.id)
            audits_for_ledger.append(link_audit)
            tabs["technical"] = {
                **crawl,
                "broken_links": {
                    "broken_count": broken_report.get("broken_count"),
                    "pages_scanned": broken_report.get("pages_scanned"),
                    "redirect_chain_count": broken_report.get("redirect_chain_count"),
                },
            }
    else:
        # Reuse recent crawl if skipping
        recent = (
            await db.execute(
                select(WebsiteAudit)
                .where(
                    WebsiteAudit.client_id == client.id,
                    WebsiteAudit.audit_type == "crawl_technical",
                )
                .order_by(WebsiteAudit.created_at.desc())
            )
        ).scalars().first()
        if recent and "broken_links" not in scope:
            crawl = recent.summary or {}
            tabs["technical"] = {**(crawl or {}), "status": "reused_prior_crawl"}
            severities["technical"] = recent.severity or "info"

    bl_audit: WebsiteAudit | None = None
    if "backlink" in scope:
        events.append(
            {
                "type": "system_notice",
                "content": "Pulling backlink profile (Ahrefs → Moz fallback if needed)…",
            }
        )
        domain = extract_domain(client.primary_url)
        try:
            bl, provider = await pull_backlinks(domain)
            spam = spam_risk_score(bl.get("sample_links", []))
            snap = BacklinkSnapshot(
                client_id=client.id,
                provider=provider,
                referring_domains=int(bl["referring_domains"] or 0),
                authority_score=Decimal(str(bl.get("authority_score") or 0)),
                spam_risk_score=Decimal(str(spam)),
                top_anchor_text=bl.get("top_anchor_text") or {},
            )
            db.add(snap)
            summary = {
                "provider": provider,
                "referring_domains": bl.get("referring_domains"),
                "authority_score": bl.get("authority_score"),
                "spam_risk_score": spam,
                "top_anchor_text": bl.get("top_anchor_text"),
                "note": bl.get("note"),
                "spam_pre_triage_note": (
                    "Spam-risk scores are advisory pre-triage only — not an automatic disavow."
                ),
            }
            bl_audit = WebsiteAudit(
                client_id=client.id,
                audit_type="backlink_summary",
                summary=summary,
                severity="warning" if spam > 0.4 else "info",
                status="pending",
            )
            if provider != "ahrefs":
                events.append(
                    {
                        "type": "system_notice",
                        "content": "Ahrefs unavailable — used Moz fallback.",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            bl_audit = WebsiteAudit(
                client_id=client.id,
                audit_type="backlink_summary",
                summary={
                    "error": str(exc),
                    "status": "unavailable",
                    "message": "Backlink data unavailable this run (primary and fallback failed).",
                },
                severity="warning",
                status="pending",
            )
            events.append(
                {
                    "type": "system_notice",
                    "content": (
                        "Backlink pull couldn't be confirmed — retried via primary and "
                        "fallback providers. Authority tab marked unavailable."
                    ),
                }
            )
        db.add(bl_audit)
        await db.flush()
        tabs["authority"] = bl_audit.summary or {}
        severities["authority"] = bl_audit.severity
        audit_ids["backlink_summary"] = str(bl_audit.id)
        audits_for_ledger.append(bl_audit)

    anom_audit: WebsiteAudit | None = None
    if "anomaly" in scope:
        if profile.tracking_status != "complete":
            events.append(
                {
                    "type": "system_notice",
                    "content": (
                        "Skipping traffic anomaly detection — requires a verified tracking "
                        f"baseline (tracking_status is '{profile.tracking_status}', need 'complete'). "
                        "Crawl/backlink results are still available."
                    ),
                }
            )
            tabs["anomalies"] = {
                "status": "skipped",
                "reason": "tracking_baseline_not_complete",
                "message": (
                    "Anomaly detection was not run against unverified tracking data. "
                    "Complete Phase 2 tracking approval first."
                ),
            }
            severities["anomalies"] = "info"
        else:
            events.append(
                {
                    "type": "system_notice",
                    "content": "Checking for traffic anomalies using verified tracking data…",
                }
            )
            anomaly = detect_traffic_anomalies()
            if crawl.get("notable_changes"):
                anomaly["correlated_change"] = crawl["notable_changes"][0]
                anomaly["correlation_note"] = (
                    "Hypothesis only — date alignment with a crawl change is not confirmed causation."
                )
            if float(anomaly.get("drop_percent") or 0) <= 10:
                anomaly["message"] = "No statistically significant anomalies detected."
                anomaly["severity"] = "info"
            anom_audit = WebsiteAudit(
                client_id=client.id,
                audit_type="traffic_anomaly",
                summary=anomaly,
                severity=anomaly.get("severity", "info"),
                status="pending",
            )
            db.add(anom_audit)
            await db.flush()
            tabs["anomalies"] = anom_audit.summary or {}
            severities["anomalies"] = anom_audit.severity
            audit_ids["traffic_anomaly"] = str(anom_audit.id)
            audits_for_ledger.append(anom_audit)

    for audit in audits_for_ledger:
        db.add(
            FindingsLedger(
                client_id=client.id,
                agent_key="website_situation_agent",
                source_table="website_audits",
                source_id=audit.id,
                confidence="medium",
                status="pending",
            )
        )

    # Publish draft into playground shared memory immediately (before Tech SEO approve)
    draft_summary: dict = {}
    for audit in audits_for_ledger:
        draft_summary[audit.audit_type] = {
            "summary": audit.summary,
            "severity": audit.severity,
            "status": audit.status or "pending",
        }
    if crawl:
        draft_summary["pages_found"] = crawl.get("pages_found")
        draft_summary["indexable"] = crawl.get("indexable")
        draft_summary["broken_links"] = crawl.get("broken_links")
        draft_summary["note"] = crawl.get("note")
        urls = crawl.get("discovered_urls") or [
            s.get("url") for s in (crawl.get("status_samples") or []) if s.get("url")
        ]
        if urls:
            draft_summary["sample_urls"] = urls[:20]
    if seo_audit_report:
        draft_summary["seo_pages_analyzed"] = seo_audit_report.get("pages_analyzed")
        draft_summary["seo_overall_score"] = seo_audit_report.get("overall_score")
        draft_summary["seo_score_band"] = seo_audit_report.get("score_band")
        if not draft_summary.get("sample_urls"):
            draft_summary["sample_urls"] = [
                p.get("url")
                for p in (seo_audit_report.get("pages") or [])[:20]
                if p.get("url")
            ]
        if draft_summary.get("pages_found") is None:
            draft_summary["pages_found"] = seo_audit_report.get("pages_analyzed")
    if draft_summary:
        draft_summary["_draft"] = True
        profile.website_situation_summary = draft_summary

    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "website_situation",
            "agent": "website_situation_agent",
            "scope": sorted(scope),
        },
    )
    await recompute_readiness(db, client.id)
    profile.website_status = "pending_signoff"

    if broken_report is not None:
        link_card = {
            "card_type": "broken_link_report",
            "title": "Broken Link Report",
            "pages_scanned": broken_report.get("pages_scanned"),
            "total_links_checked": broken_report.get("total_links_checked"),
            "broken_count": broken_report.get("broken_count"),
            "redirect_chain_count": broken_report.get("redirect_chain_count"),
            "internal": broken_report.get("internal") or [],
            "external": broken_report.get("external") or [],
            "redirect_chains": broken_report.get("redirect_chains") or [],
            "quick_fixes": broken_report.get("quick_fixes") or [],
            "agent_key": "website_situation_agent",
            "skill": "broken-link-checker",
            "actions": ["approve", "edit", "reject"] if scope == {"broken_links"} else [],
            "required_role": "technical_seo_specialist",
        }
        events.append(
            {
                "type": "agent_message",
                "agent_key": "website_situation_agent",
                "content": (
                    f"Broken Link Checker found {broken_report.get('broken_count', 0)} broken link(s) "
                    f"across {broken_report.get('pages_scanned', 0)} pages "
                    f"({broken_report.get('total_links_checked', 0)} links checked). "
                    f"{broken_report.get('redirect_chain_count', 0)} redirect chain(s) flagged."
                ),
            }
        )
        events.append({"type": "structured_card", "payload": link_card})
        if scope == {"broken_links"}:
            events.append({"type": "checkpoint", "payload": link_card})

    if on_page_report is not None:
        op_card = {
            "card_type": "on_page_seo_report",
            "card_title": f"On-Page SEO Report: {on_page_report.get('page_name', 'Page')}",
            **{k: v for k, v in on_page_report.items() if k != "severity"},
            "agent_key": "website_situation_agent",
            "skill": "on-page-seo",
            "actions": ["approve", "edit", "reject"] if scope == {"on_page"} else [],
            "required_role": "technical_seo_specialist",
        }
        events.append(
            {
                "type": "agent_message",
                "agent_key": "website_situation_agent",
                "content": (
                    f"On-page score for \"{on_page_report.get('target_keyword')}\" is "
                    f"{on_page_report.get('current_score')}/100 "
                    f"(estimated {on_page_report.get('optimized_score')}/100 after fixes). "
                    "Review title, meta, headings, schema, and internal links below."
                ),
            }
        )
        events.append({"type": "structured_card", "payload": op_card})
        if scope == {"on_page"}:
            events.append({"type": "checkpoint", "payload": op_card})

    if tech_report is not None:
        tech_card = {
            "card_type": "technical_seo_report",
            "title": "Technical SEO Audit Report",
            "site": tech_report.get("site"),
            "score": tech_report.get("score"),
            "sections": tech_report.get("sections") or {},
            "priority_fixes": tech_report.get("priority_fixes") or [],
            "agent_key": "website_situation_agent",
            "skill": "technical-seo-audit",
            "actions": ["approve", "edit", "reject"] if scope == {"technical_seo"} else [],
            "required_role": "technical_seo_specialist",
        }
        events.append(
            {
                "type": "agent_message",
                "agent_key": "website_situation_agent",
                "content": (
                    f"Technical SEO score for {tech_report.get('site')}: "
                    f"{tech_report.get('score')}/100. "
                    f"{len(tech_report.get('priority_fixes') or [])} priority fix(es) listed."
                ),
            }
        )
        events.append({"type": "structured_card", "payload": tech_card})
        if scope == {"technical_seo"}:
            events.append({"type": "checkpoint", "payload": tech_card})

    if seo_audit_report is not None:
        sa_card = {
            "card_type": "seo_audit_report",
            "title": "SEO Audit Report",
            "site": seo_audit_report.get("site"),
            "pages_analyzed": seo_audit_report.get("pages_analyzed"),
            "overall_score": seo_audit_report.get("overall_score"),
            "score_band": seo_audit_report.get("score_band"),
            "critical": seo_audit_report.get("critical") or [],
            "warnings": seo_audit_report.get("warnings") or [],
            "opportunities": seo_audit_report.get("opportunities") or [],
            "passing": seo_audit_report.get("passing") or [],
            "pages": seo_audit_report.get("pages") or [],
            "agent_key": "website_situation_agent",
            "skill": "seo-audit",
            "actions": ["approve", "edit", "reject"] if scope == {"seo_audit"} else [],
            "required_role": "technical_seo_specialist",
        }
        events.append(
            {
                "type": "agent_message",
                "agent_key": "website_situation_agent",
                "content": (
                    f"SEO Audit for {seo_audit_report.get('site')}: "
                    f"{seo_audit_report.get('overall_score')}/100 "
                    f"({seo_audit_report.get('score_band')}) across "
                    f"{seo_audit_report.get('pages_analyzed')} page(s). "
                    f"{len(seo_audit_report.get('critical') or [])} critical, "
                    f"{len(seo_audit_report.get('warnings') or [])} warnings."
                ),
            }
        )
        events.append({"type": "structured_card", "payload": sa_card})
        if scope == {"seo_audit"}:
            events.append({"type": "checkpoint", "payload": sa_card})

    # Full / multi-step website card (skip when standalone sub-skills)
    standalone = ({"broken_links"}, {"on_page"}, {"technical_seo"}, {"seo_audit"})
    if scope not in standalone:
        card = {
            "card_type": "website_audit",
            "title": "Website Situation — Audit Summary",
            "tabs": tabs,
            "severities": severities,
            "tabs_run": sorted(scope),
            "agent_key": "website_situation_agent",
            "actions": ["approve", "edit", "reject"],
            "required_role": "technical_seo_specialist",
            "audit_ids": audit_ids,
        }
        pages = crawl.get("pages_found", 0) if crawl else 0
        events.append(
            {
                "type": "agent_message",
                "agent_key": "website_situation_agent",
                "content": (
                    f"Website situation report ready (ran: {', '.join(sorted(scope))}). "
                    + (f"Crawl found {pages} pages. " if pages else "")
                    + "Review Technical / Authority / Anomalies tabs, then approve or annotate "
                    "(Technical SEO Specialist)."
                ),
            }
        )
        events.append({"type": "structured_card", "payload": card})
        events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {
                "website_status": profile.website_status,
                "overall_readiness_score": float(profile.overall_readiness_score or 0),
            },
        }
    )
    next_msg = "Next: Tech SEO Approve website report, then run competitor analysis."
    if scope == {"broken_links"}:
        next_msg = "Next: Tech SEO review broken-link fixes, then continue website/competitor phases."
    elif scope == {"on_page"}:
        next_msg = "Next: apply on-page fixes (or Approve), then continue Phase 3 / competitor analysis."
    elif scope == {"technical_seo"}:
        next_msg = "Next: tackle Critical/High fixes (or Approve), then continue Phase 3 / competitor analysis."
    elif scope == {"seo_audit"}:
        next_msg = "Next: fix Critical issues first (or Approve), then continue Phase 3 / competitor analysis."
    events.append({"type": "system_notice", "content": next_msg})
    return events
