"""Phase 10 — Content Briefing & Production agent."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, consume_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.content_brief import merge_brief_memory
from app.services.content_production import parse_topic_selection, run_content_production_plan
from app.services.role_skills import required_role_for
from app.agents.prompts import load_shared_reference, load_skill


async def run_content_production(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("content_production")
    load_skill("content_brief")
    load_skill("create_content")
    # Canonical Google helpful-content / scaled-abuse / Who-How-Why evidence for Phase 10
    _ = load_shared_reference("google-helpful-content.md", max_chars=4000)

    events: list[dict] = []
    profile = await get_profile(db, client.id)
    selection = parse_topic_selection(message)
    prior = dict(profile.content_production_summary or {})
    # Validation retries send a revision prompt, not "Write the full draft for…".
    # Keep the previously selected topic so we don't wipe a finished draft.
    if not selection and (
        prior.get("selected_keyword") or prior.get("selected_url")
    ):
        selection = {
            "keyword": str(prior.get("selected_keyword") or ""),
            "url": str(prior.get("selected_url") or ""),
        }
        selection = {k: v for k, v in selection.items() if v} or None

    planning = dict(profile.content_planning_summary or {})
    events.extend(
        consume_events(
            "content_production",
            pack_notes=[
                f"planning={profile.content_planning_status}",
                f"locked={planning.get('locked')}",
                f"selected={bool(selection)}",
            ],
        )
    )

    profile.content_production_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Writing the full draft for {selection.get('keyword') or selection.get('url')}…"
                if selection
                else "Briefing top-priority roadmap topics — pick one to draft."
            ),
        }
    )

    summary = await run_content_production_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        content_planning_status=profile.content_planning_status,
        content_planning=planning,
        seo_strategy=dict(profile.seo_strategy_summary or {}),
        site_architecture=dict(profile.site_architecture_summary or {}),
        content_audit=dict(profile.content_audit_summary or {}),
        website=dict(profile.website_situation_summary or {}),
        search_demand=dict(profile.search_demand_summary or {}),
        marketing=merge_brief_memory(
            marketing=dict(profile.marketing_context or {}),
            commercial=dict(profile.commercial_scope or {}),
            client_name=client.display_name,
        ),
        industry=client.industry,
        selected_url=(selection or {}).get("url") or None,
        selected_keyword=(selection or {}).get("keyword") or None,
        prior_briefs=list(prior.get("briefs") or []) if selection else None,
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()

    if summary.get("blocked"):
        profile.content_production_status = "not_started"
        events.extend(
            blocked_events(
                "content_production",
                str(summary.get("reason") or "Blocked — complete Content Planning first."),
                route_to="content_planning",
            )
        )
        return events

    awaiting = bool(summary.get("awaiting_topic_selection"))
    has_draft = bool(summary.get("drafts"))
    profile.content_production_summary = summary
    profile.content_production_status = "pending_signoff" if has_draft else "in_progress"

    if has_draft:
        await supersede_pending_findings(
            db, client_id=client.id, agent_key="content_production"
        )
        db.add(
            FindingsLedger(
                client_id=client.id,
                agent_key="content_production",
                source_table="client_digital_profiles",
                source_id=profile.id,
                confidence="medium",
                status="pending",
                created_by=user_id,
            )
        )

    job = AgentJob(
        session_id=session_id,
        agent_key="content_production",
        job_type="content_production",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "content_production"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "content_production",
            "briefs": summary.get("brief_count"),
            "drafts": summary.get("draft_count"),
            "awaiting_topic": awaiting,
        },
    )
    await db.flush()

    drafts = list(summary.get("drafts") or [])
    draft = drafts[0] if drafts and isinstance(drafts[0], dict) else {}
    card_payload = {
        **summary,
        "briefs": [
            {
                "keyword": b.get("keyword"),
                "url": b.get("url") or b.get("path"),
                "title": b.get("title"),
                "action": b.get("action"),
                "writer_ready": b.get("writer_ready"),
                "funnel": b.get("funnel"),
                "search_intent": b.get("search_intent"),
                "differentiation": str(b.get("differentiation") or "")[:180],
            }
            for b in (summary.get("briefs") or [])
            if isinstance(b, dict)
        ],
    }
    if has_draft:
        card_payload["draft_markdown"] = draft.get("markdown") or ""
        card_payload["draft_title"] = draft.get("title") or draft.get("keyword")
        card_payload["draft_images"] = draft.get("images") or []

    card = {
        "card_type": "content_production_report",
        "title": f"Content Production: {client.display_name}",
        "agent_key": "content_production",
        "actions": ["approve"] if has_draft else [],
        "required_role": required_role_for("content_production"),
        **card_payload,
    }

    if awaiting:
        labels = [
            str(c.get("keyword") or c.get("title"))
            for c in (summary.get("topic_choices") or [])[:6]
            if c
        ]
        msg = (
            f"{summary.get('writer_ready_count') or 0} priority topics are ready. "
            "Click one below — I will draft the full page (one topic per run)."
        )
        if labels:
            msg += " Top picks: " + "; ".join(labels) + "."
    elif has_draft:
        title = str(draft.get("title") or draft.get("keyword") or "the selected topic")
        msg = (
            f"Full draft ready for “{title}”. "
            "It’s laid out in the report card (not raw markdown) — review, then approve."
        )
    else:
        msg = (
            f"Briefed {summary.get('brief_count')} pages; "
            f"wrote {summary.get('draft_count') or 0} draft."
            + (
                f" Write held: {summary.get('write_refusal', {}).get('reason')}"
                if summary.get("write_refusal")
                else ""
            )
        )

    # Card first so the article is on screen; no On-Page handoff until they approve.
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {"type": "agent_message", "agent_key": "content_production", "content": msg}
    )
    events.append(
        {
            "type": "phase_status",
            "payload": {"content_production_status": profile.content_production_status},
        }
    )
    return events
