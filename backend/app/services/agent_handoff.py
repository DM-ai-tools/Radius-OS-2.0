"""Inter-agent handoffs — packs forward, gaps bounce upstream.

Agents do not freestyle-negotiate. They:
1. Consume the previous locked CDP pack (+ CDD)
2. Publish this phase's result
3. Hand off to the next agent (after human approve)
4. Bounce gaps to the owning upstream agent when join/validation fails
"""

from __future__ import annotations

from typing import Any

# Linear default chain. URL mapping happens with the keyword pack, before titles/calendar.
PHASE_ORDER: list[str] = [
    "discovery_agent",
    "tracking_access_agent",
    "website_situation_agent",
    "competitor_market_agent",
    "search_demand",
    "site_architecture",
    "content_strategy",
    "technical_seo",
    "content_audit",
    "content_planning",
    "content_production",
    "on_page_seo",
    "publishing",
]

AGENT_LABELS: dict[str, str] = {
    "discovery_agent": "Discovery",
    "tracking_access_agent": "Tracking",
    "website_situation_agent": "Website Situation",
    "competitor_market_agent": "Competitor Landscape",
    "search_demand": "Search Demand",
    "content_audit": "Content Audit",
    "content_strategy": "Content Strategy",
    "site_architecture": "Site Architecture",
    "technical_seo": "Technical SEO",
    "content_planning": "Content Planning",
    "content_production": "Content Production",
    "on_page_seo": "On-Page SEO",
    "publishing": "Publishing",
}

# Chip / chat prompts for the next agent
NEXT_PROMPTS: dict[str, str] = {
    "tracking_access_agent": "Run tracking check",
    "website_situation_agent": "Run website audit and sitemap",
    "competitor_market_agent": "Fetch competitors",
    "search_demand": "Find, classify, and cluster keywords",
    "site_architecture": "Map clusters to URLs — optimize existing or create new",
    "content_strategy": "Decide titles for new pages and build the content calendar",
    "technical_seo": "Run technical SEO audit",
    "content_audit": "Score existing pages marked for optimization",
    "content_planning": "Lock the selected page before drafting",
    "content_production": "Write the full draft for the next priority topic and show the preview",
    "on_page_seo": "Build the on-page package and internal linking plan",
    "publishing": "Publish through the connected WordPress account using the internal linking plan",
}

# What each agent must have consumed (for start-of-run notices)
CONSUMES: dict[str, list[str]] = {
    "tracking_access_agent": ["discovery_agent"],
    "website_situation_agent": ["tracking_access_agent"],
    "competitor_market_agent": ["discovery_agent", "website_situation_agent"],
    "search_demand": ["discovery_agent", "competitor_market_agent", "website_situation_agent"],
    "site_architecture": ["search_demand", "website_situation_agent"],
    "content_strategy": ["site_architecture", "search_demand"],
    "technical_seo": ["content_strategy", "site_architecture", "website_situation_agent"],
    "content_audit": ["website_situation_agent", "search_demand", "technical_seo"],
    "content_planning": ["content_strategy", "site_architecture", "content_audit"],
    "content_production": ["content_planning"],
    "on_page_seo": ["content_production", "site_architecture"],
    "publishing": ["on_page_seo"],
}


def label(agent_key: str) -> str:
    return AGENT_LABELS.get(agent_key, agent_key)


def next_agent_for(
    agent_key: str,
    *,
    phase_statuses: dict[str, str] | None = None,
) -> str | None:
    """Next agent: demand → URL mapping → titles/calendar → technical → draft → WordPress."""
    _ = phase_statuses

    if agent_key == "publishing":
        return None

    try:
        idx = PHASE_ORDER.index(agent_key)
    except ValueError:
        return None
    if idx + 1 >= len(PHASE_ORDER):
        return None
    return PHASE_ORDER[idx + 1]


def handoff_payload(
    from_agent: str,
    *,
    phase_statuses: dict[str, str] | None = None,
    result_line: str | None = None,
) -> dict[str, Any]:
    nxt = next_agent_for(from_agent, phase_statuses=phase_statuses)
    return {
        "from_agent": from_agent,
        "from_label": label(from_agent),
        "to_agent": nxt,
        "to_label": label(nxt) if nxt else None,
        "prompt": NEXT_PROMPTS.get(nxt or "", "") if nxt else None,
        "result_line": result_line,
        "message": _handoff_message(from_agent, nxt, result_line),
    }


def _handoff_message(from_agent: str, to_agent: str | None, result_line: str | None) -> str:
    src = label(from_agent)
    if not to_agent:
        base = f"{src} locked into shared memory. Pipeline complete for this track."
        return f"{base} {result_line}".strip() if result_line else base
    dest = label(to_agent)
    prompt = NEXT_PROMPTS.get(to_agent, f"Run {dest}")
    bits = [f"Handoff: {src} → {dest}."]
    if result_line:
        bits.append(result_line)
    bits.append(f"Approve if needed, then: {prompt}.")
    return " ".join(bits)


def consume_events(agent_key: str, *, pack_notes: list[str] | None = None) -> list[dict[str, Any]]:
    """Start-of-run notice: which upstream packs this agent is reading."""
    upstream = CONSUMES.get(agent_key) or []
    names = [label(u) for u in upstream]
    note = (
        f"{label(agent_key)} reading locked packs: {', '.join(names) or 'CDD / client URL only'}."
    )
    if pack_notes:
        note += " " + " ".join(pack_notes)
    return [
        {
            "type": "system_notice",
            "content": note,
            "payload": {
                "event_type": "agent_consume",
                "agent_key": agent_key,
                "reads": upstream,
                "pack_notes": pack_notes or [],
            },
        }
    ]


def handoff_events(
    from_agent: str,
    *,
    phase_statuses: dict[str, str] | None = None,
    result_line: str | None = None,
) -> list[dict[str, Any]]:
    """End-of-run forward handoff (after card is ready / pending approve)."""
    payload = handoff_payload(from_agent, phase_statuses=phase_statuses, result_line=result_line)
    events: list[dict[str, Any]] = [
        {
            "type": "agent_message",
            "agent_key": from_agent,
            "content": payload["message"],
        },
        {
            "type": "system_notice",
            "content": payload["message"],
            "payload": {"event_type": "agent_handoff", **payload},
        },
    ]
    return events


def bounce_events(
    from_agent: str,
    *,
    gaps: list[dict[str, Any]],
    lock_reason: str | None = None,
) -> list[dict[str, Any]]:
    """Route gaps upstream — do not invent fixes in the current agent."""
    if not gaps and not lock_reason:
        return []

    by_owner: dict[str, list[str]] = {}
    for g in gaps:
        if not isinstance(g, dict):
            continue
        pack = str(g.get("source_pack") or "").lower()
        reason = str(g.get("reason") or "")
        url = g.get("url_n") or g.get("keyword") or g.get("title") or ""
        # strategy_only → Architecture (assign URL/parent); architecture_only → Strategy (plan topic)
        if "strategy_only" in reason or pack == "strategy":
            owner = "site_architecture"
        elif "architecture_only" in reason or pack == "architecture":
            owner = "content_strategy"
        elif "audit" in pack:
            owner = "content_audit"
        else:
            owner = "site_architecture"
        line = f"{url}: {reason}".strip(": ") if url or reason else reason or str(g)
        by_owner.setdefault(owner, []).append(line)

    events: list[dict[str, Any]] = []
    if lock_reason:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"{label(from_agent)} unlocked — fix upstream then re-merge. "
                    f"{lock_reason}"
                ),
                "payload": {
                    "event_type": "agent_bounce",
                    "from_agent": from_agent,
                    "lock_reason": lock_reason,
                },
            }
        )

    for owner, lines in by_owner.items():
        sample = "; ".join(lines[:5])
        more = f" (+{len(lines) - 5} more)" if len(lines) > 5 else ""
        prompt = NEXT_PROMPTS.get(owner, f"Re-run {label(owner)}")
        msg = (
            f"Bounce: {label(from_agent)} → {label(owner)} "
            f"({len(lines)} gap(s)). {sample}{more}. "
            f"Re-run: {prompt}, then re-run {label(from_agent)}."
        )
        events.append(
            {
                "type": "agent_message",
                "agent_key": from_agent,
                "content": msg,
            }
        )
        events.append(
            {
                "type": "system_notice",
                "content": msg,
                "payload": {
                    "event_type": "agent_bounce",
                    "from_agent": from_agent,
                    "to_agent": owner,
                    "to_label": label(owner),
                    "prompt": prompt,
                    "gap_count": len(lines),
                    "gaps": lines[:12],
                },
            }
        )
    return events


def blocked_events(agent_key: str, reason: str, *, route_to: str | None = None) -> list[dict[str, Any]]:
    """Hard block — tell the user which upstream agent to run."""
    dest = route_to
    if not dest:
        # Heuristic from reason text
        lowered = reason.lower()
        if "architecture" in lowered or "url tree" in lowered:
            dest = "site_architecture"
        elif "strategy" in lowered or "priority queue" in lowered:
            dest = "content_strategy"
        elif "audit" in lowered:
            dest = "content_audit"
        elif "search demand" in lowered or "cluster" in lowered or "keyword" in lowered:
            dest = "search_demand"
        elif "discovery" in lowered or "cdd" in lowered:
            dest = "discovery_agent"
        elif "planning" in lowered or "roadmap" in lowered or "unlocked" in lowered:
            dest = "content_planning"
        elif "production" in lowered or "brief" in lowered:
            dest = "content_production"
        elif "on-page" in lowered or "on_page" in lowered:
            dest = "on_page_seo"
    prompt = NEXT_PROMPTS.get(dest or "", "")
    msg = f"Blocked — {label(agent_key)} waiting on upstream. {reason}"
    if dest:
        msg += f" Route to {label(dest)}" + (f": {prompt}." if prompt else ".")
    return [
        {
            "type": "system_notice",
            "content": msg,
            "payload": {
                "event_type": "agent_blocked",
                "agent_key": agent_key,
                "route_to": dest,
                "prompt": prompt or None,
                "reason": reason,
            },
        }
    ]


def approve_handoff(
    agent_key: str,
    *,
    phase_statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Payload returned from review/approve so the UI can prompt the next agent."""
    nxt = next_agent_for(agent_key, phase_statuses=phase_statuses)
    return {
        "from_agent": agent_key,
        "to_agent": nxt,
        "to_label": label(nxt) if nxt else None,
        "prompt": NEXT_PROMPTS.get(nxt or "") if nxt else None,
        "message": (
            f"Approved {label(agent_key)} → shared memory. "
            + (
                f"Next: {label(nxt)} — {NEXT_PROMPTS.get(nxt, '')}."
                if nxt
                else "No further phase on this track."
            )
        ),
    }
