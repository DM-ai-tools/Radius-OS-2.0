"""The model prompt behind synonym seeds.

``EXAMPLES`` shows the model the pattern to follow. It is never read back as data: there
is no lookup by service name, and a service that appears here is asked about exactly
like any other.
"""

from __future__ import annotations

from collections.abc import Iterable

# The pattern, as the operator gave it (2026-10-13): genuinely different names people use
# for the same service, across industries.
EXAMPLES_BY_INDUSTRY: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (
        "Digital marketing",
        (
            ("SEO", ("search engine optimisation", "organic search", "google ranking", "search visibility")),
            ("Google Ads", ("ppc advertising", "pay per click", "paid search", "adwords")),
            (
                "Social Media Marketing",
                ("social media management", "social media advertising", "social media strategy"),
            ),
            ("Web Development", ("website design", "web design", "website development", "site development")),
            ("Email Marketing", ("email campaigns", "email automation", "email newsletters", "edm marketing")),
        ),
    ),
    (
        "Legal",
        (
            ("Family Law", ("divorce law", "matrimonial law", "family legal services", "separation law")),
            ("Criminal Law", ("criminal defence", "criminal litigation", "criminal legal representation")),
            ("Conveyancing", ("property transfer", "property settlement", "property law", "settlement services")),
            ("Immigration Law", ("visa law", "migration law", "immigration legal services", "residency law")),
        ),
    ),
    (
        "Finance",
        (
            ("Tax Accounting", ("taxation services", "tax advisory", "tax services", "tax preparation")),
            ("Mortgage Broking", ("home loan broking", "mortgage lending", "home loan advice", "mortgage advisory")),
            (
                "Financial Planning",
                ("wealth management", "financial advisory", "investment planning", "financial advice"),
            ),
            (
                "Bookkeeping",
                ("accounts management", "financial record keeping", "accounting services", "business accounting"),
            ),
        ),
    ),
    (
        "Healthcare",
        (
            ("Physiotherapy", ("physical therapy", "physio treatment", "movement therapy", "rehabilitation therapy")),
            ("Dentistry", ("dental care", "oral health", "dental treatment", "teeth care")),
            ("Psychology", ("mental health therapy", "counselling", "psychotherapy", "talking therapy")),
            ("Chiropractic", ("spinal care", "chiropractic treatment", "spinal adjustment", "joint therapy")),
        ),
    ),
    (
        "Trades",
        (
            ("Plumbing", ("pipe work", "plumbing repairs", "drain services", "water system services")),
            ("Electrical", ("electrical services", "wiring services", "power installation", "electrical repairs")),
            ("Landscaping", ("garden design", "outdoor landscaping", "garden services", "yard maintenance")),
            (
                "Building & Renovation",
                ("home renovation", "building services", "construction services", "property renovation"),
            ),
        ),
    ),
    (
        "Education",
        (
            ("Tutoring", ("private tuition", "academic coaching", "private lessons", "learning support")),
            ("Personal Training", ("fitness coaching", "personal coaching", "fitness training", "exercise coaching")),
            (
                "Corporate Training",
                ("workplace training", "staff development", "professional training", "employee development"),
            ),
        ),
    ),
    (
        "Real estate",
        (
            (
                "Property Management",
                (
                    "rental management",
                    "landlord services",
                    "tenancy management",
                    "investment property management",
                ),
            ),
            (
                "Buyers Agent",
                ("buyers advocate", "property advocate", "home buying service", "property acquisition service"),
            ),
        ),
    ),
    (
        "Hospitality",
        (
            ("Catering", ("food catering", "event catering", "catering services", "catering solutions")),
            ("Event Management", ("event planning", "event coordination", "event organisation", "event services")),
        ),
    ),
)

# Flat view: (service, phrases) for every example.
EXAMPLES: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    pair for _industry, pairs in EXAMPLES_BY_INDUSTRY for pair in pairs
)


def _example_lines() -> str:
    lines: list[str] = []
    for industry, pairs in EXAMPLES_BY_INDUSTRY:
        lines.append(f"{industry}:")
        lines.extend(f"  {service} -> {', '.join(phrases)}" for service, phrases in pairs)
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You are an SEO keyword researcher. You are given one service a business sells and "
    "the business's industry. Different people search for the same service using "
    "completely different terms. List the genuinely different names people use for this "
    "same service: alternate names, common synonyms, and the full form of an "
    "abbreviation.\n"
    "How to choose:\n"
    "- Each name means the same service, but is worded differently enough that a "
    "keyword tool treats it as a distinct search. A plural, a singular or a reordering "
    "of the service name is not a different name.\n"
    "- Not a qualifier: never the service name with company, agency, firm, near me, "
    "best, top, cheap, affordable or professional added (seo company, plumber near me "
    "and best seo are not alternate names). A one-word service may be named with "
    "services or solutions (electrical services, catering services). A service that "
    "already has its own words must not get services stuck on the end: email marketing "
    "services and social media marketing services are the same search as the service "
    "name. An abbreviation is the bare short form (smm), not smm services.\n"
    "- Not a sub-service or a narrower specialisation (technical seo is part of SEO, not "
    "another name for it; email campaign management is part of email marketing, not "
    "another name for it). social media management is another name for social media "
    "marketing, because people use it for the whole service. Never return a different "
    "service the business also sells; those are listed with the question when known.\n"
    "- Customer language: what a potential customer types into Google. Not internal "
    "jargon, not over-broad terms that cover far more than this service.\n"
    "- Only names with real search volume potential. Nothing invented, no slogans, no "
    "questions, no locations, no brand or company names, no prices, no years.\n"
    "- Expand an abbreviation using the meaning that fits the business's industry: CRO "
    "is conversion rate optimisation for a marketing agency but contract research "
    "organisation for a pharmaceutical company.\n"
    "- Use the spelling people use in the given market.\n"
    "- Short phrases: at most 5 words each, lowercase.\n"
    "- Return 4 to 6 names, most searched first, and never more than 6. If the service "
    "genuinely has fewer real alternate names, return only those; never pad the list.\n"
    "These examples show the pattern across industries. Apply the same logic to any "
    "service in any industry, and do not copy an example unless it is the same "
    "service:\n"
    f"{_example_lines()}\n"
    'Return JSON: {"synonyms": ["...", "..."]}'
)


def build_user_prompt(
    service: str,
    *,
    industry: str | None = None,
    market: str | None = None,
    other_services: Iterable[str] = (),
) -> str:
    """The question for one service. The industry decides what an abbreviation stands
    for; the business's other services are listed so none comes back as a synonym."""
    lines = [
        f"Service: {service}",
        f"Business industry: {industry or 'not given'}",
        f"Market: {market or 'not given'}",
    ]
    others = [s for s in dict.fromkeys(other_services) if s and s.strip().lower() != service.strip().lower()]
    if others:
        lines.append(
            "Other services this business sells (these are not synonyms): " + ", ".join(others)
        )
    return "\n".join(lines)
