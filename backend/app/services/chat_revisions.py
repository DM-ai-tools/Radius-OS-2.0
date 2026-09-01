"""Apply chat-box edit requests onto the current phase report (no full re-run)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, ClientDigitalProfile, CompetitorProfile
from app.services.role_skills import required_role_for

# agent_key → (status_attr, summary_attr, card_type, title)
PHASE_PACKS: dict[str, tuple[str, str, str, str]] = {
    "discovery_agent": (
        "discovery_status",
        "commercial_scope",
        "discovery_profile",
        "Discovery profile",
    ),
    "tracking_access_agent": (
        "tracking_status",
        "tracking_baseline",
        "tracking_t6_signoff",
        "Tracking baseline",
    ),
    "website_situation_agent": (
        "website_status",
        "website_situation_summary",
        "website_audit",
        "Website situation",
    ),
    "competitor_market_agent": (
        "competitor_status",
        "competitive_landscape_summary",
        "competitor_landscape",
        "Competitor landscape",
    ),
    "search_demand": (
        "search_demand_status",
        "search_demand_summary",
        "search_demand_report",
        "Search Demand & Keyword Opportunities",
    ),
    "content_strategy": (
        "seo_strategy_status",
        "seo_strategy_summary",
        "content_strategy_report",
        "Content strategy",
    ),
    "site_architecture": (
        "site_architecture_status",
        "site_architecture_summary",
        "site_architecture_blueprint",
        "Site architecture",
    ),
    "technical_seo": (
        "technical_seo_status",
        "technical_seo_summary",
        "technical_seo_report",
        "Technical SEO",
    ),
    "content_audit": (
        "content_audit_status",
        "content_audit_summary",
        "content_audit_report",
        "Content audit",
    ),
    "content_planning": (
        "content_planning_status",
        "content_planning_summary",
        "content_planning_report",
        "Content planning",
    ),
    "content_production": (
        "content_production_status",
        "content_production_summary",
        "content_production_report",
        "Content production",
    ),
    "on_page_seo": (
        "on_page_seo_status",
        "on_page_seo_summary",
        "on_page_seo_report",
        "On-page SEO",
    ),
    "publishing": (
        "publishing_status",
        "publishing_summary",
        "publishing_report",
        "Publishing",
    ),
}

_PHASE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("phase 5", "search demand", "keyword research", "keyword list", "keywords"), "search_demand"),
    (("phase 6", "content strategy", "seo strategy", "editorial", "calendar", "priority queue"), "content_strategy"),
    (("site architecture", "information architecture", "url tree", "phase 6b"), "site_architecture"),
    (("technical seo", "phase 7"), "technical_seo"),
    (("content audit", "phase 8"), "content_audit"),
    (("content plan", "roadmap", "phase 9"), "content_planning"),
    (("draft", "production", "phase 10"), "content_production"),
    (("on-page", "on page", "phase 11"), "on_page_seo"),
    (("publish", "phase 12"), "publishing"),
    (("competitor", "landscape"), "competitor_market_agent"),
    (("website situation", "seo audit"), "website_situation_agent"),
    (("tracking", "ga4", "gtm"), "tracking_access_agent"),
    (("discovery", "questionnaire", "cdd"), "discovery_agent"),
]

_RUN_HINTS = (
    "run ",
    "re-run",
    "rerun",
    "refresh",
    "start ",
    "kick off",
    "generate ",
    "approve",
    "write the draft",
    "write draft",
    "publish this",
    "confirm re-run",
)

_REV_HINTS = (
    "add ",
    "add:",
    "include ",
    "remove ",
    "drop ",
    "delete ",
    "exclude ",
    "take out",
    "replace ",
    "swap ",
    "rename ",
    "change ",
    "update the",
    "edit the",
    "don't include",
    "do not include",
    "please add",
    "please remove",
    "put in ",
    "instead of",
)

_KIND_KEYS = (
    "keyword",
    "primary_keyword",
    "title",
    "name",
    "url",
    "path",
    "pillar",
    "query",
    "slug",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def looks_like_revision(message: str) -> bool:
    """True when the user is editing a report, not asking to run a phase."""
    text = (message or "").strip()
    if not text or len(text) > 1200:
        return False
    lowered = text.lower()
    if lowered.startswith(_RUN_HINTS):
        return False
    if any(h in lowered[:24] for h in ("run ", "refresh ", "re-run", "rerun", "approve ")):
        return False
    if not any(h in lowered for h in _REV_HINTS):
        return False
    has_object = any(
        w in lowered
        for w in (
            "keyword",
            "topic",
            "competitor",
            "url",
            "page",
            "term",
            "report",
            "calendar",
            "queue",
            "cluster",
            "opportunity",
        )
    )
    has_quote = '"' in text or "'" in text
    return has_object or has_quote


def resolve_target_agent(
    message: str,
    *,
    active_agent_key: str | None,
    statuses: dict[str, str],
) -> str | None:
    lowered = (message or "").lower()
    for hints, agent in _PHASE_HINTS:
        if any(h in lowered for h in hints):
            return agent
    if active_agent_key and active_agent_key in PHASE_PACKS:
        return active_agent_key
    for agent, (status_attr, *_rest) in PHASE_PACKS.items():
        key = status_attr
        # statuses dict from pipeline uses the same attr names
        if statuses.get(key) == "pending_signoff":
            return agent
    for agent, (status_attr, *_rest) in PHASE_PACKS.items():
        if statuses.get(status_attr) in ("complete", "pending_signoff"):
            return agent
    return active_agent_key if active_agent_key in PHASE_PACKS else None


def parse_revision_ops(message: str) -> list[dict[str, str]]:
    """Deterministic add/remove/replace parser. Values stay as the user typed them."""
    text = (message or "").strip()
    ops: list[dict[str, str]] = []
    if not text:
        return ops

    def _clean(val: str) -> str:
        v = val.strip().strip("\"'").rstrip(".").strip()
        v = re.sub(
            r"\s+to(?:\s+the)?\s+(?:keyword|report|list|opportunities|strategy|calendar).*$",
            "",
            v,
            flags=re.I,
        )
        return v.strip(" ,")

    patterns = (
        (
            "remove",
            re.compile(
                r"\b(?:remove|drop|delete|exclude|take out|don't include|do not include)\s+"
                r"(?:the\s+)?(?:keyword|kw|topic|term|competitor|url|page)s?\s+"
                r"[\"']?(.+?)[\"']?(?:\.|$|;)",
                re.I,
            ),
        ),
        (
            "add",
            re.compile(
                r"\b(?:add|include|put in|please add)\s+"
                r"(?:the\s+)?(?:keyword|kw|topic|term|competitor|url|page)s?\s+"
                r"[\"']?(.+?)[\"']?(?:\.|$|;)",
                re.I,
            ),
        ),
        (
            "add",
            re.compile(
                r"\b(?:add|include)\s+[\"'](.+?)[\"']\s+to\b",
                re.I,
            ),
        ),
        (
            "remove",
            re.compile(
                r"\b(?:remove|drop|delete|exclude)\s+[\"'](.+?)[\"']",
                re.I,
            ),
        ),
    )
    for op, pat in patterns:
        for m in pat.finditer(text + "."):
            val = _clean(m.group(1))
            if val and len(val) >= 2:
                ops.append({"op": op, "value": val})

    swap = re.search(
        r"\b(?:replace|swap|change)\s+[\"']?(.+?)[\"']?\s+(?:with|to)\s+[\"']?(.+?)[\"']?(?:\.|$)",
        text,
        re.I,
    )
    if swap:
        old, new = _clean(swap.group(1)), _clean(swap.group(2))
        if old and new:
            ops.append({"op": "replace", "value": old, "new_value": new})

    # Deduplicate while preserving order
    seen: set[tuple[str, str, str]] = set()
    uniq: list[dict[str, str]] = []
    for op in ops:
        key = (op["op"], _norm(op["value"]), _norm(op.get("new_value") or ""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(op)
    return uniq


def _field_matches(val: str, needle: str) -> bool:
    v, n = _norm(val), _norm(needle)
    if not v or not n:
        return False
    return v == n or v.startswith(n + " ")


def _matches(item: Any, needle: str) -> bool:
    if isinstance(item, str):
        return _field_matches(item, needle)
    if isinstance(item, dict):
        return any(_field_matches(str(item.get(k) or ""), needle) for k in _KIND_KEYS)
    return False


def _new_keyword_row(value: str, agent_key: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "keyword": value,
        "primary_keyword": value,
        "title": value,
        "name": value,
        "source": "chat_revision",
        "opportunity_score": 70,
        "intent": "informational",
        "priority": "Quick win",
    }
    if agent_key == "competitor_market_agent":
        url = value if value.startswith("http") else (f"https://{value}" if "." in value else "")
        name = value if not url else value.split("//")[-1].split("/")[0]
        return {"name": name, "url": url or value, "source": "chat_revision"}
    if agent_key == "content_audit":
        return {
            "title": value,
            "path": value if value.startswith("/") else f"/{_norm(value).replace(' ', '-')}",
            "disposition": "keep",
            "reason": "Added via chat",
        }
    return row


def _preferred_lists(summary: dict[str, Any], agent_key: str) -> list[str]:
    if agent_key == "search_demand":
        return [
            "best_opportunities",
            "topics",
            "seed_keywords",
            "strong_evergreen",
            "clusters",
        ]
    if agent_key == "content_strategy":
        return ["priority_queue", "core_topics", "content_gaps"]
    if agent_key == "competitor_market_agent":
        return ["competitors", "scorecards"]
    if agent_key == "site_architecture":
        return ["target_url_tree", "pages"]
    if agent_key == "content_audit":
        return ["inventory"]
    if agent_key == "content_planning":
        return ["pages", "roadmap"]
    if agent_key == "discovery_agent":
        return ["business_keywords"]
    return ["best_opportunities", "priority_queue", "competitors", "inventory", "pages"]


def _ensure_list(summary: dict[str, Any], key: str) -> list:
    cur = summary.get(key)
    if isinstance(cur, list):
        return cur
    if isinstance(cur, str) and cur.strip():
        summary[key] = [s.strip() for s in re.split(r"[,;\n]", cur) if s.strip()]
        return summary[key]
    summary[key] = []
    return summary[key]


def _add_to_list(lst: list, value: str, agent_key: str) -> bool:
    if any(_matches(item, value) for item in lst):
        return False
    if lst and all(isinstance(x, str) for x in lst):
        lst.insert(0, value)
        return True
    lst.insert(0, _new_keyword_row(value, agent_key))
    return True


def _remove_from_list(lst: list, value: str) -> int:
    kept = [item for item in lst if not _matches(item, value)]
    removed = len(lst) - len(kept)
    if removed:
        lst[:] = kept
    return removed


def apply_ops_to_summary(
    summary: dict[str, Any],
    ops: list[dict[str, str]],
    *,
    agent_key: str,
) -> dict[str, Any]:
    """Mutate a copy of the phase summary according to parsed ops."""
    out = dict(summary)
    applied: list[str] = []
    preferred = _preferred_lists(out, agent_key)

    # Nested topic_plan.topic_ideas for Phase 5
    if agent_key == "search_demand":
        plan = dict(out.get("topic_plan") or {})
        if not isinstance(plan.get("topic_ideas"), list):
            plan["topic_ideas"] = []
        out["topic_plan"] = plan

    for op in ops:
        value = (op.get("value") or "").strip()
        if not value:
            continue
        kind = op.get("op")
        if kind == "replace":
            new_value = (op.get("new_value") or "").strip()
            if not new_value:
                continue
            nested = apply_ops_to_summary(
                {k: v for k, v in out.items() if k not in ("_revision_applied", "_revised_via_chat")},
                [{"op": "remove", "value": value}, {"op": "add", "value": new_value}],
                agent_key=agent_key,
            )
            applied.extend(nested.pop("_revision_applied", []) or [])
            nested.pop("_revised_via_chat", None)
            out = nested
            continue
        if kind == "add":
            did = False
            for key in preferred:
                lst = _ensure_list(out, key)
                if _add_to_list(lst, value, agent_key):
                    did = True
            if agent_key == "search_demand":
                ideas = out["topic_plan"]["topic_ideas"]
                if isinstance(ideas, list):
                    _add_to_list(ideas, value, agent_key)
            if did:
                applied.append(f"added {value}")
        elif kind == "remove":
            removed = 0
            for key, val in list(out.items()):
                if isinstance(val, list):
                    removed += _remove_from_list(val, value)
                elif isinstance(val, dict):
                    for inner in val.values():
                        if isinstance(inner, list):
                            removed += _remove_from_list(inner, value)
            if removed:
                applied.append(f"removed {value}")

    out["_revision_applied"] = applied
    out["_revised_via_chat"] = True
    return out


def pack_has_report(profile: ClientDigitalProfile, agent_key: str) -> bool:
    meta = PHASE_PACKS.get(agent_key)
    if not meta:
        return False
    _status_attr, summary_attr, _card, _title = meta
    data = getattr(profile, summary_attr, None) or {}
    if agent_key == "discovery_agent":
        return bool(data) or (getattr(profile, "discovery_status", "") not in ("", "not_started"))
    return bool(data)


def _status_map_from_profile(profile: ClientDigitalProfile) -> dict[str, str]:
    return {attr: str(getattr(profile, attr, "not_started") or "not_started") for attr, *_ in PHASE_PACKS.values()}


async def maybe_revise_from_chat(
    db: AsyncSession,
    *,
    client: Client,
    profile: ClientDigitalProfile,
    active_agent_key: str | None,
    message: str,
) -> list[dict] | None:
    """If this turn is a report edit, apply it and return chat events. Else None."""
    if not looks_like_revision(message):
        return None

    statuses = _status_map_from_profile(profile)
    agent_key = resolve_target_agent(
        message, active_agent_key=active_agent_key, statuses=statuses
    )
    if not agent_key or agent_key not in PHASE_PACKS:
        return [
            {
                "type": "agent_message",
                "agent_key": active_agent_key or "search_demand",
                "content": (
                    "I can edit a phase report from chat (add/remove keywords, topics, "
                    "competitors). Name the phase, or run it first so there is a report to change."
                ),
            }
        ]

    status_attr, summary_attr, card_type, title = PHASE_PACKS[agent_key]
    if not pack_has_report(profile, agent_key):
        return [
            {
                "type": "agent_message",
                "agent_key": agent_key,
                "content": (
                    f"No {title} report yet. Run that phase first, then ask to add or remove items."
                ),
            }
        ]

    ops = parse_revision_ops(message)
    if not ops:
        return [
            {
                "type": "agent_message",
                "agent_key": agent_key,
                "content": (
                    "I understood this as an edit, but could not parse what to change. "
                    'Try: add keyword "local seo melbourne"  or  remove keyword "seo 2024".'
                ),
            }
        ]

    current = dict(getattr(profile, summary_attr) or {})
    updated = apply_ops_to_summary(current, ops, agent_key=agent_key)
    applied = list(updated.pop("_revision_applied", []) or [])
    setattr(profile, summary_attr, updated)
    setattr(profile, status_attr, "pending_signoff")

    if agent_key == "competitor_market_agent":
        await _sync_competitor_rows(db, client.id, ops)

    await db.flush()

    if not applied:
        return [
            {
                "type": "agent_message",
                "agent_key": agent_key,
                "content": (
                    f"No matching rows in {title} for: "
                    + ", ".join(o.get("value") or "" for o in ops)
                    + ". Check the wording against the report."
                ),
            }
        ]

    card = {
        "card_type": card_type,
        "title": title,
        "agent_key": agent_key,
        "actions": ["approve"],
        "required_role": required_role_for(agent_key),
        "revised_via_chat": True,
        **updated,
    }
    line = "; ".join(applied)
    return [
        {
            "type": "system_notice",
            "content": f"Applied chat edit to {title}: {line}. Re-approve this phase.",
        },
        {
            "type": "agent_message",
            "agent_key": agent_key,
            "content": (
                f"Updated {title} from chat ({line}). "
                "Approve again to lock shared memory. Downstream phases may need a refresh."
            ),
        },
        {"type": "structured_card", "payload": card},
        {"type": "checkpoint", "payload": card},
        {
            "type": "phase_status",
            "payload": {status_attr: "pending_signoff"},
        },
    ]


async def _sync_competitor_rows(db: AsyncSession, client_id, ops: list[dict[str, str]]) -> None:
    from sqlalchemy import select

    for op in ops:
        value = (op.get("value") or "").strip()
        if not value:
            continue
        if op.get("op") == "add":
            url = value if value.startswith("http") else (f"https://{value}" if "." in value else "")
            if not url:
                continue
            name = value.split("//")[-1].split("/")[0].removeprefix("www.")
            db.add(
                CompetitorProfile(
                    client_id=client_id,
                    name=name,
                    url=url,
                    source="manual",
                    confirmed=False,
                )
            )
        elif op.get("op") == "remove":
            rows = (
                await db.execute(
                    select(CompetitorProfile).where(CompetitorProfile.client_id == client_id)
                )
            ).scalars().all()
            needle = _norm(value)
            for row in rows:
                blob = _norm(f"{row.name} {row.url}")
                if needle in blob or blob in needle:
                    db.delete(row)
