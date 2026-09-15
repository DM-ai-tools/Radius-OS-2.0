"""Phase 6 — Site Architecture & Information Design."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm import extract_domain
from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.site_architecture import run_site_architecture_plan
from app.services.role_skills import required_role_for
from app.agents.prompts import load_skill


async def run_site_architecture(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("site_architecture")
    _ = user_id
    _ = message

    events: list[dict] = []
    profile = await get_profile(db, client.id)

    events.extend(
        consume_events(
            "site_architecture",
            pack_notes=[
                f"demand={profile.search_demand_status}",
                f"strategy={profile.seo_strategy_status}",
            ],
        )
    )

    demand = dict(profile.search_demand_summary or {})
    if profile.search_demand_status != "complete" and not (
        demand.get("cluster_report") or demand.get("clusters") or demand.get("topics")
    ):
        events.extend(
            blocked_events(
                "site_architecture",
                "Needs approved Phase 5 Search Demand (cluster map).",
                route_to="search_demand",
            )
        )
        return events

    if profile.seo_strategy_status != "complete":
        events.extend(
            blocked_events(
                "site_architecture",
                "Assigns URL/parent/depth to the approved Content Strategy queue.",
                route_to="content_strategy",
            )
        )
        return events

    profile.site_architecture_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Auditing click depth and drafting site architecture blueprint "
                "(hubs, URL tree, cluster ownership)…"
            ),
        }
    )

    domain = extract_domain(client.primary_url) or "example.com"
    summary = await run_site_architecture_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        domain=domain,
        demand=demand,
        seo_strategy=dict(profile.seo_strategy_summary or {}),
        commercial=dict(profile.commercial_scope or {}),
        website=dict(profile.website_situation_summary or {}),
        competitive=dict(profile.competitive_landscape_summary or {}),
        marketing=dict(profile.marketing_context or {}),
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()

    if summary.get("blocked"):
        profile.site_architecture_status = "not_started"
        events.extend(
            blocked_events(
                "site_architecture",
                str(summary.get("reason") or "Blocked — complete Phase 5 first."),
                route_to=str(summary.get("route_to") or "search_demand"),
            )
        )
        return events

    profile.site_architecture_summary = summary
    profile.site_architecture_status = "pending_signoff"

    # Keep Phase 5 topic_plan in sync when URL-map actions were stamped.
    topic_plan = summary.get("topic_plan")
    if isinstance(topic_plan, dict) and topic_plan.get("topic_ideas") is not None:
        demand_pack = dict(profile.search_demand_summary or {})
        demand_pack["topic_plan"] = topic_plan
        if summary.get("topics"):
            demand_pack["topics"] = summary["topics"]
        profile.search_demand_summary = demand_pack

    await supersede_pending_findings(
        db, client_id=client.id, agent_key="site_architecture"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="site_architecture",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="medium",
            status="pending",
        )
    )

    job = AgentJob(
        session_id=session_id,
        agent_key="site_architecture",
        job_type="site_architecture",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "site_architecture"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "site_architecture",
            "urls": (summary.get("current_state") or {}).get("urls_crawled"),
            "tree": len(summary.get("target_url_tree") or []),
        },
    )
    await db.flush()

    card = {
        "card_type": "site_architecture_blueprint",
        "title": f"Site Architecture Blueprint: {client.display_name}",
        "agent_key": "site_architecture",
        "actions": ["approve"],
        "required_role": required_role_for("site_architecture"),
        **summary,
    }

    cs = summary.get("current_state") or {}
    events.append(
        {
            "type": "agent_message",
            "agent_key": "site_architecture",
            "content": (
                f"IA blueprint ready: {len(summary.get('target_url_tree') or [])} planned URLs, "
                f"{len(summary.get('cluster_ownership') or summary.get('cluster_owners') or [])} cluster owners. "
                f"URL map from sitemap-classified clusters "
                f"({len((summary.get('url_map_report') or {}).get('final_url_map') or [])} rows). "
                f"Audit: {cs.get('urls_crawled')} URLs, max click depth {cs.get('max_click_depth')}, "
                f"{cs.get('depth_4_plus')} at 4+ clicks, {cs.get('orphans')} orphans. "
                "Strategist owns the blueprint; Technical SEO should approve redirects/robots impact."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {"site_architecture_status": profile.site_architecture_status},
        }
    )
    events.extend(
        handoff_events(
            "site_architecture",
            result_line=f"{len(summary.get('target_url_tree') or [])} URLs assigned from strategy queue.",
        )
    )
    return events
