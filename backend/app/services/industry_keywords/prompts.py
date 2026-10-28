"""The model prompt behind industry seeds.

``EXAMPLES`` shows the model the pattern to follow. It is never read back as data: there
is no lookup by industry, and an industry that appears here is asked about exactly like
any other.
"""

from __future__ import annotations

from collections.abc import Iterable

# The pattern: what a buyer types to find a business of this kind, the broad terms a home
# page targets. The digital marketing line holds the terms the 2026-10-27 research named.
EXAMPLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Digital marketing agency",
        (
            "digital marketing agency",
            "online marketing company",
            "marketing agency",
            "digital marketing services",
            "internet marketing agency",
        ),
    ),
    ("Dental clinic", ("dentist", "dental clinic", "dental practice", "family dentist", "dental care")),
    ("Family law firm", ("family lawyer", "family law firm", "divorce lawyer", "family law solicitor")),
    ("Plumbing", ("plumber", "plumbing company", "plumbing services", "emergency plumber", "local plumber")),
    ("Accounting firm", ("accountant", "accounting firm", "accounting services", "tax accountant")),
    ("Bakery", ("bakery", "cake shop", "artisan bakery", "bread bakery")),
    (
        "Project management software",
        ("project management software", "project management tool", "task management software"),
    ),
)


def _example_lines() -> str:
    return "\n".join(f"  {industry} -> {', '.join(terms)}" for industry, terms in EXAMPLES)


SYSTEM_PROMPT = (
    "You are an SEO keyword researcher. You are given a business's industry, its market "
    "and the services it sells. List the category-level searches a buyer types into "
    "Google to find a business of this kind: the broad terms that describe the whole "
    "business, the ones its home page should rank for.\n"
    "How to choose:\n"
    "- Each term names the kind of business or what it offers as a whole (digital "
    "marketing agency, online marketing company, dentist), worded the way customers "
    "search. Different wordings people really use are welcome.\n"
    "- Not one of the services listed with the question: each of those is researched on "
    "its own already. Not a narrower specialisation of one service.\n"
    "- Mostly commercial and transactional terms, the searches of someone looking for a "
    "provider. No questions, no how-to or what-is searches.\n"
    "- No places: no city, suburb, state, region or country, and never 'near me'. No "
    "brand or company names, no prices, no years.\n"
    "- Use the spelling people use in the given market.\n"
    "- Short phrases: at most 5 words each, lowercase.\n"
    "- Return 5 to 10 terms, most searched first. If the industry genuinely has fewer "
    "real category terms, return only those; never pad the list.\n"
    "- Return the terms only. Never a search volume, a cost or any other number: those "
    "are measured elsewhere.\n"
    "These examples show the pattern across industries. Apply the same logic to any "
    "industry, and do not copy an example unless it is the same industry:\n"
    f"{_example_lines()}\n"
    'Return JSON: {"terms": ["...", "..."]}'
)


def build_user_prompt(
    industry: str,
    *,
    market: str | None = None,
    services: Iterable[str] = (),
) -> str:
    """The question for one industry. The services are listed so none comes back as an
    industry term; the market decides the spelling."""
    lines = [
        f"Industry: {industry}",
        f"Market: {market or 'not given'}",
    ]
    listed = [s for s in dict.fromkeys(services) if s and s.strip()]
    if listed:
        lines.append(
            "Services this business sells (researched separately, not industry terms): "
            + ", ".join(listed)
        )
    return "\n".join(lines)
