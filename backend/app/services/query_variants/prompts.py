"""The model prompt behind informational query variants.

``EXAMPLES`` shows the model the pattern (the operator's two examples, 2026-10-13). It is
never read back as data.
"""

from __future__ import annotations

from collections.abc import Iterable

EXAMPLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("physiotherapy", ("how does physiotherapy work", "what is physiotherapy", "physiotherapy benefits")),
    (
        "mortgage broker",
        ("how to choose a mortgage broker", "what does a mortgage broker do", "mortgage broker vs bank"),
    ),
)


def _example_lines() -> str:
    return "\n".join(f"{seed} -> {', '.join(variants)}" for seed, variants in EXAMPLES)


SYSTEM_PROMPT = (
    "You are an SEO keyword researcher. Keyword research seeds are usually commercial "
    "terms, so the keyword tool returns mostly buying-stage searches. For each seed, "
    "write the questions and research-style searches someone types while they are still "
    "learning about it or comparing options, before they are ready to buy.\n"
    "How to write them:\n"
    "- Question-style and research-style: how, what, why, does, benefits, vs, how to "
    "choose, what to expect, is it worth it.\n"
    "- Each one keeps the seed's main subject words, so the searches stay about the "
    "same thing.\n"
    "- Natural phrasings real people search. Not buying phrases (no hire, near me, "
    "price, cost, quote, best company, agency), no locations, no brand names, no years.\n"
    "- Fit the business's industry and use the spelling of the given market.\n"
    "- At most 8 words each, lowercase.\n"
    "- 3 to 4 per seed, never more than 4.\n"
    "Examples of the pattern:\n"
    f"{_example_lines()}\n"
    'Return JSON: {"variants": {"<seed exactly as given>": ["...", "..."]}}'
)


def build_user_prompt(
    seeds: Iterable[str], *, industry: str | None = None, market: str | None = None
) -> str:
    lines = [
        f"Business industry: {industry or 'not given'}",
        f"Market: {market or 'not given'}",
        "Seeds:",
    ]
    lines.extend(f"- {seed}" for seed in seeds)
    return "\n".join(lines)
