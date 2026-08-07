from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_permission
from app.models import CompetitorProfile, DiscoveryResponse, FindingsLedger, User
from app.schemas.session import (
    ManualCompetitor,
    QuestionnaireSubmit,
    ReviewRequest,
)
from app.services.audit import log_event
from app.services.review import approve_phase_batch, review_finding

router = APIRouter(tags=["findings"])


class KnownChangesSubmit(BaseModel):
    fields: dict


@router.get("/clients/{client_id}/findings")
async def list_findings(
    client_id: UUID,
    status: str | None = "pending",
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(FindingsLedger).where(FindingsLedger.client_id == client_id)
    if status:
        q = q.where(FindingsLedger.status == status)
    result = await db.execute(q.order_by(FindingsLedger.created_at.desc()))
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "agent_key": r.agent_key,
            "source_table": r.source_table,
            "source_id": str(r.source_id),
            "confidence": r.confidence,
            "status": r.status,
        }
        for r in rows
    ]


@router.post("/findings/{ledger_id}/review")
async def review_one(
    ledger_id: UUID,
    body: ReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ledger = await review_finding(
        db,
        user=user,
        ledger_id=ledger_id,
        action=body.action,
        edits=body.edits,
        note=body.note,
    )
    return {"id": str(ledger.id), "status": ledger.status}


@router.post("/clients/{client_id}/phases/{agent_key}/review")
async def review_phase(
    client_id: UUID,
    agent_key: str,
    body: ReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await approve_phase_batch(
        db,
        user=user,
        client_id=client_id,
        agent_key=agent_key,
        action=body.action,
        edits=body.edits,
        note=body.note,
    )


@router.post("/clients/{client_id}/questionnaire")
async def submit_questionnaire(
    client_id: UUID,
    body: QuestionnaireSubmit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "discovery_agent", need_trigger=True)
    for key, value in body.fields.items():
        row = DiscoveryResponse(
            client_id=client_id,
            source="client_questionnaire",
            field_key=key,
            field_value={"value": value},
            confidence=None,
            discrepancy_flag=False,
            status="pending",
        )
        db.add(row)
    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="questionnaire_submitted",
        event_detail={"fields": list(body.fields.keys())},
    )
    await db.flush()
    from app.agents.discovery import build_discovery_signoff_events

    events = await build_discovery_signoff_events(db, client_id=client_id)
    return {"ok": True, "fields": list(body.fields.keys()), "events": events}


@router.post("/clients/{client_id}/tracking/known-changes")
async def submit_known_changes(
    client_id: UUID,
    body: KnownChangesSubmit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "tracking_access_agent", need_trigger=True)
    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="known_changes_submitted",
        event_detail={"fields": list(body.fields.keys())},
    )
    await db.flush()
    from app.agents.tracking import build_tracking_signoff_events

    events = await build_tracking_signoff_events(
        db, client_id=client_id, known_changes=body.fields
    )
    return {"ok": True, "fields": list(body.fields.keys()), "events": events}


@router.post("/clients/{client_id}/competitors/manual")
async def add_manual_competitor(
    client_id: UUID,
    body: ManualCompetitor,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "competitor_market_agent", need_trigger=True)
    cp = CompetitorProfile(
        client_id=client_id,
        name=body.name,
        url=body.url,
        source="manual",
        confirmed=False,
    )
    db.add(cp)
    await db.flush()
    return {"id": str(cp.id), "name": cp.name, "url": cp.url}
