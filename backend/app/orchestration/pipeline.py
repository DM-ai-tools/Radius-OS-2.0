"""Orchestration steps scoped to Phases 1–12."""

from __future__ import annotations

import asyncio
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AGENT_RUNNERS
from app.config import get_settings
from app.deps import agent_feature_enabled, require_permission
from app.integrations.llm import route_agent
from app.models import ChatMessage, ChatSession, Client, ClientDigitalProfile, User
from app.services.audit import log_event
from app.services.json_safe import json_safe
from app.services.role_skills import required_role_for

# Competitor scoring fans out several LLM calls; keep headroom above 5 minutes.
AGENT_TIMEOUT_SECONDS = 480

_STATUS_AGENT_PAIRS = (
    ("discovery_status", "discovery_agent"),
    ("tracking_status", "tracking_access_agent"),
    ("website_status", "website_situation_agent"),
    ("competitor_status", "competitor_market_agent"),
    ("search_demand_status", "search_demand"),
    ("site_architecture_status", "site_architecture"),
    ("seo_strategy_status", "content_strategy"),
    ("technical_seo_status", "technical_seo"),
    ("content_audit_status", "content_audit"),
    ("content_planning_status", "content_planning"),
    ("content_production_status", "content_production"),
    ("on_page_seo_status", "on_page_seo"),
    ("publishing_status", "publishing"),
)


def _chat_review_action(content: str) -> str | None:
    """Recognize explicit review commands without treating questions as actions."""
    text = " ".join(content.lower().strip().split())
    if not text or "?" in text:
        return None
    if re.match(r"^(approve|accept|sign[\s-]?off)\b", text):
        return "approve"
    if re.match(r"^(reject|send back|request (?:a )?revision|request changes)\b", text):
        return "reject"
    return None


async def _chat_review_agent(
    content: str,
    *,
    statuses: dict[str, str],
    active_agent_key: str | None,
) -> str:
    """Resolve an explicit phase hint, then active/pending phase as fallback."""
    target_hint = re.sub(
        r"^(approve|accept|sign[\s-]?off|reject|send back|request (?:a )?revision|request changes)\b",
        "",
        content.lower().strip(),
    ).strip(" .:!-")
    if target_hint and target_hint not in {"this", "it", "this phase", "the phase"}:
        return await route_agent(content, statuses)
    valid_agents = {agent for _, agent in _STATUS_AGENT_PAIRS}
    if active_agent_key in valid_agents:
        return str(active_agent_key)
    for status_attr, agent_key in _STATUS_AGENT_PAIRS:
        if statuses.get(status_attr) == "pending_signoff":
            return agent_key
    return await route_agent(content, statuses)


def agent_timeout_seconds(agent_key: str) -> int:
    """Per-agent chat-turn budget (seconds)."""
    settings = get_settings()
    if agent_key == "technical_seo":
        return int(getattr(settings, "technical_seo_agent_timeout_seconds", 900) or 900)
    if agent_key == "search_demand":
        return int(getattr(settings, "search_demand_agent_timeout_seconds", 720) or 720)
    return int(getattr(settings, "agent_timeout_seconds", AGENT_TIMEOUT_SECONDS) or AGENT_TIMEOUT_SECONDS)


def _timeout_notice(agent_key: str) -> str:
    budget_min = agent_timeout_seconds(agent_key) // 60
    if agent_key == "search_demand":
        return (
            f"Keyword research was stopped after {budget_min} minutes. "
            "It reuses the Phase 3 sitemap instead of recrawling the live site — try again."
        )
    if agent_key == "website_situation_agent":
        return (
            f"Website audit was stopped after {budget_min} minutes. "
            "Try again, or narrow the request (crawl only / SEO audit only)."
        )
    return (
        f"{agent_key} is taking longer than expected and was stopped after "
        f"{budget_min} minutes. Try again."
    )


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
        "search_demand_status": profile.search_demand_status,
        "seo_strategy_status": profile.seo_strategy_status,
        "site_architecture_status": profile.site_architecture_status,
        "technical_seo_status": profile.technical_seo_status,
        "content_audit_status": profile.content_audit_status,
        "content_planning_status": profile.content_planning_status,
        "content_production_status": profile.content_production_status,
        "on_page_seo_status": profile.on_page_seo_status,
        "publishing_status": profile.publishing_status,
    }

    review_action = _chat_review_action(content)
    if review_action:
        agent_key = await _chat_review_agent(
            content,
            statuses=statuses,
            active_agent_key=session.active_agent_key,
        )
        await require_permission(user, db, agent_key, need_approve=True)
        from app.services.review import approve_phase_batch

        result = await approve_phase_batch(
            db,
            user=user,
            client_id=client.id,
            agent_key=agent_key,
            action=review_action,
            permission_checked=True,
        )
        events = [
            {
                "type": "system_notice",
                "content": (
                    f"{agent_key} approved and advanced."
                    if review_action == "approve"
                    else f"{agent_key} rejected and returned for revision."
                ),
            },
            {
                "type": "phase_status",
                "payload": result.get("phase_statuses", {}),
            },
        ]
        await _persist_agent_events(db, session, client.id, events)
        return events

    # Chat-box edits to an existing report (add/remove keyword, topic, competitor…)
    from app.services.chat_revisions import maybe_revise_from_chat

    revision_events = await maybe_revise_from_chat(
        db,
        client=client,
        profile=profile,
        user=user,
        active_agent_key=session.active_agent_key,
        message=content,
    )
    if revision_events is not None:
        await _persist_agent_events(db, session, client.id, revision_events)
        return revision_events

    from app.services.chat_qa import maybe_answer_from_memory

    qa_events = await maybe_answer_from_memory(
        client=client, profile=profile, message=content
    )
    if qa_events is not None:
        await _persist_agent_events(db, session, client.id, qa_events)
        return qa_events

    # Step 05 — router
    agent_key = await route_agent(content, statuses)

    if not agent_feature_enabled(agent_key):
        events = [
            {
                "type": "system_notice",
                "content": f"Agent {agent_key} is disabled by feature flag.",
            }
        ]
        await _persist_agent_events(db, session, client.id, events)
        return events

    await require_permission(user, db, agent_key, need_trigger=True)

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
            "title": "Readiness Score",
            **payload,
            "agent_key": "readiness_gate",
            "actions": [],
            "required_role": required_role_for("readiness_gate"),
        }
        events = [
            {
                "type": "agent_message",
                "agent_key": "readiness_gate",
                "content": (
                    f"Overall readiness is {payload['overall']:.0f}% "
                    f"(threshold {settings.readiness_threshold:.0f}% — informational). "
                    + (
                        " Foundations look solid — approve Phase 5 Search Demand before "
                        "Phase 6 Strategy; later phases stay gated in order."
                        if not payload["missing"]
                        else " Gaps: "
                        + ", ".join(payload["missing"][:8])
                        + ". Close foundations, then run Phase 5 → approve → Phase 6."
                    )
                ),
            },
            {"type": "structured_card", "payload": card},
        ]
        await _persist_agent_events(db, session, client.id, events)
        return events

    session.active_agent_key = agent_key
    runner = AGENT_RUNNERS[agent_key]
    runner_task: asyncio.Task | None = None
    try:
        from app.services.api_meter import api_meter_context
        from app.services.phase_validation import run_phase_with_validation

        async with api_meter_context(
            client_id=client.id,
            session_id=session.id,
            agent_key=agent_key,
        ):
            runner_task = asyncio.create_task(
                run_phase_with_validation(
                    db,
                    runner=runner,
                    client=client,
                    profile=profile,
                    session_id=session.id,
                    user_id=user.id,
                    agent_key=agent_key,
                    message=content,
                )
            )
            events = await asyncio.wait_for(
                runner_task,
                timeout=agent_timeout_seconds(agent_key),
            )
    except asyncio.TimeoutError:
        if runner_task is not None and not runner_task.done():
            runner_task.cancel()
            try:
                await runner_task
            except (asyncio.CancelledError, Exception):
                pass
        events = [
            {
                "type": "error",
                "content": _timeout_notice(agent_key),
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
        payload = ev.get("payload")
        if ev["type"] in ("structured_card", "checkpoint"):
            content = (payload or {}).get("title", ev["type"])
            role = "agent"
        structured_payload = payload or ({"type": ev["type"]} if ev["type"] != "agent_message" else None)
        if structured_payload is not None:
            structured_payload = json_safe(structured_payload)
        msg = ChatMessage(
            session_id=session.id,
            role=role if ev["type"] != "agent_message" else "agent",
            content=content or ev["type"],
            structured_payload=structured_payload,
            agent_key=ev.get("agent_key") or (payload or {}).get("agent_key"),
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
            flush=False,
        )
    await db.flush()
