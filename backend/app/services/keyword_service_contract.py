"""Service-scoped relevance. A shared word is not a service match.

Broad expansion may discover candidates. This module decides whether the
whole query is about the originating service. Volume, CPC, and trend are
not inputs. Unknown services are left to the existing lexical gate
(decision SKIP) so a dental practice is not judged with an email ontology.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CLASSIFIER_VERSION = "service_contract_v2"

_NORM_RE = re.compile(r"[^a-z0-9+&]+")

# Words that appear in service names but never prove a query is about that service.
_INSUFFICIENT = frozenset(
    {
        "ai",
        "powered",
        "power",
        "and",
        "the",
        "for",
        "with",
        "management",
        "services",
        "service",
        "agency",
        "company",
        "marketing",
        "digital",
        "online",
        "solution",
        "solutions",
        "google",
        "website",
        "web",
        "optimisation",
        "optimization",
    }
)

_AU_CITIES = frozenset(
    {
        "melbourne",
        "sydney",
        "brisbane",
        "perth",
        "adelaide",
        "hobart",
        "darwin",
        "canberra",
        "geelong",
        "gold coast",
        "newcastle",
        "wollongong",
    }
)
_FOREIGN_CITIES = frozenset(
    {
        "london",
        "manchester",
        "birmingham",
        "new york",
        "nyc",
        "los angeles",
        "chicago",
        "toronto",
        "vancouver",
        "auckland",
        "dublin",
        "singapore",
        "dubai",
    }
)

_COMMERCIAL = frozenset(
    {
        "agency",
        "company",
        "services",
        "service",
        "provider",
        "consultant",
        "management",
        "strategy",
        "pricing",
        "cost",
        "hire",
        "specialist",
        "solution",
        "platform",
    }
)


@dataclass(frozen=True)
class ServiceFamily:
    id: str
    markers: tuple[str, ...]
    required_phrases: tuple[str, ...]
    required_combos: tuple[tuple[str, ...], ...]
    negative_phrases: tuple[str, ...]
    entity_phrases: tuple[str, ...]
    allowed_entities: tuple[str, ...]
    ambiguous_phrases: tuple[str, ...]


@dataclass(frozen=True)
class ServiceVerdict:
    decision: str
    family: str
    reason: str
    reason_detail: str
    service_match: float
    intent_match: float
    geo_match: float
    entity_risk: float
    ambiguity: float
    negative_match: bool
    matched_positive: tuple[str, ...] = field(default_factory=tuple)
    matched_negative: tuple[str, ...] = field(default_factory=tuple)
    classifier_version: str = CLASSIFIER_VERSION


def _norm(text: str) -> str:
    return _NORM_RE.sub(" ", str(text or "").lower()).strip()


def _padded(text: str) -> str:
    return f" {_norm(text)} "


def _has_phrase(text: str, phrase: str) -> bool:
    needle = _norm(phrase)
    if not needle:
        return False
    return f" {needle} " in _padded(text)


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t}


FAMILIES: tuple[ServiceFamily, ...] = (
    ServiceFamily(
        id="email_marketing",
        markers=("email marketing", "email automation", "email nurture"),
        required_phrases=(
            "email marketing",
            "email automation",
            "email campaign",
            "email campaigns",
            "email nurture",
            "email deliverability",
            "email personalization",
            "email personalisation",
            "ecommerce email",
            "automated email",
            "lifecycle email",
            "drip campaign",
            "newsletter marketing",
        ),
        required_combos=(
            ("email", "marketing"),
            ("email", "automation"),
            ("email", "campaign"),
            ("email", "campaigns"),
            ("email", "nurture"),
            ("email", "deliverability"),
            ("email", "personalization"),
            ("email", "personalisation"),
            ("email", "segmentation"),
            ("email", "crm"),
            ("email", "retention"),
            ("email", "lifecycle"),
            ("newsletter", "marketing"),
        ),
        negative_phrases=(
            "image generator",
            "image editor",
            "photo generator",
            "ai image",
            "ai photo",
            "humanizer",
            "humanize ai",
            "humanise ai",
            "temporary email",
            "temp email",
            "disposable",
            "gmail",
            "hotmail",
            "outlook email",
            "telstra",
            "rmit",
            "character ai",
            "google ai",
            "google gemini",
            "gemini ai",
            "ai studio",
            "poly ai",
            "our dream ai",
            "microsoft ai",
            "ask ai",
            "ai mode",
            "ai generator",
        ),
        entity_phrases=(
            "character ai",
            "google ai studio",
            "google gemini",
            "poly ai",
            "gmail",
            "hotmail",
            "telstra",
            "rmit",
        ),
        allowed_entities=("mailchimp", "klaviyo", "hubspot", "braze", "customer.io"),
        ambiguous_phrases=(
            "email services",
            "email service",
            "email platform",
            "marketing automation",
            "ai assistant",
        ),
    ),
    ServiceFamily(
        id="ppc",
        markers=("google ads", "ppc", "pay per click", "paid search", "adwords"),
        required_phrases=(
            "google ads",
            "ppc",
            "pay per click",
            "paid search",
            "adwords",
            "search ads",
            "display ads",
            "shopping ads",
        ),
        required_combos=(("google", "ads"), ("paid", "search"), ("ppc", "management")),
        negative_phrases=(
            "gmail",
            "google ai",
            "google ai studio",
            "google gemini",
            "character ai",
            "ai image",
        ),
        entity_phrases=("gmail", "google ai studio", "google gemini", "character ai"),
        allowed_entities=("google ads",),
        ambiguous_phrases=("google", "ads", "advertising"),
    ),
    ServiceFamily(
        id="cro",
        markers=(
            "conversion optimisation",
            "conversion optimization",
            "conversion rate",
            "cro",
        ),
        required_phrases=(
            "conversion optimisation",
            "conversion optimization",
            "conversion rate",
            "cro",
            "landing page",
            "a/b test",
            "ab test",
            "split test",
        ),
        required_combos=(
            ("conversion", "optimisation"),
            ("conversion", "optimization"),
            ("conversion", "rate"),
            ("landing", "page"),
        ),
        negative_phrases=("ai image", "character ai", "gmail"),
        entity_phrases=("character ai", "gmail"),
        allowed_entities=(),
        ambiguous_phrases=("website", "web design", "website design"),
    ),
    ServiceFamily(
        id="local_seo",
        markers=("local seo", "google business", "map pack"),
        required_phrases=(
            "local seo",
            "google business",
            "google business profile",
            "map pack",
            "near me",
        ),
        required_combos=(("local", "seo"), ("seo", "near"), ("google", "business")),
        negative_phrases=("character ai", "ai image", "gmail"),
        entity_phrases=("character ai",),
        allowed_entities=(),
        # City names are not service language. "melbourne zoo" shares the
        # client's city with "Local SEO Melbourne" and nothing else.
        ambiguous_phrases=("seo", "local"),
    ),
    ServiceFamily(
        id="seo",
        markers=("seo", "search engine optimisation", "search engine optimization"),
        required_phrases=(
            "seo",
            "search engine optimisation",
            "search engine optimization",
            "link building",
            "technical seo",
            "on page seo",
            "on-page seo",
            "keyword research",
        ),
        required_combos=(("search", "engine"), ("link", "building")),
        negative_phrases=(
            "character ai",
            "google ai studio",
            "ai image generator",
            "ai image",
            "gmail",
            "humanizer",
            "humanize ai",
        ),
        entity_phrases=("character ai", "google ai studio", "gmail"),
        allowed_entities=(),
        ambiguous_phrases=("ai", "marketing", "google"),
    ),
)


def family_for_service(service: str) -> ServiceFamily | None:
    name = _norm(service)
    if not name:
        return None
    for family in FAMILIES:
        if any(_has_phrase(name, marker) or marker in name.split() for marker in family.markers):
            # "cro" as a marker must be a token, not a substring of another word.
            if family.id == "cro" and not ({"cro"} & _tokens(name) or "conversion" in name):
                continue
            if family.id == "seo" and "local seo" in name:
                continue
            if family.id == "ppc" and not any(
                m in name for m in ("google ads", "ppc", "pay per click", "paid search", "adwords")
            ):
                continue
            return family
    return None


def _scope(geography: str | None) -> str:
    geo = _norm(geography or "")
    if not geo:
        return "unspecified"
    if any(_has_phrase(geo, token) for token in ("australia wide", "nationwide", "national")):
        return "australia"
    if "australia" in geo and not any(city in geo for city in _AU_CITIES):
        return "australia"
    if "australia" in geo and any(city in geo for city in _AU_CITIES):
        # "Melbourne, Australia" with no national wording is city-priority.
        if "wide" in geo or "national" in geo:
            return "australia"
        cities = [city for city in _AU_CITIES if city in geo]
        if len(cities) == 1:
            return cities[0]
        return "australia"
    cities = [city for city in _AU_CITIES if city in geo]
    if len(cities) == 1:
        return cities[0]
    return "custom"


def _geo_score(keyword: str, geography: str | None) -> tuple[float, str | None]:
    """Return (score, mismatch reason). None reason means no hard geo reject."""
    scope = _scope(geography)
    text = _norm(keyword)
    foreign = next((city for city in _FOREIGN_CITIES if _has_phrase(text, city)), None)
    if foreign and scope in ("australia", *_AU_CITIES):
        return 0.0, "geo_mismatch"
    if scope in _AU_CITIES:
        other = next(
            (city for city in _AU_CITIES if city != scope and _has_phrase(text, city)),
            None,
        )
        if other:
            return 0.1, "geo_mismatch"
        if _has_phrase(text, scope):
            return 1.0, None
        if "australia" in text:
            return 0.8, None
    if scope == "australia":
        if any(_has_phrase(text, city) for city in _AU_CITIES) or "australia" in text:
            return 1.0, None
    return 0.7, None


def _cdd_supports(keyword: str, family: ServiceFamily, cdd_keywords: list[str]) -> str | None:
    for raw in cdd_keywords:
        phrase = _norm(raw)
        if len(phrase.split()) < 2:
            continue
        if not _has_phrase(keyword, phrase):
            continue
        if any(_has_phrase(phrase, required) for required in family.required_phrases):
            return phrase
        if any(all(token in _tokens(phrase) for token in combo) for combo in family.required_combos):
            return phrase
    return None


def _intent_score(keyword: str) -> float:
    tokens = _tokens(keyword)
    if tokens & {"how", "what", "why", "guide", "tips", "strategy"}:
        return 0.7
    if tokens & _COMMERCIAL:
        return 0.95
    return 0.5


def judge_keyword(
    keyword: str,
    *,
    service: str,
    cdd_keywords: list[str] | None = None,
    geography: str | None = None,
) -> ServiceVerdict:
    """KEEP, REVIEW, DROP, or SKIP when this service has no contract."""
    family = family_for_service(service)
    if family is None:
        return ServiceVerdict(
            decision="SKIP",
            family="",
            reason="no_service_contract",
            reason_detail="",
            service_match=0.0,
            intent_match=0.0,
            geo_match=0.0,
            entity_risk=0.0,
            ambiguity=0.0,
            negative_match=False,
        )

    text = _norm(keyword)
    negatives = tuple(phrase for phrase in family.negative_phrases if _has_phrase(text, phrase))
    entities = tuple(phrase for phrase in family.entity_phrases if _has_phrase(text, phrase))
    allowed = any(_has_phrase(text, phrase) for phrase in family.allowed_entities)
    positives = tuple(phrase for phrase in family.required_phrases if _has_phrase(text, phrase))
    combo_hit = any(all(token in _tokens(text) for token in combo) for combo in family.required_combos)
    cdd_hit = _cdd_supports(keyword, family, list(cdd_keywords or []))
    geo_score, geo_reason = _geo_score(keyword, geography)

    def _verdict(
        decision: str,
        reason: str,
        *,
        service_match: float,
        entity_risk: float,
        ambiguity: float,
        negative: bool,
    ) -> ServiceVerdict:
        return ServiceVerdict(
            decision=decision,
            family=family.id,
            reason=reason,
            reason_detail=reason,
            service_match=service_match,
            intent_match=_intent_score(keyword),
            geo_match=geo_score,
            entity_risk=entity_risk,
            ambiguity=ambiguity,
            negative_match=negative,
            matched_positive=positives or ((cdd_hit,) if cdd_hit else ()),
            matched_negative=negatives or entities,
        )

    if negatives or (entities and not allowed):
        return _verdict(
            "DROP",
            "negative_concept" if negatives else "entity_mismatch",
            service_match=0.05,
            entity_risk=0.95 if entities else 0.4,
            ambiguity=0.1,
            negative=bool(negatives),
        )
    if geo_reason:
        return _verdict(
            "DROP",
            "geo_mismatch",
            service_match=0.4 if positives or combo_hit else 0.1,
            entity_risk=0.1,
            ambiguity=0.2,
            negative=False,
        )
    if positives or combo_hit or cdd_hit:
        return _verdict(
            "KEEP",
            "service_match",
            service_match=0.95 if positives or cdd_hit else 0.85,
            entity_risk=0.05,
            ambiguity=0.1,
            negative=False,
        )
    # A place name is not an ambiguous service term. "melbourne zoo" must not
    # be held for review just because the service is delivered in Melbourne.
    place_names = _AU_CITIES | _FOREIGN_CITIES | frozenset({"australia"})
    ambiguous_hits = [
        phrase
        for phrase in family.ambiguous_phrases
        if phrase not in place_names and (_has_phrase(text, phrase) or text == phrase)
    ]
    if ambiguous_hits:
        return _verdict(
            "REVIEW",
            "ambiguous_query",
            service_match=0.35,
            entity_risk=0.2,
            ambiguity=0.8,
            negative=False,
        )
    # A single insufficient token ("ai", "email", "google") is not the service.
    return _verdict(
        "DROP",
        "service_mismatch",
        service_match=0.05,
        entity_risk=0.3,
        ambiguity=0.2,
        negative=False,
    )


def partition_service_rejects(
    rows: list[dict[str, Any]],
    *,
    cdd_keywords: list[str] | None = None,
    geography: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows the service contract can reject before an LLM call."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        service = str(row.get("target") or row.get("seed") or "")
        verdict = judge_keyword(
            str(row.get("keyword") or ""),
            service=service,
            cdd_keywords=cdd_keywords,
            geography=geography,
        )
        if verdict.decision == "DROP":
            dropped.append(
                {
                    **row,
                    "relevance_drop_reason": verdict.reason,
                    "classifier_version": CLASSIFIER_VERSION,
                    "service_match_score": verdict.service_match,
                    "entity_risk_score": verdict.entity_risk,
                    "originating_service": service,
                }
            )
            continue
        if verdict.decision == "REVIEW":
            kept.append(
                {
                    **row,
                    "relevance_reason": "service_review",
                    "commercial_target_eligible": False,
                    "classifier_version": CLASSIFIER_VERSION,
                }
            )
            continue
        kept.append(row)
    return kept, dropped
