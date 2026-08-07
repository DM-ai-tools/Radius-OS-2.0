from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user, require_permission
from app.models import ClientDigitalProfile, User
from app.schemas.session import ReadinessGateRequest
from app.services.audit import log_event
from app.services.readiness import (
    compute_competitor_score,
    compute_discovery_score,
    compute_tracking_score,
    compute_website_score,
    readiness_payload,
    recompute_readiness,
)

router = APIRouter(tags=["readiness"])


@router.get("/clients/{client_id}/readiness")
async def get_readiness(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await recompute_readiness(db, client_id)
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Profile not found")
    d_s, d_m = await compute_discovery_score(db, client_id)
    t_s, t_m = await compute_tracking_score(db, client_id)
    w_s, w_m = await compute_website_score(db, client_id)
    c_s, c_m = await compute_competitor_score(db, client_id)
    return readiness_payload(
        profile,
        {
            "discovery": {"score": float(d_s), "missing": d_m},
            "tracking": {"score": float(t_s), "missing": t_m},
            "website": {"score": float(w_s), "missing": w_m},
            "competitor": {"score": float(c_s), "missing": c_m},
        },
    )


@router.post("/clients/{client_id}/readiness/gate")
async def readiness_gate(
    client_id: UUID,
    body: ReadinessGateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user = (
        await db.execute(select(User).options(selectinload(User.role)).where(User.id == user.id))
    ).scalar_one()
    await require_permission(user, db, "readiness_gate", need_approve=True)
    settings = get_settings()
    profile = await recompute_readiness(db, client_id)
    await db.refresh(profile)

    if body.approve:
        statuses = [
            profile.discovery_status,
            profile.tracking_status,
            profile.website_status,
            profile.competitor_status,
        ]
        if any(s != "complete" for s in statuses):
            raise HTTPException(400, "All four phases must be complete before the gate")
        if float(profile.overall_readiness_score or 0) < settings.readiness_threshold:
            raise HTTPException(
                400,
                f"Readiness score {profile.overall_readiness_score} below threshold "
                f"{settings.readiness_threshold}",
            )
        profile.ready_for_phase5 = True
        await log_event(
            db,
            client_id=client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="readiness_gate_passed",
            event_detail={"score": float(profile.overall_readiness_score or 0), "note": body.note},
        )
    else:
        profile.ready_for_phase5 = False
        await log_event(
            db,
            client_id=client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="readiness_gate_rejected",
            event_detail={"note": body.note},
        )
    await db.flush()
    return {
        "ready_for_phase5": profile.ready_for_phase5,
        "overall_readiness_score": float(profile.overall_readiness_score or 0),
    }
