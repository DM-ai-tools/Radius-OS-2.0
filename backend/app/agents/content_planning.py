"""Phase 9 — Content Planning agent (merge + lock + bounce gaps)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, bounce_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.content_planning import run_content_planning_plan
from app.services.role_skills import required_role_for
from app.agents.prompts import load_skill


async def run_content_planning(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("content_planning")
    _ = user_id
    _ = message

    events: list[dict] = []
    profile = await get_profile(db, client.id)

    events.extend(
        consume_events(
            "content_planning",
            pack_notes=[
                f"strategy={profile.seo_strategy_status}",
                f"architecture={profile.site_architecture_status}",
                f"audit={profile.content_audit_status}",
            ],
        )
    )

    profile.content_planning_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": "Merging strategy + architecture + audit into a locked page roadmap…",
        }
    )

    summary = await run_content_planning_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        site_architecture_status=profile.site_architecture_status,
        site_architecture=dict(profile.site_architecture_summary or {}),
        seo_strategy=dict(profile.seo_strategy_summary or {}),
        content_audit=dict(profile.content_audit_summary or {}),
        website=dict(profile.website_situation_summary or {}),
        search_demand=dict(profile.search_demand_summary or {}),
        marketing=dict(profile.marketing_context or {}),
        industry=client.industry,
        seo_strategy_status=profile.seo_strategy_status,
        website_status=profile.website_status,
        content_audit_status=profile.content_audit_status,
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()

    if summary.get("blocked"):
        profile.content_planning_status = "not_started"
        events.extend(
            blocked_events(
                "content_planning",
                str(summary.get("reason") or "Blocked — complete upstream phases first."),
            )
        )
        return events

    profile.content_planning_summary = summary
    profile.content_planning_status = "pending_signoff"

    await supersede_pending_findings(
        db, client_id=client.id, agent_key="content_planning"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="content_planning",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="medium",
            status="pending",
            created_by=user_id,
        )
    )

    job = AgentJob(
        session_id=session_id,
        agent_key="content_planning",
        job_type="content_planning",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "content_planning"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "content_planning",
            "planned": summary.get("planned_count"),
            "locked": summary.get("locked"),
            "excluded": summary.get("excluded_count"),
        },
    )
    await db.flush()

    card = {
        "card_type": "content_planning_report",
        "title": f"Content Planning: {client.display_name}",
        "agent_key": "content_planning",
        "actions": ["approve"],
        "required_role": required_role_for("content_planning"),
        **summary,
    }

    events.append(
        {
            "type": "agent_message",
            "agent_key": "content_planning",
            "content": (
                f"{'Roadmap locked' if summary.get('locked') else 'Roadmap unlocked'} — "
                f"{summary.get('planned_count') or 0} merged pages "
                f"({summary.get('create_count')} create, {summary.get('refresh_count')} refresh, "
                f"{summary.get('retire_count') or 0} retire, {summary.get('no_action_count') or 0} keep). "
                f"{summary.get('excluded_count') or 0} excluded."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {"content_planning_status": profile.content_planning_status},
        }
    )

    excluded = [e for e in (summary.get("excluded") or []) if isinstance(e, dict)]
    if excluded or summary.get("locked") is False:
        events.extend(
            bounce_events(
                "content_planning",
                gaps=excluded,
                lock_reason=None if summary.get("locked") else str(summary.get("lock_reason") or ""),
            )
        )

    if summary.get("locked"):
        briefable = (summary.get("summary") or {}).get("briefable") or (
            (summary.get("create_count") or 0) + (summary.get("refresh_count") or 0)
        )
        events.extend(
            handoff_events(
                "content_planning",
                result_line=(
                    f"Locked {summary.get('planned_count') or 0} pages; "
                    f"{briefable} create/refresh ready for briefs."
                ),
            )
        )
    else:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    "Do not run Content Production yet — roadmap is unlocked. "
                    "Fix bounced upstream packs, then re-run Content Planning."
                ),
            }
        )
    return events
