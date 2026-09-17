"""Phase 12 — Publishing & Indexation agent (preview / draft / live publish)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill
from app.integrations.wordpress import load_connection as load_wordpress_connection
from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.publishing import (
    MODE_DRAFT,
    MODE_PREVIEW,
    MODE_PUBLISH,
    run_publishing_plan,
)
from app.services.role_skills import required_role_for

# Anything that is not an unambiguous write request stays a dry run. A CMS write must be
# asked for explicitly — never inferred from a vague "run phase 12".
_DRAFT_RE = re.compile(r"\b(draft|save to wordpress|push draft|create draft)\b", re.IGNORECASE)
_PUBLISH_RE = re.compile(
    r"\b(publish (it |them |now|live)|go live|make (it|them) live|publish to wordpress)\b",
    re.IGNORECASE,
)


def resolve_mode(message: str) -> str:
    text = message or ""
    if _PUBLISH_RE.search(text):
        return MODE_PUBLISH
    if _DRAFT_RE.search(text):
        return MODE_DRAFT
    return MODE_PREVIEW


def _result_message(summary: dict) -> str:
    """Say plainly what did or did not reach the CMS — never imply a write that failed."""
    queue = summary.get("publish_queue") or []
    effective = str(summary.get("mode_effective") or MODE_PREVIEW)
    design = summary.get("design") or {}
    bits: list[str] = []

    if effective == MODE_PREVIEW:
        bits.append(
            f"Dry-run preview ready for {len(queue)} URL(s) — no CMS write was attempted."
        )
    else:
        wrote = [q for q in queue if q.get("status") not in ("failed", "preview_only")]
        failed = [q for q in queue if q.get("status") == "failed"]
        bits.append(f"WordPress write: {len(wrote)} succeeded, {len(failed)} failed.")
        downgraded = [q for q in queue if q.get("downgraded")]
        if downgraded:
            bits.append(
                f"{len(downgraded)} requested live publish but were saved as draft "
                "(WORDPRESS_ALLOW_LIVE_PUBLISH is off)."
            )
        unverified = [v for v in (summary.get("verification") or []) if not v.get("verified")]
        if unverified:
            bits.append(f"{len(unverified)} could not be verified — human review required.")

    if not design.get("brand_available"):
        bits.append(
            f"Brand assets unavailable ({design.get('brand_error') or 'not fetched'}) — "
            "preview uses neutral styling."
        )
    for blocker in summary.get("capability_blockers") or []:
        bits.append(f"Blocker — {blocker.get('blocker')}: {blocker.get('detail')}")

    bits.append("Strategist, On-Page Lead, or HoD signs off; QA can triage the queue.")
    return " ".join(bits)


async def run_publishing(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("publishing")
    _ = user_id
    mode = resolve_mode(message)

    events: list[dict] = []
    profile = await get_profile(db, client.id)

    events.extend(
        consume_events(
            "publishing",
            pack_notes=[f"on_page={profile.on_page_seo_status}"],
        )
    )

    profile.publishing_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Fetching brand assets and reference layout, rendering the exact CMS "
                "payload"
                + (
                    " — dry run, no CMS write."
                    if mode == MODE_PREVIEW
                    else f" — then writing to WordPress as '{mode}'."
                )
            ),
        }
    )

    wp_connection = await load_wordpress_connection(db, client.id)

    summary = await run_publishing_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        on_page_seo_status=profile.on_page_seo_status,
        on_page_seo=dict(profile.on_page_seo_summary or {}),
        site_architecture=dict(profile.site_architecture_summary or {}),
        content_production=dict(profile.content_production_summary or {}),
        mode=mode,
        wordpress_connection=wp_connection,
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()

    if summary.get("blocked"):
        profile.publishing_status = "not_started"
        events.extend(
            blocked_events(
                "publishing",
                str(summary.get("reason") or "Blocked — complete On-Page SEO first."),
                route_to="on_page_seo",
            )
        )
        return events

    profile.publishing_summary = summary
    profile.publishing_status = "pending_signoff"

    await supersede_pending_findings(
        db, client_id=client.id, agent_key="publishing"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="publishing",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="medium",
            status="pending",
            created_by=user_id,
        )
    )

    job = AgentJob(
        session_id=session_id,
        agent_key="publishing",
        job_type="publishing",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "publishing"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "publishing",
            "queue": len(summary.get("publish_queue") or []),
        },
    )
    await db.flush()

    card = {
        "card_type": "publishing_report",
        "title": f"Publishing & Indexation: {client.display_name}",
        "agent_key": "publishing",
        "actions": ["approve"],
        "required_role": required_role_for("publishing"),
        **summary,
    }

    events.append(
        {
            "type": "agent_message",
            "agent_key": "publishing",
            "content": _result_message(summary),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {"publishing_status": profile.publishing_status},
        }
    )
    events.extend(
        handoff_events(
            "publishing",
            result_line=f"{len(summary.get('publish_queue') or [])} URLs queued for WordPress using the internal linking plan.",
        )
    )
    return events
