"""Shared contract for content-generation signals.

Every signal is an independent module that reads what the program already knows
and returns a ``SignalResult``. A signal never raises for missing data: it
returns ``available=False`` with a note, and the context builder leaves it out
of the prompt entirely rather than substituting a placeholder.

Signals are deliberately free of Phase-10/11 specifics so Phase 5 (topic
selection) and Phase 9 (planning) can call the same modules later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Operator-facing weight vocabulary. Kept as words rather than raw numbers so
# the UI, the presets and the "which signal won" note all read the same way.
WEIGHTS: dict[str, float] = {
    "off": 0.0,
    "low": 0.5,
    "medium": 1.0,
    "high": 2.0,
}
DEFAULT_WEIGHT = "medium"


def weight_value(weight: str | float | None) -> float:
    """Numeric weight for a label, a raw number, or None (→ medium)."""
    if weight is None:
        return WEIGHTS[DEFAULT_WEIGHT]
    if isinstance(weight, (int, float)):
        return max(0.0, float(weight))
    return WEIGHTS.get(str(weight).strip().lower(), WEIGHTS[DEFAULT_WEIGHT])


def is_enabled(weight: str | float | None) -> bool:
    return weight_value(weight) > 0.0


@dataclass
class SignalResult:
    """One signal's contribution to the content context.

    ``data`` is the structured payload the prompt builder renders. ``notes`` are
    operator-facing strings that explain what the signal did or why it could not
    contribute — they end up in the "why this content" summary.
    """

    name: str
    available: bool
    weight: str = DEFAULT_WEIGHT
    data: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Angles/topics this signal argues FOR, used for conflict detection.
    suggests: list[str] = field(default_factory=list)
    # Topics this signal argues AGAINST (e.g. already covered).
    avoids: list[str] = field(default_factory=list)

    @property
    def numeric_weight(self) -> float:
        return weight_value(self.weight)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "weight": self.weight,
            "data": self.data,
            "notes": list(self.notes),
            "suggests": list(self.suggests),
            "avoids": list(self.avoids),
        }


def unavailable(name: str, reason: str, *, weight: str = DEFAULT_WEIGHT) -> SignalResult:
    """A signal with nothing to contribute. Never an error, never invented data."""
    return SignalResult(name=name, available=False, weight=weight, notes=[reason])
