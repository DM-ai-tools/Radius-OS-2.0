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
) -> dict:
    """Approve/reject all pending findings for an agent on a client (card-level action)."""
    await require_permission(user, db, agent_key, need_approve=True)
    result = await db.execute(
        select(FindingsLedger).where(
            FindingsLedger.client_id == client_id,
            FindingsLedger.agent_key == agent_key,
            FindingsLedger.status == "pending",
        )
    )
    ledgers = list(result.scalars().all())
    if not ledgers and action != "approve":
        raise HTTPException(404, "No pending findings")

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
    return {
        "resolved": len(ledgers),
        "agent_key": agent_key,
        "overall_readiness_score": float(profile.overall_readiness_score or 0),
        "phase_statuses": {
            "discovery": profile.discovery_status,
            "tracking": profile.tracking_status,
            "website": profile.website_status,
            "competitor": profile.competitor_status,
        },
    }


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
        rows = (
            await db.execute(select(WebsiteAudit).where(WebsiteAudit.client_id == client_id))
        ).scalars().all()
        profile.website_situation_summary = {
            r.audit_type: {"summary": r.summary, "severity": r.severity, "status": r.status}
            for r in rows
        }

    elif agent_key == "competitor_market_agent":
        comps = (
            await db.execute(
                select(CompetitorProfile).where(CompetitorProfile.client_id == client_id)
            )
        ).scalars().all()
        for c in comps:
            c.confirmed = True
        from app.services.cache import cache_get, competitor_cache_key

        cached = cache_get(competitor_cache_key(str(client_id))) or {}
        profile.competitive_landscape_summary = {
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
    await db.flush()
