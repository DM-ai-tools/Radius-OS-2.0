"""Phase 11 — On-Page SEO agent."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill
from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.on_page_seo import run_on_page_seo_plan
from app.services.role_skills import required_role_for


async def run_on_page_seo(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("on_page_seo")
    load_skill("schema_markup")
    load_skill("internal_linking")
    _ = user_id
    _ = message

    events: list[dict] = []
    profile = await get_profile(db, client.id)

    events.extend(
        consume_events(
            "on_page_seo",
            pack_notes=[f"production={profile.content_production_status}"],
        )
    )

    profile.on_page_seo_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Building on-page package for the selected draft — "
                "titles, H1/meta, schema JSON-LD, and internal linking (hub↔spoke)…"
            ),
        }
    )

    summary = await run_on_page_seo_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        content_production_status=profile.content_production_status,
        content_production=dict(profile.content_production_summary or {}),
        content_planning=dict(profile.content_planning_summary or {}),
        site_architecture=dict(profile.site_architecture_summary or {}),
        competitive_landscape=dict(profile.competitive_landscape_summary or {}),
        trademark_denylist=list(
            (profile.marketing_context or {}).get("trademark_denylist") or []
        ),
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()

    if summary.get("blocked"):
        profile.on_page_seo_status = "not_started"
        events.extend(
            blocked_events(
                "on_page_seo",
                str(summary.get("reason") or "Blocked — complete Content Production first."),
                route_to="content_production",
            )
        )
        return events

    profile.on_page_seo_summary = summary
    profile.on_page_seo_status = "pending_signoff"

    await supersede_pending_findings(
        db, client_id=client.id, agent_key="on_page_seo"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="on_page_seo",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="medium",
            status="pending",
            created_by=user_id,
        )
    )

    job = AgentJob(
        session_id=session_id,
        agent_key="on_page_seo",
        job_type="on_page_seo",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "on_page_seo"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "on_page_seo",
            "pages": len(summary.get("pages") or []),
            "links": len(summary.get("internal_links") or []),
        },
    )
    await db.flush()

    card = {
        "card_type": "on_page_seo_report",
        "title": f"On-Page SEO: {client.display_name}",
        "agent_key": "on_page_seo",
        "actions": ["approve"],
        "required_role": required_role_for("on_page_seo"),
        **summary,
    }

    events.append(
        {
            "type": "agent_message",
            "agent_key": "on_page_seo",
            "content": (
                f"On-page package ready for “{summary.get('target_keyword') or 'the selected topic'}” "
                f"({len(summary.get('pages') or [])} page, "
                f"{len(summary.get('internal_links') or [])} internal link suggestions). "
                "On-Page SEO Specialist should approve."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {"on_page_seo_status": profile.on_page_seo_status},
        }
    )
    events.extend(
        handoff_events(
            "on_page_seo",
            result_line=(
                f"On-page packaged for {summary.get('target_keyword') or 'selected topic'}."
            ),
        )
    )
    return events
