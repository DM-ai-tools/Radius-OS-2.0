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

# Seeds and keyword rows of the industry layer carry this ``seed_origin`` ("digital
# marketing agency" for an agency client). They belong to no single service.
INDUSTRY_ORIGIN = "industry"

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
        # Countries and large cities outside an Australian market. Whole-phrase
        # matched, so "us" and "uk" inside other words never fire.
        "usa",
        "united states",
        "america",
        "uk",
        "united kingdom",
        "england",
        "india",
        "canada",
        "philippines",
        "pakistan",
        "nigeria",
        "uae",
        "new zealand",
        "nz",
        "wellington",
        "christchurch",
        "chennai",
        "mumbai",
        "delhi",
        "bangalore",
        "hyderabad",
        "kolkata",
        "pune",
        "karachi",
        "lahore",
        "manila",
        "lagos",
        "texas",
        "california",
        "florida",
        "houston",
        "dallas",
        "miami",
        "atlanta",
        "boston",
        "seattle",
        "san francisco",
        "san diego",
        "phoenix",
        "denver",
        "montreal",
        "calgary",
        "glasgow",
        "leeds",
        "liverpool",
        "edinburgh",
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

# A 2–4 letter service token ("seo") also occurs inside personal names
# ("park seo joon") and dictionary lookups ("define seo"). Those are a
# different query. The token counts only when the rest of the query is
# service language, or it is the bare acronym.
_SHORT_ACRONYMS = frozenset({"seo", "sem", "ppc", "cro", "smm", "aeo", "geo"})
_ACRONYM_STOPS = frozenset({"a", "an", "the", "and", "or", "of", "to", "is", "are", "my", "your"})
# "for" keeps "seo for dentists". "with" does not: "tv shows with seo kang joon"
# is a person's name, and the connector was the only reason it stayed.
_ACRONYM_CONNECTORS = frozenset({"for"})
_DICTIONARY_LOOKUPS = frozenset(
    {
        "define",
        "definition",
        "definitions",
        "defined",
        "meaning",
        "meanings",
        "means",
        "def",
        "dictionary",
        "acronym",
        "abbreviation",
        "wikipedia",
    }
)
_ACRONYM_ON_TOPIC = _COMMERCIAL | _AU_CITIES | frozenset(
    {
        # Ways a buyer names the work: "ppc ads", "seo reseller", "ppc campaign".
        "ads",
        "advertising",
        "campaign",
        "campaigns",
        "help",
        "reseller",
        "resellers",
        "outsourcing",
        "outsourced",
        "advanced",
        "advance",
        "analysis",
        "competitor",
        "competitors",
        "coach",
        "cheapest",
        "paid",
        "social",
        "media",
        "agencies",
        "companies",
        "consultants",
        "consultancy",
        "firm",
        "firms",
        "expert",
        "experts",
        "specialists",
        "freelancer",
        "freelancers",
        "providers",
        "prices",
        "costs",
        "quote",
        "quotes",
        "packages",
        "package",
        "retainer",
        "proposal",
        "local",
        "technical",
        "audit",
        "audits",
        "onpage",
        "page",
        "pages",
        "ecommerce",
        "commerce",
        "shopify",
        "wordpress",
        "website",
        "web",
        "site",
        "sites",
        "ranking",
        "rankings",
        "rank",
        "backlink",
        "backlinks",
        "link",
        "links",
        "building",
        "google",
        "search",
        "engine",
        "keyword",
        "keywords",
        "research",
        "content",
        "organic",
        "traffic",
        "optimisation",
        "optimization",
        "optimise",
        "optimize",
        "blog",
        "sitemap",
        "schema",
        "crawl",
        "index",
        "indexing",
        "penalty",
        "algorithm",
        "serp",
        "title",
        "description",
        "ai",
        "automation",
        "best",
        "top",
        "cheap",
        "affordable",
        "professional",
        "enterprise",
        "small",
        "business",
        "international",
        "national",
        "white",
        "label",
        "monthly",
        "near",
        "me",
        "tips",
        "guide",
        "guides",
        "checklist",
        "strategies",
        "ideas",
        "examples",
        "example",
        "how",
        "what",
        "why",
        "when",
        "australia",
        "australian",
        "nsw",
        "vic",
        "qld",
        "gold",
        "coast",
        "marketing",
        "digital",
        "online",
        "services",
        "service",
        "management",
        "strategy",
        "agent",
        "agents",
        "consulting",
        "consultation",
        "localized",
        "localisation",
        "localization",
        "auditing",
        "dentist",
        "dentistry",
        "dental",
        "attorney",
        "lawyer",
        "lawyers",
        "realtor",
        "plumber",
        "plumbers",
        "freelance",
        "expertise",
        "optimised",
        "optimising",
        "optimized",
        "optimizing",
        "pack",
        "packs",
        "fee",
        "fees",
        "charge",
        "charges",
        "costing",
        "rate",
        "rates",
        "googlebot",
        "artificial",
        "intelligence",
        "spider",
        "frog",
        "screaming",
        "yoast",
        "canonical",
        "url",
        "urls",
        "internal",
        "linking",
        "meta",
        "tags",
        "tag",
        "reviews",
        "review",
        "au",
        "aus",
        "market",
        "marketers",
        "marketplace",
        "youtube",
        "video",
        "videos",
        "image",
        "images",
        "mobile",
        "desktop",
        "speed",
        "vitals",
        "copywriting",
        "copy",
        "report",
        "reports",
        "reporting",
        "tool",
        "tools",
        "software",
        "plugin",
        "plugins",
        "checker",
        "migration",
        "redirect",
        "redirects",
        "snippet",
        "snippets",
        "analytics",
        "console",
        "ctr",
        "impressions",
        "clicks",
        "product",
        "products",
        "store",
        "shop",
        "category",
        "homepage",
        "industry",
        "recovery",
        "markup",
        "hreflang",
        "robots",
        "indexed",
        "indexation",
        "copywriter",
        "copywriters",
        "tip",
        "global",
        "article",
        "articles",
        "lead",
        "leads",
        "startup",
        "onsite",
        "plan",
        "plans",
        "pagespeed",
        "guarantee",
        "guarantees",
        "quotation",
        "quotations",
        "journal",
        "similarweb",
        "hubspot",
        "blogger",
        "post",
        "posts",
        "bright",
        "edge",
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


_DICTIONARY_TOKENS = _DICTIONARY_LOOKUPS | frozenset({"definitions", "meanings", "words"})
_ENTERTAINMENT_TOKENS = frozenset(
    {
        "movie",
        "movies",
        "film",
        "films",
        "actor",
        "actors",
        "actress",
        "actresses",
        "drama",
        "dramas",
        "kdrama",
        "celebrity",
        "celebrities",
        "episode",
        "episodes",
    }
)
_ENTERTAINMENT_PHRASES = ("tv show", "tv shows", "tv series", "korean drama")


def _broken_spelling(text: str) -> bool:
    """True when "is" was stored as two letters, as in "what i s seo".

    A possessive ("what's a"), a plural ("7 p's"), a brand ("h&m"), and
    "a/b testing" are normal queries and stay.
    """
    tokens = _norm(text).split()
    return any(prev == "i" and nxt == "s" for prev, nxt in zip(tokens, tokens[1:]))


def off_topic_reason(text: str) -> str | None:
    """Why a query is unrelated to the business, or None when it may still be.

    Runs before a service phrase is allowed to keep the row. A dictionary gloss,
    a broken spelling, another language, or a film title is not a buyer.
    """
    if _acronym_homonym(text):
        return "acronym_homonym"
    raw = re.findall(r"[a-z0-9]+", _norm(text))
    if any(token in _DICTIONARY_TOKENS for token in raw) and "keyword" not in raw and "keywords" not in raw:
        return "dictionary_lookup"
    if _broken_spelling(text):
        return "broken_query"
    if "en" in raw:
        return "foreign_language"
    folded = re.findall(r"[a-z0-9]+", _norm(re.sub(r"[^a-z0-9\s]", " ", text.lower())))
    if "gi" in folded or any(ord(char) > 127 and char.lower() not in "éèêëáíóúüñ" for char in text):
        return "foreign_language"
    if "for" not in raw and (
        any(token in _ENTERTAINMENT_TOKENS for token in raw)
        or any(_has_phrase(text, phrase) for phrase in _ENTERTAINMENT_PHRASES)
    ):
        return "entertainment"
    return None


def _acronym_homonym(text: str) -> bool:
    """True when a short service acronym is a name or a dictionary lookup.

    "seo agency" and "seo for dentists" stay. "park seo joon" and "define seo"
    do not: nothing else in the query is about the service.
    """
    tokens = _norm(text).split()
    acronyms = [token for token in tokens if token in _SHORT_ACRONYMS]
    if len(acronyms) != 1:
        return False
    others = [token for token in tokens if token not in _SHORT_ACRONYMS and token not in _ACRONYM_STOPS]
    if not others:
        return False
    on_topic = [token for token in others if token in _ACRONYM_ON_TOPIC]
    if any(token in _DICTIONARY_LOOKUPS for token in others) and not on_topic:
        return True
    if on_topic or any(token in _ACRONYM_CONNECTORS for token in tokens):
        return False
    return True


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
        # "search engine" alone is someone using one ("private search engine",
        # "ai-powered search engine"); it is the service only with a marketing word.
        required_combos=(
            ("search", "engine", "optimisation"),
            ("search", "engine", "optimization"),
            ("search", "engine", "marketing"),
            ("search", "engine", "ranking"),
            ("search", "engine", "rankings"),
            ("search", "engine", "positioning"),
            ("search", "engine", "visibility"),
            ("search", "engine", "submission"),
            ("link", "building"),
        ),
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


def _cdd_long_tail(text: str, cdd_keywords: list[str]) -> bool:
    """True when a client discovery phrase sits inside the query."""
    tokens = _tokens(text)
    for raw in cdd_keywords:
        phrase = _norm(raw)
        parts = [token for token in phrase.split() if token not in _INSUFFICIENT and len(token) > 2]
        if len(parts) < 2:
            continue
        if _has_phrase(text, phrase) or all(token in tokens for token in parts):
            return True
    return False


def _names_other_family(text: str, family: ServiceFamily) -> bool:
    """True when the query is a phrase that belongs to a different service family."""
    for other in FAMILIES:
        if other.id == family.id:
            continue
        if any(_has_phrase(text, phrase) for phrase in other.markers):
            return True
        if any(_has_phrase(text, phrase) for phrase in other.required_phrases):
            return True
    return False


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
    # A service phrase must not keep a name, a gloss, a typo, another language,
    # or a film title. "search engine optimisation means" used to pass because
    # the full phrase was enough.
    unrelated = off_topic_reason(text)
    if unrelated and not cdd_hit:
        return _verdict(
            "DROP",
            unrelated,
            service_match=0.05,
            entity_risk=0.9,
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
    if _names_other_family(text, family):
        return _verdict(
            "DROP",
            "service_mismatch",
            service_match=0.05,
            entity_risk=0.4,
            ambiguity=0.1,
            negative=False,
        )
    # A longer query can describe the service without repeating its name when
    # the client's own discovery terms support it. Otherwise it stays a mismatch.
    if _cdd_long_tail(text, list(cdd_keywords or [])):
        return ServiceVerdict(
            decision="SKIP",
            family=family.id,
            reason="long_tail_for_llm",
            reason_detail="long_tail_for_llm",
            service_match=0.0,
            intent_match=_intent_score(keyword),
            geo_match=geo_score,
            entity_risk=0.0,
            ambiguity=0.4,
            negative_match=False,
        )
    return _verdict(
        "DROP",
        "service_mismatch",
        service_match=0.05,
        entity_risk=0.3,
        ambiguity=0.2,
        negative=False,
    )


# Words that name the form of a business, not what it does: "agency" in "digital
# marketing agency". A query names the industry when it holds every other word of it.
_BUSINESS_FORM_WORDS = frozenset(
    {
        "agency",
        "agencies",
        "company",
        "companies",
        "firm",
        "firms",
        "business",
        "businesses",
        "service",
        "services",
        "provider",
        "providers",
        "solution",
        "solutions",
        "industry",
        "and",
        "the",
        "of",
        "&",
    }
)


def _folded(token: str) -> str:
    """A word without its plural "s" ("clinics" -> "clinic"; "business" stays)."""
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def names_industry(keyword: str, industry: str | None) -> bool:
    """True when the query names the client's industry: it holds the industry's name, or
    every word of it that says what the business does, singular or plural ("digital
    marketing" of a "Digital Marketing Agency", "dental clinic" of "Dental Clinics")."""
    name = _norm(industry or "")
    if not name:
        return False
    if _has_phrase(keyword, name):
        return True
    core = {_folded(token) for token in name.split() if token not in _BUSINESS_FORM_WORDS}
    return bool(core) and core <= {_folded(token) for token in _tokens(keyword)}


def judge_industry_keyword(
    keyword: str,
    *,
    industry: str | None,
    services: list[str] | tuple[str, ...],
    cdd_keywords: list[str] | None = None,
    geography: str | None = None,
    seed: str | None = None,
) -> ServiceVerdict:
    """KEEP, REVIEW, DROP, or SKIP for a query of the industry layer.

    Such a query ("digital marketing agency") belongs to no one service, so it is
    judged against every service the client sells, each with ``judge_keyword``
    unchanged, and kept when any of them keeps it. Naming the industry counts as a
    service phrase and nothing more: a negative concept or entity of one of the
    client's services, a place outside the market, or an off-topic query still drops
    it. Holding its own ``seed`` (a category term the industry resolver vetted, such as
    "online marketing company" or "plumber") counts as naming the industry. Otherwise
    the most lenient service verdict stands (REVIEW, then SKIP, then the mismatch). A
    client none of whose services has a contract gets SKIP, as an unknown service does,
    unless the query names the industry.
    """
    names = [name for name in dict.fromkeys(str(s or "").strip() for s in services) if name]
    verdicts = [
        judge_keyword(keyword, service=name, cdd_keywords=cdd_keywords, geography=geography)
        for name in names
    ]
    kept = [v for v in verdicts if v.decision == "KEEP"]
    if kept:
        return max(kept, key=lambda v: v.service_match)
    hard = next((v for v in verdicts if v.decision == "DROP" and v.reason != "service_mismatch"), None)
    if hard is not None:
        return hard
    if names_industry(keyword, industry) or (seed and _has_phrase(keyword, seed)):
        geo_score, geo_reason = _geo_score(keyword, geography)
        reason = geo_reason or off_topic_reason(keyword)
        return ServiceVerdict(
            decision="DROP" if reason else "KEEP",
            family=INDUSTRY_ORIGIN,
            reason=reason or "industry_match",
            reason_detail=reason or "industry_match",
            service_match=0.1 if reason else 0.9,
            intent_match=_intent_score(keyword),
            geo_match=geo_score,
            entity_risk=0.3 if reason else 0.05,
            ambiguity=0.2 if reason else 0.1,
            negative_match=False,
            matched_positive=() if reason else (_norm(industry or ""),),
        )
    contracted = [v for v in verdicts if v.family]
    for decision in ("REVIEW", "SKIP", "DROP"):
        found = next((v for v in contracted if v.decision == decision), None)
        if found is not None:
            return found
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


def partition_service_rejects(
    rows: list[dict[str, Any]],
    *,
    cdd_keywords: list[str] | None = None,
    geography: str | None = None,
    services: list[str] | None = None,
    industry: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows the service contract can reject before an LLM call.

    A row of the industry layer (``seed_origin="industry"``) is judged against all of
    the client's ``services`` and its ``industry`` (``judge_industry_keyword``); every
    other row against its own service, as before.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        service = str(row.get("service") or row.get("target") or row.get("seed") or "")
        if row.get("seed_origin") == INDUSTRY_ORIGIN:
            verdict = judge_industry_keyword(
                str(row.get("keyword") or ""),
                industry=industry,
                services=list(services or []),
                cdd_keywords=cdd_keywords,
                geography=geography,
                seed=str(row.get("seed") or "") or None,
            )
        else:
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
