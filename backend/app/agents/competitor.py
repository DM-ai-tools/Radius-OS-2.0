"""Phase 4 — Competitor & Market Agent (ads-category-competitors skill)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm import extract_domain
from app.integrations.providers import discover_competitors
from app.ml.tier_competitors import build_tiered_analysis
from app.models import (
    AgentJob,
    Client,
    ClientDigitalProfile,
    CompetitorProfile,
    DiscoveryResponse,
    FindingsLedger,
)
from app.services.audit import log_event
from app.services.cache import (
    cache_delete,
    cache_get,
    cache_set,
    competitor_cache_key,
    competitor_cache_ttl,
)
from app.services.readiness import recompute_readiness
from app.skills import load_skill


def _is_placeholder_competitor(item: dict) -> bool:
    """Reject mock/synthetic competitors from discovery carryover or old caches."""
    url = str(item.get("url") or "").lower()
    name = str(item.get("name") or "").lower()
    host = url.split("//", 1)[-1].split("/", 1)[0]
    if "example" in host or "localhost" in host:
        return True
    if host.startswith("rival-") or host.startswith("competitor-a-") or host.startswith("challenger-"):
        return True
    if name.startswith("rival of ") or name in {
        "category leader co",
        "value player inc",
        "market peer co",
        "premium dtc brand",
        "value players ltd",
        "niche specialist inc",
        "growth agency hq",
    }:
        return True
    return False


def _cache_looks_placeholder(cached: dict) -> bool:
    comps = cached.get("competitors") or []
    if not comps:
        return False
    return any(_is_placeholder_competitor(c) for c in comps if isinstance(c, dict))


async def run_competitor(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("competitor_market_agent")
    _ = user_id
    events: list[dict] = []
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    profile.competitor_status = "in_progress"

    cache_key = competitor_cache_key(str(client.id))
    force_refresh = any(
        w in message.lower()
        for w in ("refresh", "re-run", "rerun", "re scan", "rescan", "new scan")
    )
    cached = cache_get(cache_key)
    if cached and _cache_looks_placeholder(cached):
        cache_delete(cache_key)
        cached = None
        events.append(
            {
                "type": "system_notice",
                "content": "Cleared stale mock competitor cache — running a fresh skill scan…",
            }
        )

    if cached and not force_refresh:
        events.append(
            {
                "type": "system_notice",
                "content": "Reusing cached tiered competitor analysis (Redis, 14-day window).",
            }
        )
        events.append(
            {
                "type": "agent_message",
                "agent_key": "competitor_market_agent",
                "content": "Loaded cached competitive intelligence. Say 'refresh competitor scan' to re-run.",
            }
        )
        events.append({"type": "structured_card", "payload": cached})
        events.append({"type": "checkpoint", "payload": cached})
        profile.competitor_status = "pending_signoff"
        return events

    job = AgentJob(
        session_id=session_id,
        agent_key="competitor_market_agent",
        job_type="competitor_scan",
        status="running",
        attempt_count=1,
    )
    db.add(job)
    await db.flush()
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Tiered competitor analysis started — scoring client baseline, "
                "then identifying and scoring 6–10 competitors across 16 parameters…"
            ),
        }
    )

    # Carry over from discovery
    disc = (
        await db.execute(
            select(DiscoveryResponse).where(
                DiscoveryResponse.client_id == client.id,
                DiscoveryResponse.field_key == "competitors",
            )
        )
    ).scalars().all()
    carried: list[dict] = []
    for d in disc:
        val = (d.field_value or {}).get("value") or []
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and not _is_placeholder_competitor(item):
                    carried.append(item)

    domain = extract_domain(client.primary_url)
    industry = (client.industry or "").strip() or None
    if not industry:
        # Prefer D1 inferred industry stored on discovery_responses
        inferred_row = (
            await db.execute(
                select(DiscoveryResponse)
                .where(
                    DiscoveryResponse.client_id == client.id,
                    DiscoveryResponse.field_key == "inferred_industry",
                )
                .order_by(DiscoveryResponse.created_at.desc())
            )
        ).scalars().first()
        if inferred_row and isinstance(inferred_row.field_value, dict):
            val = inferred_row.field_value.get("value")
            if isinstance(val, str) and val.strip():
                industry = val.strip()
                client.industry = industry
    events.append(
        {
            "type": "job_progress",
            "payload": {
                "message": (
                    f"Identifying 6–10 {industry or 'same-vertical'} competitors for "
                    f"{domain} via ads-category-competitors skill…"
                ),
            },
        }
    )
    expanded = await discover_competitors(
        client.display_name, domain, industry=industry
    )
    # Only pad with synthetic names when mock providers are on
    from app.config import get_settings

    if get_settings().use_mock_providers and len(expanded) < 6:
        expanded = list(expanded) + [
            {"name": f"Regional Challenger {i}", "url": f"https://challenger-{i}.{domain}"}
            for i in range(1, 7 - len(expanded) + 1)
        ]

    seen_urls: set[str] = set()
    candidates: list[dict] = []
    for c in carried:
        if isinstance(c, dict) and c.get("url") and c["url"] not in seen_urls:
            seen_urls.add(c["url"])
            candidates.append({**c, "source": "discovery_carryover"})
    for c in expanded:
        if c["url"] not in seen_urls:
            seen_urls.add(c["url"])
            candidates.append({**c, "source": c.get("source", "search_visibility")})

    sources = sorted({str(c.get("source") or "unknown") for c in candidates})
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Found {len(candidates)} competitors automatically"
                + (f" (sources: {', '.join(sources)})" if candidates else "")
                + f" — scoring top {min(6, len(candidates))} on 16 parameters "
                "(parallel)…"
            ),
        }
    )

    if not candidates:
        events.append(
            {
                "type": "agent_message",
                "agent_key": "competitor_market_agent",
                "content": (
                    "Automated competitor discovery returned no domains for this site yet "
                    "(ads-category-competitors skill). Try 'refresh competitor scan' after "
                    "discovery has more industry context."
                ),
            }
        )
        empty_card = {
            "card_type": "competitor_landscape",
            "title": "Competitor & Market — Tiered Analysis",
            "competitors": [],
            "tier_overview": [],
            "empty": True,
            "invite_manual": False,
            "agent_key": "competitor_market_agent",
            "actions": [],
            "required_role": "seo_strategist",
        }
        events.append({"type": "structured_card", "payload": empty_card})
        profile.competitor_status = "in_progress"
        return events

    analysis = await build_tiered_analysis(
        client.display_name,
        client.primary_url,
        industry,
        candidates[:6],
    )

    profiles: list[CompetitorProfile] = []
    for s in analysis["scorecards"]:
        cp = CompetitorProfile(
            client_id=client.id,
            name=s["name"],
            url=s["url"],
            source=s.get("source", "search_visibility"),
            positioning_cluster=f"Tier {s['tier']}: {s['tier_name']}",
            confirmed=False,
        )
        db.add(cp)
        profiles.append(cp)
    await db.flush()

    # Attach ids into scorecards / overview
    by_url = {p.url: p for p in profiles}
    for s in analysis["scorecards"]:
        p = by_url.get(s["url"])
        if p:
            s["id"] = str(p.id)
    for row in analysis["tier_overview"]:
        match = next((p for p in profiles if p.name == row["name"]), None)
        if match:
            row["id"] = str(match.id)

    for cp in profiles:
        db.add(
            FindingsLedger(
                client_id=client.id,
                agent_key="competitor_market_agent",
                source_table="competitor_profiles",
                source_id=cp.id,
                confidence="medium",
                status="pending",
            )
        )

    job.status = "succeeded"
    job.completed_at = datetime.now(timezone.utc)
    job.provider_used = "tiered_parameter_analysis"
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={"job_type": "competitor_scan", "method": "ads-category-competitors"},
    )
    await recompute_readiness(db, client.id)
    profile.competitor_status = "pending_signoff"

    card = {
        "card_type": "competitor_landscape",
        "title": "Tiered Competitor Parameter Analysis",
        "generated": analysis.get("generated"),
        "target_business": analysis.get("target_business"),
        "industry": analysis.get("industry"),
        "competitors_scored": analysis.get("competitors_scored"),
        "competitors_in_report": analysis.get("competitors_in_report"),
        "executive_summary": analysis.get("executive_summary"),
        "client_baseline": analysis["client_baseline"],
        "tier_overview": analysis["tier_overview"],
        "tier_map": analysis.get("tier_map"),
        "scorecards": analysis["scorecards"],
        "heatmap": analysis["heatmap"],
        "recommendations": analysis["recommendations"],
        "parameter_gaps": analysis.get("parameter_gaps"),
        "monitoring_plan": analysis.get("monitoring_plan"),
        "excluded_tier5": analysis["excluded_tier5"],
        "competitors": [
            {
                "id": str(p.id),
                "name": p.name,
                "url": p.url,
                "source": p.source,
                "positioning_cluster": p.positioning_cluster,
                "confirmed": p.confirmed,
            }
            for p in profiles
        ],
        "empty": False,
        "invite_manual": False,
        "agent_key": "competitor_market_agent",
        "actions": ["approve"],
        "required_role": "seo_strategist",
        "analysis_mode": "tiered_16_parameter",
        "discovery_sources": sources,
    }
    cache_set(cache_key, card, competitor_cache_ttl())

    threat = analysis["recommendations"].get("top_emerging_threat")
    threat_line = (
        f" #1 emerging threat: {threat['name']} (Future Threat {threat['future_threat']:.0f})."
        if threat
        else ""
    )
    events.append(
        {
            "type": "agent_message",
            "agent_key": "competitor_market_agent",
            "content": (
                f"Automatically identified and scored {len(candidates)} competitors "
                f"({', '.join(sources)}); {len(profiles)} remain after filtering Tier 5. "
                f"Client maturity baseline: {analysis['client_baseline']['maturity_score']:.0f}/100."
                f"{threat_line} Review tiers and approve when ready (SEO Strategist)."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {
                "competitor_status": profile.competitor_status,
                "overall_readiness_score": float(profile.overall_readiness_score or 0),
            },
        }
    )
    events.append(
        {
            "type": "system_notice",
            "content": "Next: Strategist Approve landscape, then run readiness gate.",
        }
    )
    return events
