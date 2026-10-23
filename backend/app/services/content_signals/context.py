"""The content context — one object gathering every available signal.

``build_content_context`` is pure: it takes the profile blobs and settings the
caller already has and returns a structured context. It makes no provider calls
and touches no database, so Phase 5 and Phase 9 can call it later with the same
arguments.

Missing signals are recorded in ``missing`` and omitted from the prompt block.
Nothing is ever invented to fill a gap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.logging_config import get_logger
from app.services.content_signals.base import SignalResult, is_enabled
from app.services.content_signals.business_profile import (
    collect_business_size,
    collect_business_type,
    collect_reach,
)
from app.services.content_signals.competitors import (
    collect_ai_competitors,
    collect_selected_competitors,
)
from app.services.content_signals.creative import collect_creative
from app.services.content_signals.framework import collect_framework
from app.services.content_signals.funnel import collect_funnel
from app.services.content_signals.offers import collect_offers
from app.services.content_signals.past_performance import collect_past_performance
from app.services.content_signals.scoring import score_context
from app.services.content_signals.skills import collect_skill
from app.services.content_signals.weights import (
    detect_conflicts,
    ordered_by_weight,
    resolve_weights,
)
from app.services.service_scope import scope_signal

log = get_logger("content_signals.context")

CONTEXT_VERSION = "content_context_v1"


def build_content_context(
    *,
    keyword: str,
    page_type: str | None = None,
    intent: str | None = None,
    service_id: str | None = None,
    # profile blobs (all optional — a missing blob just disables its signals)
    commercial: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    competitive: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
    production: dict[str, Any] | None = None,
    publishing: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    architecture: dict[str, Any] | None = None,
    competitors: list[dict[str, Any]] | None = None,
    # settings
    preset: str | None = None,
    weight_overrides: dict[str, str] | None = None,
    skill_override: str | None = None,
    framework_choice: str | None = None,
    selected_competitors_only: bool = False,
    creative_mode: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Gather every enabled signal into one reproducible context object."""
    weights, preset_name = resolve_weights(preset, weight_overrides)
    commercial = dict(commercial or {})
    marketing = dict(marketing or {})
    intake = dict(marketing.get("client_intake") or {})

    results: list[SignalResult] = []

    def _run(name: str, fn, **kwargs) -> SignalResult | None:
        """Run one signal unless it is switched off. A signal must never break the run."""
        weight = weights.get(name, "medium")
        if not is_enabled(weight) and name != "creative":
            return None
        try:
            result = fn(weight=weight, **kwargs)
        except Exception as exc:  # noqa: BLE001 — one bad signal must not kill generation
            log.warning("content_signal_failed", signal=name, error=str(exc)[:240])
            result = SignalResult(
                name=name,
                available=False,
                weight=weight,
                notes=[f"Signal failed: {type(exc).__name__}. Excluded from this run."],
            )
        results.append(result)
        return result

    # Structural first — funnel and page type feed the offer and framework choices.
    funnel = _run("funnel", collect_funnel, keyword=keyword, page_type=page_type, intent=intent)
    stage = (funnel.data.get("stage") if funnel and funnel.available else None)

    btype = _run("business_type", collect_business_type, commercial=commercial, marketing=marketing)
    business_type = (btype.data.get("type") if btype and btype.available else None)

    _run("business_size", collect_business_size, commercial=commercial, competitive=competitive)
    reach = _run(
        "reach",
        collect_reach,
        commercial=commercial,
        marketing=marketing,
        intake=intake,
        business_type=business_type,
    )
    locations = list((reach.data.get("locations_served") or []) if reach and reach.available else [])

    _run("skill", collect_skill, page_type=page_type, override=skill_override)
    _run(
        "framework",
        collect_framework,
        page_type=page_type,
        funnel_stage=stage,
        choice=framework_choice,
    )

    services = [str(s) for s in (commercial.get("products_for_promotion") or commercial.get("products") or []) if s]
    _run(
        "offers",
        collect_offers,
        commercial=commercial,
        keyword=keyword,
        funnel_stage=stage,
        services=services,
        locations=locations,
        now=now,
    )

    _run(
        "past_performance",
        collect_past_performance,
        keyword=keyword,
        production=production,
        publishing=publishing,
        audit=audit,
        architecture=architecture,
    )
    _run(
        "competitors_selected",
        collect_selected_competitors,
        competitors=competitors,
        search_demand=search_demand,
    )
    _run(
        "competitors_ai",
        collect_ai_competitors,
        competitors=competitors,
        search_demand=search_demand,
        selected_only=selected_competitors_only,
    )
    _run("creative", collect_creative, enabled=creative_mode, keyword=keyword)

    conflicts = detect_conflicts(results)
    scoring = score_context(results)
    ordered = ordered_by_weight(results)

    missing = [
        {"signal": r.name, "reason": (r.notes[0] if r.notes else "unavailable")}
        for r in results
        if not r.available
    ]

    # Not a weighted signal: the offered sub-services are a hard constraint, so
    # the operator cannot switch it off and it never moves the relevance score.
    prioritization = dict(commercial.get("service_prioritization") or {})
    service_scope = scope_signal(prioritization, service_id) if service_id else None

    return {
        "version": CONTEXT_VERSION,
        "keyword": keyword,
        "page_type": page_type,
        "funnel_stage": stage,
        "service_scope": service_scope,
        "preset": preset_name,
        "weights": weights,
        "settings": {
            "skill_override": skill_override,
            "framework_choice": framework_choice or "auto",
            "selected_competitors_only": selected_competitors_only,
            "creative_mode": creative_mode,
        },
        "signals": {r.name: r.to_dict() for r in results},
        "signal_order": [r.name for r in ordered],
        "missing": missing,
        "conflicts": conflicts,
        "relevance": scoring,
        "exploratory": bool(creative_mode),
    }


def render_context_block(context: dict[str, Any], *, max_chars: int = 6000) -> str:
    """The context as prompt text, heaviest signal first, omitting what's missing."""
    signals = context.get("signals") or {}
    lines: list[str] = [
        "CONTENT CONTEXT (signals below are ordered by operator-set weight; "
        "a signal that is absent was unavailable — do not invent it):",
    ]
    scope = context.get("service_scope") or {}
    if scope.get("scoped"):
        offered = ", ".join(scope.get("offered") or []) or "none (service page only)"
        lines.append(
            f"\n[service scope · {scope.get('service')}] Offered sub-services: {offered}. "
            + (
                "Never mention, compare against, or promote: "
                + ", ".join(scope.get("excluded") or [])
                + ". "
                if scope.get("excluded")
                else ""
            )
            + "Do not introduce any sub-service that is not in the offered list."
        )
    elif scope:
        lines.append(
            f"\n[service scope · {scope.get('service')}] The offered sub-services for this "
            "service have not been confirmed. Do not add sub-services beyond what the brief names."
        )
    for name in context.get("signal_order") or []:
        signal = signals.get(name) or {}
        data = signal.get("data") or {}
        weight = signal.get("weight")
        # Instruction bodies are injected separately; keep the block scannable.
        summary = {k: v for k, v in data.items() if k != "instructions"}
        note = (signal.get("notes") or [""])[0]
        lines.append(f"\n[{name} · weight={weight}] {note}")
        for key, value in list(summary.items())[:8]:
            if value in (None, "", [], {}):
                continue
            rendered = str(value)
            if len(rendered) > 400:
                rendered = rendered[:400] + "…"
            lines.append(f"  - {key}: {rendered}")

    if context.get("conflicts"):
        lines.append("\n[conflicts]")
        for clash in context["conflicts"]:
            lines.append(f"  - {clash['subject']}: {clash['resolution']}")

    if context.get("missing"):
        lines.append(
            "\n[unavailable signals] "
            + "; ".join(f"{m['signal']}" for m in context["missing"])
        )

    if context.get("exploratory"):
        lines.append(
            "\n[EXPLORATORY] Label this piece exploratory: its angle is not "
            "derived from performance or competitor evidence."
        )

    text = "\n".join(lines)
    return text[:max_chars]
