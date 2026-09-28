"""Relevance score 0-100 with a per-signal breakdown.

The score answers one question: how well supported is this piece of content by
the signals that were actually available? It is a weighted coverage measure, not
a quality prediction — a piece can score low simply because the client has no
history and no offers yet, and the breakdown says exactly that.

Structural signals (skill, framework, funnel) always resolve, so they are
excluded from the denominator: crediting them would float every score upward and
hide the absence of the evidential signals that matter.
"""

from __future__ import annotations

from typing import Any

from app.services.content_signals.base import SignalResult

# Signals that always resolve — excluded from the score so they cannot inflate it.
_STRUCTURAL = {"skill", "framework", "funnel"}

# A cannibalisation clash is a real defect in the brief, not a missing input.
_CLASH_PENALTY = 25


def score_context(results: list[SignalResult]) -> dict[str, Any]:
    """Weighted coverage of the evidential signals, plus a breakdown."""
    breakdown: list[dict[str, Any]] = []
    earned = 0.0
    possible = 0.0

    for result in results:
        weight = result.numeric_weight
        structural = result.name in _STRUCTURAL
        contributes = weight > 0 and not structural
        if contributes:
            possible += weight
            if result.available:
                earned += weight
        breakdown.append(
            {
                "signal": result.name,
                "weight": result.weight,
                "available": result.available,
                "counted": contributes,
                "contribution": round(weight, 2) if contributes and result.available else 0.0,
                "note": (result.notes[0] if result.notes else ""),
            }
        )

    base = int(round((earned / possible) * 100)) if possible > 0 else 0

    penalties: list[dict[str, Any]] = []
    for result in results:
        if result.name == "past_performance" and result.available:
            if (result.data or {}).get("cannibalisation_clash"):
                penalties.append(
                    {
                        "reason": "target keyword already covered by an existing page",
                        "points": _CLASH_PENALTY,
                    }
                )

    total = max(0, min(100, base - sum(int(p["points"]) for p in penalties)))
    return {
        "score": total,
        "base_score": base,
        "signals_available": sum(1 for r in results if r.available),
        "signals_counted": sum(1 for b in breakdown if b["counted"]),
        "breakdown": breakdown,
        "penalties": penalties,
        "basis": (
            "weighted coverage of evidential signals; structural signals "
            "(skill, framework, funnel) excluded"
        ),
    }
