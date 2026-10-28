"""Choose the keywords a service pillar page should use, and the ones that deserve a guide.

A keyword is kept only when it has a job. Hire-intent variants of a service
optimize that service's existing page: one primary, and only the secondaries
that are the same search in other words. A question, a comparison, or a
how-to becomes a blog or a guide. Large searches are preferred. A service
with no large guide still keeps the questions already in the keyword set.
Everything else is left unused.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.keyword_cluster_roles import RoleContext, finalize_cluster
from app.services.keyword_clustering import _primary_with_secondaries
from app.services.keyword_exclusions import buyer_fit_reject, hard_reject_term
from app.services.keyword_opportunity import _phrases
from app.services.service_scope import partition_scope_rejects
from app.services.topic_naming import phrase_title
from app.services.url_mapping import (
    _SLUG_STOP_WORDS,
    _matching_service,
    _named_city,
    _service_phrases,
    _slug,
    _spell,
    url_n,
)

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
_GUIDES_PER_SERVICE = 40
_SECONDARY_CAP = 25
_PLACE_RE = re.compile(r"(?<![a-z0-9])(?:australia|australian)(?![a-z0-9])")
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
    ("social media advertising", "Social Media Marketing"),
    ("instagram influencer", "Social Media Marketing"),
    ("instagram marketing", "Social Media Marketing"),
    ("facebook marketing", "Social Media Marketing"),
    ("facebook ads", "Social Media Marketing"),
    ("linkedin marketing", "Social Media Marketing"),
    ("tiktok advertising", "Social Media Marketing"),
    ("tiktok marketing", "Social Media Marketing"),
    ("social marketing", "Social Media Marketing"),
    ("smm", "Social Media Marketing"),
    ("social media", "Social Media Marketing"),
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
    ("social media", "smm"),
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
    # "services for seo" names the service after "for". "ads for small business" does not.
    if " for " in f" {text} ":
        after = text.split(" for ", 1)[1]
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", after)
            for alias, _service in _SERVICE_ALIASES
        ):
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


def _place(keyword: str) -> str | None:
    city = _named_city(keyword)
    if city:
        return city
    if _PLACE_RE.search(_norm(keyword)):
        return "australia"
    return None


def _other_market(keyword: str, primary: str) -> bool:
    """A city, a country, or the word local is a different page from the national term."""
    if _place(keyword) not in {None, _place(primary)}:
        return True
    text = _norm(keyword)
    primary_text = _norm(primary)
    local = re.compile(r"(?<![a-z0-9])local(?![a-z0-9])")
    return bool(local.search(text)) and not local.search(primary_text)


def _list_query(keyword: str) -> bool:
    """A best-of or top-list is its own page, not a wording of the service."""
    return bool(re.search(r"(?<![a-z0-9])(?:best|top)(?![a-z0-9])", _norm(keyword)))


def _stacked_wording(keyword: str) -> bool:
    """Two provider words, or both the short and long service name, is not a clean variant."""
    from app.services.keyword_cluster_roles import _PROVIDER_WORDS

    words = _norm(keyword).split()
    providers = [word for word in words if word in _PROVIDER_WORDS or word.rstrip("s") in _PROVIDER_WORDS]
    if len(providers) > 1:
        return True
    text = " ".join(words)
    return bool(re.search(r"(?<![a-z0-9])seo(?![a-z0-9])", text) and "search engine" in text)


def _bare_variant(keyword: str) -> str:
    text = _spell(_norm(keyword))
    for word in ("best", "top", "online", "professional"):
        text = re.sub(rf"(?<![a-z0-9]){word}(?![a-z0-9])", " ", text)
    words: list[str] = []
    for word in text.split():
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def _channel_head(keyword: str, service: str) -> bool:
    """The short name of the service, such as "social media" for social media marketing."""
    text = _spell(_norm(keyword))
    if not any(text == alias and owner == service for alias, owner in _SERVICE_ALIASES):
        return False
    return any(alias.startswith(f"{text} ") and owner == service for alias, owner in _SERVICE_ALIASES)


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
    family = [
        row
        for row in usable
        if _same_search(str(row.get("keyword") or ""), seed_text)
        or _channel_head(str(row.get("keyword") or ""), service)
    ]
    national = [
        row
        for row in family
        if not _other_market(str(row.get("keyword") or ""), seed_text)
    ]
    if not national:
        national = [seed]
    longer = [row for row in national if not _channel_head(str(row.get("keyword") or ""), service)]
    primary = max(longer or national, key=lambda row: (_volume(row), len(str(row.get("keyword") or "").split())))
    primary = {**primary, "role": "Primary"}
    seen = {_bare_variant(_canon(str(primary.get("keyword") or "")))}
    chosen: dict[str, dict[str, Any]] = {}
    for row in national:
        keyword = str(row.get("keyword") or "")
        if _norm(keyword) == _norm(str(primary.get("keyword") or "")) or _stacked_wording(keyword):
            continue
        bare = _bare_variant(_canon(keyword))
        if not bare or bare in seen or _list_query(keyword):
            continue
        current = chosen.get(bare)
        if current is None or (
            -_volume(row),
            len(keyword.split()),
        ) < (
            -_volume(current),
            len(str(current.get("keyword") or "").split()),
        ):
            chosen[bare] = row
    secondaries = sorted(chosen.values(), key=lambda item: (-_volume(item), _norm(str(item.get("keyword") or ""))))
    cluster = [primary, *secondaries][: 1 + _SECONDARY_CAP]
    used = {_norm(str(row.get("keyword") or "")) for row in national}
    return cluster, used


_EXTRA_VOLUME = 80
_EXTRAS_PER_SERVICE = 8


# A search that names one channel, not the whole service. Each one can be its own page.
_SPECIFIC_PAGES: tuple[str, ...] = (
    "instagram influencer",
    "instagram marketing",
    "facebook marketing",
    "facebook ads",
    "linkedin marketing",
    "tiktok advertising",
    "tiktok marketing",
)


def _specific_page(keyword: str) -> str | None:
    text = _spell(_norm(keyword))
    matches = [
        alias
        for alias in _SPECIFIC_PAGES
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text)
    ]
    if not matches:
        return None
    return max(matches, key=len)


def _related_service_pages(
    rows: list[dict[str, Any]],
    used: set[str],
    service: str,
    ctx: RoleContext,
) -> list[dict[str, Any]]:
    """One page per channel search, such as Instagram marketing under social."""
    del ctx
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        keyword = str(row.get("keyword") or "")
        if _norm(keyword) in used or not _usable(keyword) or _other_market(keyword, ""):
            continue
        alias = _specific_page(keyword)
        if alias is None:
            continue
        groups.setdefault(alias, []).append(row)
    pages: list[dict[str, Any]] = []
    ranked_groups = sorted(
        groups.values(),
        key=lambda group: -max(_volume(row) for row in group),
    )
    for group in ranked_groups:
        if max(_volume(row) for row in group) < _EXTRA_VOLUME:
            continue
        primary_row = min(group, key=lambda row: (len(str(row.get("keyword") or "").split()), -_volume(row)))
        primary = str(primary_row.get("keyword") or "").strip()
        seen = {_bare_variant(primary)}
        secondaries: list[str] = []
        for row in sorted(group, key=lambda item: -_volume(item)):
            keyword = str(row.get("keyword") or "").strip()
            bare = _bare_variant(keyword)
            if not keyword or _norm(keyword) == _norm(primary) or not bare or bare in seen:
                continue
            if not _same_search(keyword, primary):
                continue
            seen.add(bare)
            secondaries.append(keyword)
        pages.append(
            {
                "service": service,
                "page_job": "related_service_page",
                "page_type": "Collection",
                "primary_keyword": primary,
                "secondary_keywords": secondaries[:_SECONDARY_CAP],
                "search_volume": _volume(primary_row),
                "cpc": primary_row.get("cpc"),
            }
        )
        if len(pages) >= _EXTRAS_PER_SERVICE:
            break
    return pages


def _guide_kind(keyword: str) -> str:
    text = _norm(keyword)
    if re.search(r"\b(?:tips|ideas|examples|example|trends)\b", text):
        return "Blog"
    return "Guide"


_GUIDE_DROP = {
    "a", "an", "the", "of", "in", "for", "to", "on", "your", "and",
    "is", "are", "does", "do", "what", "how", "much", "digital", "marketing",
    "website", "site", "between", "difference",
}
_GUIDE_WORD = {
    "beginner": "starter",
    "beginners": "starter",
    "increase": "improve",
    "price": "cost",
    "prices": "cost",
}


def _guide_key(keyword: str) -> str:
    """One key for the same question written in different words."""
    raw = _spell(_norm(keyword))
    kind = "versus" if re.search(r"\b(?:vs|versus|difference)\b", raw) else "question"
    words = []
    for word in _canon(raw).replace("versus", " ").replace("vs", " ").split():
        word = _GUIDE_WORD.get(word, word)
        if word in _GUIDE_DROP:
            continue
        words.append(word)
    return f"{kind}:{' '.join(sorted(words))}"


def merge_same_question_guides(guides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one page when several guides ask the same question."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for guide in guides:
        service = str(guide.get("service") or guide.get("l1_category") or "")
        key = (service, _guide_key(str(guide.get("primary_keyword") or "")))
        if key not in grouped:
            order.append(key)
        grouped.setdefault(key, []).append(guide)
    merged: list[dict[str, Any]] = []
    for key in order:
        group = sorted(
            grouped[key],
            key=lambda row: (-int(row.get("search_volume") or 0), str(row.get("primary_keyword") or "")),
        )
        lead = dict(group[0])
        secondaries = [str(item) for item in (lead.get("secondary_keywords") or [])]
        seen = {_norm(str(lead.get("primary_keyword") or ""))}
        seen.update(_norm(item) for item in secondaries)
        for other in group[1:]:
            for keyword in [other.get("primary_keyword"), *(other.get("secondary_keywords") or [])]:
                text = str(keyword or "").strip()
                if text and _norm(text) not in seen:
                    seen.add(_norm(text))
                    secondaries.append(text)
        lead["secondary_keywords"] = secondaries[:_SECONDARY_CAP]
        merged.append(lead)
    return merged


def build_pillar_and_guide_clusters(
    keywords: list[dict[str, Any]],
    *,
    offer_synonyms: list[dict[str, Any]] | None = None,
    services: list[str] | None = None,
    industry: str | None = None,
    guide_volume_floor: int = _GUIDE_VOLUME_FLOOR,
    guides_per_service: int = _GUIDES_PER_SERVICE,
    prioritization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the service-page clusters and the blog or guide clusters worth writing."""
    if prioritization:
        keywords, _dropped = partition_scope_rejects(
            [row for row in keywords if isinstance(row, dict)],
            prioritization,
        )
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
            chosen, used = _pillar_cluster(rows, service, phrases)
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
            pillars.extend(_related_service_pages(rows, used, service, ctx))
            continue
        grouped = _mark_roles(rows, ctx)
        ranked_groups = sorted(
            grouped,
            key=lambda group: -_volume(next((row for row in group if row.get("role") == "Primary"), group[0])),
        )
        eligible: list[dict[str, Any]] = []
        for group in ranked_groups:
            primary, secondaries, volume, cpc = _cluster_keywords(group)
            if len(primary.split()) < 2 or volume <= 0:
                continue
            eligible.append(
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
        # Keep the large guides. A service that has none still keeps the real
        # questions already in the keyword set, even when each one is small.
        strong = [row for row in eligible if int(row["search_volume"]) >= guide_volume_floor]
        for row in (strong or eligible)[:guides_per_service]:
            guides.append(row)

    pillars.sort(key=lambda row: (-int(row["search_volume"] or 0), row["service"]))
    guides = merge_same_question_guides(
        sorted(guides, key=lambda row: (row["service"], -int(row["search_volume"] or 0), row["primary_keyword"]))
    )
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
    pillars = list(classified.get("pillar_pages") or [])
    guides = list(classified.get("guides") or [])
    hub_volume = {
        str(item.get("service") or ""): int(item.get("search_volume") or 0)
        for item in pillars
        if item.get("page_job") == "optimize_service_page"
    }
    ordered: list[dict[str, Any]] = []
    for service in sorted(hub_volume, key=lambda name: (-hub_volume[name], name)):
        ordered.extend(
            item for item in pillars if item.get("service") == service and item.get("page_job") == "optimize_service_page"
        )
        ordered.extend(
            item for item in pillars if item.get("service") == service and item.get("page_job") == "related_service_page"
        )
        ordered.extend(item for item in guides if item.get("service") == service)
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
        elif job == "related_service_page":
            l2 = phrase_title(primary)
            notes = (
                f"Separate page under {service}. "
                "This is a different search from the main service page."
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
                "status": "Optimize" if job in {"optimize_service_page", "related_service_page"} else "Draft",
                "current_url": None,
                "proposed_url": None,
                "in_scope": "YES",
                "notes": notes,
            }
        )
    return rows


def _url_slug(text: str) -> str:
    parts = [part for part in _slug(text).split("-") if part and part not in _SLUG_STOP_WORDS]
    slug = "-".join(parts)[:60].strip("-")
    return slug or _slug(text)[:60]


def _content_prefix(pages: list[dict[str, Any]]) -> str:
    """Folder this site already uses for articles. A flat site keeps posts at the root."""
    counts: dict[str, int] = {}
    for page in pages:
        segments = [part for part in url_n(str(page.get("path") or page.get("url") or "")).split("/") if part]
        if len(segments) >= 2 and segments[0] in {"blog", "blogs", "guides", "guide", "articles"}:
            counts[segments[0]] = counts.get(segments[0], 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda name: counts[name])


def _live_paths(pages: list[dict[str, Any]] | None) -> set[str]:
    """Paths that are actually on the site. Template drafts are not live pages."""
    live: set[str] = set()
    for page in pages or []:
        if isinstance(page, dict):
            raw = str(page.get("path") or page.get("url") or "")
        else:
            raw = str(page or "")
        path = url_n(raw)
        if not path or path == "/" or path.startswith("/pxl-template"):
            continue
        live.add(path)
    return live


def _slug_forms(slug: str) -> list[str]:
    """Spellings and the service-page suffix this site already uses."""
    spell = _spell(slug.strip("/"))
    forms: list[str] = []

    def add(value: str) -> None:
        value = value.strip("-")
        if value and value not in forms:
            forms.append(value)

    add(spell)
    if spell.endswith("-services"):
        add(spell[: -len("-services")])
    elif spell.endswith("-service"):
        add(spell[: -len("-service")])
    else:
        add(f"{spell}-services")
    if spell.endswith("-marketing"):
        add(f"{spell[: -len('-marketing')]}-management")
    elif spell.endswith("-management"):
        add(f"{spell[: -len('-management')]}-marketing")
    return forms


def _existing_path(proposed: str, live: set[str], *, hub: bool) -> str | None:
    """The live path for this row.

    A service hub can already exist under the site's own slug: Australian
    spelling, a "-services" ending, or "management" where the keyword says
    "marketing". A city page or a template draft is not that hub.
    """
    wanted = url_n(proposed)
    if wanted in live:
        return wanted
    slug = wanted.strip("/").split("/")[-1]
    if not slug:
        return None
    forms = set(_slug_forms(slug))
    exact = {_spell(slug), slug}
    candidates: list[tuple[int, int, int, str]] = []
    for path in live:
        segments = [part for part in path.split("/") if part]
        if not segments or (hub and len(segments) != 1):
            continue
        last = segments[-1]
        if last not in forms and _spell(last) not in forms:
            continue
        candidates.append((0 if last in exact or _spell(last) in exact else 1, len(segments), len(path), path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]


def assign_sheet_urls(
    rows: list[dict[str, Any]],
    *,
    pages: list[dict[str, Any]] | None = None,
    service_paths: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fill Current URL from a live page, otherwise Proposed URL for the page to create."""
    live = _live_paths(pages)
    hubs = {name: url_n(path) for name, path in (service_paths or {}).items() if path}
    prefix = _content_prefix(list(pages or []))
    for row in rows:
        service = str(row.get("l1_category") or "")
        primary = str(row.get("primary_keyword") or "")
        job = row.get("page_job")
        hub = hubs.get(service) or f"/{_url_slug(service)}"
        if job == "optimize_service_page":
            proposed = hub
        elif job == "related_service_page":
            proposed = url_n(f"{hub}/{_url_slug(primary)}")
        else:
            folder = f"/{prefix}" if prefix else ""
            proposed = url_n(f"{folder}/{_url_slug(primary)}")
        existing = _existing_path(proposed, live, hub=job == "optimize_service_page")
        _write_url(row, existing, proposed)
    return rows


def _write_url(row: dict[str, Any], existing: str | None, proposed: str) -> None:
    if existing:
        row["current_url"] = existing
        row["proposed_url"] = None
        row["create_url"] = None
        row["selected_url"] = existing
        row["action"] = "OPTIMIZE_EXISTING"
        row["url_status"] = "existing_url"
        return
    row["current_url"] = None
    row["proposed_url"] = proposed
    row["create_url"] = proposed
    row["selected_url"] = proposed
    row["action"] = "CREATE"
    row["url_status"] = "new_proposed_url"


def apply_live_urls(rows: list[dict[str, Any]], pages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Point a saved row at the live page. Leave its proposed path unchanged when none exists."""
    live = _live_paths(pages)
    kept: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = " ".join(
            str(row.get(key) or "")
            for key in ("primary_keyword", "proposed_url", "current_url", "l2_subcategory", "cluster", "section_label")
        ).lower()
        if "tiktok" in label:
            continue
        proposed = str(row.get("proposed_url") or row.get("create_url") or row.get("selected_url") or "").strip()
        if not proposed:
            kept.append(row)
            continue
        hub = row.get("page_job") == "optimize_service_page" or (
            not str(row.get("l2_subcategory") or "").strip() and str(row.get("level") or "") in {"1", "L1"}
        )
        _write_url(row, _existing_path(proposed, live, hub=hub), url_n(proposed))
        kept.append(row)
    return kept
