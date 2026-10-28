"""Phase 6 — SEO Strategy & Information Architecture (content_strategy skill)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill, skill_system_preamble
from app.integrations.llm import extract_domain
from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.role_skills import required_role_for


async def run_content_strategy(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = skill_system_preamble("content_strategy")
    skill_chars = len(load_skill("content_strategy") or "")
    _ = user_id
    _ = message

    events: list[dict] = []
    profile = await get_profile(db, client.id)

    events.extend(
        consume_events(
            "content_strategy",
            pack_notes=[
                f"demand={profile.search_demand_status}",
                f"url_map={profile.site_architecture_status}",
            ],
        )
    )

    from app.services.content_strategy import attach_page_plan, run_content_strategy_plan, url_map_entries

    demand = dict(profile.search_demand_summary or {})
    architecture = attach_page_plan(dict(profile.site_architecture_summary or {}))
    has_map = bool(url_map_entries(architecture) or architecture.get("target_url_tree"))
    demand_ready = profile.search_demand_status in ("complete", "pending_signoff") or bool(
        demand.get("cluster_report") or demand.get("clusters") or demand.get("topics")
    )
    if not demand_ready and not has_map:
        events.extend(
            blocked_events(
                "content_strategy",
                "Approve Phase 5 Search Demand first so strategy uses locked keyword/cluster memory.",
                route_to="search_demand",
            )
        )
        return events

    if profile.site_architecture_status != "complete" and not has_map:
        events.extend(
            blocked_events(
                "content_strategy",
                "URL mapping must decide optimize-versus-create before titles and the calendar.",
                route_to="site_architecture",
            )
        )
        return events

    profile.seo_strategy_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Writing titles for new pages and building the content calendar "
                "from the URL map (existing pages stay on the optimize path)…"
            ),
        }
    )

    commercial = dict(profile.commercial_scope or {})
    marketing = dict(profile.marketing_context or {})
    competitive = dict(profile.competitive_landscape_summary or {})
    website = dict(profile.website_situation_summary or {})
    domain = extract_domain(client.primary_url) or "example.com"
    content_audit = dict(profile.content_audit_summary or {})

    summary = await run_content_strategy_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        industry=client.industry,
        commercial=commercial,
        marketing=marketing,
        competitive=competitive,
        website=website,
        demand=demand,
        domain=domain,
        content_audit=content_audit,
        site_architecture=architecture,
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["skills_loaded"] = {"content_strategy": skill_chars}

    profile.seo_strategy_summary = summary

    # Phase 9 plans pages from target_url_tree, so the pages this phase's
    # priority queue introduces have to reach that tree or they are never
    # planned. Phase 6 used to reach forward for them, which only worked on a
    # re-run; contributing them here keeps the order declared and the result
    # the same every time.
    from app.services.site_architecture import merge_strategy_nodes_into_tree

    if architecture is not profile.site_architecture_summary:
        profile.site_architecture_summary = architecture
    if architecture:
        enriched = merge_strategy_nodes_into_tree(architecture, summary)
        if enriched is not architecture:
            profile.site_architecture_summary = enriched
            added = int(enriched.get("strategy_nodes_added") or 0)
            if added:
                events.append(
                    {
                        "type": "system_notice",
                        "content": (
                            f"Added {added} planned page(s) from the content calendar "
                            "to the site architecture tree so Phase 9 can plan them."
                        ),
                    }
                )
    profile.seo_strategy_status = "pending_signoff"

    await supersede_pending_findings(
        db, client_id=client.id, agent_key="content_strategy"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="content_strategy",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="medium",
            status="pending",
        )
    )

    job = AgentJob(
        session_id=session_id,
        agent_key="content_strategy",
        job_type="content_strategy",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "content_strategy"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "content_strategy",
            "pillars": len(summary.get("pillars") or []),
            "queue": len(summary.get("content_plan") or summary.get("priority_queue") or []),
        },
    )
    await db.flush()

    card = {
        "card_type": "content_strategy_report",
        "title": f"Content Strategy: {client.display_name}",
        "agent_key": "content_strategy",
        "actions": ["approve"],
        "required_role": required_role_for("content_strategy"),
        **summary,
    }

    queue = summary.get("content_plan") or summary.get("priority_queue") or []
    qw = sum(1 for p in queue if isinstance(p, dict) and p.get("priority") == "Quick win")
    events.append(
        {
            "type": "agent_message",
            "agent_key": "content_strategy",
            "content": (
                f"Phase 6 draft ready for {client.display_name}: "
                f"{len(summary.get('core_topics') or [])} core topics, "
                f"{len(queue)} prioritized pieces "
                f"({qw} quick wins), "
                f"{len(summary.get('content_gaps') or [])} content gaps, "
                "12-week calendar"
                + (
                    f", {len(summary.get('audit_overrides') or [])} audit overrides (refresh > new)"
                    if summary.get("content_audit_applied")
                    else ". Note: without content-audit, decay/cannibalisation unverified"
                )
                + (
                    f"; combined queue {summary.get('queue_length') or 0} items"
                    if summary.get("both_skills_present")
                    else ""
                )
                + ". SEO Strategist can approve into shared memory."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {"seo_strategy_status": profile.seo_strategy_status},
        }
    )
    events.extend(
        handoff_events(
            "content_strategy",
            result_line=f"{len(queue)} topics in the content plan.",
        )
    )
    return events
