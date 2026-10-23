"""Choose the keywords a service pillar page should use, and the ones that deserve a guide.

A keyword is kept only when it has a job. Hire-intent variants of a service
optimize that service's existing page: one primary, and only the secondaries
that are the same search in other words. A question, a comparison, or a
how-to becomes a blog or a guide when the search is large enough to be worth
a page. Everything else is left unused.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.keyword_cluster_roles import RoleContext, finalize_cluster
from app.services.keyword_clustering import _primary_with_secondaries
from app.services.keyword_exclusions import buyer_fit_reject, hard_reject_term
from app.services.keyword_opportunity import _phrases
from app.services.topic_naming import phrase_title
from app.services.url_mapping import _matching_service, _named_city, _service_phrases, _spell

_GUIDE_RE = _phrases(
    "what is",
    "what are",
    "what does",
    "how to",
    "how do",
    "how does",
    "how can",
    "why",
    "meaning",
    "definition",
    "guide",
    "tutorial",
    "tips",
    "ideas",
    "examples",
    "example",
    "checklist",
    "template",
    "templates",
    "vs",
    "versus",
    "difference between",
    "how much",
    "cost of",
    "costs of",
    "price of",
    "best practice",
    "best practices",
    "best way",
)
_BUYER_RE = _phrases(
    "agency",
    "agencies",
    "company",
    "companies",
    "service",
    "services",
    "consultant",
    "consultants",
    "consultancy",
    "firm",
    "firms",
    "management",
    "specialist",
    "specialists",
    "expert",
    "experts",
    "provider",
    "providers",
    "near me",
    "hire",
)
_GUIDE_VOLUME_FLOOR = 50
_GUIDES_PER_SERVICE = 8
_SECONDARY_CAP = 10
# Names people actually search, pointed at the service the client sells.
_SERVICE_ALIASES: tuple[tuple[str, str], ...] = (
    ("search engine optimisation", "SEO"),
    ("search engine optimization", "SEO"),
    ("pay per click", "PPC Management"),
    ("google ads", "PPC Management"),
    ("adwords", "PPC Management"),
    ("ppc", "PPC Management"),
    ("email marketing", "Email Marketing"),
    ("email campaign", "Email Marketing"),
    ("email newsletter", "Email Marketing"),
    ("edm", "Email Marketing"),
    ("conversion rate", "Conversion Rate Optimization"),
    ("conversion optimisation", "Conversion Rate Optimization"),
    ("conversion optimization", "Conversion Rate Optimization"),
    ("landing page optimisation", "Conversion Rate Optimization"),
    ("landing page optimization", "Conversion Rate Optimization"),
    ("cro", "Conversion Rate Optimization"),
    ("social media marketing", "Social Media Marketing"),
    ("social media management", "Social Media Marketing"),
    ("social media advertising", "Social Media Marketing"),
    ("social media strategy", "Social Media Marketing"),
    ("smm", "Social Media Marketing"),
    ("seo", "SEO"),
)
# Same offer, different words, so a role check can see they are one search.
_CANON_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("search engine optimisation", "seo"),
    ("search engine optimization", "seo"),
    ("pay per click", "ppc"),
    ("google ads", "ppc"),
    ("adwords", "ppc"),
    ("email marketing", "edm"),
    ("email campaigns", "edm"),
    ("email newsletters", "edm"),
    ("conversion rate optimisation", "cro"),
    ("conversion rate optimization", "cro"),
    ("conversion optimisation", "cro"),
    ("conversion optimization", "cro"),
    ("social media marketing", "smm"),
    ("social media management", "smm"),
    ("social media advertising", "smm"),
)
_JUNK_RE = _phrases(
    "get",
    "platform",
    "platforms",
    "software",
    "tool",
    "tools",
    "job",
    "jobs",
    "course",
    "courses",
    "salary",
    "degree",
    "master",
    "masters",
    "template",
    "templates",
)


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _volume(row: dict[str, Any]) -> int:
    try:
        return int(round(float(row.get("volume") or 0)))
    except (TypeError, ValueError):
        return 0


def _service_for(keyword: str, phrases: dict[str, str]) -> str | None:
    text = _spell(_norm(keyword))
    matched = _matching_service(text, phrases)
    if matched:
        return matched
    for alias, service in _SERVICE_ALIASES:
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text):
            return service
    return None


def _canon(keyword: str) -> str:
    text = _spell(_norm(keyword))
    for source, target in _CANON_REPLACEMENTS:
        text = re.sub(rf"(?<![a-z0-9]){re.escape(source)}(?![a-z0-9])", target, text)
    return " ".join(text.split())


def _usable(keyword: str) -> bool:
    text = _spell(_norm(keyword))
    if not text or _JUNK_RE.search(text):
        return False
    if " for " in f" {text} ":
        return False
    return True


def _job(keyword: str, phrases: dict[str, str]) -> str | None:
    """pillar, guide, or None when the keyword should not be used."""
    text = _spell(_norm(keyword))
    if not text or not _usable(text):
        return None
    if _GUIDE_RE.search(text):
        return "guide"
    if _service_for(text, phrases) is None:
        return None
    return "pillar"


def _cluster_keywords(group: list[dict[str, Any]]) -> tuple[str, list[str], int, float | None]:
    ranked = sorted(group, key=lambda row: (-_volume(row), _norm(str(row.get("keyword") or ""))))
    # The peel already put the primary first in spirit; trust role when present.
    primary_row = next((row for row in group if row.get("role") == "Primary"), None)
    if primary_row is None:
        primary_row = ranked[0]
    primary = str(primary_row.get("keyword") or "").strip()
    secondaries = [
        str(row.get("keyword") or "").strip()
        for row in sorted(group, key=lambda row: (-_volume(row), _norm(str(row.get("keyword") or ""))))
        if str(row.get("keyword") or "").strip() and _norm(str(row.get("keyword") or "")) != _norm(primary)
    ][:_SECONDARY_CAP]
    cpc = primary_row.get("cpc")
    try:
        cpc_value = float(cpc) if cpc not in (None, "") else None
    except (TypeError, ValueError):
        cpc_value = None
    return primary, secondaries, _volume(primary_row), cpc_value


def _mark_roles(group: list[dict[str, Any]], ctx: RoleContext) -> list[dict[str, Any]]:
    pieces = _primary_with_secondaries(group, ctx)
    marked: list[dict[str, Any]] = []
    for piece in pieces:
        draft_rows = []
        finalized = finalize_cluster({"keywords": piece}, ctx)
        by_kw = {_norm(str(row.get("keyword") or "")): row for row in piece}
        for member in finalized.get("keywords") or []:
            if not isinstance(member, dict):
                continue
            key = _norm(str(member.get("keyword") or ""))
            source = dict(by_kw.get(key) or {})
            source["keyword"] = member.get("keyword") or source.get("keyword")
            source["role"] = member.get("role")
            draft_rows.append(source)
        if draft_rows:
            marked.append(draft_rows)
    return marked


def _same_search(candidate: str, primary: str) -> bool:
    from app.services.keyword_cluster_roles import _LIGHT_MODIFIERS, _core

    mine, theirs = _core(_canon(candidate)), _core(_canon(primary))
    if not mine or not theirs:
        return False
    if mine == theirs:
        return True
    extra = mine ^ theirs
    return (mine <= theirs or theirs <= mine) and extra <= _LIGHT_MODIFIERS


def _exact_service_phrase(keyword: str, service: str, phrases: dict[str, str]) -> bool:
    text = _spell(_norm(keyword))
    if phrases.get(text) == service:
        return True
    return any(text == alias and alias_service == service for alias, alias_service in _SERVICE_ALIASES)


def _pillar_cluster(
    rows: list[dict[str, Any]], service: str, phrases: dict[str, str]
) -> list[dict[str, Any]]:
    """One primary for the service page, plus national wording variants of that search."""
    usable = [row for row in rows if _usable(str(row.get("keyword") or ""))]
    buyers = [row for row in usable if _BUYER_RE.search(_spell(_norm(str(row.get("keyword") or ""))))]
    exact = [row for row in usable if _exact_service_phrase(str(row.get("keyword") or ""), service, phrases)]
    seeds = buyers or exact or usable
    if not seeds:
        return []
    seed = max(seeds, key=lambda row: (_volume(row), -len(str(row.get("keyword") or ""))))
    seed_text = str(seed.get("keyword") or "")
    family = [row for row in usable if _same_search(str(row.get("keyword") or ""), seed_text)]
    national = [
        row
        for row in family
        if not (_named_city(str(row.get("keyword") or "")) and not _named_city(seed_text))
    ]
    if not national:
        national = [seed]
    primary = max(national, key=lambda row: (_volume(row), len(str(row.get("keyword") or "").split())))
    primary = {**primary, "role": "Primary"}
    ordered = [primary] + [
        row
        for row in sorted(national, key=lambda row: (-_volume(row), _norm(str(row.get("keyword") or ""))))
        if _norm(str(row.get("keyword") or "")) != _norm(str(primary.get("keyword") or ""))
    ]
    return ordered[: 1 + _SECONDARY_CAP]


def _guide_kind(keyword: str) -> str:
    text = _norm(keyword)
    if re.search(r"\b(?:tips|ideas|examples|example|trends)\b", text):
        return "Blog"
    return "Guide"


def build_pillar_and_guide_clusters(
    keywords: list[dict[str, Any]],
    *,
    offer_synonyms: list[dict[str, Any]] | None = None,
    services: list[str] | None = None,
    industry: str | None = None,
    guide_volume_floor: int = _GUIDE_VOLUME_FLOOR,
    guides_per_service: int = _GUIDES_PER_SERVICE,
) -> dict[str, Any]:
    """Return the service-page clusters and the blog or guide clusters worth writing."""
    phrases = _service_phrases(
        [],
        commercial={"selected_services": list(services or [])},
        offer_synonyms=offer_synonyms,
    )
    ctx = RoleContext(services=list(dict.fromkeys([*phrases.values(), *(services or [])])))
    pools: dict[tuple[str, str], list[dict[str, Any]]] = {}
    considered = 0
    seen: set[str] = set()
    for raw in keywords:
        if not isinstance(raw, dict):
            continue
        keyword = " ".join(str(raw.get("keyword") or "").split())
        key = _norm(keyword)
        if not key or key in seen:
            continue
        seen.add(key)
        considered += 1
        if hard_reject_term(keyword, industry=industry) or buyer_fit_reject(
            keyword, industry=industry, services=services
        ):
            continue
        job = _job(keyword, phrases)
        service = _service_for(keyword, phrases)
        if job is None or not service:
            continue
        pools.setdefault((service, job), []).append({**raw, "keyword": keyword})

    pillars: list[dict[str, Any]] = []
    guides: list[dict[str, Any]] = []
    for (service, job), rows in pools.items():
        if job == "pillar":
            chosen = _pillar_cluster(rows, service, phrases)
            if not chosen:
                continue
            primary, secondaries, volume, cpc = _cluster_keywords(chosen)
            pillars.append(
                {
                    "service": service,
                    "page_job": "optimize_service_page",
                    "page_type": "Hub Page",
                    "primary_keyword": primary,
                    "secondary_keywords": secondaries,
                    "search_volume": volume,
                    "cpc": cpc,
                }
            )
            continue
        grouped = _mark_roles(rows, ctx)
        ranked_groups = sorted(
            grouped,
            key=lambda group: -_volume(next((row for row in group if row.get("role") == "Primary"), group[0])),
        )
        kept = 0
        for group in ranked_groups:
            primary, secondaries, volume, cpc = _cluster_keywords(group)
            if volume < guide_volume_floor or len(primary.split()) < 2:
                continue
            guides.append(
                {
                    "service": service,
                    "page_job": "draft_guide",
                    "page_type": _guide_kind(primary),
                    "primary_keyword": primary,
                    "secondary_keywords": secondaries,
                    "search_volume": volume,
                    "cpc": cpc,
                }
            )
            kept += 1
            if kept >= guides_per_service:
                break

    pillars.sort(key=lambda row: (-int(row["search_volume"] or 0), row["service"]))
    guides.sort(key=lambda row: (row["service"], -int(row["search_volume"] or 0), row["primary_keyword"]))
    used = sum(1 + len(row["secondary_keywords"]) for row in [*pillars, *guides])
    return {
        "pillar_pages": pillars,
        "guides": guides,
        "considered": considered,
        "used_keywords": used,
        "unused_keywords": considered - used,
    }


def url_map_rows_from_clusters(classified: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows the URL map table can show: service pillar first, then guides."""
    rows: list[dict[str, Any]] = []
    ordered = [*(classified.get("pillar_pages") or []), *(classified.get("guides") or [])]
    for index, item in enumerate(ordered, start=1):
        service = str(item.get("service") or "")
        primary = str(item.get("primary_keyword") or "")
        secondaries = list(item.get("secondary_keywords") or [])
        job = item.get("page_job")
        if job == "optimize_service_page":
            l2 = None
            notes = (
                f"Optimize the current {service} page. "
                "The primary is the query that page should rank for. "
                "Secondaries are the same search in other words."
            )
        else:
            l2 = phrase_title(primary)
            kind = "blog post" if item.get("page_type") == "Blog" else "guide"
            notes = (
                f"Draft as a {kind} under {service}. "
                "Do not target this query on the service page."
            )
        rows.append(
            {
                "row_no": index,
                "level": 1 if l2 is None else 2,
                "l1_category": service,
                "l2_subcategory": l2,
                "l3_subsubcategory": None,
                "l4_attribution": None,
                "primary_keyword": primary,
                "secondary_keywords": secondaries,
                "secondary_keywords_sheet": secondaries,
                "search_volume": item.get("search_volume"),
                "combined_cluster_volume": item.get("search_volume"),
                "cpc": item.get("cpc"),
                "page_type": item.get("page_type"),
                "page_job": job,
                "cluster": service if l2 is None else phrase_title(primary),
                "status": "Optimize" if job == "optimize_service_page" else "Draft",
                "current_url": None,
                "proposed_url": None,
                "in_scope": "YES",
                "notes": notes,
            }
        )
    return rows
