"""Signal 11 — funnel relevance.

Wraps the existing ``detect_funnel`` / ``detect_intent`` heuristics rather than
replacing them, so a keyword gets the same stage here as it does in Phase 5.
TOFU / MOFU / BOFU only — retention is deliberately not modelled yet.

The page type from ``resolve_page_type`` is a second input: a service page is a
decision-stage asset even when its keyword reads neutrally, and a guide is an
awareness asset even under commercial intent.
"""

from __future__ import annotations

from typing import Any

from app.services.content_signals.base import SignalResult
from app.services.keyword_opportunity import detect_funnel, detect_intent

STAGES = ("TOFU", "MOFU", "BOFU")

# Page types that pin a stage regardless of keyword phrasing.
_STAGE_BY_PAGE_TYPE: dict[str, str] = {
    "service": "BOFU",
    "sub_service": "BOFU",
    "landing": "BOFU",
    "location": "BOFU",
    "product": "BOFU",
    "comparison": "MOFU",
    "guide": "TOFU",
    "faq": "TOFU",
    "blog": "TOFU",
    "article": "TOFU",
    "listicle": "MOFU",
}

STAGE_PLAYBOOK: dict[str, dict[str, Any]] = {
    "TOFU": {
        "goal": "earn attention and trust; no hard sell",
        "cta_style": "soft — newsletter, checklist, lead magnet, related reading",
        "cta_strength": "soft",
        "avoid": "pricing tables, 'book now' urgency, sales pressure",
        "formats": ["guide", "blog", "faq"],
    },
    "MOFU": {
        "goal": "help the reader compare options and shortlist",
        "cta_style": "medium — comparison, case study, consultation offer",
        "cta_strength": "medium",
        "avoid": "definitional filler the reader is past",
        "formats": ["comparison", "listicle", "guide"],
    },
    "BOFU": {
        "goal": "convert a reader who has already decided to buy",
        "cta_style": "strong — book a call, request a quote, start now",
        "cta_strength": "strong",
        "avoid": "long definitions, generic background, hedging",
        "formats": ["service", "landing", "location", "product"],
    },
}


def resolve_stage(
    *, keyword: str, page_type: str | None = None, intent: str | None = None
) -> tuple[str, str]:
    """(stage, why). Page type pins the stage; otherwise the keyword heuristic decides."""
    ptype = str(page_type or "").strip().lower()
    pinned = _STAGE_BY_PAGE_TYPE.get(ptype)
    if pinned:
        return pinned, f"page type {ptype.replace('_', ' ')!r} is a {pinned} asset"

    resolved_intent = detect_intent(keyword, intent)
    stage = detect_funnel(keyword, resolved_intent)
    if stage not in STAGES:
        stage = "MOFU"
    return stage, f"keyword phrasing with {resolved_intent} intent"


def collect_funnel(
    *,
    keyword: str = "",
    page_type: str | None = None,
    intent: str | None = None,
    weight: str = "high",
) -> SignalResult:
    stage, why = resolve_stage(keyword=keyword, page_type=page_type, intent=intent)
    play = STAGE_PLAYBOOK[stage]
    return SignalResult(
        name="funnel",
        available=True,
        weight=weight,
        data={
            "stage": stage,
            "reason": why,
            "intent": detect_intent(keyword, intent),
            **play,
        },
        notes=[f"Funnel stage: {stage} ({why}). CTA should be {play['cta_strength']}."],
    )
