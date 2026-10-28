"""CDD / service / website relevance gate for Phase 5 keywords.

Expansions from Ahrefs and DataForSEO include a lot of adjacent-industry noise.
This module is the single contract for what may stay in clusters, opportunity
buckets, and downstream topics:

Keep a keyword only when it is supported by CDD services/keywords, service or
sub-service page evidence, website themes, valid seed lineage, or an approved
competitor gap that still overlaps the client's business. Every original seed
is retained as a cluster shell even when all of its expansions are rejected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.services.keyword_exclusions import is_commercially_targetable
from app.services.keyword_opportunity import is_stale_year_keyword

__all__ = [
    "RelevanceContext",
    "build_relevance_context",
    "evaluate_keyword",
    "filter_relevant_keywords",
    "is_commercially_targetable",
]

_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "to",
    "of",
    "in",
    "on",
    "with",
    "vs",
    "versus",
}
# Tokens too generic to be evidence on their own. They appear in real service
# names ("AI-Powered SEO", "app development", "digital marketing"), so they are
# not stopwords — they just cannot be the ONLY thing tying a keyword to this
# business. Click Trends' catalog made "ai" distinctive, and one shared
# three-letter token then admitted "google ai", "ai mode", "ai girlfriend" and
# the rest of the consumer-AI category into a B2B email-marketing pool.
_NON_DISTINCTIVE = {
    "ai",
    "app",
    "apps",
    "tool",
    "tools",
    "online",
    "digital",
    "platform",
    "platforms",
    "software",
    "system",
    "systems",
    "tech",
    "service",
    "services",
    "solution",
    "solutions",
    "powered",
    "focus",
    "primary",
    "secondary",
    "management",
    "web",
    "site",
    "data",
    "cloud",
    # Platform brands: real service names carry them ("Google Ads/PPC
    # Management"), but the brand alone is not the service. "google ai" is not a
    # Google Ads query — it needs the platform's service head ("ads", "ppc",
    # "adwords"), which stays distinctive, exactly as the paid-media family
    # logic below already assumes for shared heads.
    "google",
    "meta",
    "facebook",
    "instagram",
    "microsoft",
    "linkedin",
    "tiktok",
    # Business-entity nouns. CDD keywords routinely read "email marketing
    # company" / "email marketing agency", which made "company" and "agency"
    # distinctive — so "fujifilm company" (11,105/mo) topped a real client's head
    # terms on one shared word. These name a KIND of business, never which one.
    "company",
    "companies",
    "agency",
    "agencies",
    "consultant",
    "consultants",
    "consultancy",
    "firm",
    "firms",
    "specialist",
    "specialists",
    "expert",
    "experts",
    "provider",
    "providers",
    "freelancer",
    "freelancers",
    "partner",
    "partners",
}

_WEAK = {
    "best",
    "top",
    "near",
    "me",
    "how",
    "choose",
    "official",
    "login",
    "careers",
    "jobs",
}
# Broader set used only for brand-detection: these modifiers don't make a
# keyword non-branded (e.g. "King Kong pricing" is still a brand query).
_BRAND_GENERIC = _WEAK | {
    "hire",
    "pricing",
    "price",
    "cost",
    "quote",
    "cheap",
    "affordable",
    "company",
    "companies",
    "agency",
    "agencies",
    "services",
    "service",
    "solutions",
    "solution",
}
_GENERIC_BRAND = {
    "marketing",
    "seo",
    "sem",
    "ppc",
    "digital",
    "agency",
    "agencies",
    "media",
    "group",
    "studio",
    "studios",
    "solutions",
    "services",
    "service",
    "company",
    "consulting",
    "consultants",
    "labs",
    "creative",
    "web",
    "online",
    "co",
    "inc",
    "ltd",
    "llc",
    "pty",
    "the",
    "and",
}

# Paid-media platform families. Shared heads like "ads" are not distinctive —
# "google ads" expanded from a Meta Ads seed must not inherit Meta lineage.
_PLATFORM_FAMILIES: dict[str, frozenset[str]] = {
    "meta": frozenset({"meta", "facebook", "fb", "instagram", "ig"}),
    # Include singularized forms ("gads" → "gad" via _singular).
    "google": frozenset({"google", "adwords", "adword", "gads", "gad", "gadwords", "gadword"}),
    "linkedin": frozenset({"linkedin"}),
    "tiktok": frozenset({"tiktok"}),
    "youtube": frozenset({"youtube"}),
}

_URL_RE = re.compile(r"(https?://|www\.|[a-z0-9-]+\.(com|net|org|io|co|au|uk)(/|\b))", re.IGNORECASE)

KEEP_REASONS = (
    "seed_exact",
    "seed_phrase",
    "seed_related",
    "cdd_phrase",
    "service_overlap",
    "page_overlap",
    "theme_overlap",
    "competitor_gap",
    "hygiene_only",
)
DROP_REASONS = (
    "empty",
    "stale_year",
    "url_noise",
    "competitor_brand",
    "client_brand_noise",
    "no_business_evidence",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _deamericanise(tok: str) -> str:
    """Fold -ize/-ization onto -ise/-isation so spellings match.

    A CDD written in British English ("conversion optimisation") otherwise fails
    to match the American keyword ("conversion optimization"), which cost a real
    client its "conversion optimization agency" term. Applied to both sides, so
    the direction of the fold does not matter.
    """
    if len(tok) > 5:
        if "izatio" in tok:
            tok = tok.replace("izatio", "isatio")
        elif tok.endswith(("ize", "izes", "ized", "izing", "izer", "izers")):
            tok = tok.replace("iz", "is", 1) if "iz" in tok else tok
    return tok


def _singular(tok: str) -> str:
    tok = _deamericanise(tok)
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith("ses") and len(tok) > 4:
        return tok[:-2]
    if tok.endswith("s") and not tok.endswith("ss") and len(tok) > 3:
        return tok[:-1]
    return tok


def _token_list(text: str) -> list[str]:
    return [_singular(t) for t in re.findall(r"[a-z0-9]+", _norm(text)) if t not in _STOP and len(t) > 1]


def _tokens(text: str) -> set[str]:
    return set(_token_list(text))


def _platform_families(text: str) -> set[str]:
    """Return platform family keys present in ``text`` (e.g. {'meta'}, {'google'})."""
    toks = _tokens(text)
    if not toks:
        return set()
    found: set[str] = set()
    for family, aliases in _PLATFORM_FAMILIES.items():
        if toks & aliases:
            found.add(family)
    return found


def _platform_mismatch(keyword: str, *, seed: str | None, services: Iterable[str]) -> bool:
    """True when keyword's platform family conflicts with seed / selected services."""
    kw_fam = _platform_families(keyword)
    if not kw_fam:
        return False
    seed_text = str(seed or "").strip()
    if seed_text:
        seed_fam = _platform_families(seed_text)
        if seed_fam and seed_fam.isdisjoint(kw_fam):
            return True
        if seed_fam:
            return False
    svc_fam: set[str] = set()
    for svc in services or []:
        svc_fam |= _platform_families(str(svc or ""))
    return bool(svc_fam) and svc_fam.isdisjoint(kw_fam)


# Derivational families the plural-only stemmer cannot connect. Without these,
# "baker salary" matched no evidence at all for a bakery, and "dentistry" missed
# a "dental" service — the gate looked stricter than it was, for the wrong reason.
# Kept deliberately short and explicit: no stemming library, no embeddings.
LEMMA_ROOTS: dict[str, str] = {
    "dentist": "dent",
    "dental": "dent",
    "dentistry": "dent",
    "bake": "bake",
    "baker": "bake",
    "bakery": "bake",
    "bakeri": "bake",
    "baked": "bake",
    "baking": "bake",
    "bread": "bread",
    "plumber": "plumb",
    "plumbing": "plumb",
    "electrician": "electric",
    "electrical": "electric",
    "roofer": "roof",
    "roofing": "roof",
    "builder": "build",
    "building": "build",
    "cleaner": "clean",
    "cleaning": "clean",
    "painter": "paint",
    "painting": "paint",
    "legal": "law",
    "lawyer": "law",
    "accountant": "account",
    "accounting": "account",
    "pastry": "pastry",
    "pastries": "pastry",
}


def _lemma(token: str) -> str:
    """Singularize, then fold known derivational families to one root."""
    tok = _singular(token)
    return LEMMA_ROOTS.get(tok, LEMMA_ROOTS.get(token, tok))


def _lemmas(text: str) -> set[str]:
    return {_lemma(t) for t in _token_list(text)}


def _phrases(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if isinstance(raw, dict):
            for key in ("keyword", "title", "theme", "term", "name", "path", "url"):
                val = raw.get(key)
                if val:
                    _append_phrase(out, seen, str(val))
            continue
        if isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, dict):
                    for key in ("keyword", "title", "theme", "term", "name", "path", "url"):
                        if item.get(key):
                            _append_phrase(out, seen, str(item[key]))
                else:
                    _append_phrase(out, seen, str(item))
            continue
        _append_phrase(out, seen, str(raw) if raw is not None else "")
    return out


def _append_phrase(out: list[str], seen: set[str], raw: str) -> None:
    text = str(raw or "").strip()
    if not text:
        return
    # Path / URL last-segment fallback — only for real URLs/paths, not
    # service labels like "AEO Services (AI/answer engine optimization)".
    looks_like_url = bool(re.search(r"https?://|www\.|\.[a-z]{2,3}(/|$)", text, re.IGNORECASE))
    looks_like_path = text.startswith("/") or (
        "/" in text and " " not in text and not text.startswith("(")
    )
    if looks_like_url or looks_like_path:
        seg = [s for s in re.sub(r"^https?://", "", text).split("/") if s and "." not in s]
        if seg:
            text = seg[-1].replace("-", " ").replace("_", " ")
    key = _norm(text)
    if not key or len(key) < 2 or key in seen:
        return
    seen.add(key)
    out.append(text.strip())


def website_resource_blobs(website: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten draft + approved (nested audit) website summaries into payload dicts."""
    if not isinstance(website, dict):
        return []
    blobs: list[dict[str, Any]] = [website]
    for value in website.values():
        if not isinstance(value, dict):
            continue
        summary = value.get("summary")
        if isinstance(summary, dict):
            blobs.append(summary)
        elif any(key in value for key in ("pages", "page_hierarchy", "themes", "target_keywords", "top_pages")):
            blobs.append(value)
    return blobs


def page_terms_from_website(website: dict[str, Any] | None) -> list[str]:
    """CDD-tagged service / sub-service page terms, plus titles and path segments."""
    terms: list[str] = []
    seen: set[str] = set()

    def _consider(page: dict[str, Any]) -> None:
        ptype = str(page.get("page_type") or "")
        cluster = str(page.get("cluster") or "")
        service_like = ptype in ("service", "sub_service") or cluster in (
            "services",
            "sub_services",
            "service_hub",
        )
        cdd_terms = page.get("cdd_terms") or []
        if isinstance(cdd_terms, list):
            for term in cdd_terms:
                _append_phrase(terms, seen, str(term))
        if not service_like and not cdd_terms:
            return
        title = str(page.get("title") or "").strip()
        if title and title.lower() not in ("home", "services", "about", "contact", "blog"):
            _append_phrase(terms, seen, title)
        path = str(page.get("path") or page.get("url") or "")
        if path:
            _append_phrase(terms, seen, path)

    for blob in website_resource_blobs(website):
        for key in ("pages", "page_hierarchy"):
            rows = blob.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    _consider(row)
    return terms


def theme_terms_from_website(website: dict[str, Any] | None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for blob in website_resource_blobs(website):
        for key in ("themes", "target_keywords", "top_pages", "cdd_focus_terms"):
            val = blob.get(key)
            if isinstance(val, dict):
                for items in val.values():
                    if isinstance(items, list):
                        for item in items:
                            _append_phrase(terms, seen, str(item))
                continue
            if not isinstance(val, list):
                continue
            for item in val:
                _append_phrase(terms, seen, item if not isinstance(item, dict) else "")
                if isinstance(item, dict):
                    for fk in ("keyword", "title", "theme", "term"):
                        if item.get(fk):
                            _append_phrase(terms, seen, str(item[fk]))
    return terms


def competitor_brand_blocklist(
    names: Iterable[str],
    domains: Iterable[str],
    *,
    protected_phrases: Iterable[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Return (full brand phrases, distinctive brand tokens)."""
    protected_tokens: set[str] = set()
    for phrase in protected_phrases or []:
        protected_tokens |= {t for t in _token_list(str(phrase)) if t not in _GENERIC_BRAND}

    full: set[str] = set()
    for name in names:
        n = _norm(str(name))
        if n:
            full.add(n)
    for domain in domains:
        base = str(domain or "").lower().removeprefix("www.").split(".")[0]
        if base and len(base) > 2:
            full.add(_norm(base.replace("-", " ")))
            full.add(base)

    tokens: set[str] = set()
    for name in full:
        for tok in re.split(r"[^a-z0-9]+", name):
            if len(tok) > 2 and tok not in _GENERIC_BRAND and tok not in protected_tokens:
                tokens.add(_singular(tok))
    return full, tokens


def is_competitor_brand_term(
    term: str,
    *,
    brand_full: set[str],
    brand_tokens: set[str],
) -> bool:
    t = _norm(term)
    if not t:
        return False
    if t in brand_full:
        return True
    toks = _token_list(t)
    distinctive = [tok for tok in toks if tok not in _GENERIC_BRAND]
    if distinctive and len(distinctive) <= 3 and all(tok in brand_tokens for tok in distinctive):
        return True
    # Brand token plus a generic industry word: "king kong seo"
    if any(tok in brand_tokens for tok in distinctive) and len(distinctive) <= 3:
        non_brand = [tok for tok in distinctive if tok not in brand_tokens]
        if non_brand and all(tok in _GENERIC_BRAND or tok in _BRAND_GENERIC for tok in non_brand):
            return True
    return False


def is_url_like(keyword: str) -> bool:
    kw = str(keyword or "").strip()
    if not kw:
        return False
    if _URL_RE.search(kw):
        return True
    if kw.startswith("/") or kw.startswith("www."):
        return True
    if "://" in kw or "?" in kw or "#" in kw:
        return True
    return False


@dataclass
class RelevanceContext:
    services: list[str] = field(default_factory=list)
    cdd_keywords: list[str] = field(default_factory=list)
    page_terms: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    evidence_phrases: list[str] = field(default_factory=list)
    evidence_tokens: set[str] = field(default_factory=set)
    distinctive_tokens: set[str] = field(default_factory=set)
    competitor_brand_full: set[str] = field(default_factory=set)
    competitor_brand_tokens: set[str] = field(default_factory=set)
    client_brand_tokens: set[str] = field(default_factory=set)
    has_business_evidence: bool = False
    distinctive_lemmas: set[str] = field(default_factory=set)
    industry: str | None = None
    geography: str | None = None
    # Other names of the selected services ("search engine optimization" for "SEO").
    service_synonyms: list[str] = field(default_factory=list)
    # Model-suggested sub-service topics of the selected services ("seo audit" for "SEO").
    service_subtopics: list[str] = field(default_factory=list)
    # True for the industry layer's fetch: its keywords ("digital marketing agency")
    # belong to no one service, so the service contract judges them against every
    # service and the industry itself (``judge_industry_keyword``).
    industry_layer: bool = False

    def seed_is_supported(self, seed: str) -> bool:
        return bool(self._phrase_hit(seed) or self._token_hit(seed))

    def seed_is_service(self, seed: str) -> bool:
        """True when the seed is one of the client's own services/sub-services.

        Seed lineage is only a relevance signal when the seed is something the
        client actually sells — a CDD keyword or a website theme is too loose. A
        service's synonym is that service under another name, and a sub-service topic
        seeded for a service is part of it, so both count.
        """
        target = _norm(seed)
        if not target:
            return False
        for service in (*self.services, *self.service_synonyms, *self.service_subtopics):
            svc = _norm(service)
            if not svc:
                continue
            if svc == target or svc in target or target in svc:
                return True
            if _lemmas(service) and _lemmas(service) <= _lemmas(target):
                return True
        return False

    def _lemma_hit(self, text: str) -> set[str]:
        return _lemmas(text) & self.distinctive_lemmas

    def _phrase_hit(self, text: str) -> str | None:
        kw = _norm(text)
        if not kw:
            return None
        for phrase in self.evidence_phrases:
            p = _norm(phrase)
            if not p or len(p) < 2:
                continue
            if p == kw or p in kw or (len(kw) >= 4 and kw in p):
                return phrase
        return None

    def _token_hit(self, text: str) -> set[str]:
        return _tokens(text) & self.distinctive_tokens


def build_relevance_context(
    *,
    services: Iterable[str] | None = None,
    cdd_keywords: Iterable[str] | None = None,
    website: dict[str, Any] | None = None,
    page_terms: Iterable[str] | None = None,
    themes: Iterable[str] | None = None,
    seeds: Iterable[str] | None = None,
    competitor_names: Iterable[str] | None = None,
    competitor_domains: Iterable[str] | None = None,
    brand_name: str | None = None,
    domain: str | None = None,
    industry: str | None = None,
    geography: str | None = None,
    service_synonyms: Iterable[str] | None = None,
    service_subtopics: Iterable[str] | None = None,
    industry_layer: bool = False,
) -> RelevanceContext:
    """``service_synonyms`` are other names of the selected services and
    ``service_subtopics`` the sub-service topics seeded for them ("seo audit" for SEO).
    Both count as whole phrases ("search engine optimization" is evidence of SEO) and
    as service seeds for lineage, but their single words do not become evidence
    tokens: one model-suggested word must not widen the gate for every keyword.
    ``industry_layer`` marks the context of the industry seeds' fetch."""
    svc = _phrases(services or [])
    cdd = _phrases(cdd_keywords or [])
    pages = list(page_terms) if page_terms is not None else page_terms_from_website(website)
    pages = _phrases(pages)
    theme_list = list(themes) if themes is not None else theme_terms_from_website(website)
    theme_list = _phrases(theme_list)
    seed_list = _phrases(seeds or [])
    synonyms = _phrases(service_synonyms or [])
    subtopics = _phrases(service_subtopics or [])

    evidence = _phrases([*svc, *cdd, *pages, *theme_list])
    evidence_tokens: set[str] = set()
    for phrase in evidence:
        evidence_tokens |= _tokens(phrase)
    distinctive = {
        t for t in evidence_tokens if t not in _WEAK and t not in _NON_DISTINCTIVE
    }

    brand_full, brand_tokens = competitor_brand_blocklist(
        competitor_names or [],
        competitor_domains or [],
        protected_phrases=[*evidence, *synonyms, *subtopics],
    )
    client_brand: set[str] = set()
    if brand_name:
        client_brand |= {
            t for t in _token_list(brand_name) if t not in _GENERIC_BRAND and t not in distinctive
        }
    if domain:
        root = str(domain).lower().removeprefix("www.").split(".")[0]
        if root and len(root) > 2 and root not in _GENERIC_BRAND and root not in distinctive:
            client_brand.add(_singular(root))

    return RelevanceContext(
        services=svc,
        cdd_keywords=cdd,
        page_terms=pages,
        themes=theme_list,
        seeds=seed_list,
        evidence_phrases=_phrases([*evidence, *synonyms, *subtopics]),
        evidence_tokens=evidence_tokens,
        distinctive_tokens=distinctive,
        competitor_brand_full=brand_full,
        competitor_brand_tokens=brand_tokens,
        client_brand_tokens=client_brand,
        has_business_evidence=bool(distinctive or evidence),
        distinctive_lemmas={_lemma(t) for t in distinctive},
        industry=industry,
        geography=geography,
        service_synonyms=synonyms,
        service_subtopics=subtopics,
        industry_layer=bool(industry_layer),
    )


def evaluate_keyword(
    keyword: str,
    ctx: RelevanceContext,
    *,
    match_class: str | None = None,
    seed: str | None = None,
    gap_flag: bool = False,
    provider_fallback: bool = False,
) -> tuple[bool, str, str]:
    """Return (keep, reason, evidence_note)."""
    from app.services.keyword_exclusions import (
        buyer_fit_reject,
        hard_reject_term,
        has_commercial_signal,
    )

    kw = str(keyword or "").strip()
    if not kw or len(_norm(kw)) < 2:
        return False, "empty", ""
    if is_stale_year_keyword(kw):
        return False, "stale_year", ""
    if is_url_like(kw):
        return False, "url_noise", ""
    # Tier 1 runs before every other signal. A job ad that happens to contain two
    # service words is still a job ad.
    excluded = hard_reject_term(kw, industry=ctx.industry)
    if excluded:
        return False, f"excluded_{excluded.replace(' ', '_')}", excluded
    # Buyer fit: the query names the service, but the searcher wants a tool, a
    # course, the platform itself, or a page in another language.
    unfit = buyer_fit_reject(
        kw,
        industry=ctx.industry,
        services=ctx.services,
        geography=getattr(ctx, "geography", None),
    )
    if unfit:
        return False, unfit[0], unfit[1]
    # The agency's own category is the business, even when the service list
    # only names SEO, PPC, email, CRO and social. "digital marketing melbourne"
    # is a buyer. "google lens" never reaches here.
    industry_text = _norm(getattr(ctx, "industry", "") or "")
    if "digital marketing" in industry_text and "digital marketing" in _norm(kw):
        return True, "service_overlap", "digital marketing"
    if is_competitor_brand_term(
        kw,
        brand_full=ctx.competitor_brand_full,
        brand_tokens=ctx.competitor_brand_tokens,
    ):
        return False, "competitor_brand", ""

    kw_tokens = _tokens(kw)
    if ctx.client_brand_tokens and kw_tokens and kw_tokens <= ctx.client_brand_tokens and len(kw_tokens) <= 2:
        return False, "client_brand_noise", ""

    cls = str(match_class or "").strip().lower()
    seed_text = str(seed or "").strip()
    seed_supported = bool(seed_text) and ctx.seed_is_supported(seed_text)
    phrase_hit = ctx._phrase_hit(kw)
    token_hit = ctx._token_hit(kw)
    # Broader token check: include evidence_tokens (not just distinctive) so
    # commercial-intent modifiers like "pricing", "services" count as overlap.
    broad_token_hit = _tokens(kw) & ctx.evidence_tokens if ctx.evidence_tokens else set()

    if not ctx.has_business_evidence:
        # Nothing to check relevance against. Passing everything here was the
        # widest hole in the gate: the clients with the thinnest profiles — the
        # ones least able to spot a bad keyword — got the least filtering.
        # Keep the seed shells so the phase can still report what it was asked
        # to expand, and reject the expansions as unverifiable.
        seed_norms = {_norm(s) for s in ctx.seeds if _norm(s)}
        if _norm(kw) in seed_norms or (seed_text and _norm(kw) == _norm(seed_text)):
            return True, "relevance_unverified", "no business evidence on file"
        return False, "no_business_evidence", ""

    from app.services.keyword_opportunity import is_ambiguous_seed, is_noisy_keyword

    if is_noisy_keyword(kw):
        return False, "noisy", ""

    from app.services.keyword_service_contract import (
        judge_industry_keyword,
        judge_keyword,
        off_topic_reason,
    )

    unrelated = off_topic_reason(kw)
    if unrelated:
        return False, unrelated, ""

    if getattr(ctx, "industry_layer", False):
        # An industry seed belongs to no one service: its keyword is judged against
        # every service the client sells, and passes when it names the industry.
        contract = judge_industry_keyword(
            kw,
            industry=ctx.industry,
            services=list(ctx.services or []),
            cdd_keywords=list(ctx.cdd_keywords or []),
            geography=getattr(ctx, "geography", None),
            seed=seed_text or None,
        )
    else:
        service_for_contract = seed_text if seed_text and ctx.seed_is_service(seed_text) else seed_text
        contract = judge_keyword(
            kw,
            service=service_for_contract or "",
            cdd_keywords=list(ctx.cdd_keywords or []),
            geography=getattr(ctx, "geography", None),
        )
    if contract.decision == "DROP":
        return False, contract.reason, contract.reason_detail
    if contract.decision == "KEEP":
        # The service contract already verified the whole query. Do not drop it
        # later for a weak lexical overlap.
        return True, "service_match", contract.family or contract.reason_detail
    if contract.decision == "REVIEW":
        return True, "service_review", contract.reason_detail

    def _related_stays_on_seed() -> bool:
        """A related query must still carry the seed, not merely share its parent."""
        if not seed_text:
            return False
        seed_norm = _norm(seed_text)
        kw_norm = _norm(kw)
        if seed_norm and (seed_norm in kw_norm or kw_norm in seed_norm):
            return True
        seed_core = _tokens(seed_text) - _NON_DISTINCTIVE - _WEAK - _STOP
        if not seed_core:
            seed_core = _tokens(seed_text) - _STOP
        return bool(_tokens(kw) & seed_core)

    # Cross-platform expansions (e.g. "google ads" under a Meta Ads seed) never
    # inherit the seed's parent category — shared "ads" is not affinity.
    if _platform_mismatch(kw, seed=seed_text, services=ctx.services):
        return False, "platform_mismatch", seed_text or ""

    def _ambiguous_seed_on_topic() -> bool:
        """Short acronym seeds must keep expansions on the business sense of the seed."""
        if not seed_text or not is_ambiguous_seed(seed_text):
            return True
        seed_toks = _tokens(seed_text)
        kw_toks = _tokens(kw)
        if not seed_toks:
            return True
        has_seed_token = bool(seed_toks & kw_toks)
        extra = (kw_toks & ctx.distinctive_tokens) - seed_toks
        if has_seed_token and extra:
            return True
        # Allow the bare acronym itself (exact seed row)
        if has_seed_token and kw_toks <= seed_toks:
            return True
        # Prefer full service/CDD strings — evidence_phrases may fragment parentheticals
        parents = [
            *list(ctx.services or []),
            *list(ctx.cdd_keywords or []),
            *list(ctx.seeds or []),
            *list(ctx.evidence_phrases or []),
        ]
        for phrase in parents:
            pn = _norm(phrase)
            if not pn or pn == _norm(seed_text):
                continue
            if not (seed_toks & _tokens(phrase)):
                continue
            phrase_extra = _tokens(phrase) - seed_toks
            overlap = kw_toks & phrase_extra
            if len(overlap) >= 2:
                return True
            if overlap and len(kw_toks) <= 4:
                return True
            if _norm(kw) in pn or pn in _norm(kw):
                return True
        return False

    # Coverage rows generated after provider failure — keep when the seed itself
    # is a supported service/CDD term (already hygiene-checked above).
    if provider_fallback and seed_supported:
        return True, "seed_related" if cls in ("related", "broad") else "seed_phrase", seed_text

    # Seed lineage: if the seed is supported, keep its expansions
    if seed_text and _norm(kw) == _norm(seed_text) and seed_supported:
        return True, "seed_exact", seed_text
    if cls == "exact" and seed_supported:
        if not _ambiguous_seed_on_topic():
            return False, "ambiguous_seed_drift", seed_text
        return True, "seed_exact", seed_text
    if cls == "phrase" and seed_supported:
        if not _ambiguous_seed_on_topic():
            return False, "ambiguous_seed_drift", seed_text
        return True, "seed_phrase", seed_text
    if cls == "related" and seed_supported:
        if not _ambiguous_seed_on_topic():
            return False, "ambiguous_seed_drift", seed_text
        if _related_stays_on_seed():
            return True, "seed_related", seed_text

    # Even when the seed isn't fully supported, keep exact/phrase expansions
    # that themselves share evidence tokens (broader match).
    if cls in ("exact", "phrase") and seed_text and broad_token_hit:
        if not _ambiguous_seed_on_topic():
            return False, "ambiguous_seed_drift", seed_text
        return True, "seed_phrase", " ".join(sorted(broad_token_hit))

    if phrase_hit:
        source = "cdd_phrase"
        if _norm(phrase_hit) in {
            _norm(p) for p in (*ctx.services, *ctx.service_synonyms, *ctx.service_subtopics)
        }:
            source = "service_overlap"
        elif _norm(phrase_hit) in {_norm(p) for p in ctx.page_terms}:
            source = "page_overlap"
        elif _norm(phrase_hit) in {_norm(p) for p in ctx.themes}:
            source = "theme_overlap"
        return True, source, phrase_hit

    # Lemma overlap catches dentist/dental/dentistry and bake/baker/bakery, which
    # plural-only stemming missed entirely.
    lemma_hit = ctx._lemma_hit(kw)
    effective_hit = token_hit or lemma_hit
    if effective_hit:
        # One shared token is not evidence of relevance on its own — "dental"
        # matches a job ad, a museum and a school just as well as a patient.
        # Require a second, independent signal.
        if len(effective_hit) < 2:
            # The head noun is what the searcher actually wants. "site speed
            # audit" is a kind of audit; "dental museum exhibition" is not a kind
            # of dentistry — same single shared token, opposite meaning.
            kw_tokens = _token_list(kw)
            head_noun_match = bool(
                kw_tokens and _lemma(kw_tokens[-1]) in ctx.distinctive_lemmas
            )
            second_signal = (
                head_noun_match
                or has_commercial_signal(kw)
                or (bool(seed_text) and ctx.seed_is_service(seed_text))
            )
            if not second_signal:
                return False, "weak_single_token", " ".join(sorted(effective_hit))
        if effective_hit & (_tokens(" ".join(ctx.services)) | _lemmas(" ".join(ctx.services))):
            return True, "service_overlap", " ".join(sorted(effective_hit))
        if effective_hit & _tokens(" ".join(ctx.page_terms)):
            return True, "page_overlap", " ".join(sorted(effective_hit))
        if effective_hit & _tokens(" ".join(ctx.themes)):
            return True, "theme_overlap", " ".join(sorted(effective_hit))
        return True, "cdd_phrase", " ".join(sorted(effective_hit))

    # Broader evidence token overlap (includes "weak" commercial modifiers)
    if broad_token_hit and len(broad_token_hit) >= 2:
        return True, "theme_overlap", " ".join(sorted(broad_token_hit))

    if gap_flag and seed_supported:
        if not _ambiguous_seed_on_topic():
            return False, "ambiguous_seed_drift", seed_text
        return True, "competitor_gap", seed_text

    # Last resort: a related expansion that still names the seed. One generic
    # evidence token ("marketing", "services") is not enough.
    if broad_token_hit and seed_text and _related_stays_on_seed():
        if not _ambiguous_seed_on_topic():
            return False, "ambiguous_seed_drift", seed_text
        return True, "seed_related", " ".join(sorted(broad_token_hit))

    return False, "no_business_evidence", ""


def empty_cleaning_audit() -> dict[str, Any]:
    return {
        "input_count": 0,
        "kept_count": 0,
        "removed_count": 0,
        "removed_by_reason": {},
        "removed_by_class": {},
        "removed_by_source": {},
        "sample": [],
    }


def merge_cleaning_audits(*audits: dict[str, Any] | None) -> dict[str, Any]:
    merged = empty_cleaning_audit()
    samples: list[dict[str, Any]] = []
    seen_sample: set[str] = set()
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        merged["input_count"] += int(audit.get("input_count") or 0)
        merged["kept_count"] += int(audit.get("kept_count") or 0)
        merged["removed_count"] += int(audit.get("removed_count") or 0)
        for key in ("removed_by_reason", "removed_by_class", "removed_by_source"):
            bucket = merged[key]
            for name, count in (audit.get(key) or {}).items():
                bucket[str(name)] = int(bucket.get(str(name)) or 0) + int(count or 0)
        for row in audit.get("sample") or []:
            if not isinstance(row, dict):
                continue
            kw = _norm(str(row.get("keyword") or ""))
            if not kw or kw in seen_sample:
                continue
            seen_sample.add(kw)
            samples.append(row)
            if len(samples) >= 12:
                break
    merged["sample"] = samples[:12]
    return merged


def compact_cleaning_audit(audit: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(audit, dict) or not audit.get("input_count"):
        return None
    return {
        "input_count": int(audit.get("input_count") or 0),
        "kept_count": int(audit.get("kept_count") or 0),
        "removed_count": int(audit.get("removed_count") or 0),
        "removed_by_reason": dict(audit.get("removed_by_reason") or {}),
        "sample": [
            {
                "keyword": row.get("keyword"),
                "reason": row.get("reason"),
                "match_class": row.get("match_class"),
                "seed": row.get("seed"),
            }
            for row in (audit.get("sample") or [])[:8]
            if isinstance(row, dict)
        ],
    }


def filter_relevant_keywords(
    rows: list[dict[str, Any]],
    ctx: RelevanceContext,
    *,
    source_label: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Keep client-relevant rows. Seeds are not invented or dropped here."""
    from app.services.keyword_exclusions import reroute_term

    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    audit = empty_cleaning_audit()
    audit["input_count"] = len(rows)
    reason_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        keyword = str(row.get("keyword") or "").strip()
        cls = str(row.get("match_class") or "").strip().lower() or None
        seed = str(row.get("seed") or "").strip() or None
        keep, reason, evidence = evaluate_keyword(
            keyword,
            ctx,
            match_class=cls,
            seed=seed,
            gap_flag=bool(row.get("gap_flag")),
            provider_fallback=bool(row.get("provider_fallback")),
        )
        if keep:
            # Tier 2: a real searcher with do-it-yourself intent. Keep the row for
            # content, but never let a commercial/service page target it.
            reroute = reroute_term(
                keyword, industry=ctx.industry, service_terms=ctx.services
            )
            kept_row = {**row, "relevance_reason": reason, "relevance_evidence": evidence}
            if reason == "relevance_unverified" or reason == "service_review":
                kept_row["relevance_unverified"] = reason != "service_review"
                kept_row["commercial_target_eligible"] = False
            if reason == "service_review":
                kept_row["classifier_version"] = "service_contract_v2"
            if reroute:
                kept_row["intent"] = "informational"
                kept_row["commercial_target_eligible"] = False
                kept_row["reroute_reason"] = reroute
            else:
                kept_row.setdefault("commercial_target_eligible", True)
            kept.append(kept_row)
            continue
        source = str(source_label or row.get("source") or "unknown")
        excluded.append(
            {
                "keyword": keyword,
                "reason": reason,
                "match_class": cls,
                "seed": seed,
                "source": source,
                "volume": row.get("volume"),
            }
        )
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if cls:
            class_counts[cls] = class_counts.get(cls, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1

    audit["kept_count"] = len(kept)
    audit["removed_count"] = len(excluded)
    audit["removed_by_reason"] = reason_counts
    audit["removed_by_class"] = class_counts
    audit["removed_by_source"] = source_counts
    audit["sample"] = excluded[:12]
    return kept, excluded, audit
