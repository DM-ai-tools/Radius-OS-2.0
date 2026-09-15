from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import ChatMessage, ChatSession, Client, User
from app.schemas.session import MessageOut, SessionCreate, SessionOut
from app.services.audit import log_event

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = (
        await db.execute(select(Client).where(Client.id == body.client_id))
    ).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    session = ChatSession(
        client_id=body.client_id,
        user_id=user.id,
        is_onboarding=client.is_onboarding,
    )
    db.add(session)
    await db.flush()
    await log_event(
        db,
        client_id=body.client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="session_started",
        event_detail={"session_id": str(session.id)},
    )
    return session


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    client_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(ChatSession).where(ChatSession.user_id == user.id)
    if client_id:
        q = q.where(ChatSession.client_id == client_id)
    q = q.order_by(ChatSession.started_at.desc()).offset(offset).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: UUID,
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = (
        await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id, ChatSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())
