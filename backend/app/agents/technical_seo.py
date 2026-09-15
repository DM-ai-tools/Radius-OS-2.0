"""Phase 7 — Technical SEO agent.

Entry point for Phase 7. Loads skill contracts (technical-seo + companions),
consumes Phase 6 site-architecture shared memory, and emits a composite report
with Not Measured / specialist handoffs for CWV, rendering, and hreflang.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill, load_skill_file, skill_system_preamble
from app.models import AgentJob, Client, FindingsLedger
from app.services.agent_handoff import blocked_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.role_skills import required_role_for
from app.services.technical_seo import PHASE7_SKILL_DIRS, run_technical_seo_plan


def _load_phase7_contracts() -> dict[str, int]:
    """Warm skill cache and return char counts (proves contracts are on disk)."""
    # Entry agent preamble (truncated SKILL.md used if/when LLM assist is added)
    _ = skill_system_preamble("technical_seo")
    loaded: dict[str, int] = {"technical_seo": len(load_skill("technical_seo") or "")}
    for dirname in PHASE7_SKILL_DIRS:
        body = load_skill_file(dirname)
        loaded[dirname] = len(body or "")
    return loaded


def _ahrefs_project_id(profile) -> int | None:
    """Per-client Ahrefs Site Audit project override from commercial_scope."""
    scope = dict(profile.commercial_scope or {})
    raw = scope.get("ahrefs_site_audit_project_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def run_technical_seo(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    contracts = _load_phase7_contracts()
    _ = user_id
    _ = message

    events: list[dict] = []
    profile = await get_profile(db, client.id)

    events.extend(
        consume_events(
            "technical_seo",
            pack_notes=[f"architecture={profile.site_architecture_status}"],
        )
    )

    ia = dict(profile.site_architecture_summary or {})
    ia_status = str(profile.site_architecture_status or "not_started")
    has_tree = bool(ia.get("target_url_tree") or ia.get("redirect_map"))
    ia_fallback = False
    if ia_status not in ("complete", "pending_signoff") and not has_tree:
        website = dict(profile.website_situation_summary or {})
        demand = dict(profile.search_demand_summary or {})
        website_ready = str(profile.website_status or "") in ("complete", "pending_signoff")
        has_clusters = bool(
            (demand.get("cluster_report") or {}).get("clusters") or demand.get("clusters")
        )
        if website_ready or has_clusters:
            from app.services.technical_seo_ia import build_phase7_ia_fallback

            ia = build_phase7_ia_fallback(
                primary_url=client.primary_url,
                website=website,
                demand=demand,
            )
            ia_fallback = True
            events.append(
                {
                    "type": "system_notice",
                    "content": (
                        "Site Architecture not approved — using fallback URL tree from "
                        f"Website Situation + Search Demand ({ia.get('fallback_url_nodes', 0)} nodes) "
                        "for Phase 7. Approve Site Architecture later for full redirect map."
                    ),
                }
            )
        else:
            events.extend(
                blocked_events(
                    "technical_seo",
                    "Needs Site Architecture redirect map / URL tree, or completed Website Situation with Search Demand clusters.",
                    route_to="site_architecture",
                )
            )
            return events

    ia_ready = ia_status in ("complete", "pending_signoff") or has_tree or ia_fallback

    profile.technical_seo_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Running Technical SEO (Phase 7) — technical audit, SEO themes, broken links"
                + (
                    ", plus Phase 6 IA redirect/depth/robots handoffs…"
                    if ia_ready
                    else "…"
                )
            ),
        }
    )

    from app.integrations.search_console import load_gsc_snapshot_for_client

    gsc_data = await load_gsc_snapshot_for_client(
        db,
        client_id=client.id,
        primary_url=client.primary_url,
    )

    summary = await run_technical_seo_plan(
        client_name=client.display_name,
        primary_url=client.primary_url,
        site_architecture=ia,
        website=dict(profile.website_situation_summary or {}),
        tracking=dict(profile.tracking_baseline or {}),
        ahrefs_project_id=_ahrefs_project_id(profile),
        previous_summary=dict(profile.technical_seo_summary or {}),
        gsc_data=gsc_data,
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["skills_loaded"] = contracts
    summary["phase6_status"] = profile.site_architecture_status
    summary["phase6_fallback_ia"] = ia_fallback

    profile.technical_seo_summary = summary
    profile.technical_seo_status = "pending_signoff"

    await supersede_pending_findings(
        db, client_id=client.id, agent_key="technical_seo"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="technical_seo",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="medium",
            status="pending",
            created_by=user_id,
        )
    )

    job = AgentJob(
        session_id=session_id,
        agent_key="technical_seo",
        job_type="technical_seo",
        status="succeeded",
        completed_at=datetime.now(timezone.utc),
        provider_used=str(summary.get("source") or "technical_seo"),
    )
    db.add(job)
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "technical_seo",
            "score": summary.get("score"),
            "broken": summary.get("broken_link_count"),
            "phase6_connected": summary.get("phase6_connected"),
            "ia_handoffs": len(summary.get("ia_actions") or []),
        },
    )
    await db.flush()

    card = {
        "card_type": "technical_seo_report",
        "title": f"Technical SEO: {client.display_name}",
        "agent_key": "technical_seo",
        "actions": ["approve"],
        "required_role": required_role_for("technical_seo"),
        **summary,
    }

    ia_n = len(summary.get("ia_actions") or [])
    events.append(
        {
            "type": "agent_message",
            "agent_key": "technical_seo",
            "content": (
                f"Technical SEO ready — score {summary.get('score')}, "
                f"{summary.get('broken_link_count') or 0} broken links, "
                f"{len(summary.get('priority_backlog') or [])} backlog items"
                + (f", {ia_n} Phase 6 IA handoffs" if ia_n else "")
                + ". CWV, rendering snapshot, hreflang surface, and GSC (when connected) are included; see Not Measured for full specialist handoffs. "
                "Technical SEO Specialist should approve."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {"technical_seo_status": profile.technical_seo_status},
        }
    )
    events.extend(
        handoff_events(
            "technical_seo",
            phase_statuses={
                "website": profile.website_status,
                "content_audit": profile.content_audit_status,
                "technical_seo": "pending_signoff",
            },
            result_line=f"score {summary.get('score')}; {ia_n} IA handoffs.",
        )
    )
    return events
