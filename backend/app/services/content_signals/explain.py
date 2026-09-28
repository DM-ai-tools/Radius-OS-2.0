"""The operator-facing "why this content" summary.

Reads only the context object, so it can be regenerated for a stored piece
without re-running anything.
"""

from __future__ import annotations

from typing import Any


def explain_content_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """Which signals shaped this piece, and how."""
    context = dict(context or {})
    signals = context.get("signals") or {}

    def _data(name: str) -> dict[str, Any]:
        signal = signals.get(name) or {}
        return signal.get("data") or {} if signal.get("available") else {}

    influences: list[dict[str, str]] = []
    for name in context.get("signal_order") or []:
        signal = signals.get(name) or {}
        note = (signal.get("notes") or [""])[0]
        if note:
            influences.append({"signal": name, "weight": signal.get("weight", ""), "how": note})

    offers = _data("offers")
    primary_offer = offers.get("primary_offer") or {}
    relevance = context.get("relevance") or {}

    return {
        "target_keyword": context.get("keyword"),
        "content_type": context.get("page_type"),
        "funnel_stage": context.get("funnel_stage"),
        "skill": _data("skill").get("skill"),
        "skill_reason": _data("skill").get("reason"),
        "framework": _data("framework").get("framework"),
        "framework_reason": _data("framework").get("reason"),
        "offer_linked": primary_offer.get("name"),
        "offer_match_score": primary_offer.get("match_score"),
        "preset": context.get("preset"),
        "exploratory": bool(context.get("exploratory")),
        "relevance_score": relevance.get("score"),
        "influenced_by": influences,
        "conflicts": context.get("conflicts") or [],
        "unavailable_signals": [m.get("signal") for m in (context.get("missing") or [])],
        "summary": _one_liner(context, primary_offer),
    }


def _one_liner(context: dict[str, Any], offer: dict[str, Any]) -> str:
    signals = context.get("signals") or {}

    def _avail(name: str) -> bool:
        return bool((signals.get(name) or {}).get("available"))

    parts: list[str] = []
    stage = context.get("funnel_stage")
    ptype = context.get("page_type") or "page"
    parts.append(f"{stage or 'unstaged'} {ptype}")

    skill = ((signals.get("skill") or {}).get("data") or {}).get("skill")
    framework = ((signals.get("framework") or {}).get("data") or {}).get("framework")
    if skill:
        parts.append(f"written with the {skill} skill")
    if framework:
        parts.append(f"structured on {str(framework).upper()}")
    if _avail("competitors_selected"):
        parts.append("aimed at operator-selected competitors")
    elif _avail("competitors_ai"):
        parts.append("informed by AI-detected competitors")
    if _avail("past_performance"):
        parts.append("checked against existing coverage")
    if offer.get("name"):
        parts.append(f"linked to {offer['name']!r}")
    if context.get("exploratory"):
        parts.append("EXPLORATORY — angle not evidence-backed")

    score = (context.get("relevance") or {}).get("score")
    tail = f" Relevance {score}/100." if score is not None else ""
    return "; ".join(parts) + "." + tail
