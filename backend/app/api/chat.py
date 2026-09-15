"""WebSocket chat + REST + SSE streaming for message turns."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import AsyncSessionLocal, get_db
from app.deps import get_current_user
from app.models import ChatSession, User
from app.orchestration.pipeline import process_chat_turn
from app.security import decode_access_token, parse_uuid

router = APIRouter(tags=["chat"])


class ChatTurnRequest(BaseModel):
    content: str


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


@router.post("/sessions/{session_id}/messages")
async def post_message(
    session_id: UUID,
    body: ChatTurnRequest,
    db=Depends(get_db),
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
    events = await process_chat_turn(db, session=session, user=user, content=body.content)
    return {"events": events}


@router.post("/sessions/{session_id}/messages/stream")
async def post_message_stream(
    session_id: UUID,
    body: ChatTurnRequest,
    user: User = Depends(get_current_user),
):
    """SSE stream: thinking immediately, then events as the turn finishes."""
    async with AsyncSessionLocal() as db:
        session = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.id == session_id, ChatSession.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if not session:
            raise HTTPException(404, "Session not found")
        session_id_val = session.id
        active_hint = session.active_agent_key

    user_id = user.id

    async def event_gen():
        yield _sse({"type": "thinking", "agent_key": active_hint})
        yield _sse(
            {
                "type": "job_progress",
                "payload": {
                    "status": "running",
                    "message": "Working on your request…",
                },
            }
        )
        await asyncio.sleep(0.05)

        async def _run_turn() -> tuple[list[dict], str | None]:
            async with AsyncSessionLocal() as stream_db:
                try:
                    sess = (
                        await stream_db.execute(
                            select(ChatSession).where(ChatSession.id == session_id_val)
                        )
                    ).scalar_one_or_none()
                    usr = (
                        await stream_db.execute(
                            select(User).options(selectinload(User.role)).where(User.id == user_id)
                        )
                    ).scalar_one_or_none()
                    if not sess or not usr:
                        raise RuntimeError("Session/user not found")
                    events = await process_chat_turn(
                        stream_db, session=sess, user=usr, content=body.content
                    )
                    await stream_db.commit()
                    return events, sess.active_agent_key
                except Exception:
                    await stream_db.rollback()
                    raise

        turn_task = asyncio.create_task(_run_turn())
        elapsed = 0
        try:
            while True:
                done, _ = await asyncio.wait({turn_task}, timeout=15)
                if turn_task in done:
                    break
                elapsed += 15
                if elapsed <= 60:
                    msg = "Still working… this step talks to keyword and crawl APIs."
                elif elapsed <= 180:
                    msg = (
                        f"Still analyzing ({elapsed}s)… keyword research and "
                        "site data can take a couple of minutes."
                    )
                else:
                    msg = (
                        f"Almost there ({elapsed}s)… finishing scoring and "
                        "the report card."
                    )
                yield _sse(
                    {
                        "type": "job_progress",
                        "payload": {"status": "running", "message": msg},
                    }
                )

            events, active_agent = turn_task.result()
        except Exception as exc:  # noqa: BLE001
            # Prefer the original DB failure over the follow-on PendingRollbackError.
            root = exc
            seen: set[int] = set()
            while root is not None and id(root) not in seen:
                seen.add(id(root))
                if "rolled back" not in str(root).lower():
                    break
                root = getattr(root, "__cause__", None) or getattr(root, "orig", None)
            msg = str(root) or str(exc)
            yield _sse({"type": "error", "content": msg[:400]})
            yield _sse({"type": "done"})
            return

        if active_agent:
            yield _sse({"type": "active_agent", "agent_key": active_agent})

        for ev in events:
            yield _sse(ev)
            await asyncio.sleep(0.02)

        yield _sse({"type": "done"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: UUID):
    await websocket.accept()
    settings = get_settings()
    token = websocket.query_params.get("token")
    user_id = None

    if settings.auth_disabled:
        pass
    elif not token:
        await websocket.send_json({"type": "error", "content": "Missing token"})
        await websocket.close()
        return
    else:
        try:
            payload = decode_access_token(token)
            user_id = parse_uuid(payload["sub"])
        except ValueError:
            await websocket.send_json({"type": "error", "content": "Invalid token"})
            await websocket.close()
            return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"content": raw}
            content = data.get("content") or data.get("message") or ""
            if not content.strip():
                continue

            async with AsyncSessionLocal() as db:
                if settings.auth_disabled:
                    user = (
                        await db.execute(
                            select(User)
                            .options(selectinload(User.role))
                            .where(User.email == "csm@trafficradius.com")
                        )
                    ).scalar_one_or_none()
                else:
                    user = (
                        await db.execute(
                            select(User)
                            .options(selectinload(User.role))
                            .where(User.id == user_id)
                        )
                    ).scalar_one_or_none()
                session = None
                if user:
                    session = (
                        await db.execute(
                            select(ChatSession).where(
                                ChatSession.id == session_id,
                                ChatSession.user_id == user.id,
                            )
                        )
                    ).scalar_one_or_none()
                if not user or not session:
                    await websocket.send_json(
                        {"type": "error", "content": "Session/user not found"}
                    )
                    continue

                await websocket.send_json(
                    {"type": "thinking", "agent_key": session.active_agent_key}
                )
                try:
                    events = await process_chat_turn(
                        db, session=session, user=user, content=content
                    )
                except HTTPException as exc:
                    await db.rollback()
                    await websocket.send_json(
                        {
                            "type": "error",
                            "content": str(exc.detail),
                            "status": exc.status_code,
                        }
                    )
                    continue
                await db.commit()
                for ev in events:
                    await websocket.send_json(ev)
                await websocket.send_json(
                    {
                        "type": "active_agent",
                        "agent_key": session.active_agent_key,
                    }
                )
    except WebSocketDisconnect:
        return
