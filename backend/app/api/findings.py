from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_permission
from app.models import (
    ChatSession,
    CompetitorProfile,
    DiscoveryResponse,
    FindingsLedger,
    User,
)
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
    session_id: UUID | None = None


@router.get("/clients/{client_id}/phases/{agent_key}/validation")
async def get_phase_validation_history(
    client_id: UUID,
    agent_key: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Traceable validation history for a phase — why accepted/rejected, which checks ran."""
    from app.models.governance import PhaseValidation
    from app.services.phase_validation import (
        list_applicable_parameters,
        phase_criteria_as_dict,
    )

    rows = (
        await db.execute(
            select(PhaseValidation)
            .where(
                PhaseValidation.client_id == client_id,
                PhaseValidation.agent_key == agent_key,
            )
            .order_by(PhaseValidation.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    return {
        "agent_key": agent_key,
        "applicable_parameters": list_applicable_parameters(agent_key),
        "criteria": phase_criteria_as_dict(agent_key),
        "history": [
            {
                "id": str(r.id),
                "iteration": r.iteration,
                "decision": r.decision,
                "output_fingerprint": r.output_fingerprint,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "result": r.result,
            }
            for r in rows
        ],
    }


@router.get("/clients/{client_id}/findings")
async def list_findings(
    client_id: UUID,
    status: str | None = "pending",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(FindingsLedger).where(FindingsLedger.client_id == client_id)
    if status:
        q = q.where(FindingsLedger.status == status)
    result = await db.execute(
        q.order_by(FindingsLedger.created_at.desc()).offset(offset).limit(limit)
    )
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
    # Questionnaire submission is a separate REST action from the chat stream.
    # Persist its D3/D4 transition events so a refresh or re-entry does not
    # reload only the old D1/D2 cards and make Discovery appear stuck.
    session = None
    if body.session_id:
        session = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.id == body.session_id,
                    ChatSession.client_id == client_id,
                    ChatSession.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
    if session is None:
        session = (
            await db.execute(
                select(ChatSession)
                .where(
                    ChatSession.client_id == client_id,
                    ChatSession.user_id == user.id,
                )
                .order_by(ChatSession.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if session is not None:
        from app.orchestration.pipeline import _persist_agent_events

        await _persist_agent_events(db, session, client_id, events)
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
    # Persist confirmation-gate events the same way questionnaire does, so a refresh
    # does not drop the Approve card and leave Tracking stuck on the draft report.
    session = None
    if body.session_id:
        session = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.id == body.session_id,
                    ChatSession.client_id == client_id,
                    ChatSession.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
    if session is None:
        session = (
            await db.execute(
                select(ChatSession)
                .where(
                    ChatSession.client_id == client_id,
                    ChatSession.user_id == user.id,
                )
                .order_by(ChatSession.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if session is not None:
        from app.orchestration.pipeline import _persist_agent_events

        await _persist_agent_events(db, session, client_id, events)
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
