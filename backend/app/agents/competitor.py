"""Phase 4 — Competitor & Market Agent (ads-category-competitors skill)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill
from app.integrations.llm import extract_domain
from app.integrations.providers import discover_competitors
from app.ml.tier_competitors import build_tiered_analysis
from app.models import (
    AgentJob,
    Client,
    CompetitorProfile,
    DiscoveryResponse,
    FindingsLedger,
)
from app.services.agent_runtime import get_profile
from app.services.audit import log_event
from app.services.cache import (
    cache_delete,
    cache_get,
    cache_set,
    competitor_cache_key,
    competitor_cache_ttl,
)
from app.services.readiness import recompute_readiness
from app.services.role_skills import required_role_for

# ads-category-competitors skill: identify 6–10, report 5–8 after Tier-5 filter.
MIN_COMPETITORS_TARGET = 6
MAX_COMPETITORS_TO_SCORE = 10


def _usable_competitor_url(item: dict) -> str:
    """Return a real http(s) competitor URL, or '' for category labels / nulls."""
    raw = item.get("url") or item.get("domain") or item.get("website") or ""
    url = str(raw).strip()
    if not url or url.lower() in {"none", "null", "n/a", "-"}:
        return ""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    host = url.split("//", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")
    if not host or "." not in host or " " in host:
        return ""
    return url


def _is_placeholder_competitor(item: dict) -> bool:
    """Reject mock/synthetic competitors from discovery carryover or old caches."""
    url = _usable_competitor_url(item).lower()
    name = str(item.get("name") or "").lower()
    host = url.split("//", 1)[-1].split("/", 1)[0]
    if not url:
        return True
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


_CHUNK_URL_RE = re.compile(r"https?://\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}", re.I)


def _discovery_competitor_inputs(raw: object) -> tuple[list[dict], list[str]]:
    """Split a Phase 1 competitors answer into URL seeds and name hints."""
    if isinstance(raw, list):
        text = "\n".join(str(item) for item in raw if str(item).strip())
    elif isinstance(raw, dict):
        text = str(raw.get("value") or raw.get("text") or "")
    else:
        text = str(raw or "")
    seeds: list[dict] = []
    hints: list[str] = []
    for chunk in re.split(r"[\n;]+", text):
        chunk = chunk.strip(" -•*\t")
        if not chunk:
            continue
        match = _CHUNK_URL_RE.search(chunk)
        if match:
            token = match.group(0).strip(".,)")
            name = chunk[: match.start()].strip(" -–—:|,") or token
            url = _usable_competitor_url({"name": name, "url": token})
            if url:
                seeds.append({"name": name[:120], "url": url, "source": "discovery"})
                continue
        if len(chunk) > 1:
            hints.append(chunk[:80])
    return seeds, hints


def _cache_looks_placeholder(cached: dict) -> bool:
    comps = cached.get("competitors") or []
    if not comps:
        return False
    return any(_is_placeholder_competitor(c) for c in comps if isinstance(c, dict))


def _merge_competitor_candidates(
    seeds: list[dict],
    discovered: list[dict],
) -> list[dict]:
    """Discovery seeds are priority; auto-discovery fills the set (never caps at seed count)."""
    candidates: list[dict] = []
    seen: set[str] = set()
    for c in seeds:
        if not isinstance(c, dict):
            continue
        url = _usable_competitor_url(c)
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append({**c, "url": url, "source": c.get("source", "discovery_override")})
    for c in discovered:
        if not isinstance(c, dict):
            continue
        url = _usable_competitor_url(c)
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append({**c, "url": url, "source": c.get("source", "search_visibility")})
    return candidates


def _publish_draft_landscape(profile, card: dict) -> None:
    """Stash a slim landscape on the CDP so validation/approve can see peers before sign-off."""
    from app.services.memory_packs import slim_competitor_memory

    draft = slim_competitor_memory(card)
    draft["_draft"] = True
    scorecards = card.get("scorecards") or []
    if scorecards:
        draft["scorecards"] = [
            {
                "name": s.get("name"),
                "url": s.get("url"),
                "tier": s.get("tier"),
                "tier_name": s.get("tier_name"),
                "composite": s.get("composite"),
                "future_threat": (s.get("derived") or {}).get("future_threat"),
            }
            for s in scorecards[:12]
            if isinstance(s, dict)
        ]
    if card.get("excluded_tier5"):
        draft["excluded_tier5"] = card.get("excluded_tier5")
    baseline = card.get("client_baseline")
    if isinstance(baseline, dict):
        draft["client_baseline"] = {
            "name": baseline.get("name"),
            "maturity_score": baseline.get("maturity_score"),
        }
    profile.competitive_landscape_summary = draft


async def run_competitor(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("competitor_market_agent")
    events: list[dict] = []
    profile = await get_profile(db, client.id)

    from app.services.agent_handoff import blocked_events, consume_events

    events.extend(
        consume_events(
            "competitor_market_agent",
            pack_notes=[
                f"discovery={profile.discovery_status}",
                f"website={profile.website_status}",
            ],
        )
    )
    if profile.website_status != "complete":
        events.extend(
            blocked_events(
                "competitor_market_agent",
                "Approve Phase 3 Website Situation first so competitor scan uses locked site context.",
                route_to="website_situation_agent",
            )
        )
        return events

    profile.competitor_status = "in_progress"

    cache_key = competitor_cache_key(str(client.id))
    force_refresh = any(
        w in message.lower()
        for w in ("refresh", "re-run", "rerun", "re scan", "rescan", "new scan")
    )
    cached = await cache_get(cache_key)
    if cached and _cache_looks_placeholder(cached):
        await cache_delete(cache_key)
        cached = None
        events.append(
            {
                "type": "system_notice",
                "content": "Cleared stale mock competitor cache — running a fresh skill scan…",
            }
        )

    if cached and not force_refresh:
        # Redis can outlive a DB clear (profiles + findings wiped). A hollow
        # cache hit would set pending_signoff with nothing to approve.
        existing_profiles = (
            await db.execute(
                select(CompetitorProfile).where(CompetitorProfile.client_id == client.id)
            )
        ).scalars().all()
        if not existing_profiles:
            await cache_delete(cache_key)
            cached = None
            events.append(
                {
                    "type": "system_notice",
                    "content": (
                        "Cached competitor landscape had no DB profiles "
                        "(cleared or never persisted) — running a fresh scan…"
                    ),
                }
            )
        else:
            pending = (
                await db.execute(
                    select(FindingsLedger).where(
                        FindingsLedger.client_id == client.id,
                        FindingsLedger.agent_key == "competitor_market_agent",
                        FindingsLedger.status == "pending",
                    )
                )
            ).scalars().all()
            if not pending:
                for cp in existing_profiles:
                    db.add(
                        FindingsLedger(
                            client_id=client.id,
                            agent_key="competitor_market_agent",
                            source_table="competitor_profiles",
                            source_id=cp.id,
                            confidence="medium",
                            status="pending",
                            created_by=user_id,
                        )
                    )
                await db.flush()
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
            _publish_draft_landscape(profile, cached)
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

    # Competitor discovery is owned by Phase 4. Carry every previously saved
    # competitor profile (manual + discovery_override + prior scans) so a refresh
    # cannot wipe the set when live auto-discovery fails. Phase 1 still does not
    # seed new rivals — these rows only exist after Phase 4 has already written them.
    carried: list[dict] = []
    prior_rows = (
        await db.execute(
            select(CompetitorProfile).where(CompetitorProfile.client_id == client.id)
        )
    ).scalars().all()
    for row in prior_rows:
        if not row.url:
            continue
        item = {
            "name": row.name,
            "url": row.url,
            "source": row.source or "prior_scan",
        }
        if _is_placeholder_competitor(item):
            continue
        carried.append(item)

    discovery_hints: list[str] = []
    discovery_row = (
        await db.execute(
            select(DiscoveryResponse)
            .where(
                DiscoveryResponse.client_id == client.id,
                DiscoveryResponse.field_key == "competitors",
            )
            .order_by(DiscoveryResponse.created_at.desc())
        )
    ).scalars().first()
    if discovery_row and isinstance(discovery_row.field_value, dict):
        seeds, discovery_hints = _discovery_competitor_inputs(
            discovery_row.field_value.get("value")
        )
        seen_urls = {_usable_competitor_url(item) for item in carried}
        for seed in seeds:
            if _is_placeholder_competitor(seed) or seed["url"] in seen_urls:
                continue
            carried.append(seed)
            seen_urls.add(seed["url"])

    domain = extract_domain(client.primary_url)
    industry = (client.industry or "").strip() or None
    if not industry:
        # Prefer the Phase 1 inferred industry stored on discovery responses.
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
                    f"Identifying {MIN_COMPETITORS_TARGET}–{MAX_COMPETITORS_TO_SCORE} "
                    f"{industry or 'same-vertical'} competitors for "
                    f"{domain} via ads-category-competitors skill…"
                    if not carried
                    else (
                        f"Using {len(carried)} saved Phase 4 competitor(s) for {domain}"
                        + (
                            f" — auto-discovery will supplement (target "
                            f"{MIN_COMPETITORS_TARGET}–{MAX_COMPETITORS_TO_SCORE})…"
                            if len(carried) < MIN_COMPETITORS_TARGET
                            else " — scoring saved set…"
                        )
                    )
                ),
            },
        }
    )

    # Phase 4 discovery supplements explicit manual overrides when the set is thin.
    discover_error = ""
    discovered: list[dict] = []
    seed_count = len(carried)
    need_discovery = seed_count < MIN_COMPETITORS_TARGET
    if need_discovery or not carried:
        try:
            discovered = await discover_competitors(
                client.display_name,
                domain,
                industry=industry,
                hints=discovery_hints or None,
            )
        except Exception as exc:  # noqa: BLE001
            discover_error = str(exc)[:280]
            discovered = []

    from app.config import get_settings

    if get_settings().use_mock_providers and len(discovered) < MIN_COMPETITORS_TARGET:
        discovered = list(discovered) + [
            {"name": f"Regional Challenger {i}", "url": f"https://challenger-{i}.{domain}"}
            for i in range(1, max(0, MIN_COMPETITORS_TARGET - len(discovered)) + 1)
        ]

    candidates = _merge_competitor_candidates(carried, discovered)
    sources = sorted({str(c.get("source") or "unknown") for c in candidates})

    if carried and need_discovery:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Phase 4 has {seed_count} saved competitor(s) — supplementing with auto-discovery "
                    f"to reach {MIN_COMPETITORS_TARGET}–{MAX_COMPETITORS_TO_SCORE} for tiered scoring "
                    f"(now {len(candidates)} total)."
                ),
            }
        )
    elif carried:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Scoring {len(candidates)} saved Phase 4 competitor(s) "
                    f"(sources: {', '.join(sources)})."
                ),
            }
        )
    else:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Found {len(candidates)} competitors automatically"
                    + (f" (sources: {', '.join(sources)})" if candidates else "")
                    + (
                        f" — scoring top {min(MAX_COMPETITORS_TO_SCORE, len(candidates))} "
                        "on 16 parameters (parallel)…"
                        if candidates
                        else ""
                    )
                ),
            }
        )

    if not candidates:
        events.append(
            {
                "type": "agent_message",
                "agent_key": "competitor_market_agent",
                "content": (
                    (
                        "Competitor discovery failed"
                        + (f": {discover_error}" if discover_error else ".")
                        + " Add competitors manually below, then refresh the scan — "
                        "or retry after checking the Anthropic API key and SKILL_MODEL."
                    )
                    if discover_error
                    else (
                        "Automated competitor discovery returned no domains for this site yet. "
                        "Add at least one competitor URL below, then run 'refresh competitor scan'."
                    )
                ),
            }
        )
        empty_card = {
            "card_type": "competitor_landscape",
            "title": "Competitor & Market — Tiered Analysis",
            "competitors": [],
            "tier_overview": [],
            "empty": True,
            "empty_reason": (
                discover_error
                or "Automated discovery returned no usable websites. Add competitor URLs below, then refresh the scan."
            ),
            "invite_manual": True,
            "agent_key": "competitor_market_agent",
            # Keep the gate actionable so Phase 4 cannot soft-lock the pipeline
            # when live discovery fails — operator can add peers then refresh.
            "actions": [],
            "required_role": required_role_for("competitor_market_agent"),
        }
        events.append({"type": "structured_card", "payload": empty_card})
        events.append({"type": "checkpoint", "payload": empty_card})
        # pending_signoff would imply an approvable landscape; stay in_progress
        # but surface a clear next step instead of a silent hang.
        profile.competitor_status = "in_progress"
        job.status = "failed"
        job.error_detail = discover_error or "no_competitor_domains"
        job.completed_at = datetime.now(timezone.utc)
        events.append(
            {
                "type": "phase_status",
                "payload": {"competitor_status": profile.competitor_status},
            }
        )
        return events

    analysis = await build_tiered_analysis(
        client.display_name,
        client.primary_url,
        industry,
        candidates[:MAX_COMPETITORS_TO_SCORE],
    )

    # Replace prior auto/discovery profiles so later phases don't mix stale competitors.
    # Keep operator-added manual rows unless the new set already covers that URL.
    existing_all = (
        await db.execute(
            select(CompetitorProfile).where(CompetitorProfile.client_id == client.id)
        )
    ).scalars().all()
    new_urls = {str(s.get("url") or "").rstrip("/") for s in analysis["scorecards"]}
    for old in existing_all:
        if old.source == "manual" and (old.url or "").rstrip("/") not in new_urls:
            continue
        # Supersede pending ledgers pointing at this profile
        ledgers = (
            await db.execute(
                select(FindingsLedger).where(
                    FindingsLedger.source_id == old.id,
                    FindingsLedger.agent_key == "competitor_market_agent",
                    FindingsLedger.status == "pending",
                )
            )
        ).scalars().all()
        for led in ledgers:
            led.status = "superseded"
        db.delete(old)
    await db.flush()

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
                created_by=user_id,
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
        "invite_manual": True,
        "agent_key": "competitor_market_agent",
        "actions": ["approve"],
        "required_role": required_role_for("competitor_market_agent"),
        "analysis_mode": "tiered_16_parameter",
        "discovery_sources": sources,
    }
    await cache_set(cache_key, card, competitor_cache_ttl())
    _publish_draft_landscape(profile, card)

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
            "content": "Next: Strategist Approve landscape, then run keyword research (Phase 5).",
        }
    )
    return events
