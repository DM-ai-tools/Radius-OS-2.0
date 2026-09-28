"""Signal 4 — AI creative / exploratory mode.

Off by default. When on, it tells the model to propose angles that are NOT
derived from the data or the competitor set, for finding openings nobody in the
niche is covering.

Content produced with this signal active is labelled exploratory in the output,
because it carries no evidential backing by construction — that is the point of
the mode, and the operator has to be able to see which pieces are speculative.
"""

from __future__ import annotations

from app.services.content_signals.base import SignalResult, unavailable

# Prompts that push for a genuinely different angle rather than a rephrasing.
EXPLORATORY_DIRECTIVES = [
    "Propose an angle that no competitor in the provided set is using.",
    "Do not derive the angle from search volume or existing rankings.",
    "Prefer a specific, concrete point of view over a survey of the topic.",
    "It is acceptable for the angle to target a small, underserved audience.",
    "State the assumption the angle rests on, so the operator can judge it.",
]


def collect_creative(
    *,
    enabled: bool = False,
    keyword: str = "",
    weight: str = "off",
) -> SignalResult:
    if not enabled:
        return unavailable(
            "creative",
            "Exploratory mode off — angles stay grounded in data and competitors.",
            weight="off",
        )
    return SignalResult(
        name="creative",
        available=True,
        weight=weight if weight != "off" else "high",
        data={
            "exploratory": True,
            "directives": list(EXPLORATORY_DIRECTIVES),
            "label": "EXPLORATORY",
            "disclaimer": (
                "Generated in exploratory mode: the angle is deliberately not "
                "derived from performance or competitor data and is unvalidated."
            ),
        },
        notes=[
            "Exploratory mode ON — this piece is speculative and labelled "
            "EXPLORATORY in the output."
        ],
        suggests=[f"fresh angle for {keyword}" if keyword else "fresh angle"],
    )
