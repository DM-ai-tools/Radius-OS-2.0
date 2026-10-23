"""Canonical Service → Sub-service taxonomy.

Definitions used throughout this module:

* **Service** — a distinct line of work a customer hires the business for, broad
  enough to own several specific offerings (Roofing, SEO, Home Loan Broking).
* **Sub-service** — one specific, purchasable offering that belongs to exactly one
  service (Roof Repair under Roofing, Local SEO under SEO).
* **Keyword** — how people search for a service ("roof repairs", "best roof repair
  near me"). Keyword variants never become services; they belong to keyword research.
* **Modifier** — a quality, price or urgency claim (best, affordable, 24/7, near me).
  Stripped from names.
* **Location** — where the service is delivered (Melbourne, Sydney). Stripped.
* **Customer segment** — who it is for (First Home Buyers, Small Business, Dentists).
  An attribute of a service, not a service.
* **Language** — the language the service is delivered in (Hindi Brokers). An
  attribute of a service.
* **Technology / material / method** — stays part of a name when it defines the
  offering (Metal Roofing, WordPress Development, Google Ads Management).

Everything here is deterministic: the same input always gives the same taxonomy. The
LLM proposes; this layer normalises names, merges duplicates, moves sub-services to the
service they belong to, and turns attributes into metadata instead of services. Nothing
the client offers is dropped silently: merged names become aliases, attributes keep
their offering as a keyword qualifier for seeding, and everything removed is listed in
the report with a reason.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.services.keyword_service_contract import _AU_CITIES, _FOREIGN_CITIES
from app.services.service_prioritization import (
    _ACRONYMS,
    _BRAND_CASE,
    _PLACE_TOKENS,
    NOT_OFFERED_SOURCES,
    _is_document_stamp,
    _norm_key,
)

MAX_NAME_WORDS = 6

# --------------------------------------------------------------------- vocabulary

_STOPWORDS = frozenset(
    {"and", "&", "or", "of", "for", "the", "a", "an", "to", "with", "in", "on", "at", "by", "your", "our"}
)
# Left dangling at the end of a name once a location is cut off ("Brokers In").
_TRAILING_CONNECTORS = frozenset(
    {"in", "near", "at", "for", "around", "across", "from", "of", "the", "and", "&", "to", "with", "by"}
)
_PLACES = frozenset(
    set(_PLACE_TOKENS)
    | set(_AU_CITIES)
    | set(_FOREIGN_CITIES)
    | {
        "nsw", "vic", "qld", "victoria", "queensland", "tasmania", "new south wales",
        "western australia", "south australia", "northern territory", "sunshine coast",
        "central coast", "cbd", "nationwide", "australia wide", "australia-wide", "worldwide",
    }
)
# Claims about quality, price or speed. They sell; they do not name what is sold.
_MODIFIERS = frozenset(
    {
        "best", "top", "top rated", "top-rated", "affordable", "cheap", "cheapest", "low cost",
        "low-cost", "discount", "professional", "expert", "trusted", "reliable", "leading",
        "premium", "quality", "high quality", "certified", "licensed", "insured",
        "experienced", "award winning", "award-winning", "award", "winning", "no 1",
        "number 1", "#1", "fast", "quick", "same day", "same-day", "24/7", "24 7", "24hr",
        "24 hour", "urgent", "instant", "guaranteed", "free", "near me", "nearby", "our",
    }
)
# A modifier word that is part of a real name when followed by these.
_MODIFIER_EXCEPTIONS = {"professional": {"indemnity", "development"}, "free": {"range"}}
# Display-only suffixes: "Roof Repair Services" is "Roof Repair".
_GENERIC_SUFFIXES = frozenset(
    {
        "service", "services", "company", "companies", "agency", "agencies", "solution",
        "solutions", "expert", "experts", "specialist", "specialists", "provider",
        "providers", "contractor", "contractors",
    }
)
# Words that describe who provides the service. Ignored when deciding whether two names
# are one offering ("Mortgage Broking" = "Mortgage Brokers" = "Home Loans").
_PROVIDER_WORDS = frozenset(
    _GENERIC_SUFFIXES
    | {"broker", "brokers", "broking", "brokerage", "consultant", "consultants", "staff", "team", "people"}
)
_LANGUAGES = frozenset(
    {
        "multilingual", "bilingual", "gujarati", "haryanvi", "hindi", "singlish", "tamil",
        "telugu", "punjabi", "mandarin", "cantonese", "chinese", "vietnamese", "arabic",
        "spanish", "italian", "greek", "korean", "japanese", "urdu", "bengali", "nepali",
        "sinhala", "sinhalese", "filipino", "tagalog", "indonesian", "malay", "thai",
        "french", "german", "portuguese", "russian", "persian", "farsi", "turkish", "polish",
        "marathi", "kannada", "malayalam", "dari", "pashto", "macedonian", "serbian", "croatian",
    }
)
# With these, the language IS the service (Chinese Translation), not an attribute.
_LANGUAGE_SERVICE_WORDS = frozenset(
    {
        "translation", "translations", "translator", "translators", "translating",
        "interpreting", "interpreter", "interpreters", "tutoring", "tutor", "tutors",
        "lessons", "lesson", "classes", "class", "course", "courses", "teaching",
        "school", "subtitling", "subtitles", "transcription", "localisation", "localization",
    }
)
_LANGUAGE_PROVIDER_WORDS = frozenset(
    {"speaking", "speaker", "speakers", "adviser", "advisers", "advisor", "advisors", "agent", "agents"}
    | _PROVIDER_WORDS
)
# Who a service is for. A name made only of these is a customer segment.
_SEGMENT_PHRASES = tuple(
    sorted(
        {
            "first home buyers", "first home buyer", "first home owners", "first home owner",
            "first time buyers", "first time buyer", "first-time buyers", "first-time buyer",
            "home buyers", "home buyer", "homebuyers", "homebuyer", "home owners",
            "home owner", "homeowners", "homeowner", "property investors", "property investor",
            "investors", "investor", "retirees", "retiree", "seniors", "senior", "pensioners",
            "pensioner", "self employed", "self-employed", "small businesses", "small business",
            "smb", "smbs", "sme", "smes", "startups", "startup", "start ups", "start up",
            "start-ups", "start-up", "families", "family", "students", "student", "expats",
            "expat", "non residents", "non resident", "non-residents", "non-resident",
            "doctors", "doctor", "dentists", "dentist", "lawyers", "lawyer", "solicitors",
            "accountants", "medical professionals", "nurses", "tradies", "tradespeople",
            "landlords", "landlord", "tenants", "tenant", "residential", "commercial",
            "enterprise", "enterprises", "b2b", "b2c", "business", "businesses",
            "healthcare", "hospitality", "retail", "retailers", "manufacturing",
            "manufacturers", "government", "nonprofits", "nonprofit", "not for profit",
            "not-for-profit", "charities", "automotive", "plumbers", "electricians",
            "builders", "roofers", "real estate agents", "realtors", "restaurants", "cafes",
            "gyms", "clinics", "hotels", "schools", "franchises", "architects", "engineers",
            "physiotherapists", "chiropractors", "vets", "veterinarians", "psychologists",
            "therapists", "salons", "law firms", "ecommerce stores", "ecommerce businesses",
        },
        key=lambda p: (-len(p), p),
    )
)
_CONTENT_RE = re.compile(
    r"^\d|\?\s*$|^(?:what|how|why|when|where|which|who|is|are|can|should|do|does|did)\b"
    r"|\b(?:should|every|secrets?|tips|guide|mistakes|reasons|ways|steps|questions|"
    r"checklist|vs|versus|explained|everything|things|ultimate)\b"
    r"|^[a-z]+ing\s+(?:in|on|up|out|into|with|your|the|a|an)\b",
    re.IGNORECASE,
)
# Last-word plurals that are activities, so the canonical name is singular
# ("Roof Repairs" → "Roof Repair"). Product plurals (Facebook Ads, Dental Implants,
# Finance Leases) are left as written.
_ACTIVITY_PLURAL_RE = re.compile(r"(?:ions|ments|airs|als|ups|ances|ences)$")
_ACTIVITY_PLURALS = frozenset(
    {"repairs", "audits", "checks", "purchases", "fixes", "inspections", "installs", "cleans", "quotes"}
)
_EXTRA_ACRONYMS = frozenset(
    {"smsf", "hvac", "ndis", "bas", "gst", "it", "pr", "sem", "smm", "edm", "cctv", "led", "diy",
     "ev", "hr", "b2c", "nsw", "qld", "vic", "usa", "uk", "sme", "smb", "seo", "ppc"}
)
_SMALL_WORDS = frozenset({"and", "or", "of", "for", "in", "on", "the", "to", "a", "an", "with", "at", "by"})

# Spellings of one concept, applied before stemming.
_PHRASE_SYNONYMS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), r)
    for p, r in (
        (r"\bsearch engine optimi[sz]ation\b", "seo"),
        (r"\bpay[\s-]*per[\s-]*click\b", "ppc"),
        (r"\bgoogle adwords\b|\badwords\b", "google ads"),
        (r"\bhome loans?\b", "mortgage"),
        (r"\bweb ?sites?\b", "web"),
        (r"\be[\s-]?commerce\b", "ecommerce"),
        (r"\bair[\s-]?con(?:ditioning|ditioners?)?\b", "aircon"),
        (r"\bsmm\b", "social media marketing"),
        (r"\bconversion rate optimi[sz]ation\b", "cro"),
        (r"\bsearch engine marketing\b", "sem"),
        (r"\bhot water systems?\b", "hot water"),
        (r"\bfix(?:es|ing)?\b", "repair"),
    )
)


def _stem(token: str) -> str:
    """A small, deterministic stemmer: equal stems mean one word in different forms.

    Only used for comparison keys, never for names shown to anyone.
    """
    t = token.lower()
    if len(t) > 3 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    for _ in range(2):
        for suffix in ("ation", "ion", "ment", "ancy", "ency", "ing", "ant", "er"):
            if t.endswith(suffix) and len(t) - len(suffix) >= 3:
                t = t[: -len(suffix)]
                break
        else:
            break
    if t.endswith("e") and len(t) > 3:
        t = t[:-1]
    if t.endswith("is") and len(t) > 4:
        t = t[:-2] + "iz"
    return _STEM_SYNONYMS.get(t, t)


_STEM_SYNONYMS = {"advertiz": "ad", "ads": "ad", "remodel": "renov", "renovat": "renov"}


def _stems(text: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in re.split(r"[^a-z0-9+#]+", text.lower()) if w)


# Activity words and agency boilerplate. Shared by many services, so they never show
# that a sub-service belongs to one service rather than another.
_GENERIC_STEMS = frozenset(
    _stem(w)
    for w in ["marketing", "management", "manager", "consulting", "consultation", "strategy", "digital", "online", "repair", "installation", "install", "replacement", "inspection", "maintenance", "cleaning", "restoration", "upgrade", "design", "development", "building", "build", "audit", "support", "setup", "set", "up", "report", "reporting", "analytics", "plan", "planning", "advice", "advisory", "help", "custom", "ad", "advertising", "campaign", "optimization", "general", "other", "new", "assessment", "check", "review", "quote", "cost", "price", "pricing", "service", "solutions", "local", "professional", "power", "powered"]
)

# Coarse domain families. Two families that do not overlap mean a sub-service is under
# the wrong parent (Roof Repair under Plumbing). Unknown words never conflict.
_FAMILY_MARKERS: dict[str, tuple[frozenset[str], ...]] = {
    family: tuple(_stems(m) for m in markers)
    for family, markers in {
        "seo": ("seo", "search engine", "organic search", "backlink", "link building", "citation building", "keyword research"),
        "ppc": ("ppc", "google ads", "bing ads", "microsoft ads", "shopping ads", "google shopping", "display ads", "remarketing", "retargeting", "sem", "paid search", "search ads", "performance max"),
        "social": ("social", "facebook", "instagram", "meta ads", "tiktok", "linkedin", "pinterest", "twitter", "snapchat", "influencer"),
        "content": ("content", "blog", "copywriting", "copywriter", "article"),
        "email": ("email", "newsletter", "edm"),
        "web": ("web", "wordpress", "shopify", "woocommerce", "ecommerce", "landing page", "ux", "ui", "magento", "webflow"),
        "roofing": ("roof", "gutter", "skylight", "flashing", "downpipe", "fascia"),
        "plumbing": ("plumbing", "plumber", "pipe", "drain", "drainage", "sewer", "toilet", "tap", "hot water", "gas fitting", "backflow"),
        "electrical": ("electrical", "electrician", "wiring", "switchboard", "power point", "rewiring"),
        "hvac": ("aircon", "heating", "ducted", "hvac", "split system", "evaporative cooling"),
        "lending": ("loan", "mortgage", "refinancing", "lending", "bridging", "equity", "hire purchase", "lease", "smsf", "finance"),
    }.items()
}


def _families(stems: frozenset[str]) -> frozenset[str]:
    return frozenset(f for f, markers in _FAMILY_MARKERS.items() if any(m <= stems for m in markers))


# ------------------------------------------------------------------------ naming


def _words(text: str) -> list[str]:
    return [w for w in " ".join(str(text or "").replace("|", " ").split()).split(" ") if w]


def _bare(word: str) -> str:
    return word.lower().strip(".,;:!?()[]\"'’")


def _strip_phrase(words: list[str], phrases: Iterable[str], *, leading: bool, trailing: bool) -> tuple[list[str], list[str]]:
    """Remove phrases from the start and/or end of ``words``. Returns (words, removed)."""
    removed: list[str] = []
    ordered = sorted(set(phrases), key=lambda p: (-len(p.split()), p))
    changed = True
    while changed and words:
        changed = False
        low = [_bare(w) for w in words]
        for phrase in ordered:
            parts = phrase.split()
            n = len(parts)
            if n > len(words):
                continue
            if leading and low[:n] == parts:
                nxt = low[n] if len(low) > n else ""
                if nxt in _MODIFIER_EXCEPTIONS.get(phrase, set()):
                    continue
                removed.append(" ".join(words[:n]))
                words = words[n:]
                changed = True
                break
            if trailing and low[-n:] == parts:
                removed.append(" ".join(words[-n:]))
                words = words[:-n]
                changed = True
                break
    return words, removed


def _drop_connectors(words: list[str]) -> list[str]:
    while words and _bare(words[-1]) in _TRAILING_CONNECTORS:
        words = words[:-1]
    while words and _bare(words[0]) in _TRAILING_CONNECTORS - {"the"}:
        words = words[1:]
    return words


def _singular(word: str) -> str:
    low = word.lower()
    if low in _ACTIVITY_PLURALS or (_ACTIVITY_PLURAL_RE.search(low) and len(low) > 5):
        if low.endswith(("xes", "ches", "shes")):
            return word[:-2]
        return word[:-1]
    return word


def _case(word: str, first: bool, shouting: bool = False) -> str:
    low = word.lower()
    if any(c.isupper() for c in word[1:]) and not word.isupper():
        return word  # WooCommerce, iPhone: keep the source's own casing
    if low in _ACRONYMS or low in _EXTRA_ACRONYMS:
        return low.upper()
    if low in _BRAND_CASE:
        return _BRAND_CASE[low]
    if not shouting and word.isupper() and 2 <= len(word) <= 5 and word.isalpha():
        return word  # an acronym in the source (SMSF, ANZ)
    if low in _SMALL_WORDS and not first:
        return low
    if "-" in word:
        return "-".join(p[:1].upper() + p[1:].lower() for p in word.split("-"))
    return word[:1].upper() + word[1:].lower()


def _title(words: list[str]) -> str:
    # A name typed all in capitals says nothing about which words are acronyms.
    letters = [c for w in words for c in w if c.isalpha()]
    shouting = len(words) > 1 and bool(letters) and all(c.isupper() for c in letters)
    return " ".join(_case(w, i == 0, shouting) for i, w in enumerate(words))


def _segment_rest(text: str) -> str:
    """``text`` with every customer-segment phrase removed."""
    rest = f" {text.lower()} "
    for phrase in _SEGMENT_PHRASES:
        rest = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", " ", rest)
    return " ".join(w for w in rest.split() if w not in _STOPWORDS)


@dataclass(frozen=True)
class NameAnalysis:
    """What a raw service name really is."""

    raw: str
    kind: str  # service | segment | language | content | location | modifier | empty
    name: str  # canonical display name ("" unless kind == "service")
    clean: str  # name with modifiers/locations/suffixes removed but attributes kept
    key: frozenset[str]  # concept key: equal keys are one offering
    attributes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def is_service(self) -> bool:
        return self.kind == "service"


def concept_key(text: str) -> frozenset[str]:
    """Order-, case-, plural- and spelling-insensitive key of an offering name."""
    low = " ".join(str(text or "").lower().replace("&", " and ").split())
    for pattern, repl in _PHRASE_SYNONYMS:
        low = pattern.sub(repl, low)
    words = [w for w in re.split(r"[^a-z0-9+#]+", low) if w]
    return frozenset(
        _stem(w) for w in words if w not in _STOPWORDS and w not in _PROVIDER_WORDS
    )


def analyze_name(raw: Any) -> NameAnalysis:
    """Classify a raw name and give its canonical form. Pure and deterministic."""
    text = " ".join(str(raw or "").split()).strip(" .-–—,;:|")
    empty = NameAnalysis(raw=str(raw or ""), kind="empty", name="", clean="", key=frozenset())
    if not text or _is_document_stamp(text):
        return empty
    if _CONTENT_RE.search(text) or len(_words(text)) > MAX_NAME_WORDS + 2:
        return NameAnalysis(raw=text, kind="content", name="", clean=text, key=concept_key(text))

    attributes: dict[str, list[str]] = {}
    words = _words(text)
    # "near me" anywhere, then places, modifiers and generic suffixes at either end.
    low = [_bare(w) for w in words]
    for i in range(len(low) - 1):
        if low[i] == "near" and low[i + 1] == "me":
            words = words[:i] + words[i + 2 :]
            attributes.setdefault("modifiers", []).append("near me")
            break
    for _ in range(3):
        words, places = _strip_phrase(words, _PLACES, leading=False, trailing=True)
        words = _drop_connectors(words)
        words, mods = _strip_phrase(words, _MODIFIERS, leading=True, trailing=True)
        words, suffixes = _strip_phrase(words, _GENERIC_SUFFIXES, leading=False, trailing=True)
        words = _drop_connectors(words)
        if places:
            attributes.setdefault("locations", []).extend(places)
        if mods:
            attributes.setdefault("modifiers", []).extend(m.lower() for m in mods)
        if not (places or mods or suffixes):
            break

    if not words:
        if attributes.get("locations"):
            return NameAnalysis(raw=text, kind="location", name="", clean="", key=frozenset(), attributes=_freeze(attributes))
        if attributes.get("modifiers"):
            return NameAnalysis(raw=text, kind="modifier", name="", clean="", key=frozenset(), attributes=_freeze(attributes))
        return empty
    low = [_bare(w) for w in words]
    if all(w in _PLACES for w in low):
        return NameAnalysis(raw=text, kind="location", name="", clean="", key=frozenset(), attributes=_freeze(attributes))

    words[-1] = _singular(words[-1])
    clean = _title(words)

    # "SEO for Dentists": the tail is who it is for.
    lowered = [_bare(w) for w in words]
    if "for" in lowered[1:]:
        cut = lowered.index("for", 1)
        tail = " ".join(lowered[cut + 1 :])
        if tail and not _segment_rest(tail):
            attributes.setdefault("segments", []).append(_title(words[cut + 1 :]))
            words = words[:cut]
            lowered = lowered[:cut]

    # Languages: an attribute unless the language is what is sold (Chinese Translation).
    langs = [w for w, l in zip(words, lowered) if l in _LANGUAGES]
    if langs and not any(l in _LANGUAGE_SERVICE_WORDS for l in lowered):
        rest = [w for w, l in zip(words, lowered) if l not in _LANGUAGES]
        attributes.setdefault("languages", []).extend(_title([w]) for w in langs)
        meaningful = [w for w in rest if _bare(w) not in _LANGUAGE_PROVIDER_WORDS | _STOPWORDS]
        if not meaningful:
            return NameAnalysis(raw=text, kind="language", name="", clean=clean, key=frozenset(), attributes=_freeze(attributes))
        words = _drop_connectors(rest)
        lowered = [_bare(w) for w in words]

    if not _segment_rest(" ".join(lowered)):
        attributes.setdefault("segments", []).append(_title(words))
        return NameAnalysis(raw=text, kind="segment", name="", clean=clean, key=frozenset(), attributes=_freeze(attributes))

    key = concept_key(" ".join(lowered))
    if not key:
        return empty
    return NameAnalysis(raw=text, kind="service", name=_title(words), clean=clean, key=key, attributes=_freeze(attributes))


def _freeze(attrs: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    return {k: tuple(dict.fromkeys(v)) for k, v in attrs.items() if v}


def canonical_name(raw: Any) -> str | None:
    """The canonical display name of a service, or ``None`` when it is not a service."""
    found = analyze_name(raw)
    return found.name if found.is_service else None


def same_concept(a: Any, b: Any) -> bool:
    ka, kb = analyze_name(a), analyze_name(b)
    return ka.is_service and kb.is_service and ka.key == kb.key


# --------------------------------------------------------------------- the tree


@dataclass
class Node:
    """One service or sub-service while the taxonomy is being refined."""

    name: str
    key: frozenset[str]
    data: dict[str, Any]
    client: bool  # the client demonstrably offers it
    covers: list[str] = field(default_factory=list)
    subs: list[Node] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    attributes: dict[str, list[str]] = field(default_factory=dict)
    qualifiers: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    moved_from: str | None = None
    parent_confidence: float | None = None
    order: int = 0

    @property
    def distinctive(self) -> frozenset[str]:
        return self.key - _GENERIC_STEMS

    @property
    def families(self) -> frozenset[str]:
        return _families(self.key)

    def vocab(self) -> frozenset[str]:
        words = set(self.key)
        for cover in self.covers:
            words |= analyze_name(cover).key
        return frozenset(words - _GENERIC_STEMS)


MergeData = Callable[[dict[str, Any], dict[str, Any]], None]


def _add_unique(target: list[str], items: Iterable[str]) -> None:
    for item in items:
        if item and item not in target:
            target.append(item)


def _absorb(into: Node, other: Node, merge_data: MergeData) -> None:
    """``other`` is the same offering as ``into``: keep one, lose nothing."""
    _add_unique(into.aliases, [a for a in (other.name, *other.aliases) if a != into.name])
    _add_unique(into.covers, other.covers)
    _add_unique(into.qualifiers, other.qualifiers)
    for k, v in other.attributes.items():
        _add_unique(into.attributes.setdefault(k, []), v)
    into.client = into.client or other.client
    merge_data(into.data, other.data)
    for sub in other.subs:
        into.subs.append(sub)


def _add_attribute_node(target: Node, attr: Node) -> None:
    for k, v in attr.attributes.items():
        _add_unique(target.attributes.setdefault(k, []), v)
    _add_unique(target.covers, attr.covers)
    _add_unique(target.qualifiers, attr.qualifiers)


@dataclass
class Report:
    renamed: list[dict[str, str]] = field(default_factory=list)
    merged: list[dict[str, str]] = field(default_factory=list)
    moved: list[dict[str, str]] = field(default_factory=list)
    demoted: list[dict[str, str]] = field(default_factory=list)
    promoted: list[dict[str, str]] = field(default_factory=list)
    attributes: list[dict[str, str]] = field(default_factory=list)
    excluded: list[dict[str, str]] = field(default_factory=list)
    broad: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}


def _weights(tops: list[Node]) -> dict[str, float]:
    df: dict[str, int] = {}
    for top in tops:
        for stem in top.vocab():
            df[stem] = df.get(stem, 0) + 1
    return {stem: 1.0 / n for stem, n in df.items()}


def _fit(sub: Node, parent: Node, weights: dict[str, float]) -> float:
    shared = sub.distinctive & parent.vocab()
    score = sum(weights.get(s, 1.0) for s in shared)
    if sub.families & parent.families:
        score += 1.0
    return round(score, 4)


def _conflicts(sub: Node, parent: Node) -> bool:
    a, b = sub.families, parent.families
    return bool(a and b and not (a & b))


def refine(
    tops: list[Node],
    *,
    merge_data: MergeData,
    domain_vocab: Iterable[str] = (),
    verify: Callable[[Node], bool] | None = None,
) -> tuple[list[Node], Report]:
    """Refine an analysed tree in place of the raw one. See the module docstring.

    ``verify`` marks unverified top-level candidates (pages nobody confirmed are
    services); one that shares no word with the client's own offerings or
    ``domain_vocab`` is left out as unrelated.
    """
    report = Report()
    tops = list(tops)

    # 1. Unrelated, unverified candidates (brand pages, tools, stray pages).
    if verify is not None:
        domain: set[str] = set()
        for name in domain_vocab:
            domain |= analyze_name(name).key
        for top in tops:
            if top.client:
                domain |= top.vocab()
        domain -= _GENERIC_STEMS
        kept = []
        for top in tops:
            # Nothing known about the client yet: no basis to call a page unrelated.
            if domain and not verify(top) and top.distinctive and not (top.distinctive & domain):
                report.excluded.append({"name": top.name, "reason": "unrelated"})
                continue
            kept.append(top)
        tops = kept

    # 2. "Commercial and Business Loans" is the two services it names, listed together.
    def is_compound(node: Node) -> bool:
        return bool(re.search(r"\s(?:and|&)\s", f" {node.data.get('_raw_name', node.name)} ".lower()))

    kept = []
    for top in tops:
        if is_compound(top):
            parts = [o for o in tops if o is not top and o.key and o.key < top.key]
            union: set[str] = set().union(*(o.key for o in parts)) if parts else set()
            if len(parts) >= 2 and union == set(top.key):
                _absorb(parts[0], top, merge_data)
                report.merged.append({"name": top.name, "into": parts[0].name, "reason": "combined"})
                continue
        kept.append(top)
    tops = kept

    # 3. Exact duplicates: one offering under several spellings.
    by_key: dict[frozenset[str], Node] = {}
    kept = []
    for top in sorted(tops, key=lambda n: (not n.client, n.order)):
        if top.key in by_key:
            survivor = by_key[top.key]
            _absorb(survivor, top, merge_data)
            report.merged.append({"name": top.name, "into": survivor.name, "reason": "duplicate"})
            continue
        by_key[top.key] = top
        kept.append(top)
    tops = sorted(kept, key=lambda n: n.order)

    # 4. Too narrow to be a service: Local SEO next to SEO is SEO's sub-service.
    for top in sorted(tops, key=lambda n: (-len(n.key), n.order)):
        if is_compound(top) or "_demoted" in top.flags:
            continue
        parents = [
            o for o in tops
            if o is not top and "_demoted" not in o.flags and o.key and o.key < top.key and not is_compound(o)
        ]
        if not parents:
            continue
        parent = max(parents, key=lambda o: (len(o.key), -o.order))
        top.flags.append("_demoted")
        parent.subs.append(top)
        parent.client = parent.client or top.client
        for sub in top.subs:
            parent.subs.append(sub)
        top.subs = []
        report.demoted.append({"name": top.name, "under": parent.name})
    tops = [t for t in tops if "_demoted" not in t.flags]
    for top in tops:
        for sub in top.subs:
            if "_demoted" in sub.flags:
                sub.flags.remove("_demoted")

    weights = _weights(tops)

    # 5. Sub-services: attributes out, then each one under the service it belongs to.
    for top in tops:
        kept_subs: list[Node] = []
        for sub in top.subs:
            kind = sub.data.get("_kind", "service")
            if kind in {"segment", "language"}:
                _add_attribute_node(top, sub)
                report.attributes.append({"name": sub.data.get("_raw_name", sub.name), "of": top.name, "kind": kind})
                continue
            if kind != "service":
                report.excluded.append({"name": sub.data.get("_raw_name", sub.name), "reason": kind})
                continue
            kept_subs.append(sub)
        top.subs = kept_subs

    # 6. A sub-service that is its parent, or another service, merges into it.
    top_by_key = {t.key: t for t in tops}
    for top in tops:
        kept_subs = []
        for sub in top.subs:
            home = top_by_key.get(sub.key)
            if home is not None:
                # Only the names and evidence move: a sub-service row's fields do not
                # belong on a service row.
                _absorb(home, sub, lambda a, b: None)
                report.merged.append({"name": sub.name, "into": home.name, "reason": "same as service"})
                continue
            kept_subs.append(sub)
        top.subs = kept_subs

    # 7. Wrong parent: move to the service it belongs to, or make it a service.
    promoted: list[Node] = []
    for top in list(tops):
        kept_subs = []
        for sub in top.subs:
            here = _fit(sub, top, weights)
            others = [o for o in tops if o is not top]
            best = max(others, key=lambda o: (_fit(sub, o, weights), -o.order), default=None)
            best_fit = _fit(sub, best, weights) if best is not None else 0.0
            if best is not None and _conflicts(sub, best):
                best_fit = 0.0
            if _conflicts(sub, top):
                if best is not None and best_fit > 0:
                    sub.moved_from = top.name
                    best.subs.append(sub)
                    report.moved.append({"name": sub.name, "from": top.name, "to": best.name})
                else:
                    sub.moved_from = top.name
                    sub.flags.append("promoted")
                    promoted.append(sub)
                    report.promoted.append({"name": sub.name, "from": top.name})
                continue
            if best is not None and best_fit >= here + 1.0:
                sub.moved_from = top.name
                best.subs.append(sub)
                report.moved.append({"name": sub.name, "from": top.name, "to": best.name})
                continue
            kept_subs.append(sub)
        top.subs = kept_subs
    for node in promoted:
        node.order = len(tops) + node.order
        tops.append(node)
    weights = _weights(tops)

    # 8. One sub-service, one parent: duplicates within and across services collapse.
    homes: dict[frozenset[str], tuple[Node, Node]] = {}
    for top in tops:
        kept_subs = []
        for sub in top.subs:
            prior = homes.get(sub.key)
            if prior is None:
                homes[sub.key] = (top, sub)
                kept_subs.append(sub)
                continue
            prior_top, prior_sub = prior
            if prior_top is not top and _fit(sub, top, weights) > _fit(prior_sub, prior_top, weights):
                prior_top.subs = [s for s in prior_top.subs if s is not prior_sub]
                _absorb(sub, prior_sub, merge_data)
                homes[sub.key] = (top, sub)
                kept_subs.append(sub)
                report.merged.append({"name": prior_sub.name, "into": f"{top.name} › {sub.name}", "reason": "duplicate"})
            else:
                _absorb(prior_sub, sub, merge_data)
                report.merged.append({"name": sub.name, "into": f"{prior_top.name} › {prior_sub.name}", "reason": "duplicate"})
        top.subs = kept_subs

    # 9. Confidence that each sub-service sits under the right service, and broad services.
    for top in tops:
        for sub in top.subs:
            if sub.subs:  # two levels only
                top.subs.extend(s for s in sub.subs if s.key not in {x.key for x in top.subs})
                sub.subs = []
            fit = _fit(sub, top, weights)
            sub.parent_confidence = 1.0 if fit >= 1.0 else round(0.5 + fit / 2, 2) if fit > 0 else 0.5
        sub_families = {f for s in top.subs for f in s.families}
        if not top.distinctive or (not top.families and len(sub_families) >= 3):
            top.flags.append("broad")
            report.broad.append(top.name)
    return tops, report


def node_meta(node: Node, *, is_sub: bool) -> dict[str, Any]:
    """Internal metadata kept on a catalog row. Not shown to end users."""
    meta: dict[str, Any] = {"is_canonical": True}
    if node.aliases:
        meta["aliases"] = list(node.aliases)
    attrs = {k: list(v) for k, v in node.attributes.items() if v and k != "modifiers"}
    if attrs:
        meta["attributes"] = attrs
    if node.qualifiers:
        meta["keyword_qualifiers"] = list(node.qualifiers)
    flags = [f for f in node.flags if not f.startswith("_")]
    if flags:
        meta["flags"] = flags
    if is_sub:
        meta["parent_confidence"] = node.parent_confidence if node.parent_confidence is not None else 1.0
        if node.moved_from:
            meta["moved_from"] = node.moved_from
    return meta


def _node(
    raw_name: str,
    *,
    data: dict[str, Any],
    client: bool,
    covers: list[str],
    order: int,
    prior: dict[str, Any] | None = None,
) -> Node:
    found = analyze_name(raw_name)
    name = found.name or found.clean or " ".join(str(raw_name or "").split())
    node = Node(
        name=name,
        key=found.key,
        data={**{k: v for k, v in data.items() if k != "taxonomy"}, "_raw_name": raw_name, "_kind": found.kind},
        client=client,
        covers=list(covers),
        order=order,
    )
    # What an earlier pass already worked out, so refining twice changes nothing.
    prior = prior if isinstance(prior, dict) else {}
    _add_unique(node.aliases, [a for a in prior.get("aliases") or [] if isinstance(a, str) and a != name])
    for k, v in (prior.get("attributes") or {}).items():
        if isinstance(v, list):
            _add_unique(node.attributes.setdefault(str(k), []), [str(x) for x in v])
    _add_unique(node.qualifiers, [q for q in prior.get("keyword_qualifiers") or [] if isinstance(q, str)])
    if isinstance(prior.get("moved_from"), str):
        node.moved_from = prior["moved_from"]
    for k, v in found.attributes.items():
        _add_unique(node.attributes.setdefault(k, []), v)
    # An attribute the client actually offers is still worth searching for:
    # "First Home Buyer", "Hindi Brokers", "SEO for Dentists". Taken from the client's
    # own offering names, not the model's wording.
    for cover in covers:
        seen = analyze_name(cover)
        if seen.clean and any(k in seen.attributes for k in ("segments", "languages")):
            _add_unique(node.qualifiers, [seen.clean])
    return node


def _raw_vocab(node: Node) -> frozenset[str]:
    """Plain word stems of a node's name and covers, before synonyms.

    A customer segment has no service words of its own, so it is matched to a service
    on the words they share ("First Home Buyer" → "Equity Home Loan").
    """
    words: set[str] = set()
    for text in (node.data.get("_raw_name") or node.name, *node.covers):
        for w in re.split(r"[^a-z0-9+#]+", str(text).lower()):
            if w and w not in _STOPWORDS and w not in _LANGUAGE_PROVIDER_WORDS and w not in _LANGUAGES:
                words.add(_stem(w))
    return frozenset(words - _GENERIC_STEMS)


def _report_renames(report: Report, nodes: Iterable[Node]) -> None:
    for node in nodes:
        raw = " ".join(str(node.data.get("_raw_name") or "").split())
        if raw and node.data.get("_kind") == "service" and raw.lower() != node.name.lower():
            report.renamed.append({"from": raw, "to": node.name})


# ------------------------------------------------------------- the LLM taxonomy


def refine_taxonomy(taxonomy: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Refine ``[{"name", "covers", "subservices": [{"name", "covers"}]}]``.

    Returns the refined list in the same shape (each node also carries a
    ``taxonomy`` metadata dict) and a report of what changed.
    """
    report_pre = Report()
    tops: list[Node] = []
    attribute_tops: list[Node] = []
    order = 0
    for svc in taxonomy or []:
        if not isinstance(svc, dict):
            continue
        covers = [c for c in svc.get("covers") or [] if isinstance(c, str)]
        top = _node(
            str(svc.get("name") or ""), data={}, client=bool(covers), covers=covers, order=order,
            prior=svc.get("taxonomy"),
        )
        order += 1
        for sub in svc.get("subservices") or []:
            if isinstance(sub, str):
                sub = {"name": sub}
            if not isinstance(sub, dict):
                continue
            sub_covers = [c for c in sub.get("covers") or [] if isinstance(c, str)]
            node = _node(
                str(sub.get("name") or ""), data={}, client=bool(sub_covers), covers=sub_covers,
                order=order, prior=sub.get("taxonomy"),
            )
            order += 1
            top.subs.append(node)
        kind = top.data["_kind"]
        if kind in {"segment", "language"}:
            attribute_tops.append(top)
        elif kind == "service":
            tops.append(top)
        else:
            report_pre.excluded.append({"name": top.data["_raw_name"], "reason": kind})
            # Its sub-services may still be real offerings.
            attribute_tops.append(top)

    _report_renames(report_pre, [*tops, *(s for t in tops for s in t.subs)])
    tops = _attach_attribute_tops(tops, attribute_tops, report_pre)
    refined, report = refine(tops, merge_data=lambda a, b: None)
    report = _combine(report_pre, report)

    out: list[dict[str, Any]] = []
    for top in refined:
        out.append(
            {
                "name": top.name,
                "covers": list(top.covers),
                "subservices": [
                    {"name": s.name, "covers": list(s.covers), "taxonomy": node_meta(s, is_sub=True)}
                    for s in top.subs
                ],
                "taxonomy": node_meta(top, is_sub=False),
            }
        )
    return out, report.as_dict()


def _attach_attribute_tops(tops: list[Node], attribute_tops: list[Node], report: Report) -> list[Node]:
    """A top-level customer segment or language joins the service it qualifies."""
    for attr in attribute_tops:
        if attr.data.get("_kind") in {"segment", "language"}:
            probe = Node(name=attr.name, key=frozenset(), data={}, client=attr.client, covers=attr.covers)
            target = _best_by_vocab(probe, tops)
            if target is not None:
                _add_attribute_node(target, attr)
                report.attributes.append({"name": attr.data["_raw_name"], "of": target.name, "kind": attr.data["_kind"]})
            else:
                report.excluded.append({"name": attr.data["_raw_name"], "reason": attr.data["_kind"]})
        for sub in attr.subs:
            if sub.data.get("_kind") == "service":
                target = _best_by_vocab(sub, tops)
                if target is None:
                    sub.order += 10_000
                    tops.append(sub)
                    report.promoted.append({"name": sub.name, "from": attr.data["_raw_name"]})
                else:
                    sub.moved_from = attr.data["_raw_name"]
                    target.subs.append(sub)
                    report.moved.append({"name": sub.name, "from": attr.data["_raw_name"], "to": target.name})
            elif sub.data.get("_kind") in {"segment", "language"}:
                target = _best_by_vocab(attr, tops) or _best_by_vocab(sub, tops)
                if target is not None:
                    _add_attribute_node(target, sub)
                    report.attributes.append({"name": sub.data["_raw_name"], "of": target.name, "kind": sub.data["_kind"]})
                else:
                    report.excluded.append({"name": sub.data["_raw_name"], "reason": sub.data["_kind"]})
            else:
                report.excluded.append({"name": sub.data["_raw_name"], "reason": sub.data.get("_kind", "empty")})
    return tops


def _best_by_vocab(node: Node, tops: list[Node]) -> Node | None:
    """The service a node shares the most (rarest) words with, or ``None``."""
    if not tops:
        return None
    vocab = node.vocab() | node.distinctive
    for cover in node.covers:
        vocab |= analyze_name(cover).key - _GENERIC_STEMS
    weights = _weights(tops)
    if vocab:
        scored = [(sum(weights.get(s, 1.0) for s in vocab & t.vocab()), -t.order, t) for t in tops]
        score, _, best = max(scored, key=lambda x: (x[0], x[1]))
        if score > 0:
            return best
    raw = _raw_vocab(node)
    if not raw:
        return None
    df: dict[str, int] = {}
    for t in tops:
        for s in _raw_vocab(t):
            df[s] = df.get(s, 0) + 1
    scored = [(sum(1.0 / df[s] for s in raw & _raw_vocab(t)), -t.order, t) for t in tops]
    score, _, best = max(scored, key=lambda x: (x[0], x[1]))
    return best if score > 0 else None


def _combine(a: Report, b: Report) -> Report:
    out = Report()
    for k in out.__dict__:
        getattr(out, k).extend(getattr(a, k))
        getattr(out, k).extend(getattr(b, k))
    return out


# ----------------------------------------------------------- the catalog rows


def _merge_rows(into: dict[str, Any], other: dict[str, Any]) -> None:
    """Two catalog rows of one offering: keep the first's id, take the best of both."""
    if not into:
        return
    into["selected"] = bool(into.get("selected") or other.get("selected"))
    if into.get("candidate_new_service") and not other.get("candidate_new_service"):
        for k in ("source", "priority_tier", "path"):
            if other.get(k):
                into[k] = other[k]
    into["candidate_new_service"] = bool(into.get("candidate_new_service") and other.get("candidate_new_service"))
    if not into.get("path") and other.get("path"):
        into["path"] = other["path"]
    for k in ("competitors",):
        if other.get(k):
            merged = list(into.get(k) or [])
            _add_unique(merged, other[k])
            into[k] = merged


def _row_is_client(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").lower()
    return not row.get("candidate_new_service") and source not in NOT_OFFERED_SOURCES


def refine_catalog(
    catalog: list[dict[str, Any]],
    *,
    vocabulary: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Refine a service catalog (the selection card's rows) without changing its shape.

    Row ids survive, so saved selections keep matching. Manually added services are
    the operator's own words and are passed through untouched.
    """
    report_pre = Report()
    passthrough: list[tuple[int, dict[str, Any]]] = []
    tops: list[Node] = []
    order = 0
    for idx, row in enumerate(catalog or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "").lower() == "manual":
            passthrough.append((idx, row))
            continue
        client = _row_is_client(row)
        name = str(row.get("name") or row.get("id") or "")
        data = {k: v for k, v in row.items() if k != "subservices"}
        top = _node(
            name, data=data, client=client, covers=[name] if client else [], order=order,
            prior=row.get("taxonomy"),
        )
        order += 1
        for sub in row.get("subservices") or []:
            if not isinstance(sub, dict):
                continue
            sub_name = str(sub.get("name") or sub.get("id") or "")
            sub_client = str(sub.get("source") or "").lower() not in NOT_OFFERED_SOURCES
            node = _node(
                sub_name, data=dict(sub), client=sub_client, covers=[sub_name] if sub_client else [],
                order=order, prior=sub.get("taxonomy"),
            )
            order += 1
            top.subs.append(node)
        kind = top.data["_kind"]
        if kind == "service":
            tops.append(top)
        elif kind in {"segment", "language"}:
            tops.append(top)  # attached below once the services are known
        else:
            report_pre.excluded.append({"name": name, "reason": kind})
            for sub in top.subs:
                if sub.data["_kind"] == "service":
                    sub.order += 10_000
                    tops.append(_as_top(sub))

    services = [t for t in tops if t.data["_kind"] == "service"]
    attrs = [t for t in tops if t.data["_kind"] != "service"]
    _report_renames(report_pre, [*services, *(s for t in services for s in t.subs)])
    services = _attach_attribute_tops(services, attrs, report_pre)
    refined, report = refine(
        services,
        merge_data=_merge_rows,
        domain_vocab=vocabulary,
        # Only plain pages of the client's own site are unverified: they can be a
        # lender's brand page or a calculator. Competitor and industry suggestions
        # are deliberately outside the client's current vocabulary.
        verify=lambda n: n.client or str(n.data.get("source") or "") != "website_page",
    )
    report = _combine(report_pre, report)

    out: list[dict[str, Any]] = []
    for top in refined:
        row = _row_from_node(top)
        out.append(row)
    for idx, row in passthrough:
        out.insert(min(idx, len(out)), row)
    return out, report.as_dict()


def _as_top(node: Node) -> Node:
    data = node.data
    data.setdefault("id", _norm_key(node.name))
    data.setdefault("priority_tier", None)
    data.setdefault("candidate_new_service", not node.client)
    return node


def _public(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _row_from_node(top: Node) -> dict[str, Any]:
    row = _public(top.data)
    row.pop("parent_id", None)
    row.setdefault("id", _norm_key(top.name))
    row["name"] = top.name
    row.setdefault("selected", top.client)
    row.setdefault("priority_tier", None)
    row.setdefault("candidate_new_service", not top.client)
    if "source" not in row:
        row["source"] = "website" if top.client else "industry"
    row["subservices"] = []
    for sub in top.subs:
        node = _public(sub.data)
        node.pop("priority_tier", None)
        node.pop("candidate_new_service", None)
        node.setdefault("id", _norm_key(sub.name))
        node["name"] = sub.name
        node.setdefault("selected", sub.client)
        node.setdefault("source", "website" if sub.client else "industry")
        if "parent_id" in node:
            node["parent_id"] = row["id"]
        node["taxonomy"] = node_meta(sub, is_sub=True)
        row["subservices"].append(node)
    row["taxonomy"] = node_meta(top, is_sub=False)
    return row


def remap_saved_selection(existing: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """A saved selection whose ids point at names that were since made canonical.

    "hire purchases" saved before the rename now selects the "Hire Purchase" row. Ids
    already in the catalog, and ids with no match, are left exactly as they were.
    """
    if not existing:
        return existing
    from app.services.service_prioritization import service_identity_key

    aliases = selection_aliases(catalog)
    current = set()
    for row in catalog or []:
        if isinstance(row, dict):
            current.add(str(row.get("id") or ""))
            current.update(
                str(s.get("id") or "") for s in row.get("subservices") or [] if isinstance(s, dict)
            )

    def one(value: Any) -> Any:
        sid = str(value or "")
        if not sid or sid in current:
            return value
        return aliases.get(_norm_key(sid)) or aliases.get(service_identity_key(sid)) or value

    def many(values: Any) -> Any:
        if not isinstance(values, list):
            return values
        return list(dict.fromkeys(one(v) for v in values))

    out = dict(existing)
    for key in ("selected_service_ids", "selected_subservice_ids", "primary_service_ids"):
        if key in out:
            out[key] = many(out[key])
    if out.get("top_priority_service_id"):
        out["top_priority_service_id"] = one(out["top_priority_service_id"])
    scope = out.get("service_scope")
    if isinstance(scope, dict):
        remapped: dict[str, Any] = {}
        for sid, entry in scope.items():
            remapped.setdefault(str(one(sid)), entry)
        out["service_scope"] = remapped
    return out


def selection_aliases(catalog: list[dict[str, Any]]) -> dict[str, str]:
    """Map every old spelling of a service or sub-service to its current id.

    Lets a selection saved before names were made canonical still find its rows.
    """
    from app.services.service_prioritization import service_identity_key

    out: dict[str, str] = {}

    def add(row: dict[str, Any]) -> None:
        rid = str(row.get("id") or "")
        if not rid:
            return
        names = [rid, str(row.get("name") or "")]
        names.extend((row.get("taxonomy") or {}).get("aliases") or [])
        for name in names:
            for key in (_norm_key(name), service_identity_key(name)):
                if key:
                    out.setdefault(key, rid)

    for row in catalog or []:
        if isinstance(row, dict):
            add(row)
            for sub in row.get("subservices") or []:
                if isinstance(sub, dict):
                    add(sub)
    return out
