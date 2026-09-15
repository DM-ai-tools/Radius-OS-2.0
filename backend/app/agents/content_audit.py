"""Phase 8 — Existing Content Audit agent."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill, skill_system_preamble
from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.content_audit import run_content_audit_plan
from app.services.role_skills import required_role_for


async def run_content_audit(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = skill_system_preamble("content_audit")
    skill_chars = len(load_skill("content_audit") or "")
    _ = user_id
    _ = message

    events: list[dict] = []
    profile = await get_profile(db, client.id)

    events.extend(
        consume_events(
            "content_audit",
            pack_notes=[
                f"website={profile.website_status}",
                f"demand={profile.search_demand_status}",
                f"technical={profile.technical_seo_status}",
            ],
        )
    )

    if profile.technical_seo_status != "complete":
        events.extend(
            blocked_events(
                "content_audit",
                "Approve Phase 7 Technical SEO first so the audit uses locked crawl/tech findings.",
                route_to="technical_seo",
            )
        )
        return events

    ia = dict(profile.site_architecture_summary or {})
    strategy = dict(profile.seo_strategy_summary or {})
    website = dict(profile.website_situation_summary or {})
    demand = dict(profile.search_demand_summary or {})
    has_inventory_signal = bool(
        ia.get("target_url_tree")
        or strategy.get("priority_queue")
        or strategy.get("content_gaps")
        or website.get("sample_urls")
        or website.get("site_sitemap")
        or demand.get("best_opportunities")
        or demand.get("keyword_dataset")
        or profile.search_demand_status == "complete"
        or profile.website_status == "complete"
    )
    if not has_inventory_signal:
        events.extend(
            blocked_events(
                "content_audit",
                "Needs website crawl samples and/or Phase 5 demand (or IA tree / strategy queue).",
                route_to="search_demand",
            )
        )
        return events

    profile.content_audit_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Auditing existing content — dispositions (keep/refresh/optimise/retitle/"
                "consolidate/noindex/delete-candidate), cannibalisation, review queues…"
            ),
        }
    )

    summary = await run_content_audit_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        website=website,
        site_architecture=ia,
        seo_strategy=strategy,
        search_demand=demand,
        technical_seo=dict(profile.technical_seo_summary or {}),
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    summary.setdefault("skills_loaded", {})["content_audit"] = skill_chars

    if summary.get("blocked"):
        profile.content_audit_status = "not_started"
        events.extend(
            blocked_events(
                "content_audit",
                str(summary.get("reason") or "Content audit blocked — empty inventory.")
                + " Run website situation analysis if the crawl inventory is empty.",
                route_to="website_situation_agent",
            )
        )
        return events

    profile.content_audit_summary = summary
    profile.content_audit_status = "pending_signoff"

    await supersede_pending_findings(
        db, client_id=client.id, agent_key="content_audit"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="content_audit",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="medium",
            status="pending",
            created_by=user_id,
        )
    )

    job = AgentJob(
        session_id=session_id,
        agent_key="content_audit",
        job_type="content_audit",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "content_audit"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "content_audit",
            "inventory": len(summary.get("inventory") or []),
            "counts": summary.get("summary_counts"),
        },
    )
    await db.flush()

    counts = summary.get("summary_counts") or {}
    card = {
        "card_type": "content_audit_report",
        "title": f"Content Audit: {client.display_name}",
        "agent_key": "content_audit",
        "actions": ["approve"],
        "required_role": required_role_for("content_audit"),
        **summary,
    }

    events.append(
        {
            "type": "agent_message",
            "agent_key": "content_audit",
            "content": (
                f"Content audit ready — {len(summary.get('inventory') or [])} pages "
                f"(keep {counts.get('keep', 0)}, refresh {counts.get('refresh', 0)}, "
                f"consolidate {counts.get('consolidate', 0)}, "
                f"review {counts.get('delete_candidate', 0) + counts.get('noindex', 0)}"
                f"{' · qualitative (no GSC)' if summary.get('qualitative') else ''}). "
                "Strategist should approve. DELETE_CANDIDATE is review-only."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {"content_audit_status": profile.content_audit_status},
        }
    )
    statuses = {
        "website": profile.website_status,
        "content_audit": "pending_signoff",
        "seo_strategy": profile.seo_strategy_status,
        "site_architecture": profile.site_architecture_status,
    }
    events.extend(
        handoff_events(
            "content_audit",
            phase_statuses=statuses,
            result_line=f"{len(summary.get('inventory') or [])} pages dispositioned.",
        )
    )
    return events
