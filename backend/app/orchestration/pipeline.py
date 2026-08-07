"""Orchestration steps 01–09 scoped to Phases 1–4."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import AGENT_RUNNERS
from app.config import get_settings
from app.deps import agent_feature_enabled
from app.integrations.llm import route_agent
from app.models import ChatMessage, ChatSession, Client, ClientDigitalProfile, RolePermission, User
from app.services.audit import log_event

# Competitor scoring fans out several LLM calls; keep headroom above 5 minutes.
AGENT_TIMEOUT_SECONDS = 480


async def process_chat_turn(
    db: AsyncSession,
    *,
    session: ChatSession,
    user: User,
    content: str,
) -> list[dict]:
    settings = get_settings()
    client = (
        await db.execute(select(Client).where(Client.id == session.client_id))
    ).scalar_one()
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()

    # Persist user message
    user_msg = ChatMessage(
        session_id=session.id,
        role="user",
        content=content,
    )
    db.add(user_msg)
    await log_event(
        db,
        client_id=client.id,
        actor_type="user",
        actor_id=user.id,
        event_type="chat_message",
        event_detail={"role": "user", "content": content[:500]},
    )

    statuses = {
        "discovery_status": profile.discovery_status,
        "tracking_status": profile.tracking_status,
        "website_status": profile.website_status,
        "competitor_status": profile.competitor_status,
    }

    # Step 05 — router
    agent_key = await route_agent(content, statuses)

    if agent_key == "readiness_gate":
        from app.services.readiness import (
            compute_competitor_score,
            compute_discovery_score,
            compute_tracking_score,
            compute_website_score,
            readiness_payload,
            recompute_readiness,
        )

        await recompute_readiness(db, client.id)
        await db.refresh(profile)
        d_s, d_m = await compute_discovery_score(db, client.id)
        t_s, t_m = await compute_tracking_score(db, client.id)
        w_s, w_m = await compute_website_score(db, client.id)
        c_s, c_m = await compute_competitor_score(db, client.id)
        payload = readiness_payload(
            profile,
            {
                "discovery": {"score": float(d_s), "missing": d_m},
                "tracking": {"score": float(t_s), "missing": t_m},
                "website": {"score": float(w_s), "missing": w_m},
                "competitor": {"score": float(c_s), "missing": c_m},
            },
        )
        card = {
            "card_type": "readiness_score",
            "title": "Readiness Gate",
            **payload,
            "agent_key": "readiness_gate",
            "actions": ["approve"] if payload["can_gate"] else [],
            "required_role": "seo_qa_lead",
        }
        events = [
            {
                "type": "agent_message",
                "agent_key": "readiness_gate",
                "content": (
                    f"Overall readiness is {payload['overall']:.0f}% "
                    f"(threshold {settings.readiness_threshold:.0f}%). "
                    + (
                        "All phases complete — QA Lead can approve the Phase 5 handoff."
                        if payload["can_gate"]
                        else "Still missing: " + ", ".join(payload["missing"][:8])
                    )
                ),
            },
            {"type": "structured_card", "payload": card},
        ]
        await _persist_agent_events(db, session, client.id, events)
        return events

    if not agent_feature_enabled(agent_key):
        events = [
            {
                "type": "system_notice",
                "content": f"Agent {agent_key} is disabled by feature flag.",
            }
        ]
        await _persist_agent_events(db, session, client.id, events)
        return events

    # Step 02 — role permission for trigger
    # Strategist can view all but only trigger competitor; allow CSM/Tech for their lanes
    user_loaded = (
        await db.execute(
            select(User).options(selectinload(User.role)).where(User.id == user.id)
        )
    ).scalar_one()
    perm = (
        await db.execute(
            select(RolePermission).where(
                RolePermission.role_id == user_loaded.role_id,
                RolePermission.agent_key == agent_key,
            )
        )
    ).scalar_one_or_none()

    role_name = user_loaded.role.name if user_loaded.role else ""
    can_run = settings.auth_disabled or bool(perm and perm.can_trigger)
    if not can_run:
        events = [
            {
                "type": "system_notice",
                "content": (
                    f"Your role ({role_name}) cannot trigger {agent_key}. "
                    "Switch to the accountable specialist or ask them to run this phase."
                ),
            }
        ]
        await _persist_agent_events(db, session, client.id, events)
        return events

    session.active_agent_key = agent_key
    runner = AGENT_RUNNERS[agent_key]
    try:
        events = await asyncio.wait_for(
            runner(
                db,
                client=client,
                session_id=session.id,
                user_id=user.id,
                message=content,
            ),
            timeout=AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        events = [
            {
                "type": "error",
                "content": (
                    f"{agent_key} is taking longer than expected and was stopped after "
                    f"{AGENT_TIMEOUT_SECONDS // 60} minutes. Try again, or narrow the "
                    "request (e.g. crawl only / SEO audit only)."
                ),
            }
        ]
    await _persist_agent_events(db, session, client.id, events)
    return events


async def _persist_agent_events(
    db: AsyncSession,
    session: ChatSession,
    client_id: UUID,
    events: list[dict],
) -> None:
    for ev in events:
        role = "system" if ev["type"] in ("system_notice", "job_progress", "phase_status", "error") else "agent"
        content = ev.get("content") or ""
        if ev["type"] in ("structured_card", "checkpoint"):
            content = ev.get("payload", {}).get("title", ev["type"])
            role = "agent"
        msg = ChatMessage(
            session_id=session.id,
            role=role if ev["type"] != "agent_message" else "agent",
            content=content or ev["type"],
            structured_payload=ev.get("payload") or ({"type": ev["type"]} if ev["type"] != "agent_message" else None),
            agent_key=ev.get("agent_key") or (ev.get("payload") or {}).get("agent_key"),
        )
        # Attach event type for UI reconstruction
        if msg.structured_payload is None and ev["type"] != "agent_message":
            msg.structured_payload = {"event_type": ev["type"]}
        elif isinstance(msg.structured_payload, dict) and "event_type" not in msg.structured_payload:
            if ev["type"] != "agent_message":
                msg.structured_payload = {**msg.structured_payload, "event_type": ev["type"]}
        db.add(msg)
        await log_event(
            db,
            client_id=client_id,
            actor_type="agent" if role == "agent" else "system",
            event_type="chat_message" if ev["type"] == "agent_message" else ev["type"],
            event_detail={"type": ev["type"]},
        )
    await db.flush()
