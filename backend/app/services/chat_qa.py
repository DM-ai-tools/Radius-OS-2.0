"""Answer chat questions from CDP shared memory and phase reports."""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.models import Client, ClientDigitalProfile
from app.services.memory_packs import (
    slim_competitor_memory,
    slim_content_audit_memory,
    slim_content_planning_memory,
    slim_content_production_memory,
    slim_on_page_seo_memory,
    slim_publishing_memory,
    slim_search_demand_memory,
    slim_seo_strategy_memory,
    slim_site_architecture_memory,
    slim_technical_seo_memory,
)

_QUESTION_START = (
    "what",
    "what's",
    "whats",
    "which",
    "who",
    "where",
    "when",
    "why",
    "how",
    "show",
    "list",
    "tell",
    "summarize",
    "summarise",
    "explain",
    "do we",
    "did we",
    "is there",
    "are there",
    "can you tell",
    "give me",
)

_RUN_PREFIX = (
    "run ",
    "re-run",
    "rerun",
    "refresh ",
    "approve ",
    "start ",
    "write ",
    "draft ",
    "create ",
    "generate ",
    "build ",
)
# Instructions to produce a draft or preview must not be answered from memory.
_ACTION_PHRASES = (
    "write the full draft",
    "write the draft",
    "write draft",
    "show the preview",
    "site preview",
    "draft the next",
    "next priority topic",
)


def looks_like_question(message: str) -> bool:
    text = (message or "").strip()
    if not text or len(text) > 1500:
        return False
    lowered = text.lower()
    if lowered.startswith(_RUN_PREFIX):
        return False
    if any(phrase in lowered for phrase in _ACTION_PHRASES):
        return False
    if "?" in text:
        return True
    return any(lowered.startswith(p) or f" {p} " in f" {lowered} " for p in _QUESTION_START)


def build_memory_brief(client: Client, profile: ClientDigitalProfile) -> dict[str, Any]:
    """Compact shared-memory snapshot for Q&A (slim packs, not full chat cards)."""
    statuses = {
        "discovery": profile.discovery_status,
        "tracking": profile.tracking_status,
        "website": profile.website_status,
        "competitors": profile.competitor_status,
        "search_demand": profile.search_demand_status,
        "content_strategy": profile.seo_strategy_status,
        "site_architecture": profile.site_architecture_status,
        "technical_seo": profile.technical_seo_status,
        "content_audit": profile.content_audit_status,
        "content_planning": profile.content_planning_status,
        "content_production": profile.content_production_status,
        "on_page_seo": profile.on_page_seo_status,
        "publishing": profile.publishing_status,
    }
    commercial = dict(profile.commercial_scope or {})
    marketing = dict(profile.marketing_context or {})
    intake = dict(marketing.get("client_intake") or {})
    brief: dict[str, Any] = {
        "client": {
            "name": client.display_name,
            "url": client.primary_url,
            "industry": client.industry,
        },
        "statuses": statuses,
        "discovery": {
            k: commercial.get(k)
            for k in (
                "business_keywords",
                "products",
                "products_for_promotion",
                "geographic_focus",
                "positioning",
                "target_demographic",
            )
            if commercial.get(k) not in (None, "", [], {})
        },
        "intake": {
            k: intake.get(k)
            for k in ("business_keywords", "products", "geographic_focus", "objectives")
            if intake.get(k) not in (None, "", [], {})
        },
        "competitors": slim_competitor_memory(dict(profile.competitive_landscape_summary or {})),
        "search_demand": slim_search_demand_memory(dict(profile.search_demand_summary or {})),
        "content_strategy": slim_seo_strategy_memory(dict(profile.seo_strategy_summary or {})),
        "site_architecture": slim_site_architecture_memory(
            dict(profile.site_architecture_summary or {})
        ),
        "technical_seo": slim_technical_seo_memory(dict(profile.technical_seo_summary or {})),
        "content_audit": slim_content_audit_memory(dict(profile.content_audit_summary or {})),
        "content_planning": slim_content_planning_memory(
            dict(profile.content_planning_summary or {})
        ),
        "content_production": slim_content_production_memory(
            dict(profile.content_production_summary or {})
        ),
        "on_page_seo": slim_on_page_seo_memory(dict(profile.on_page_seo_summary or {})),
        "publishing": slim_publishing_memory(dict(profile.publishing_summary or {})),
    }
    return {k: v for k, v in brief.items() if v not in (None, "", [], {})}


def _fmt_kw(row: Any) -> str | None:
    if isinstance(row, str) and row.strip():
        return row.strip()
    if not isinstance(row, dict):
        return None
    kw = str(row.get("keyword") or row.get("primary_keyword") or row.get("title") or "").strip()
    if not kw:
        return None
    bits = [kw]
    extra = []
    if row.get("volume") not in (None, ""):
        extra.append(f"vol {row.get('volume')}")
    if row.get("difficulty") not in (None, ""):
        extra.append(f"KD {row.get('difficulty')}")
    if row.get("opportunity_score") not in (None, ""):
        extra.append(f"score {row.get('opportunity_score')}")
    if extra:
        bits.append(f"({', '.join(str(x) for x in extra)})")
    url = str(row.get("url") or row.get("path") or row.get("suggested_url") or "").strip()
    if url and url.lower() not in kw.lower():
        bits.append(f"— {url}")
    return " ".join(bits)


def _list_block(title: str, rows: list[Any], *, limit: int = 12) -> str:
    lines = []
    for row in rows[:limit]:
        line = _fmt_kw(row)
        if not line and isinstance(row, dict):
            name = row.get("name") or row.get("pillar") or row.get("url") or row.get("path")
            if name:
                url = row.get("url") or ""
                line = f"{name}" + (f" — {url}" if url and url != name else "")
            elif row.get("from") and row.get("to"):
                line = f"{row.get('from')} → {row.get('to')}"
            elif row.get("issue"):
                prio = row.get("priority")
                line = f"[{prio}] {row.get('issue')}" if prio else str(row.get("issue"))
            elif row.get("disposition") and (row.get("url") or row.get("title")):
                line = f"{row.get('url') or row.get('title')} ({row.get('disposition')})"
        if line:
            lines.append(f"- {line}")
    if not lines:
        return ""
    return title + "\n" + "\n".join(lines)


def _missing(phase: str) -> str:
    return (
        f"That is not in shared memory yet. Run **{phase}** first, then ask again. "
        "I only answer from locked reports — I will not invent keywords, URLs, or scores."
    )


def direct_answer(question: str, brief: dict[str, Any]) -> str | None:
    """Fast extractors for common report questions. None = use LLM or missing copy."""
    q = (question or "").lower()
    demand = dict(brief.get("search_demand") or {})
    strategy = dict(brief.get("content_strategy") or {})
    comps = dict(brief.get("competitors") or {})
    statuses = dict(brief.get("statuses") or {})

    if any(w in q for w in ("keyword", "opportunit", "search demand", "volume", "cluster")):
        if not demand:
            return _missing("Phase 5 Search Demand")
        parts = [
            _list_block("Best opportunities", list(demand.get("best_opportunities") or [])),
            _list_block("Evergreen", list(demand.get("strong_evergreen") or [])),
            _list_block(
                "Topics",
                list(demand.get("topics") or [])
                or list((demand.get("topic_plan") or {}).get("topic_ideas") or []),
            ),
        ]
        body = "\n\n".join(p for p in parts if p)
        return body or _missing("Phase 5 Search Demand")

    if any(w in q for w in ("competitor", "rival", "landscape")):
        rows = list(comps.get("competitors") or [])
        if not rows:
            return _missing("Phase 4 Competitor scan")
        return _list_block("Competitors in shared memory", rows, limit=10)

    if any(w in q for w in ("topic", "pillar", "calendar", "strategy", "what should i write", "priority queue")):
        if not strategy:
            return _missing("Phase 6 Content Strategy")
        parts = [
            _list_block("Core topics / pillars", list(strategy.get("core_topics") or strategy.get("pillars") or [])),
            _list_block("Priority queue", list(strategy.get("priority_queue") or [])),
        ]
        body = "\n\n".join(p for p in parts if p)
        return body or _missing("Phase 6 Content Strategy")

    if any(w in q for w in ("status", "what's done", "whats done", "which phase", "progress")):
        lines = [f"- {k.replace('_', ' ')}: {v}" for k, v in statuses.items()]
        name = (brief.get("client") or {}).get("name") or "this client"
        return f"Phase status for {name}:\n" + "\n".join(lines)

    if any(w in q for w in ("industry", "product", "service", "geo", "where are they")):
        disc = dict(brief.get("discovery") or {})
        if not disc and not brief.get("intake"):
            return _missing("Phase 1 Discovery")
        bits = []
        client = dict(brief.get("client") or {})
        if client.get("industry"):
            bits.append(f"- Industry: {client['industry']}")
        for label, key in (
            ("Keywords", "business_keywords"),
            ("Products", "products"),
            ("Geo", "geographic_focus"),
            ("Positioning", "positioning"),
        ):
            val = disc.get(key)
            if val:
                bits.append(f"- {label}: {val if not isinstance(val, list) else ', '.join(str(x) for x in val[:8])}")
        return "From Discovery shared memory:\n" + "\n".join(bits) if bits else _missing("Phase 1 Discovery")

    if any(w in q for w in ("architecture", "url tree", "information architecture", "redirect")):
        ia = dict(brief.get("site_architecture") or {})
        if not ia:
            return _missing("Phase 7 Site Architecture")
        parts = []
        cs = dict(ia.get("current_state") or {})
        if cs:
            parts.append("Current state\n" + "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in cs.items()))
        parts.append(_list_block("Target URL tree", list(ia.get("target_url_tree") or []), limit=20))
        parts.append(_list_block("Redirect map", list(ia.get("redirect_map") or []), limit=15))
        body = "\n\n".join(p for p in parts if p)
        return body or (str(ia.get("executive_summary") or "").strip() or _missing("Phase 7 Site Architecture"))

    if any(w in q for w in ("technical seo", "cwv", "core web", "broken link", "tech score")):
        tech = dict(brief.get("technical_seo") or {})
        if not tech:
            return _missing("Phase 8 Technical SEO")
        parts = []
        if tech.get("score") is not None:
            parts.append(f"Technical SEO score: {tech.get('score')}")
        if tech.get("broken_link_count") is not None:
            parts.append(f"Broken links: {tech.get('broken_link_count')}")
        parts.append(_list_block("Priority backlog", list(tech.get("priority_backlog") or [])))
        parts.append(_list_block("Themes", list(tech.get("themes") or [])))
        body = "\n\n".join(p for p in parts if p)
        return body or _missing("Phase 8 Technical SEO")

    if any(w in q for w in ("content audit", "refresh queue", "cannibalis")):
        audit = dict(brief.get("content_audit") or {})
        if not audit:
            return _missing("Phase 9 Content Audit")
        parts = [
            _list_block("Inventory", list(audit.get("inventory") or []), limit=15),
            _list_block("Refresh queue", list(audit.get("refresh_queue") or [])),
        ]
        body = "\n\n".join(p for p in parts if p)
        return body or _missing("Phase 9 Content Audit")

    return None


def _has_any_report(brief: dict[str, Any]) -> bool:
    return any(
        brief.get(k)
        for k in (
            "discovery",
            "intake",
            "competitors",
            "search_demand",
            "content_strategy",
            "site_architecture",
            "technical_seo",
            "content_audit",
            "content_planning",
            "content_production",
            "on_page_seo",
            "publishing",
        )
    )


async def answer_from_memory(
    *,
    client: Client,
    profile: ClientDigitalProfile,
    message: str,
) -> list[dict]:
    brief = build_memory_brief(client, profile)
    agent_key = "memory_qa"

    if not _has_any_report(brief):
        return [
            {
                "type": "agent_message",
                "agent_key": agent_key,
                "content": (
                    "Shared memory is empty for this client. Run Discovery (and later phases) "
                    "first — I only answer from reports that already exist."
                ),
            }
        ]

    answer = direct_answer(message, brief)
    settings = get_settings()
    if answer is None and not settings.use_mock_llm:
        from app.integrations.llm import synthesize_text

        payload = json.dumps(brief, default=str)[:14000]
        system = (
            "You are Radius OS. Answer ONLY from the shared-memory JSON. "
            "If the fact is missing, say which phase to run. "
            "Never invent keywords, URLs, scores, or competitors. "
            "Be concise. Use short bullets when listing."
        )
        user = f"Question: {message}\n\nShared memory JSON:\n{payload}"
        text = (await synthesize_text(system, user)).strip()
        if text and text != user[:200] and "Shared memory JSON" not in text:
            answer = text
    if not answer:
        answer = (
            "I can answer from shared memory (keywords, competitors, strategy, status). "
            "Ask something like: what keywords did we find? who are the competitors? "
            "what is in the content strategy?"
        )
        if direct_answer("what is the status", brief):
            answer += "\n\n" + (direct_answer("what is the status", brief) or "")

    return [
        {
            "type": "system_notice",
            "content": "Answered from shared memory / locked phase reports (not a new scan).",
        },
        {
            "type": "agent_message",
            "agent_key": agent_key,
            "content": answer,
        },
    ]


async def maybe_answer_from_memory(
    *,
    client: Client,
    profile: ClientDigitalProfile,
    message: str,
) -> list[dict] | None:
    if not looks_like_question(message):
        return None
    return await answer_from_memory(client=client, profile=profile, message=message)
