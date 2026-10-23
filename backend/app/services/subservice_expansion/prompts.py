"""The model prompt behind sub-service expansion seeds.

``EXAMPLES`` shows the model the pattern to follow. It is never read back as data: there
is no lookup by service name, and a service that appears here is asked about exactly
like any other.
"""

from __future__ import annotations

from collections.abc import Iterable

# The pattern, as the operator gave it (2026-10-12): what someone searches for WITHIN a
# service, as opposed to another name for the service itself.
EXAMPLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "SEO",
        (
            "technical seo",
            "local seo",
            "ecommerce seo",
            "on-page optimisation",
            "link building",
            "keyword research",
            "seo audit",
            "enterprise seo",
        ),
    ),
    (
        "Google Ads",
        (
            "search campaigns",
            "shopping campaigns",
            "display advertising",
            "remarketing",
            "performance max",
            "conversion tracking",
            "google ads audit",
            "google ads management",
        ),
    ),
    (
        "Social Media Marketing",
        (
            "instagram marketing",
            "facebook marketing",
            "linkedin marketing",
            "content calendar",
            "community management",
            "social media audit",
            "influencer marketing",
            "organic social",
        ),
    ),
    (
        "Web Development",
        (
            "landing page design",
            "wordpress development",
            "shopify development",
            "website redesign",
            "mobile responsive design",
            "website speed optimisation",
            "custom web application",
        ),
    ),
    (
        "Content Marketing",
        (
            "blog writing",
            "pillar page strategy",
            "content calendar",
            "case study writing",
            "whitepapers",
            "content audit",
            "thought leadership content",
        ),
    ),
    (
        "Email Marketing",
        (
            "welcome sequence",
            "abandoned cart emails",
            "newsletter design",
            "email list segmentation",
            "klaviyo setup",
            "mailchimp campaigns",
            "drip automation",
        ),
    ),
    (
        "Branding",
        (
            "logo design",
            "brand guidelines",
            "brand naming",
            "visual identity",
            "brand refresh",
            "tone of voice",
            "brand strategy",
        ),
    ),
    (
        "Paid Social",
        (
            "meta ads",
            "facebook retargeting",
            "instagram ads",
            "tiktok ads",
            "linkedin sponsored content",
            "audience targeting",
            "lookalike audiences",
        ),
    ),
)


def _example_lines() -> str:
    return "\n".join(f"{service} -> {', '.join(phrases)}" for service, phrases in EXAMPLES)


SYSTEM_PROMPT = (
    "You are an SEO keyword researcher planning keyword research for one service a "
    "business sells. List the sub-service topics that belong UNDER that service: the "
    "natural specialisations, delivery formats, audience segments and topic verticals "
    "that a potential customer would search for within it.\n"
    "How to choose:\n"
    "- Each topic is narrower than the service and clearly part of it. Never another "
    "name or synonym for the service itself (search engine optimisation is SEO, not a "
    "topic under it), and never a different service the business also sells; those are "
    "listed with the question when known.\n"
    "- Never repeat a sub-service the question already lists, and never suggest one the "
    "business has said it does not offer.\n"
    "- Customer search language: what someone types into Google when they want that "
    "part of the service. Not internal jargon, not job titles, not questions, not "
    "locations, no prices, no years, no competitor or agency names. Widely used tools "
    "and platforms are fine when people search for help with them (klaviyo setup, "
    "shopify development).\n"
    "- Fit the business's industry: a dental clinic's implant service and a marketing "
    "agency's SEO service break down differently.\n"
    "- Use the spelling people use in the given market.\n"
    "- Short phrases: at most 5 words each, lowercase.\n"
    "- Return 5 to 10 topics, most searched first, and never more than 10. If the "
    "service genuinely has fewer real sub-topics, return only those; never pad the "
    "list.\n"
    "These examples show the pattern. Apply the same logic to any service in any "
    "industry, and do not copy an example unless it is the same service:\n"
    f"{_example_lines()}\n"
    'Return JSON: {"sub_services": ["...", "..."]}'
)


def build_user_prompt(
    service: str,
    *,
    industry: str | None = None,
    market: str | None = None,
    existing: Iterable[str] = (),
    excluded: Iterable[str] = (),
    other_services: Iterable[str] = (),
) -> str:
    """The question for one service: the business context, the sub-services it already
    has (not to be repeated), the ones it does not offer, and its other services."""

    def _listed(values: Iterable[str]) -> list[str]:
        own = service.strip().lower()
        return [v for v in dict.fromkeys(str(x).strip() for x in values) if v and v.lower() != own]

    lines = [
        f"Service: {service}",
        f"Business industry: {industry or 'not given'}",
        f"Market: {market or 'not given'}",
    ]
    have = _listed(existing)
    lines.append(
        "Sub-services it already has (do not repeat): " + (", ".join(have) if have else "none")
    )
    not_offered = _listed(excluded)
    if not_offered:
        lines.append("Sub-services it does NOT offer (never suggest): " + ", ".join(not_offered))
    others = _listed(other_services)
    if others:
        lines.append("Other services this business sells (not sub-topics of this one): " + ", ".join(others))
    return "\n".join(lines)
