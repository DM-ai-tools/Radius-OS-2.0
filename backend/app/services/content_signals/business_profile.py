"""Signals 7, 8 and 9 — business type, size and reach.

All three read what the program already holds:

  type  — commercial_scope.business_model / b2b_b2c / industry (Phase 1 CDD)
  size  — Phase 4's scored ``company_size`` dimension (ml/tier_competitors)
  reach — resolve_geographic_focus(), which already derives location_type,
          is_local and city from the CDD's geographic_focus

An operator override in ``commercial_scope.business_profile`` wins over anything
inferred, so the UI can correct a bad guess without touching the phases.
"""

from __future__ import annotations

from typing import Any

from app.services.competitive_context import resolve_geographic_focus
from app.services.content_signals.base import SignalResult, unavailable

# --- signal 7: type of business -------------------------------------------

# Business type → the tone/compliance/CTA adjustments that actually differ.
BUSINESS_TYPES: dict[str, dict[str, Any]] = {
    "service": {
        "label": "Service business",
        "tone": "practical, reassuring, proof-led",
        "cta": "book a call, request a quote",
        "compliance": [],
    },
    "saas": {
        "label": "SaaS",
        "tone": "clear, benefit-led, low-jargon",
        "cta": "start free trial, book a demo",
        "compliance": [],
    },
    "agency": {
        "label": "Agency",
        "tone": "expert, results-led, specific",
        "cta": "request an audit, book a strategy call",
        "compliance": ["no guaranteed-ranking claims"],
    },
    "ecommerce": {
        "label": "E-commerce",
        "tone": "product-led, concrete, benefit-first",
        "cta": "shop now, add to cart, compare",
        "compliance": ["accurate pricing and availability"],
    },
    "healthcare": {
        "label": "Healthcare",
        "tone": "careful, plain-language, non-alarming",
        "cta": "book an appointment",
        "compliance": [
            "no diagnosis or treatment promises",
            "no patient testimonials implying guaranteed outcomes",
            "defer clinical claims to a qualified practitioner",
        ],
    },
    "restaurant": {
        "label": "Restaurant / hospitality",
        "tone": "warm, sensory, local",
        "cta": "book a table, order online, view menu",
        "compliance": ["allergen statements must not be invented"],
    },
    "professional_services": {
        "label": "Professional services",
        "tone": "precise, credentialed, measured",
        "cta": "arrange a consultation",
        "compliance": ["no legal or financial advice framed as advice"],
    },
    "trades": {
        "label": "Trades / home services",
        "tone": "direct, availability-led, local",
        "cta": "call now, get a quote",
        "compliance": ["licence and insurance claims must be verifiable"],
    },
}

_TYPE_ALIASES = {
    "services": "service",
    "service business": "service",
    "software": "saas",
    "software as a service": "saas",
    "b2b saas": "saas",
    "marketing agency": "agency",
    "digital marketing agency": "agency",
    "digital agency": "agency",
    "e-commerce": "ecommerce",
    "ecom": "ecommerce",
    "retail": "ecommerce",
    "online store": "ecommerce",
    "medical": "healthcare",
    "dental": "healthcare",
    "clinic": "healthcare",
    "hospitality": "restaurant",
    "cafe": "restaurant",
    "legal": "professional_services",
    "law": "professional_services",
    "accounting": "professional_services",
    "finance": "professional_services",
    "consulting": "professional_services",
    "plumbing": "trades",
    "electrical": "trades",
    "construction": "trades",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _match_type(*candidates: Any) -> str | None:
    for raw in candidates:
        key = _norm(raw)
        if not key:
            continue
        if key in BUSINESS_TYPES:
            return key
        if key in _TYPE_ALIASES:
            return _TYPE_ALIASES[key]
        # Substring pass so "Digital Marketing Agency" resolves to agency.
        for alias, mapped in _TYPE_ALIASES.items():
            if alias in key:
                return mapped
        for known in BUSINESS_TYPES:
            if known in key:
                return known
    return None


def collect_business_type(
    *,
    commercial: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    weight: str = "medium",
) -> SignalResult:
    commercial = dict(commercial or {})
    marketing = dict(marketing or {})
    profile = dict(commercial.get("business_profile") or {})
    intake = dict(marketing.get("client_intake") or {})

    override = _norm(profile.get("business_type"))
    resolved = _match_type(override) if override else None
    source = "operator override" if resolved else None

    if not resolved:
        resolved = _match_type(
            commercial.get("business_model"),
            commercial.get("industry_targeting"),
            commercial.get("inferred_industry"),
            marketing.get("industry"),
            intake.get("industry"),
        )
        source = "inferred from CDD"

    if not resolved:
        return unavailable(
            "business_type",
            "No business type in the CDD and nothing to infer it from.",
            weight=weight,
        )

    spec = BUSINESS_TYPES[resolved]
    return SignalResult(
        name="business_type",
        available=True,
        weight=weight,
        data={
            "type": resolved,
            "label": spec["label"],
            "tone": spec["tone"],
            "preferred_cta": spec["cta"],
            "compliance_notes": list(spec["compliance"]),
            "b2b_b2c": _norm(commercial.get("b2b_b2c")) or None,
            "source": source,
        },
        notes=[f"Business type: {spec['label']} ({source})."],
    )


# --- signal 8: size of business -------------------------------------------

# Phase 4 scores company_size 0-10 (ml/tier_competitors.PARAMS). Bands chosen so
# a mid-market score cannot read as enterprise.
SIZE_BANDS: list[tuple[float, str]] = [
    (2.5, "solo"),
    (5.0, "small"),
    (7.5, "mid"),
    (10.1, "enterprise"),
]

SIZE_GUIDANCE: dict[str, dict[str, Any]] = {
    "solo": {
        "label": "Solo / freelancer",
        "positioning": "personal, direct-access, specialist",
        "claims": "no team-size or scale claims; no enterprise client name-drops",
        "keyword_strategy": "long-tail, low-difficulty, niche and hyper-local terms",
        "max_keyword_difficulty": 25,
    },
    "small": {
        "label": "Small business",
        "positioning": "responsive, local, hands-on",
        "claims": "modest, verifiable; avoid 'leading' or 'number one'",
        "keyword_strategy": "long-tail and local terms; a few mid-difficulty heads",
        "max_keyword_difficulty": 40,
    },
    "mid": {
        "label": "Mid-size",
        "positioning": "established, process-led, multi-service",
        "claims": "track-record claims acceptable where evidenced",
        "keyword_strategy": "mid-difficulty heads plus supporting long-tail",
        "max_keyword_difficulty": 60,
    },
    "enterprise": {
        "label": "Enterprise",
        "positioning": "category authority, scale, governance",
        "claims": "scale and market-position claims acceptable where evidenced",
        "keyword_strategy": "competitive head terms and topical authority clusters",
        "max_keyword_difficulty": 100,
    },
}


def _band_for_score(score: float) -> str:
    for ceiling, band in SIZE_BANDS:
        if score < ceiling:
            return band
    return "enterprise"


def _client_company_size(competitive: dict[str, Any]) -> float | None:
    """Phase 4's company_size score for the CLIENT, not a competitor."""
    for key in ("client_scores", "client_scorecard", "client"):
        blob = competitive.get(key)
        if isinstance(blob, dict):
            scores = blob.get("scores") if isinstance(blob.get("scores"), dict) else blob
            raw = scores.get("company_size") if isinstance(scores, dict) else None
            if isinstance(raw, (int, float)):
                return float(raw)
    return None


def collect_business_size(
    *,
    commercial: dict[str, Any] | None = None,
    competitive: dict[str, Any] | None = None,
    weight: str = "medium",
) -> SignalResult:
    commercial = dict(commercial or {})
    competitive = dict(competitive or {})
    profile = dict(commercial.get("business_profile") or {})

    override = _norm(profile.get("business_size"))
    if override in SIZE_GUIDANCE:
        spec = SIZE_GUIDANCE[override]
        return SignalResult(
            name="business_size",
            available=True,
            weight=weight,
            data={"size": override, "source": "operator override", **spec},
            notes=[f"Business size: {spec['label']} (operator override)."],
        )

    score = _client_company_size(competitive)
    if score is None:
        return unavailable(
            "business_size",
            "Phase 4 stored no company_size score for this client "
            "(competitor analysis ran in slim mode), and no operator override is set.",
            weight=weight,
        )

    band = _band_for_score(score)
    spec = SIZE_GUIDANCE[band]
    return SignalResult(
        name="business_size",
        available=True,
        weight=weight,
        data={
            "size": band,
            "company_size_score": score,
            "source": "Phase 4 company_size score",
            **spec,
        },
        notes=[
            f"Business size: {spec['label']} (Phase 4 company_size {score:g}/10). "
            f"Keyword ceiling ~KD {spec['max_keyword_difficulty']}."
        ],
    )


# --- signal 9: reach / location model -------------------------------------

REACH_MODELS: dict[str, dict[str, Any]] = {
    "location_based": {
        "label": "Single physical location",
        "local_seo": True,
        "elements": [
            "NAP consistency (name, address, phone)",
            "LocalBusiness schema",
            "maps embed and directions copy",
            "opening hours",
        ],
        "intent": "include place names and 'near me' style phrasing",
    },
    "local": {
        "label": "Local service area",
        "local_seo": True,
        "elements": [
            "service-area list with nearby suburbs",
            "LocalBusiness schema with areaServed",
            "local proof points (jobs completed nearby)",
        ],
        "intent": "suburb and 'near me' phrasing; avoid national framing",
    },
    "city_wide": {
        "label": "City-wide",
        "local_seo": True,
        "elements": [
            "city landing page",
            "neighbourhood coverage list",
            "LocalBusiness schema with areaServed",
        ],
        "intent": "city-qualified terms plus neighbourhood variants",
    },
    "regional": {
        "label": "Multi-city / regional",
        "local_seo": True,
        "elements": [
            "one page per served city",
            "regional proof points",
            "Organization schema plus per-location LocalBusiness",
        ],
        "intent": "city-qualified terms per location; avoid one generic page",
    },
    "national": {
        "label": "National",
        "local_seo": False,
        "elements": ["Organization schema", "national proof points"],
        "intent": "unqualified head terms; do not force place names",
    },
    "online": {
        "label": "E-commerce / online-only",
        "local_seo": False,
        "elements": [
            "Product and Offer schema",
            "category and buying-guide content",
            "comparison tables",
        ],
        "intent": "transactional and comparison intent; product and category terms",
    },
}

# resolve_geographic_focus() location_type → reach model.
_GEO_TYPE_TO_REACH = {
    "city": "city_wide",
    "metro": "city_wide",
    "local": "local",
    "suburb": "local",
    "region": "regional",
    "state": "regional",
    "country": "national",
    "national": "national",
    "global": "national",
}


def collect_reach(
    *,
    commercial: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    intake: dict[str, Any] | None = None,
    business_type: str | None = None,
    weight: str = "medium",
) -> SignalResult:
    commercial = dict(commercial or {})
    profile = dict(commercial.get("business_profile") or {})

    override = _norm(profile.get("location_model"))
    if override in REACH_MODELS:
        spec = REACH_MODELS[override]
        return SignalResult(
            name="reach",
            available=True,
            weight=weight,
            data={
                "model": override,
                "source": "operator override",
                "locations_served": list(profile.get("locations_served") or []),
                **spec,
            },
            notes=[f"Reach: {spec['label']} (operator override)."],
        )

    # E-commerce implies online-only unless the operator says otherwise.
    if _norm(business_type) == "ecommerce":
        spec = REACH_MODELS["online"]
        return SignalResult(
            name="reach",
            available=True,
            weight=weight,
            data={"model": "online", "source": "implied by e-commerce business type", **spec},
            notes=["Reach: e-commerce / online-only (implied by business type)."],
        )

    geo = resolve_geographic_focus(commercial, intake or {}, marketing or {})
    raw_focus = str(geo.get("geographic_focus") or "").strip()
    if not raw_focus:
        return unavailable(
            "reach",
            "No geographic_focus in the CDD and no location_model override.",
            weight=weight,
        )

    loc_type = _norm(geo.get("location_type"))
    model = _GEO_TYPE_TO_REACH.get(loc_type)
    if model is None:
        model = "local" if geo.get("is_local") else "national"
    spec = REACH_MODELS[model]

    locations = [str(geo.get("city"))] if geo.get("city") else []
    locations += [str(x) for x in (profile.get("locations_served") or []) if str(x).strip()]

    return SignalResult(
        name="reach",
        available=True,
        weight=weight,
        data={
            "model": model,
            "source": f"derived from geographic_focus ({loc_type or 'unspecified'})",
            "geographic_focus": raw_focus,
            "location_name": geo.get("location_name"),
            "city": geo.get("city"),
            "locations_served": list(dict.fromkeys(locations)),
            **spec,
        },
        notes=[
            f"Reach: {spec['label']} from geographic_focus {raw_focus!r}."
            + (" Local SEO elements required." if spec["local_seo"] else "")
        ],
    )
