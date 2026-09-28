"""Signal weights, preset modes and conflict resolution.

Each signal can be off / low / medium / high. Presets are named weight sets so
the operator does not have to tune eleven sliders to get a sensible run; "custom"
starts from the defaults and takes whatever overrides are passed.

Conflict resolution is deliberately simple and explainable: when two signals
argue for different things, the one with the higher numeric weight wins and the
loser is recorded in ``conflicts`` so it reaches the "why this content" summary.
Equal weights are recorded as unresolved rather than broken arbitrarily.
"""

from __future__ import annotations

from typing import Any

from app.services.content_signals.base import SignalResult, weight_value

SIGNAL_NAMES = (
    "past_performance",
    "competitors_ai",
    "competitors_selected",
    "creative",
    "skill",
    "framework",
    "business_type",
    "business_size",
    "reach",
    "offers",
    "funnel",
)

# Skill, framework and funnel are structural — they decide the shape of the page,
# so they default high. Creative is off unless asked for.
DEFAULT_WEIGHTS: dict[str, str] = {
    "past_performance": "medium",
    "competitors_ai": "medium",
    "competitors_selected": "high",
    "creative": "off",
    "skill": "high",
    "framework": "high",
    "business_type": "medium",
    "business_size": "medium",
    "reach": "medium",
    "offers": "medium",
    "funnel": "high",
}

PRESETS: dict[str, dict[str, str]] = {
    "data_driven": {
        **DEFAULT_WEIGHTS,
        "past_performance": "high",
        "competitors_ai": "high",
        "competitors_selected": "high",
        "creative": "off",
    },
    "competitor_beating": {
        **DEFAULT_WEIGHTS,
        "competitors_selected": "high",
        "competitors_ai": "medium",
        "skill": "high",
        "past_performance": "low",
        "creative": "off",
    },
    "creative": {
        **DEFAULT_WEIGHTS,
        "creative": "high",
        "past_performance": "low",
        "competitors_ai": "off",
        "competitors_selected": "low",
    },
    "local_growth": {
        **DEFAULT_WEIGHTS,
        "reach": "high",
        "business_size": "high",
        "offers": "high",
        "competitors_ai": "low",
    },
    "custom": dict(DEFAULT_WEIGHTS),
}
DEFAULT_PRESET = "data_driven"


def resolve_weights(
    preset: str | None = None, overrides: dict[str, str] | None = None
) -> tuple[dict[str, str], str]:
    """(weights, preset_name). Unknown preset falls back to the default."""
    name = str(preset or DEFAULT_PRESET).strip().lower()
    if name not in PRESETS:
        name = DEFAULT_PRESET
    weights = dict(PRESETS[name])
    for key, value in (overrides or {}).items():
        if key in weights and value is not None:
            weights[key] = str(value).strip().lower()
    return weights, name


def detect_conflicts(results: list[SignalResult]) -> list[dict[str, Any]]:
    """Where one signal's `suggests` collides with another's `avoids`.

    The clearest real case: past performance says a topic is already covered
    while a competitor signal pushes the same topic. Whichever carries more
    weight wins, and the decision is recorded either way.
    """
    conflicts: list[dict[str, Any]] = []
    available = [r for r in results if r.available and r.numeric_weight > 0]
    for blocker in available:
        for avoided in blocker.avoids:
            target = avoided.split(":", 1)[-1].strip().lower()
            if not target:
                continue
            for pusher in available:
                if pusher.name == blocker.name:
                    continue
                for suggestion in pusher.suggests:
                    if target and target in suggestion.lower():
                        if pusher.numeric_weight > blocker.numeric_weight:
                            winner, loser = pusher.name, blocker.name
                            outcome = f"{pusher.name} outweighs {blocker.name}"
                        elif blocker.numeric_weight > pusher.numeric_weight:
                            winner, loser = blocker.name, pusher.name
                            outcome = f"{blocker.name} outweighs {pusher.name}"
                        else:
                            winner, loser = None, None
                            outcome = (
                                f"{pusher.name} and {blocker.name} carry equal weight — "
                                "unresolved, operator should decide"
                            )
                        conflicts.append(
                            {
                                "subject": target,
                                "pushed_by": pusher.name,
                                "blocked_by": blocker.name,
                                "winner": winner,
                                "loser": loser,
                                "resolution": outcome,
                            }
                        )
    return conflicts


def ordered_by_weight(results: list[SignalResult]) -> list[SignalResult]:
    """Heaviest available signals first — the order they enter the prompt."""
    return sorted(
        [r for r in results if r.available and r.numeric_weight > 0],
        key=lambda r: (-r.numeric_weight, r.name),
    )


def weight_label(value: float) -> str:
    for label in ("high", "medium", "low"):
        if weight_value(label) == value:
            return label
    return "off" if value <= 0 else "custom"
