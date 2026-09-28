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
