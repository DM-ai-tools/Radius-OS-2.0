"""Human checkpoint approve / edit / reject / flag flows."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import require_permission
from app.models import (
    ClientDigitalProfile,
    CompetitorProfile,
    DiscoveryResponse,
    FindingsLedger,
    TrackingAudit,
    User,
    WebsiteAudit,
)
from app.services.audit import log_event
from app.services.readiness import recompute_readiness


async def _apply_review(
    db: AsyncSession,
    *,
    user: User,
    ledger: FindingsLedger,
    action: str,
    edits: dict | None = None,
    note: str | None = None,
) -> FindingsLedger:
    if action == "reject":
        ledger.status = "rejected"
        ledger.resolved_at = datetime.now(timezone.utc)
        await _update_source_status(db, ledger, "rejected", user.id, edits)
        await _set_phase_status(db, ledger.client_id, ledger.agent_key, "in_progress")
        await log_event(
            db,
            client_id=ledger.client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="finding_rejected",
            event_detail={"ledger_id": str(ledger.id), "note": note},
        )
    elif action == "flag_for_client":
        if ledger.source_table != "tracking_audits":
            raise HTTPException(400, "flag_for_client only applies to tracking audits")
        ledger.status = "rejected"
        ledger.resolved_at = datetime.now(timezone.utc)
        await db.execute(
            update(TrackingAudit)
            .where(TrackingAudit.id == ledger.source_id)
            .values(status="flagged_for_client", reviewed_by=user.id)
        )
        await log_event(
            db,
            client_id=ledger.client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="finding_flagged",
            event_detail={"ledger_id": str(ledger.id), "note": note},
        )
    elif action in ("approve", "edit"):
        status = "edited" if action == "edit" else "approved"
        ledger.status = status
        ledger.resolved_at = datetime.now(timezone.utc)
        await _update_source_status(db, ledger, status, user.id, edits)
        await log_event(
            db,
            client_id=ledger.client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="finding_edited" if action == "edit" else "finding_approved",
            event_detail={"ledger_id": str(ledger.id), "edits": edits, "note": note},
        )
    else:
        raise HTTPException(400, f"Unknown action {action}")

    await db.flush()
    return ledger


async def _assert_not_self_review(user: User, ledger: FindingsLedger) -> None:
    """Architecture v1.9: domain reviewer is never the person who drafted it.

    Head of Department may override (escalation lead).
    """
    role_name = getattr(getattr(user, "role", None), "name", None) or ""
    if role_name == "head_of_department":
        return
    if ledger.created_by and ledger.created_by == user.id:
        raise HTTPException(
            403,
            "Architecture v1.9: the person who drafted this finding cannot approve it. "
            "Ask a domain reviewer or HoD to sign off.",
        )


async def review_finding(
    db: AsyncSession,
    *,
    user: User,
    ledger_id: UUID,
    action: str,
    edits: dict | None = None,
    note: str | None = None,
) -> FindingsLedger:
    user = (
        await db.execute(select(User).options(selectinload(User.role)).where(User.id == user.id))
    ).scalar_one()
    ledger = (
        await db.execute(select(FindingsLedger).where(FindingsLedger.id == ledger_id))
    ).scalar_one_or_none()
    if not ledger:
        # Allow reviewing by agent_key batch when ledger_id is a sentinel / first pending
        raise HTTPException(404, "Finding not found")

    await require_permission(user, db, ledger.agent_key, need_approve=True)
    if action in ("approve", "edit"):
        await _assert_not_self_review(user, ledger)
        from app.services.phase_validation import latest_validation_blocks_approve

        blocked = await latest_validation_blocks_approve(
            db, client_id=ledger.client_id, agent_key=ledger.agent_key
        )
        if blocked is not None:
            raise HTTPException(
                400,
                f"{ledger.agent_key} failed validation ({blocked.decision}) and cannot be approved. "
                f"{blocked.summary or 'Re-run the phase after correcting the issues.'}",
            )
    return await _apply_review(db, user=user, ledger=ledger, action=action, edits=edits, note=note)


async def approve_phase_batch(
    db: AsyncSession,
    *,
    user: User,
    client_id: UUID,
    agent_key: str,
    action: str,
    edits: dict | None = None,
    note: str | None = None,
    permission_checked: bool = False,
) -> dict:
    """Approve/reject all pending findings for an agent on a client (card-level action)."""
    user = (
        await db.execute(select(User).options(selectinload(User.role)).where(User.id == user.id))
    ).scalar_one()
    if not permission_checked:
        await require_permission(user, db, agent_key, need_approve=True)

    if action in ("approve", "edit"):
        from app.services.phase_validation import latest_validation_blocks_approve

        blocked = await latest_validation_blocks_approve(
            db, client_id=client_id, agent_key=agent_key
        )
        if blocked is not None:
            raise HTTPException(
                400,
                f"{agent_key} failed validation ({blocked.decision}) and cannot be approved. "
                f"{blocked.summary or 'Re-run the phase after correcting the issues.'}",
            )

    result = await db.execute(
        select(FindingsLedger).where(
            FindingsLedger.client_id == client_id,
            FindingsLedger.agent_key == agent_key,
            FindingsLedger.status == "pending",
        )
    )
    ledgers = list(result.scalars().all())
    if not ledgers and action == "approve":
        superseded = (
            await db.execute(
                select(FindingsLedger)
                .where(
                    FindingsLedger.client_id == client_id,
                    FindingsLedger.agent_key == agent_key,
                    FindingsLedger.status == "superseded",
                )
                .order_by(FindingsLedger.created_at.desc())
            )
        ).scalars().all()
        if superseded:
            newest = superseded[0].created_at
            batch = [
                row
                for row in superseded
                if newest is not None
                and row.created_at is not None
                and abs((row.created_at - newest).total_seconds()) < 120
            ]
            for row in batch:
                row.status = "pending"
            ledgers = batch
    if not ledgers:
        if action != "approve":
            raise HTTPException(404, "No pending findings")
        # Approving with nothing pending is only valid as an idempotent re-approve of a
        # phase that already produced findings. A blocked/empty run has none — marking it
        # "complete" would tell downstream phases their upstream pack is ready when it is
        # empty, silently propagating hollow deliverables through the rest of the chain.
        already_resolved = (
            await db.execute(
                select(FindingsLedger.id)
                .where(
                    FindingsLedger.client_id == client_id,
                    FindingsLedger.agent_key == agent_key,
                    FindingsLedger.status.in_(("approved", "edited")),
                )
                .limit(1)
            )
        ).first()
        if not already_resolved:
            raise HTTPException(
                400,
                f"{agent_key} produced no findings to approve — it was blocked or "
                "returned an empty result. Run the upstream phase it asked for, "
                "then re-run this phase before approving.",
            )

    if action in ("approve", "edit"):
        for ledger in ledgers:
            await _assert_not_self_review(user, ledger)

    for ledger in ledgers:
        await _apply_review(
            db,
            user=user,
            ledger=ledger,
            action="approve" if action == "approve" else action,
            edits=edits if action == "edit" else None,
            note=note,
        )

    # Roll up into client digital profile when approving
    if action in ("approve", "edit"):
        await _rollup_to_profile(db, client_id, agent_key, edits)
        await _set_phase_status(db, client_id, agent_key, "complete")
    elif action == "reject":
        await _set_phase_status(db, client_id, agent_key, "in_progress")
    elif action == "flag_for_client":
        # Tracking skill: keep phase in progress; do not write baseline
        await _set_phase_status(db, client_id, agent_key, "in_progress")

    profile = await recompute_readiness(db, client_id)
    phase_statuses = {
        "discovery": profile.discovery_status,
        "tracking": profile.tracking_status,
        "website": profile.website_status,
        "competitor": profile.competitor_status,
        "search_demand": profile.search_demand_status,
        "seo_strategy": profile.seo_strategy_status,
        "site_architecture": profile.site_architecture_status,
        "technical_seo": profile.technical_seo_status,
        "content_audit": profile.content_audit_status,
        "content_planning": profile.content_planning_status,
        "content_production": profile.content_production_status,
        "on_page_seo": profile.on_page_seo_status,
        "publishing": profile.publishing_status,
    }
    out: dict = {
        "resolved": len(ledgers),
        "agent_key": agent_key,
        "overall_readiness_score": float(profile.overall_readiness_score or 0),
        "phase_statuses": phase_statuses,
    }
    if action in ("approve", "edit"):
        from app.services.agent_handoff import approve_handoff

        out["handoff"] = approve_handoff(agent_key, phase_statuses=phase_statuses)
    return out


async def _update_source_status(
    db: AsyncSession,
    ledger: FindingsLedger,
    status: str,
    user_id: UUID,
    edits: dict | None,
) -> None:
    table = ledger.source_table
    if table == "discovery_responses":
        row = (
            await db.execute(
                select(DiscoveryResponse).where(DiscoveryResponse.id == ledger.source_id)
            )
        ).scalar_one_or_none()
        if row:
            row.status = status
            row.reviewed_by = user_id
            if edits and row.field_key in edits:
                row.field_value = {"value": edits[row.field_key]}
    elif table == "tracking_audits":
        row = (
            await db.execute(select(TrackingAudit).where(TrackingAudit.id == ledger.source_id))
        ).scalar_one_or_none()
        if row:
            row.status = "approved" if status in ("approved", "edited") else status
            row.reviewed_by = user_id
    elif table == "website_audits":
        row = (
            await db.execute(select(WebsiteAudit).where(WebsiteAudit.id == ledger.source_id))
        ).scalar_one_or_none()
        if row:
            row.status = status
            row.reviewed_by = user_id
            if edits:
                row.summary = {**(row.summary or {}), **edits}
    elif table == "competitor_profiles":
        row = (
            await db.execute(
                select(CompetitorProfile).where(CompetitorProfile.id == ledger.source_id)
            )
        ).scalar_one_or_none()
        if row:
            row.confirmed = status in ("approved", "edited")


async def _set_phase_status(db: AsyncSession, client_id: UUID, agent_key: str, status: str) -> None:
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one()
    mapping = {
        "discovery_agent": "discovery_status",
        "tracking_access_agent": "tracking_status",
        "website_situation_agent": "website_status",
        "competitor_market_agent": "competitor_status",
        "search_demand": "search_demand_status",
        "content_strategy": "seo_strategy_status",
        "site_architecture": "site_architecture_status",
        "technical_seo": "technical_seo_status",
        "content_audit": "content_audit_status",
        "content_planning": "content_planning_status",
        "content_production": "content_production_status",
        "on_page_seo": "on_page_seo_status",
        "publishing": "publishing_status",
    }
    field = mapping.get(agent_key)
    if field:
        setattr(profile, field, status)
    await db.flush()


async def _rollup_to_profile(
    db: AsyncSession, client_id: UUID, agent_key: str, edits: dict | None
) -> None:
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one()

    if agent_key == "discovery_agent":
        rows = (
            await db.execute(
                select(DiscoveryResponse).where(DiscoveryResponse.client_id == client_id)
            )
        ).scalars().all()
        commercial_keys = {
            "business_model",
            "business_keywords",
            "products",
            "products_for_promotion",
            "positioning",
            "revenue_split",
            "average_order_value",
            "average_ticket_size",
            "lifetime_value",
            "lead_modes",
            "sales_cycle",
            "seasonality",
            "geographic_focus",
            "target_demographic",
            "b2b_b2c",
            "industry_targeting",
            "inferred_industry",
            "seo_traffic_current",
            "seo_traffic_target",
            "sem_leads_current",
            "sem_leads_target",
        }
        marketing_keys = {
            "objectives",
            "business_goal",
            "sales_promises",
            "compliance_constraints",
            "brand_guidelines",
            "other_marketing_spend",
            "competitors",
            "public_reviews_summary",
            "social_presence",
            "google_business_signals",
            "strengths",
            "weaknesses",
            "opportunities",
            "threats",
            "strategy_approach",
            "content_creation_notes",
            "blogs_notes",
        }
        commercial, marketing = {}, {}
        # Prefer client_questionnaire answers over pre_research when both exist
        by_key: dict = {}
        for r in rows:
            existing = by_key.get(r.field_key)
            if existing is None:
                by_key[r.field_key] = r
            elif r.source == "client_questionnaire":
                by_key[r.field_key] = r
        # Preserve prior early intake (contacts etc.) under client_intake
        prior_ctx = dict(profile.marketing_context or {})
        prior_intake = prior_ctx.get("client_intake")
        for key, r in by_key.items():
            if r.status not in ("approved", "edited", "pending"):
                continue
            val = (r.field_value or {}).get("value")
            if edits and key in edits:
                val = edits[key]
            if key in commercial_keys:
                commercial[key] = val
            if key in marketing_keys:
                marketing[key] = val
        if prior_intake:
            marketing["client_intake"] = prior_intake

        # Publish CDD access / credential notes for Tracking (T1)
        from app.services.discovery_fields import ACCESS_FIELDS

        access_notes: dict = {}
        for k in ACCESS_FIELDS:
            if edits and k in edits and edits[k] not in (None, "", [], {}):
                access_notes[k] = edits[k]
            elif k in by_key and by_key[k].status in ("approved", "edited", "pending"):
                val = (by_key[k].field_value or {}).get("value")
                if val not in (None, "", [], {}):
                    access_notes[k] = val
        if access_notes:
            marketing["access_credentials"] = access_notes
            prior_tracking = dict(profile.tracking_baseline or {})
            prior_tracking["cdd_access"] = access_notes
            profile.tracking_baseline = prior_tracking

        profile.commercial_scope = commercial
        profile.marketing_context = marketing

    elif agent_key == "tracking_access_agent":
        rows = (
            await db.execute(
                select(TrackingAudit)
                .where(TrackingAudit.client_id == client_id)
                .order_by(TrackingAudit.detected_at.desc())
            )
        ).scalars().all()
        # Latest per element
        latest: dict[str, TrackingAudit] = {}
        for r in rows:
            if r.element not in latest:
                latest[r.element] = r
        prior = dict(profile.tracking_baseline or {})
        profile.tracking_baseline = {
            "system_access": prior.get("t1_platforms") or [],
            "elements": [
                {
                    "element": r.element,
                    "check_result": r.check_result,
                    "status": "approved",
                    "detail": r.detail,
                }
                for r in latest.values()
            ],
            "historical_baseline": prior.get("historical_draft") or {},
            "known_changes": prior.get("known_changes") or {},
            "last_verified": datetime.now(timezone.utc).isoformat(),
        }

    elif agent_key == "website_situation_agent":
        prior = dict(profile.website_situation_summary or {})
        rows = (
            await db.execute(select(WebsiteAudit).where(WebsiteAudit.client_id == client_id))
        ).scalars().all()
        summary: dict = {
            r.audit_type: {"summary": r.summary, "severity": r.severity, "status": r.status}
            for r in rows
        }
        # Keep the Phase 3 site sitemap as the process-wide URL inventory.
        carry_keys = (
            "site_sitemap",
            "sitemap_url_count",
            "sample_urls",
            "pages_found",
            "indexable",
            "page_hierarchy",
            "page_clusters",
            "cdd_pages_count",
            "cdd_coverage_gaps",
            "audit_focus_note",
            "business_weighted_score",
            "seo_pages_analyzed",
            "seo_overall_score",
            "seo_score_band",
            "note",
        )
        for key in carry_keys:
            if prior.get(key) is not None and key not in summary:
                summary[key] = prior[key]
        crawl_blob = summary.get("crawl_technical")
        if isinstance(crawl_blob, dict):
            crawl_sum = crawl_blob.get("summary") if isinstance(crawl_blob.get("summary"), dict) else {}
            if isinstance(crawl_sum, dict):
                if crawl_sum.get("site_sitemap") and not summary.get("site_sitemap"):
                    summary["site_sitemap"] = crawl_sum["site_sitemap"]
                if crawl_sum.get("sitemap_url_count") is not None and summary.get("sitemap_url_count") is None:
                    summary["sitemap_url_count"] = crawl_sum["sitemap_url_count"]
                if crawl_sum.get("discovered_urls") and not summary.get("sample_urls"):
                    summary["sample_urls"] = list(crawl_sum.get("discovered_urls") or [])[:40]
                if crawl_sum.get("pages_found") is not None and summary.get("pages_found") is None:
                    summary["pages_found"] = crawl_sum.get("pages_found")
        profile.website_situation_summary = summary

    elif agent_key == "competitor_market_agent":
        comps = (
            await db.execute(
                select(CompetitorProfile).where(CompetitorProfile.client_id == client_id)
            )
        ).scalars().all()
        for c in comps:
            c.confirmed = True
        from app.services.cache import cache_get, competitor_cache_key

        cached = await cache_get(competitor_cache_key(str(client_id))) or {}
        from app.services.memory_packs import slim_competitor_memory

        profile.competitive_landscape_summary = slim_competitor_memory(
            {
                "analysis_mode": cached.get("analysis_mode", "tiered_16_parameter"),
                "tier_overview": cached.get("tier_overview", []),
                "recommendations": cached.get("recommendations", {}),
                "client_baseline_maturity": (cached.get("client_baseline") or {}).get(
                    "maturity_score"
                ),
                "competitors": [
                    {
                        "name": c.name,
                        "url": c.url,
                        "cluster": c.positioning_cluster,
                        "source": c.source,
                    }
                    for c in comps
                ],
            }
        )

    elif agent_key == "search_demand":
        from app.services.memory_packs import slim_search_demand_memory

        current = dict(profile.search_demand_summary or {})
        if edits:
            current.update(edits)
        # Keep full detail on the chat card; store essentials in shared memory
        profile.search_demand_summary = slim_search_demand_memory(current)

    elif agent_key == "content_strategy":
        from app.services.memory_packs import slim_seo_strategy_memory

        current = dict(profile.seo_strategy_summary or {})
        if edits:
            current.update(edits)
        profile.seo_strategy_summary = slim_seo_strategy_memory(current)

    elif agent_key == "site_architecture":
        from app.services.memory_packs import slim_site_architecture_memory

        current = dict(profile.site_architecture_summary or {})
        if edits:
            current.update(edits)
        profile.site_architecture_summary = slim_site_architecture_memory(current)

    elif agent_key == "technical_seo":
        from app.services.memory_packs import slim_technical_seo_memory

        current = dict(profile.technical_seo_summary or {})
        if edits:
            current.update(edits)
        profile.technical_seo_summary = slim_technical_seo_memory(current)

    elif agent_key == "content_audit":
        from app.services.memory_packs import slim_content_audit_memory

        current = dict(profile.content_audit_summary or {})
        if edits:
            current.update(edits)
        profile.content_audit_summary = slim_content_audit_memory(current)

    elif agent_key == "content_planning":
        from app.services.memory_packs import slim_content_planning_memory

        current = dict(profile.content_planning_summary or {})
        if edits:
            current.update(edits)
        profile.content_planning_summary = slim_content_planning_memory(current)

    elif agent_key == "content_production":
        from app.services.memory_packs import slim_content_production_memory

        current = dict(profile.content_production_summary or {})
        if edits:
            current.update(edits)
        profile.content_production_summary = slim_content_production_memory(current)

    elif agent_key == "on_page_seo":
        from app.services.memory_packs import slim_on_page_seo_memory

        current = dict(profile.on_page_seo_summary or {})
        if edits:
            current.update(edits)
        profile.on_page_seo_summary = slim_on_page_seo_memory(current)

    elif agent_key == "publishing":
        from app.services.memory_packs import slim_publishing_memory

        current = dict(profile.publishing_summary or {})
        if edits:
            current.update(edits)
        profile.publishing_summary = slim_publishing_memory(current)

    await db.flush()
