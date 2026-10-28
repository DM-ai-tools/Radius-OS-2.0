"""Two-tier keyword exclusions — the single place term lists live.

Tier 1, HARD_REJECT: the searcher is not a customer of any business in this
position. A job seeker, a student, someone looking for a login page. These are
dropped outright.

Tier 2, REROUTE_INFORMATIONAL: a real potential customer asking a question that
is not a buying question — "how do I do this myself". Worth content, never
worth pointing a commercial/service page at. These are kept and flagged
``commercial_target_eligible=False``.

Two rules that keep this honest:

* Matching is on whole tokens and whole phrases. "jobs" must not fire inside
  "jobsite", "diy" must not fire inside "diyanet".
* "free" is NOT universal. "free trial" is the core funnel of a SaaS business
  and "free consultation" is a legitimate lead magnet in any service vertical,
  while "free teeth whitening" is a bargain hunter. It lives in the per-industry
  table, with an explicit always-allowed phrase list on top.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# --- tier 1 ---------------------------------------------------------------

HARD_REJECT_TERMS: frozenset[str] = frozenset(
    {
        # employment
        "job",
        "jobs",
        "salary",
        "salaries",
        "career",
        "careers",
        "hiring",
        "internship",
        "internships",
        "vacancy",
        "vacancies",
        "resume",
        "resumes",
        "recruitment",
        "apprenticeship",
        # account / reference, not a buyer
        "login",
        "sign in",
        "signin",
        "wiki",
        "wikipedia",
        # education seekers — someone training to ENTER the trade, not buy from it.
        # Added beyond the original spec list because "dental school admission"
        # is a MUST_REJECT case that no employment term catches.
        "school",
        "schools",
        "admission",
        "admissions",
        "syllabus",
        "curriculum",
        "scholarship",
        "scholarships",
        "student",
        "students",
    }
)

# --- tier 2 ---------------------------------------------------------------

REROUTE_TERMS: frozenset[str] = frozenset(
    {
        "diy",
        "how to",
        "homemade",
        "at home",
        "recipe",
        "recipes",
        "course",
        "courses",
        "training",
        "certification",
        "certificate",
        "definition",
        "meaning",
        "what is",
        "what are",
        "tutorial",
        "tutorials",
    }
)

# "reviews" is only informational when it is about a product or appliance the
# client does not sell. "teeth whitening reviews" is a buyer checking a service.
REVIEW_TERMS: frozenset[str] = frozenset({"review", "reviews"})
PRODUCT_NOUNS: frozenset[str] = frozenset(
    {
        "maker",
        "machine",
        "machines",
        "mixer",
        "mixers",
        "oven",
        "ovens",
        "appliance",
        "appliances",
        "kit",
        "kits",
        "tool",
        "tools",
        "device",
        "devices",
        "gadget",
        "gadgets",
        "equipment",
        "model",
        "models",
        "brand",
        "brands",
        "software",
        "app",
        "apps",
    }
)

# --- consumer AI / mailbox products --------------------------------------
#
# A seed like "AI-Powered Email Marketing" shares one token with the entire
# consumer AI universe. On a real Click Trends run that single overlap pulled in
# "ai girlfriend" (12,100/mo), "ai clothing remover" (8,100), "spicy ai",
# "faceswap ai", "undetected ai" and "ai music generator" — plus "free email
# services", which is somebody shopping for a mailbox, not for email marketing.
#
# These are multi-word PHRASES, never the bare token "ai", so genuine service
# terms ("ai email marketing", "ai lead scoring") are untouched. Anything
# ambiguous is left to the per-keyword LLM gate.
CONSUMER_AI_TERMS: frozenset[str] = frozenset(
    {
        # adult / companion toys
        "ai girlfriend",
        "ai boyfriend",
        "ai companion",
        "spicy ai",
        "nsfw ai",
        "ai clothing remover",
        "clothes remover",
        "undress ai",
        "faceswap ai",
        "face swap ai",
        "deepfake",
        # academic-integrity tooling — the searcher is a student, not a buyer
        "ai detection",
        "ai detector",
        "ai detectors",
        "ai detection tool",
        "ai checker",
        "ai checkers",
        "undetected ai",
        "originality ai",
        "humanise ai",
        "humanize ai",
        "ai humaniser",
        "ai humanizer",
        "plagiarism checker",
        # consumer generative media
        "ai image generator",
        "ai photo generator",
        "ai picture generator",
        "ai video generator",
        "ai music generator",
        "ai song generator",
        "ai art generator",
        "ai generated images",
        "ai generated image",
        "ai voice generator",
    }
)

# Consumer mailbox shopping — a free/secure inbox, not an email-marketing buyer.
CONSUMER_MAILBOX_TERMS: frozenset[str] = frozenset(
    {
        "free email services",
        "free email service",
        "free email account",
        "free email accounts",
        "secure email services",
        "secure email service",
        "email provider",
        "email providers",
        "webmail",
        "temp mail",
        "temporary email",
    }
)

# Third-party AI product and assistant names. Informational at best for a
# marketing agency and never a commercial page target, so these reroute rather
# than hard-reject: a "how X works" post can be legitimate, a service page
# ranking for someone else's brand cannot.
THIRD_PARTY_AI_BRANDS: frozenset[str] = frozenset(
    {
        "google ai",
        "google gemini",
        "gemini ai",
        "meta ai",
        "microsoft ai",
        "copilot",
        "chatgpt",
        "chat gpt",
        "deepseek",
        "deepseek ai",
        "character ai",
        "poly ai",
        "duck ai",
        "blackbox ai",
        "viggle ai",
        "turbo ai",
        "lovable ai",
        "claude ai",
        "grok ai",
        "perplexity ai",
    }
)

# --- per-industry overrides ----------------------------------------------

# industry slug -> extra reroute terms for that vertical only.
INDUSTRY_REROUTE_TERMS: dict[str, frozenset[str]] = {
    "dental": frozenset({"free"}),
    "bakery": frozenset({"free"}),
    "saas": frozenset(),  # "free trial" / "free tier" are core funnel terms
}

# industry slug -> extra hard-reject terms for that vertical only.
INDUSTRY_HARD_REJECT_TERMS: dict[str, frozenset[str]] = {}

# Phrases that stay allowed no matter the industry table.
ALWAYS_ALLOWED_PHRASES: frozenset[str] = frozenset(
    {
        "free consultation",
        "free quote",
        "free estimate",
        "free trial",
    }
)

# Substring probes used to derive an industry slug from free-text industry.
_INDUSTRY_SLUG_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dental", ("dental", "dentist", "dentistry", "orthodont", "endodont")),
    ("bakery", ("bakery", "baker", "patisserie", "pastry", "bread")),
    ("saas", ("saas", "software as a service", "b2b software", "software platform")),
)

# --- commercial signals (second gate signal) ------------------------------

COMMERCIAL_SIGNAL_TERMS: frozenset[str] = frozenset(
    {
        "price",
        "prices",
        "pricing",
        "cost",
        "costs",
        "quote",
        "quotes",
        "buy",
        "order",
        "book",
        "booking",
        "appointment",
        "appointments",
        "hire",
        "near me",
        "open",
        "opening",
        "delivery",
        "deliver",
        "same day",
        "emergency",
        "affordable",
        "cheap",
        "best",
        "top",
        "service",
        "services",
        "company",
        "companies",
        # "company" was here but "agency" was not, so "facebook ads agency" had
        # no commercial signal while "fujifilm company" did — that asymmetry kept
        # the junk and dropped a real keyword. These nouns are NOT service
        # identity (see _NON_DISTINCTIVE in keyword_relevance.py) but they ARE
        # commercial intent: "<service> agency" is a buyer query.
        "agency",
        "agencies",
        "consultancy",
        "firm",
        "firms",
        "expert",
        "experts",
        "providers",
        "freelancer",
        "specialist",
        "specialists",
        "consultant",
        "consultants",
        "shop",
        "store",
        "online",
        "clinic",
        "provider",
    }
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))


def _contains_term(keyword: str, term: str) -> bool:
    """Whole-token / whole-phrase containment. Never a substring match."""
    kw_tokens = _tokens(keyword)
    term_tokens = _tokens(term)
    if not kw_tokens or not term_tokens:
        return False
    if len(term_tokens) == 1:
        return term_tokens[0] in kw_tokens
    span = len(term_tokens)
    return any(
        kw_tokens[i : i + span] == term_tokens
        for i in range(len(kw_tokens) - span + 1)
    )


def industry_slug(industry: str | None) -> str | None:
    """Coarse slug for an industry free-text string, or None when unknown."""
    text = _norm(industry)
    if not text:
        return None
    for slug, probes in _INDUSTRY_SLUG_PROBES:
        if any(probe in text for probe in probes):
            return slug
    return None


def _is_always_allowed(keyword: str) -> bool:
    return any(_contains_term(keyword, phrase) for phrase in ALWAYS_ALLOWED_PHRASES)


def hard_reject_term(keyword: str, *, industry: str | None = None) -> str | None:
    """The hard-reject term this keyword contains, or None."""
    kw = _norm(keyword)
    if not kw:
        return None
    terms = set(HARD_REJECT_TERMS) | set(CONSUMER_AI_TERMS) | set(CONSUMER_MAILBOX_TERMS)
    slug = industry_slug(industry)
    if slug:
        terms |= set(INDUSTRY_HARD_REJECT_TERMS.get(slug, frozenset()))
    # Longest first so "sign in" is reported over a bare token.
    for term in sorted(terms, key=lambda t: (-len(t.split()), -len(t))):
        if _contains_term(kw, term):
            return term
    return None


def reroute_term(
    keyword: str,
    *,
    industry: str | None = None,
    service_terms: Iterable[str] | None = None,
) -> str | None:
    """The informational-reroute term this keyword contains, or None.

    ``service_terms`` are the client's own services — a "reviews" query about
    something the client actually sells is a buyer query, not a reroute.
    """
    kw = _norm(keyword)
    if not kw:
        return None
    if _is_always_allowed(kw):
        return None

    terms = set(REROUTE_TERMS) | set(THIRD_PARTY_AI_BRANDS)
    slug = industry_slug(industry)
    if slug:
        terms |= set(INDUSTRY_REROUTE_TERMS.get(slug, frozenset()))

    for term in sorted(terms, key=lambda t: (-len(t.split()), -len(t))):
        if _contains_term(kw, term):
            return term

    # "reviews" only when paired with a product/appliance noun the client
    # does not sell.
    if any(_contains_term(kw, term) for term in REVIEW_TERMS):
        own = {_norm(s) for s in (service_terms or []) if _norm(s)}
        if any(_contains_term(kw, service) for service in own):
            return None
        if any(_contains_term(kw, noun) for noun in PRODUCT_NOUNS):
            return "product_reviews"
    return None


def is_commercially_targetable(
    keyword: str,
    *,
    industry: str | None = None,
    service_terms: Iterable[str] | None = None,
) -> bool:
    """False when a keyword must never back a commercial/service page."""
    return (
        reroute_term(keyword, industry=industry, service_terms=service_terms) is None
    )


def has_commercial_signal(keyword: str) -> bool:
    """Commercial / transactional / local buying signal in the keyword itself."""
    kw = _norm(keyword)
    if not kw:
        return False
    return any(_contains_term(kw, term) for term in COMMERCIAL_SIGNAL_TERMS)


# --- buyer fit (2026-10-22) -------------------------------------------------
#
# Tier 1 says who is not a customer of ANY business. Buyer fit says who cannot
# become a customer of THIS kind of business, even though the query names its
# service: someone after a free checker, a course, a template, the platform's
# own login page, or a page in another language. On the stored Click Trends run
# these made up a large share of the kept keywords ("cek seo website", "check
# google ranking", "best email marketing software", "google keyword planner",
# "advanced search on google"). The test is the one SEO practitioners use for
# business potential: could the client pitch its service to this searcher?
#
# Each list is whole-word / whole-phrase matched, like the tiers above.

# The searcher wants a tool or a piece of software, not someone to do the work.
TOOL_SEEKER_TERMS: frozenset[str] = frozenset(
    {
        "checker",
        "checkers",
        "check my",
        "check your",
        "check site",
        "check website",
        "check google",
        "check keyword",
        "check keywords",
        "check ranking",
        "check rankings",
        "check rank",
        "checking google",
        "rank checker",
        "ranking checker",
        "tool",
        "tools",
        "generator",
        "generators",
        "analyzer",
        "analyser",
        "grader",
        "calculator",
        "tester",
        "speed test",
        "results test",
        "seo test",
        # Free rank / deliverability checkers: "google rank test", "google local ranking check".
        "ranking test",
        "rank test",
        "ranking check",
        "rank check",
        "deliverability test",
        "test my",
        "web test",
        "website test",
        "site test",
        "test website",
        "test site",
        "free download",
        "download",
        "apk",
    }
)
# Named products. A services agency does not sell the tool, so the brand is
# enough on its own: "screaming frog seo spider" is the crawler, not a buyer.
TOOL_BRAND_TERMS: frozenset[str] = frozenset(
    {
        "screaming frog",
        "screaming spider",
        "seo spider",
        "semrush",
        "ahrefs",
        "moz",
        "ubersuggest",
        "yoast",
        "rank math",
    }
)
# "test website for seo" and "seo website testing" are a free checker.
# "seo testing services" still asks for the agency.
_SITE_TEST_TERMS: frozenset[str] = frozenset(
    {
        "website testing",
        "seo testing",
        "site testing",
        "testing website",
        "testing site",
    }
)
# Product nouns that are a tool search on their own but a SERVICE search next to
# a provider or build word: "email marketing software" vs "software development
# company", "wordpress plugin" vs "plugin development".
TOOL_PRODUCT_TERMS: frozenset[str] = frozenset(
    {
        "software",
        "plugin",
        "plugins",
        "extension",
        "extensions",
        "app",
        "apps",
        "builder",
        "builders",
        "check",
        "checking",
        "checkup",
    }
)
_BUILD_OR_PROVIDER_WORDS: frozenset[str] = frozenset(
    {
        "development",
        "developer",
        "developers",
        "develop",
        "engineering",
        "custom",
        "agency",
        "agencies",
        "company",
        "companies",
        "consultant",
        "consultants",
        "consultancy",
        "consulting",
        "services",
        "service",
        "management",
        "expert",
        "experts",
        "specialist",
        "specialists",
        "freelancer",
        "hire",
        "integration",
        "implementation",
        "setup",
    }
)

# Learning material: someone studying the subject, not buying the service.
LEARNING_RESOURCE_TERMS: frozenset[str] = frozenset(
    {
        "course",
        "courses",
        "training",
        "certification",
        "certifications",
        "certificate",
        "certified",
        "tutorial",
        "tutorials",
        "template",
        "templates",
        "example",
        "examples",
        "sample",
        "samples",
        "pdf",
        "ppt",
        "ebook",
        "books",
        "cheat sheet",
        "for dummies",
        "for beginners",
        "quiz",
        "interview questions",
        "definition",
        "meaning",
        "full form",
        "stand for",
        "stands for",
        "abbreviation",
        "glossary",
        "notes",
        "assignment",
        "thesis",
    }
)
# Businesses that sell learning themselves keep these.
_LEARNING_SELLER_WORDS: frozenset[str] = frozenset(
    {"training", "course", "courses", "education", "school", "academy", "tutoring", "coaching", "university"}
)
_TOOL_SELLER_WORDS: frozenset[str] = frozenset(
    {"software", "saas", "app", "apps", "platform", "tool", "tools", "plugin", "extension"}
)

# Third-party platforms. A query that is just the platform (plus a navigation
# word) is someone going to that product, not hiring help with it: "google
# ads" (the advertiser login), "google keyword planner", "mailchimp".
PLATFORM_TERMS: frozenset[str] = frozenset(
    {
        "google ads",
        "google adwords",
        "adwords",
        "google keyword planner",
        "keyword planner",
        "google search console",
        "search console",
        "google analytics",
        "google tag manager",
        "tag manager",
        "google trends",
        "google merchant center",
        "google my business",
        "google business profile",
        "meta ads manager",
        "facebook ads manager",
        "ads manager",
        "business manager",
        "microsoft ads",
        "bing ads",
        "mailchimp",
        "klaviyo",
        "hubspot",
        "constant contact",
        "brevo",
        "sendinblue",
        "activecampaign",
        "convertkit",
        "campaign monitor",
        "wix",
        "squarespace",
        "wordpress",
        "shopify",
        "godaddy",
        "webflow",
        "elementor",
        "canva",
        "figma",
        "semrush",
        "ahrefs",
        "moz",
        "ubersuggest",
        "yoast",
        "rank math",
        "screaming frog",
        "screaming spider",
        "seo spider",
        "gmail",
        "outlook",
        "bing",
        "yahoo",
        "duckduckgo",
        "chatgpt",
        "perplexity",
        "gemini",
        "copilot",
        "google",
    }
)
# Words that only take the searcher to the platform itself.
_PLATFORM_NAV_WORDS: frozenset[str] = frozenset(
    {
        "login",
        "log",
        "in",
        "sign",
        "up",
        "account",
        "accounts",
        "support",
        "help",
        "contact",
        "phone",
        "number",
        "customer",
        "dashboard",
        "app",
        "download",
        "api",
        "console",
        "manager",
        "editor",
        "status",
        "outage",
        "free",
        "online",
        "website",
        "site",
        "official",
        "home",
        "page",
        "homepage",
        "com",
        "au",
        "new",
        "my",
        "the",
        "a",
        "an",
    }
)

# Someone using a search engine, not marketing on one.
CONSUMER_SEARCH_TERMS: frozenset[str] = frozenset(
    {
        "advanced search",
        "reverse search",
        "reverse image search",
        "reverse google search",
        "image search",
        "private search",
        "private search engine",
        "safe search",
        "safesearch",
        "search history",
        "incognito",
        "search bar",
        "search in website",
        "site search",
        "search engines list",
        "list of search engines",
        "best search engine",
        "best search engines",
        "ai search engine",
        "ai search engines",
        "ai powered search",
        "ai-powered search",
        "ai-powered search engine",
    }
)
# A bare "search engine" / "ai search" query is consumer use unless one of these
# marketing words turns it into the service.
_SEARCH_MARKETING_WORDS: frozenset[str] = frozenset(
    {
        "seo",
        "optimisation",
        "optimization",
        "optimise",
        "optimize",
        "optimising",
        "optimizing",
        "marketing",
        "ranking",
        "rankings",
        "rank",
        "visibility",
        "agency",
        "agencies",
        "ads",
        "advertising",
        "ppc",
        "sem",
        "console",
        "positioning",
        "submission",
        "traffic",
    }
)
_CONSUMER_SEARCH_HEADS: tuple[str, ...] = ("search engine", "search engines", "ai search", "google search")

# Words that are not English and show up in DataForSEO's pool for English
# markets ("cek seo website" is Indonesian for "check seo website").
FOREIGN_LANGUAGE_TERMS: frozenset[str] = frozenset(
    {
        "cek",
        "jasa",
        "harga",
        "murah",
        "terbaik",
        "situs",
        "toko",
        "kursus",
        "belajar",
        "cara",
        "gratis",
        "kostenlos",
        "agentur",
        "empresa",
        "agencia",
        "posicionamiento",
        "pagina",
        "paginas",
        "sitio",
        "criar",
        "fazer",
        "referencement",
        "agence",
        "entreprise",
        "diseno",
        "desarrollo",
        "tienda",
        "kaise",
        "kya",
        "sitesi",
    }
)
_ENGLISH_MARKETS: tuple[str, ...] = (
    "australia",
    "new zealand",
    "united kingdom",
    "united states",
    "usa",
    "canada",
    "ireland",
    "melbourne",
    "sydney",
    "brisbane",
    "perth",
    "adelaide",
    "canberra",
    "hobart",
    "darwin",
    "gold coast",
    "london",
    "england",
)

_PRICE_WORDS: frozenset[str] = frozenset({"price", "prices", "pricing", "cost", "costs", "quote", "quotes"})
_LOCAL_WORDS: frozenset[str] = frozenset(
    {
        "near me",
        "melbourne",
        "sydney",
        "brisbane",
        "perth",
        "adelaide",
        "canberra",
        "hobart",
        "darwin",
        "gold coast",
        "geelong",
        "newcastle",
        "wollongong",
        "sunshine coast",
        "australia",
        "local",
    }
)

BUYER_FIT_REASONS: tuple[str, ...] = (
    "tool_seeker",
    "learning_resource",
    "platform_navigation",
    "consumer_search",
    "consumer_platform",
    "foreign_language",
)


def _consumer_platform_query(keyword: str) -> str | None:
    """A product of Google, Facebook, or similar, with no request for the service.

    "google lens" and "minesweeper google" are people using the product.
    "google ads management" is someone who can hire the agency.
    """
    platform = _any_term(keyword, PLATFORM_TERMS)
    if not platform:
        return None
    rest = set(_tokens(keyword))
    for name in PLATFORM_TERMS:
        if _contains_term(keyword, name):
            rest -= set(_tokens(name))
    rest -= {"a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "with"}
    rest -= _PLATFORM_NAV_WORDS
    if not rest:
        return None
    if rest & ((_SEARCH_MARKETING_WORDS - {"marketing"}) | _BUYER_ROLE_WORDS):
        return None
    return platform


# Words that turn a platform query into a buyer query. "marketing" alone does
# not: "google marketing platform" is still the product.
_BUYER_ROLE_WORDS: frozenset[str] = frozenset(
    {
        "agency",
        "agencies",
        "management",
        "manage",
        "manager",
        "consultant",
        "consultants",
        "consultancy",
        "service",
        "services",
        "specialist",
        "specialists",
        "expert",
        "experts",
        "firm",
        "company",
        "companies",
        "audit",
        "quote",
        "pricing",
        "price",
        "cost",
        "hire",
        "campaign",
        "campaigns",
        "developer",
        "developers",
        "development",
        "plugin",
        "plugins",
        "builder",
        "builders",
    }
)


def _any_term(keyword: str, terms: Iterable[str]) -> str | None:
    for term in sorted(terms, key=lambda t: (-len(t.split()), -len(t))):
        if _contains_term(keyword, term):
            return term
    return None


def _sells(words: frozenset[str], industry: str | None, services: Iterable[str] | None) -> bool:
    text = " ".join([_norm(industry or ""), *(_norm(s) for s in services or [])])
    return bool(set(_tokens(text)) & words)


def _english_market(geography: str | None) -> bool:
    geo = _norm(geography or "")
    return not geo or any(_contains_term(geo, place) for place in _ENGLISH_MARKETS)


def _free_without_offer(keyword: str) -> bool:
    """A bare "free" is a freebie hunter; a free quote, consultation or audit is a lead."""
    if not _contains_term(keyword, "free") or _is_always_allowed(keyword):
        return False
    return not _contains_term(keyword, "audit")


def _platform_only(keyword: str) -> str | None:
    """The platform named, when nothing else in the query asks for help with it."""
    platform = _any_term(keyword, PLATFORM_TERMS)
    if not platform:
        return None
    rest = set(_tokens(keyword))
    for name in PLATFORM_TERMS:
        if _contains_term(keyword, name):
            rest -= set(_tokens(name))
    if rest <= _PLATFORM_NAV_WORDS:
        return platform
    return None


def buyer_fit_reject(
    keyword: str,
    *,
    industry: str | None = None,
    services: Iterable[str] | None = None,
    geography: str | None = None,
) -> tuple[str, str] | None:
    """(reason, matched term) when the searcher cannot become a client, else None.

    Reasons are in ``BUYER_FIT_REASONS``. A business that sells software keeps
    tool searches; one that sells training keeps learning searches.
    """
    kw = _norm(keyword)
    if not kw:
        return None
    services = list(services or [])
    if _english_market(geography):
        foreign = _any_term(kw, FOREIGN_LANGUAGE_TERMS)
        if foreign:
            return "foreign_language", foreign
    if not _sells(_TOOL_SELLER_WORDS, industry, services) and industry_slug(industry) != "saas":
        tool = _any_term(kw, TOOL_SEEKER_TERMS)
        # A price or cost calculator is a buyer working out what the service costs.
        if tool in ("calculator", "estimator") and _any_term(kw, _PRICE_WORDS):
            tool = None
        if tool:
            return "tool_seeker", tool
        brand = _any_term(kw, TOOL_BRAND_TERMS)
        if brand:
            return "tool_seeker", brand
        site_test = _any_term(kw, _SITE_TEST_TERMS)
        if site_test and not (set(_tokens(kw)) & _BUILD_OR_PROVIDER_WORDS):
            return "tool_seeker", site_test
        product = _any_term(kw, TOOL_PRODUCT_TERMS)
        # "melbourne website builders" is local firms, not a DIY site builder.
        local = _any_term(kw, _LOCAL_WORDS)
        if product and not local and not (set(_tokens(kw)) & _BUILD_OR_PROVIDER_WORDS):
            return "tool_seeker", product
        if _free_without_offer(kw):
            return "tool_seeker", "free"
    if not _sells(_LEARNING_SELLER_WORDS, industry, services):
        learning = _any_term(kw, LEARNING_RESOURCE_TERMS)
        if learning:
            return "learning_resource", learning
    # "best search engine" is a consumer; "best search engine marketing agency"
    # is a buyer. A marketing word always turns the query back into the service.
    if not (set(_tokens(kw)) & _SEARCH_MARKETING_WORDS):
        consumer = _any_term(kw, CONSUMER_SEARCH_TERMS) or _any_term(kw, _CONSUMER_SEARCH_HEADS)
        if consumer:
            return "consumer_search", consumer
    platform = _platform_only(kw)
    if platform:
        return "platform_navigation", platform
    product = _consumer_platform_query(kw)
    if product:
        return "consumer_platform", product
    return None
