"""URL mapping — execute the topic decision against real URLs.

URL mapping runs LAST. By the time a cluster arrives here,
``topic_classification`` has already compared it against the site map and
decided *what it is* (existing / needs optimization / needs consolidation /
new / supporting / cannibalization risk / out of scope / uncertain). This module
picks the URL that decision implies and scores the evidence for it — it does not
get to invent the topic structure a second time.

CLUSTER (+ topic_status from Phase 5)
      ↓
PRIMARY KEYWORD → SEARCH INTENT → SERP ANALYSIS
      ↓
WEBSITE URL CRAWL → URL CANDIDATES
      ↓
Semantic / Intent / Keyword-topic / Ranking / Business scoring
   (real on-site matches only — fallback and proposed-URL rows never score)
      ↓
URL SCORE → HIGH / MEDIUM / LOW
      ↓
topic_status governs:
  existing_url | existing_url_needs_optimization | existing_url_needs_consolidation
  | new_proposed_url | no_dedicated_url
      ↓
duplicate-proposed-URL check → FINAL URL MAP (every row with a reason)
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.services.content_pipeline import (
    identify_primary_keyword,
    identify_secondary_keywords,
    primary_keyword_metrics,
)
from app.services.keyword_cluster_structure import stable_cluster_id
from app.services.keyword_opportunity import detect_intent
from app.services.page_clusters import (
    cdd_terms_from_commercial,
    classify_audit_page,
    match_cdd_page,
)

_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "is", "are", "your", "our",
}
_HIGH_THRESHOLD = 65
_MEDIUM_THRESHOLD = 35

# The five scoring components and their maxima. They sum to 100, so a row
# with every signal available is scored out of 100; a row missing a signal
# is scored out of the points it could actually obtain.
COMPONENT_MAX = {
    "semantic_match": 25.0,
    "intent_match": 15.0,
    "keyword_topic_match": 25.0,
    "ranking_evidence": 20.0,
    "traffic_business": 15.0,
}
COMPONENT_LABELS = {
    "semantic_match": "title/slug/heading match",
    "intent_match": "intent fit",
    "keyword_topic_match": "keyword coverage",
    "ranking_evidence": "ranking evidence (Search Console / SERP)",
    "traffic_business": "traffic and business value",
}
# Semantic sources and their weights: the title says most about a page,
# then the slug, then the main heading.
_SEMANTIC_WEIGHTS = {"title": 0.4, "slug": 0.35, "h1": 0.25}

_INTENT_PAGE_AFFINITY = {
    "transactional": {"service", "subservice", "sub_service", "service_hub", "services", "sub_services", "location", "product", "landing"},
    "commercial": {"service", "subservice", "sub_service", "services", "comparison", "guides", "location", "sub_services"},
    "informational": {"blog", "guides", "guide", "article", "hub", "spoke"},
    "navigational": {"home", "service_hub", "other"},
}


# Crawl candidate reasons that mean a real on-site page matched the keyword.
_REAL_CRAWL_MATCH_REASONS = frozenset({
    "suggested_url_exact",
    "keyword_topic_overlap",
    "title_or_keyword_overlap",
})
# A cluster survived Phase 5's relevance gate to exist at all — these are the
# stronger reasons (direct CDD/service/page match) vs. weaker ones (gap-only,
# or "we had no business evidence to check against") that warrant a human look
# before this row is treated as committed scope, not just theoretically kept.
_STRONG_IN_SCOPE_REASONS = {
    "seed_exact", "seed_phrase", "seed_related", "cdd_phrase",
    "service_overlap", "page_overlap", "theme_overlap",
}


# Folders that organise URLs but are not categories. The workbook's L1 for
# /product-category/accessories/ is the category, not "Product Category".
_STRUCTURAL_PREFIXES = frozenset(
    {
        "product-category",
        "product-categories",
        "product",
        "products",
        "category",
        "categories",
        "shop",
        "services",
        "service",
        "blog",
        "blogs",
        "guides",
        "guide",
        "pricing",
        "news",
        "locations",
        "location",
        "brands",
        "brand",
    }
)


def _title_segment(text: str) -> str:
    raw = " ".join(str(text or "").replace("_", " ").split()).strip()
    if not raw:
        return ""
    # Slugs become title case. A name that already has capitals is kept,
    # so a pillar called "SEO" does not turn into "Seo".
    if "-" in raw or raw.islower():
        return raw.replace("-", " ").title()
    return raw.replace("-", " ")


# Words that describe the same offer. The workbook keeps these in the
# secondary-keyword column of the category page. They do not become a title
# or a second URL. "near me", "company", and "sydney" are not products.
_LINE_MODIFIERS = (
    "near me",
    "gold coast",
    "companies",
    "company",
    "businesses",
    "business",
    "agencies",
    "agency",
    "services",
    "service",
    "consultants",
    "consultant",
    "consultancy",
    "firms",
    "firm",
    "providers",
    "provider",
    "specialists",
    "specialist",
    "experts",
    "expert",
    "freelancers",
    "freelancer",
    "australia",
    "australian",
    "sydney",
    "melbourne",
    "brisbane",
    "perth",
    "adelaide",
    "canberra",
    "hobart",
    "darwin",
    "nsw",
    "vic",
    "qld",
    "tas",
    "act",
    "online",
    "meaning",
    "definition",
    "defined",
)
_LINE_FILLER = {
    "in", "for", "the", "a", "an", "and", "of", "to", "with", "is", "are", "your", "our",
}
# A how-to, a comparison, a price page, and a best-of list are different pages.
# A company/near-me/city phrasing of the same offer is not.
_DISTINCT_PAGE = re.compile(
    r"^(?:how to|how do|how does|why |when to|should i|best |top )\b"
    r"|\b(?:pricing|prices?|costs?|how much|quotes?|rates?|buy|buying|purchase)\b"
    r"|\b(?:vs|versus)\b"
)


def _spell(text: str) -> str:
    """One spelling for a match. The page title still uses the service's own name."""
    return text.replace("optimization", "optimisation").replace("organization", "organisation")


def _clean_offer_line(keyword: str) -> str | None:
    """The offer named by a keyword, after filler and commercial modifiers.

    "email marketing agency sydney" and "email marketing" are the same line.
    "technical seo" is not the same line as "seo": a real sub-service keeps
    its own words. A how-to, a comparison, a price page, and a best-of list
    are different pages and return None.
    """
    raw = " ".join(str(keyword or "").lower().split())
    if not raw or _DISTINCT_PAGE.search(raw):
        return None
    text = _spell(raw)
    text = re.sub(r"^(?:what is|what are)\s+", "", text)
    for mod in sorted(_LINE_MODIFIERS, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(mod)}\b", " ", text)
    tokens = [t for t in text.split() if t not in _LINE_FILLER]
    # "seo marketing" is still the SEO offer. "email marketing" is not.
    if "marketing" in tokens:
        without = [t for t in tokens if t != "marketing"]
        if " ".join(without) in ("seo", "search engine optimisation", ""):
            tokens = without
    text = " ".join(tokens)
    return text or None


def _offer_index(synonym_rows: list[dict[str, Any]] | None) -> dict[str, str]:
    """Map every name of a service onto that service.

    The names come from keyword research: the service the client sells and the
    alternate names the synonym step found for it. The same table works for
    every service. A name that is not in the table stays its own offer.
    """
    index: dict[str, str] = {}
    for row in synonym_rows or []:
        if not isinstance(row, dict):
            continue
        service = str(row.get("service") or "").strip()
        if not service:
            continue
        canon = _clean_offer_line(service) or _spell(service.lower())
        names = [service, *(row.get("suggested") or []), *(row.get("added") or [])]
        for name in names:
            line = _clean_offer_line(str(name)) or _spell(" ".join(str(name).lower().split()))
            if line:
                index[line] = canon
    return index


def _publishable_head(keyword: str, offer_index: dict[str, str] | None = None) -> str | None:
    """Page line for this keyword.

    Modifier wording and a known alternate name point at the one service page.
    Anything else keeps the line the keyword itself names.
    """
    line = _clean_offer_line(keyword)
    if not line:
        return None
    if offer_index and line in offer_index:
        return offer_index[line]
    return line


def _retitle_modifier_variant(
    entry: dict[str, Any],
    primary: str,
    offer_index: dict[str, str] | None = None,
) -> None:
    """Point a new modifier or alternate name at the one page for that offer."""
    if entry.get("current_url"):
        return
    head = _publishable_head(primary, offer_index)
    if not head or _spell(" ".join(str(primary or "").lower().split())) == head:
        return
    from app.services.create_content import path_for_page_type
    from app.services.topic_naming import phrase_title

    hub = path_for_page_type("service", head)
    entry["proposed_url"] = hub
    entry["create_url"] = hub
    entry["selected_url"] = hub
    entry["target_url"] = hub
    entry["l1_category"] = phrase_title(head)
    entry["l2_subcategory"] = None
    entry["l3_subsubcategory"] = None
    entry["l4_attribution"] = None
    entry["level"] = 1
    entry["page_type"] = "Hub Page"
    entry["ia_page_type"] = "service"
    _set_priority(entry)
    entry.setdefault("decision_trail", []).append(
        f"Modifier or alternate name of '{head}'; proposal points at the one page for that offer ({hub})."
    )


def _fold_same_line_pages(
    entries: list[dict[str, Any]],
    offer_index: dict[str, str] | None = None,
) -> None:
    """One offer, one page. Other names and modifier queries become secondaries.

    This is the same rule for every service. "email marketing agency" sits on
    the Email Marketing page. "seo" and "search engine optimisation" sit on
    one page when keyword research recorded them as names of the same service.
    A different offer, such as technical SEO, keeps its own page.
    """
    groups: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        head = _publishable_head(str(entry.get("primary_keyword") or ""), offer_index)
        if not head:
            head = _publishable_head(str(entry.get("l1_category") or ""), offer_index)
        if not head:
            continue
        entry["offer_line"] = head
        groups.setdefault(head, []).append(index)
    drop: set[int] = set()
    for _head, indexes in groups.items():
        if len(indexes) < 2:
            continue
        indexes.sort(
            key=lambda i: (
                1 if entries[i].get("current_url") else 0,
                float(entries[i].get("search_volume") or 0),
            ),
            reverse=True,
        )
        owner = entries[indexes[0]]
        owner_live = url_n(owner["current_url"]) if owner.get("current_url") else None
        seconds = [
            str(s).strip()
            for s in (owner.get("secondary_keywords") or owner.get("secondary_keywords_sheet") or [])
            if str(s).strip()
        ]
        seen = {s.lower() for s in seconds}
        seen.add(str(owner.get("primary_keyword") or "").strip().lower())
        for index in indexes[1:]:
            other = str(entries[index].get("primary_keyword") or "").strip()
            other_live = url_n(entries[index]["current_url"]) if entries[index].get("current_url") else None
            if other_live and other_live != owner_live:
                # A row with its own live page is never dropped: two live pages
                # for one offer is a decision for a person, not a silent fold.
                note = (
                    f"Same offer line '{_head}' as {owner.get('current_url') or owner.get('proposed_url')}; "
                    f"two pages compete for it. Review which page owns the offer."
                )
                entries[index].setdefault("collisions", []).append(note)
                entries[index]["needs_human_review"] = True
                entries[index].setdefault("decision_trail", []).append(note)
                owner_note = (
                    f"Same offer line '{_head}' as {entries[index]['current_url']} "
                    f"(cluster '{entries[index].get('cluster')}'); two pages compete for it."
                )
                owner.setdefault("collisions", []).append(owner_note)
                owner["needs_human_review"] = True
                owner.setdefault("decision_trail", []).append(owner_note)
                continue
            owner.setdefault("decision_trail", []).append(
                f"Folded '{other}' into this row: same offer line '{_head}'."
            )
            if other and other.lower() not in seen:
                seconds.insert(0, other)
                seen.add(other.lower())
            for extra in entries[index].get("secondary_keywords") or []:
                text = str(extra).strip()
                if text and text.lower() not in seen:
                    seconds.append(text)
                    seen.add(text.lower())
            drop.add(index)
        owner["secondary_keywords"] = seconds[:10]
        owner["secondary_keywords_sheet"] = seconds[:10]
    if drop:
        entries[:] = [entry for index, entry in enumerate(entries) if index not in drop]


def _taxonomy_levels(
    url: str | None,
    *,
    pillar: str | None = None,
    attribution: str | None = None,
) -> dict[str, Any]:
    """Category columns the way the Category & URL Mapping sheet is built.

    Structural folders (product-category, services, blog, locations, brands)
    are skipped. A pillar name, when Phase 6a has one, is the L1 parent and the
    URL's own categories shift down. No segment is discarded: L1-L3 are the
    first three category names and L4 (attribution) is whatever lies deeper,
    joined. When the URL is not that deep, L4 is the cluster's own narrowing
    dimension (a location or an audience) if it has one.

    ``level`` is the number of filled category levels, counting L4.
    """
    raw = str(url or "").strip()
    path = urlparse(raw).path if "://" in raw else raw
    segments = [s.strip() for s in path.split("/") if s.strip()]
    kind = None
    if segments and segments[0].lower() in ("locations", "location"):
        kind = "landing"
    elif segments and segments[0].lower() in ("brands", "brand"):
        kind = "brand"
    names = [_title_segment(s) for s in segments if s.lower() not in _STRUCTURAL_PREFIXES]
    parent = _title_segment(pillar or "")
    if parent and names and parent.lower() != names[0].lower():
        names = [parent, *names]
    elif parent and not names:
        names = [parent]
    deeper = names[3:]
    l4 = " / ".join(deeper) if deeper else None
    if not l4 and attribution:
        already = {n.lower() for n in names}
        if attribution.lower() not in already and not any(attribution.lower() in n.lower() for n in names):
            l4 = attribution
    top = names[:3]
    return {
        "level": len(top) + (1 if l4 else 0),
        "l1_category": top[0] if len(top) >= 1 else None,
        "l2_subcategory": top[1] if len(top) >= 2 else None,
        "l3_subsubcategory": top[2] if len(top) >= 3 else None,
        "l4_attribution": l4,
        "sheet_kind": kind,
    }


def _label_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _word_forms(word: str) -> set[str]:
    forms = {word}
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        forms.add(word[:-1])
    elif len(word) > 2:
        forms.add(word + "s")
    return forms


def _tokens_fit_inside(parent: str, child: str) -> bool:
    """True when every word of the broader page appears in the more specific one.

    The workbook puts "King Electric Adjustable Bed" under "King Adjustable Bed":
    the extra word is the attribution, so the parent name is not one contiguous phrase.
    """
    parent_words = _label_tokens(parent)
    child_words = _label_tokens(child)
    if not parent_words or len(parent_words) >= len(child_words):
        return False
    used: set[int] = set()
    for word in parent_words:
        forms = _word_forms(word)
        found = None
        for index, other in enumerate(child_words):
            if index in used:
                continue
            if forms & _word_forms(other):
                found = index
                break
        if found is None:
            return False
        used.add(found)
    return True


def _page_label(entry: dict[str, Any]) -> str:
    from app.services.topic_naming import phrase_title

    return phrase_title(str(entry.get("primary_keyword") or entry.get("keyword") or ""))


def _slug_phrase(entry: dict[str, Any]) -> str:
    raw = str(entry.get("current_url") or entry.get("proposed_url") or entry.get("selected_url") or "")
    path = urlparse(raw).path if "://" in raw else raw
    parts = [part for part in path.split("/") if part and part.lower() not in _STRUCTURAL_PREFIXES]
    if not parts:
        return ""
    return parts[-1].replace("-", " ")


def _first_folder(entry: dict[str, Any]) -> str:
    raw = str(entry.get("current_url") or entry.get("proposed_url") or entry.get("selected_url") or "")
    path = urlparse(raw).path if "://" in raw else raw
    parts = [part for part in path.split("/") if part]
    return parts[0].lower() if parts else ""


def _named_city(text: str) -> str | None:
    from app.services.keyword_service_contract import _AU_CITIES

    raw = " ".join(str(text or "").lower().split())
    if re.search(r"\bnear me\b", raw):
        return "near me"
    cities = sorted(set(_AU_CITIES) | {"sunshine coast", "geelong"}, key=len, reverse=True)
    for city in cities:
        if re.search(rf"\b{re.escape(city)}\b", raw):
            return city
    return None


def _service_phrases(
    entries: list[dict[str, Any]],
    *,
    commercial: dict[str, Any] | None,
    offer_synonyms: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Map a cleaned service name onto the label the sheet uses as L1."""
    phrases: dict[str, str] = {}

    def add(alias: str, display: str, *, overwrite: bool = False) -> None:
        cleaned = _clean_offer_line(alias) or _spell(" ".join(str(alias or "").lower().split()))
        label = str(display or "").strip()
        if not cleaned or not label:
            return
        if overwrite or cleaned not in phrases:
            phrases[cleaned] = label
        words = cleaned.split()
        if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
            singular_phrase = " ".join([*words[:-1], words[-1][:-1]])
            if singular_phrase not in phrases:
                phrases[singular_phrase] = label

    for row in offer_synonyms or []:
        if not isinstance(row, dict) or not row.get("service"):
            continue
        display = str(row["service"]).strip()
        add(display, display, overwrite=True)
        for key in ("suggested", "added"):
            for name in row.get(key) or []:
                add(str(name), display)
    commercial = commercial or {}
    selected = commercial.get("selected_services") or []
    if isinstance(selected, dict):
        selected = selected.get("services") or selected.get("names") or []
    for item in selected:
        name = item.get("name") or item.get("service") or item.get("label") if isinstance(item, dict) else item
        if name:
            add(str(name), str(name).strip(), overwrite=True)
    for entry in entries:
        pillar = str(entry.get("pillar") or "").strip()
        if pillar:
            add(pillar, pillar)
    return phrases


def _matching_service(text: str, phrases: dict[str, str]) -> str | None:
    raw = _spell(" ".join(str(text or "").lower().split()))
    if not raw:
        return None
    cleaned = _clean_offer_line(raw) or raw
    if cleaned in phrases:
        return phrases[cleaned]
    for phrase in sorted(phrases, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", raw) or re.search(rf"\b{re.escape(phrase)}\b", cleaned):
            return phrases[phrase]
    return None


def _same_label(left: str, right: str) -> bool:
    return _spell(left.lower()) == _spell(right.lower())


def _fallback_category(keyword: str) -> str | None:
    """Parent category for a page that is not one of the named services.

    The sheet still gives that page an L1. The longer phrase wins, so
    "digital marketing agency" sits under Digital Marketing.
    """
    raw = _spell(" ".join(str(keyword or "").lower().split()))
    categories = (
        ("digital marketing", "Digital Marketing"),
        ("content marketing", "Content Marketing"),
        ("online marketing", "Online Marketing"),
        ("marketing agency", "Marketing Agency"),
    )
    for phrase, display in categories:
        if re.search(rf"\b{re.escape(phrase)}\b", raw):
            return display
    return None


def apply_workbook_taxonomy(
    entries: list[dict[str, Any]],
    *,
    commercial: dict[str, Any] | None = None,
    offer_synonyms: list[dict[str, Any]] | None = None,
) -> None:
    """L1-L4 the way the Category & URL Mapping sheet is filled.

    L1 is the parent category (the service, the local section, or Shop by Brand).
    L2 is this page's name. L3 is this page when it is a more specific version of
    another page in that same L1. L4 is the same step one level further down.
    The reference sheet leaves L4 blank unless a page is that specific.
    """
    phrases = _service_phrases(entries, commercial=commercial, offer_synonyms=offer_synonyms)
    tagged: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = _page_label(entry)
        slug = _slug_phrase(entry)
        folder = _first_folder(entry)
        raw_primary = _spell(" ".join(str(entry.get("primary_keyword") or "").lower().split()))
        slug_plain = _spell(slug.lower()) if slug else ""
        city = _named_city(str(entry.get("primary_keyword") or "")) or _named_city(slug)
        # The hub is the category page itself. A longer keyword that only
        # contains the category ("conversion optimization experts") is a child.
        is_hub = raw_primary in phrases or (bool(slug_plain) and slug_plain in phrases)
        kind = None
        l1 = l2 = None
        if folder in {"brands", "brand"}:
            kind = "brand"
            l1 = "Shop by Brand"
            l2 = label
        elif is_hub:
            l1 = phrases.get(raw_primary) or phrases.get(slug_plain) or label
            l2 = None
        elif folder in {"locations", "location"} or city:
            # A city is part of the keyword. L1 stays the service, not "Melbourne / Local".
            kind = "landing"
            service = (
                _matching_service(str(entry.get("primary_keyword") or ""), phrases)
                or _matching_service(str(entry.get("offer_line") or ""), phrases)
                or _matching_service(slug, phrases)
            )
            if service and not _same_label(label, service):
                l1, l2 = service, label
            elif service:
                l1, l2 = service, None
            else:
                l1, l2 = label, None
        else:
            service = (
                _matching_service(str(entry.get("primary_keyword") or ""), phrases)
                or _matching_service(str(entry.get("offer_line") or ""), phrases)
                or _matching_service(slug, phrases)
            )
            if service and _same_label(label, service):
                l1, l2 = service, None
            elif service:
                l1, l2 = service, label
            else:
                fallback = _fallback_category(str(entry.get("primary_keyword") or ""))
                if fallback and _same_label(label, fallback):
                    l1, l2 = fallback, None
                elif fallback:
                    l1, l2 = fallback, label
                else:
                    l1, l2 = label, None
        entry["_taxonomy_label"] = label
        entry["l1_category"] = l1
        entry["l2_subcategory"] = l2
        entry["l3_subsubcategory"] = None
        entry["l4_attribution"] = None
        entry["_taxonomy_kind"] = kind
        tagged.append(entry)

    for entry in sorted(tagged, key=lambda row: len(_label_tokens(row.get("_taxonomy_label") or ""))):
        if not entry.get("l2_subcategory"):
            continue
        parents = [
            other
            for other in tagged
            if other is not entry
            and other.get("l1_category") == entry.get("l1_category")
            and other.get("l2_subcategory")
            and _tokens_fit_inside(str(other.get("_taxonomy_label") or ""), str(entry.get("_taxonomy_label") or ""))
        ]
        if not parents:
            continue
        parent = max(parents, key=lambda row: len(_label_tokens(row.get("_taxonomy_label") or "")))
        own = str(entry.get("_taxonomy_label") or "")
        if parent.get("l3_subsubcategory"):
            entry["l2_subcategory"] = parent.get("l2_subcategory")
            entry["l3_subsubcategory"] = parent.get("_taxonomy_label")
            entry["l4_attribution"] = own
        else:
            entry["l2_subcategory"] = parent.get("_taxonomy_label")
            entry["l3_subsubcategory"] = own
            entry["l4_attribution"] = None

    for entry in tagged:
        kind = entry.pop("_taxonomy_kind", None)
        entry.pop("_taxonomy_label", None)
        filled = sum(
            1
            for key in ("l1_category", "l2_subcategory", "l3_subsubcategory", "l4_attribution")
            if entry.get(key)
        )
        entry["level"] = filled
        if kind == "brand":
            entry["page_type"] = "Brand Collection"
        elif kind == "landing":
            entry["page_type"] = "Landing Page"
        elif entry.get("l3_subsubcategory") or entry.get("l4_attribution"):
            entry["page_type"] = "Attribution Collection"
        elif entry.get("l2_subcategory"):
            entry["page_type"] = "Collection"
        else:
            entry["page_type"] = "Hub Page"
        _set_priority(entry)


def _sheet_page_type(taxonomy: dict[str, Any]) -> str:
    """Page Type labels on the sheet, consistent with the levels filled.

    An L4 attribution makes an Attribution Collection; otherwise the depth of
    the category levels decides: one level is a hub, two a collection, three a
    sub-collection.
    """
    if taxonomy.get("sheet_kind") == "landing":
        return "Landing Page"
    if taxonomy.get("sheet_kind") == "brand":
        return "Brand Collection"
    if taxonomy.get("l4_attribution"):
        return "Attribution Collection"
    categories = sum(1 for k in ("l1_category", "l2_subcategory", "l3_subsubcategory") if taxonomy.get(k))
    if categories >= 3:
        return "Sub-collection"
    if categories == 2:
        return "Collection"
    return "Hub Page"


def _sheet_priority(
    page_type: str,
    *,
    live: bool,
    band: str,
    volume: Any = None,
    difficulty: Any = None,
) -> str:
    """P0-P3 from page type and opportunity (see _priority_with_reason)."""
    return _priority_with_reason(page_type, live=live, band=band, volume=volume, difficulty=difficulty)[0]


def _sheet_status(current_url: str | None) -> str:
    """LIVE when the row has a current URL, NEW when it only has a proposal."""
    return "LIVE" if current_url else "NEW"


def _sheet_secondary_keywords(
    primary: str,
    cluster: dict[str, Any],
    *,
    geography: Any = None,
) -> list[str]:
    """Secondary keywords are the other measured phrases in this cluster.

    The column is not padded with city, country, buy, best, or online variants.
    Those appear only when keyword research actually returned them.
    """
    del geography  # market is not a reason to invent a keyword
    primary_key = " ".join(str(primary or "").split()).lower()
    out: list[str] = []
    seen: set[str] = set()
    if primary_key:
        seen.add(primary_key)
    for extra in identify_secondary_keywords(cluster, limit=10):
        text = " ".join(str(extra).split())
        key = text.lower()
        if not text or key in seen:
            continue
        out.append(text)
        seen.add(key)
        if len(out) >= 10:
            break
    return out


def _cluster_measured_volume(cluster: dict[str, Any], primary_volume: Any) -> int | None:
    """Sum of measured volumes in the cluster. Unmeasured phrases add nothing."""
    total = 0
    seen = False
    for row in cluster.get("keywords") or []:
        if not isinstance(row, dict):
            continue
        volume = row.get("volume")
        if volume is None or volume == "":
            continue
        try:
            total += int(round(float(volume)))
        except (TypeError, ValueError):
            continue
        seen = True
    if seen:
        return total
    if primary_volume is None or primary_volume == "":
        return None
    try:
        return int(round(float(primary_volume)))
    except (TypeError, ValueError):
        return None


def _market_from(cluster: dict[str, Any], commercial: dict[str, Any] | None) -> Any:
    commercial = commercial or {}
    return (
        cluster.get("geography")
        or cluster.get("location")
        or commercial.get("geography")
        or commercial.get("location")
        or commercial.get("service_area")
    )


def _apply_sheet_columns(
    entry: dict[str, Any],
    cluster: dict[str, Any],
    *,
    commercial: dict[str, Any] | None = None,
    pillar: str | None = None,
) -> None:
    """Fill the Category & URL Mapping columns from the decision already made."""
    url = entry.get("current_url") or entry.get("proposed_url") or entry.get("selected_url")
    taxonomy = _taxonomy_levels(
        url,
        pillar=pillar or entry.get("pillar"),
        attribution=_attribution(cluster, str(entry.get("primary_keyword") or "")),
    )
    entry["difficulty"] = cluster.get("avg_difficulty")
    _set_taxonomy(entry, taxonomy)
    entry["status"] = _sheet_status(entry.get("current_url"))
    entry["secondary_keywords_sheet"] = _sheet_secondary_keywords(
        str(entry.get("primary_keyword") or ""),
        cluster,
        geography=_market_from(cluster, commercial),
    )
    entry["combined_cluster_volume"] = _cluster_measured_volume(
        cluster, entry.get("search_volume")
    )
    entry["in_scope"] = _in_scope_status(cluster)
    _set_priority(entry)


def _set_taxonomy(entry: dict[str, Any], taxonomy: dict[str, Any]) -> None:
    entry["level"] = taxonomy["level"]
    entry["l1_category"] = taxonomy["l1_category"]
    entry["l2_subcategory"] = taxonomy["l2_subcategory"]
    entry["l3_subsubcategory"] = taxonomy["l3_subsubcategory"]
    entry["l4_attribution"] = taxonomy["l4_attribution"]
    entry["page_type"] = _sheet_page_type(taxonomy)


def _set_priority(entry: dict[str, Any]) -> None:
    priority, reason = _priority_with_reason(
        str(entry.get("page_type") or ""),
        live=bool(entry.get("current_url")),
        band=str(entry.get("score_band") or ""),
        volume=entry.get("combined_cluster_volume") if entry.get("combined_cluster_volume") is not None else entry.get("search_volume"),
        difficulty=entry.get("difficulty"),
    )
    entry["priority"] = priority
    entry["priority_reason"] = reason


def _estimate_products(cluster: dict[str, Any], commercial: dict[str, Any] | None) -> int | None:
    """Best-effort count of CDD products/offerings this URL's keywords cover.

    Only meaningful when the client actually has a product/offering list on
    file (commercial_scope). Returns None rather than a guessed number when
    there's nothing to count against — a service business with no product
    catalog shouldn't get a fabricated "Est. Products" figure.
    """
    commercial = commercial or {}
    catalog = list(commercial.get("products") or commercial.get("products_for_promotion") or [])
    if not catalog:
        return None
    cluster_tokens: set[str] = set()
    for row in cluster.get("keywords") or []:
        if isinstance(row, dict):
            cluster_tokens |= _tokens(str(row.get("keyword") or ""))
    if not cluster_tokens:
        cluster_tokens = _tokens(identify_primary_keyword(cluster))
    matched = 0
    for item in catalog:
        name = item.get("name") if isinstance(item, dict) else item
        if name and _tokens(str(name)) & cluster_tokens:
            matched += 1
    return matched or None


def _in_scope_status(cluster: dict[str, Any]) -> str:
    """Yes/Review for the URL Mapping sheet's In Scope? column, from the same
    relevance_reason keyword_relevance.py already attached to the primary
    keyword — not a new judgement invented at mapping time.
    """
    primary_norm = _norm_kw(identify_primary_keyword(cluster))
    for row in cluster.get("keywords") or []:
        if isinstance(row, dict) and _norm_kw(str(row.get("keyword") or "")) == primary_norm:
            reason = str(row.get("relevance_reason") or "")
            if reason and reason not in _STRONG_IN_SCOPE_REASONS:
                return "REVIEW"
            return "YES"
    return "YES"


_WEAK_MATCH_NOTE = "Existing page is a weak match — confirm before merging"


def _url_map_notes(entry: dict[str, Any], cluster: dict[str, Any]) -> str:
    """Short, human-readable summary of why this row landed where it did —
    the columns a strategist scans first when triaging a large sheet."""
    bits: list[str] = []
    serp = entry.get("serp_analysis") or {}
    if serp.get("validated"):
        fmt = serp.get("dominant_format")
        bits.append(f"SERP validated ({fmt})" if fmt else "SERP validated")
    competing = entry.get("competing_urls") or []
    if competing:
        bits.append(f"{len(competing)} competing URL(s) on this site")
    if entry.get("action") == "REVIEW_MERGE_REDIRECT":
        bits.append(_WEAK_MATCH_NOTE)
    primary_norm = _norm_kw(identify_primary_keyword(cluster))
    for row in cluster.get("keywords") or []:
        if isinstance(row, dict) and _norm_kw(str(row.get("keyword") or "")) == primary_norm:
            if row.get("ungrounded"):
                bits.append("Primary keyword not in the grounded seed set — verify before publishing")
            break
    return "; ".join(bits) or "—"


def url_n(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return "/"
    if "://" in raw:
        raw = urlparse(raw).path or "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw.rstrip("/").lower() or "/"


def _norm_kw(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm_kw(text)) if t not in _STOP and len(t) > 1}


def _overlap_ratio(a: str, b: str) -> float:
    """Containment-biased overlap, used for *scoring* a candidate's slug/title.

    Deliberately asymmetric: a slug that contains the keyword is a strong signal
    even though the slug is longer. Not suitable for deciding whether a page is
    a candidate at all — see _jaccard.
    """
    na, nb = _norm_kw(a), _norm_kw(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    aw, bw = _tokens(a), _tokens(b)
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / max(1, min(len(aw), len(bw)))


def _jaccard(a: str, b: str) -> float:
    """Symmetric token overlap, used to *gate* candidates.

    _overlap_ratio divides by the smaller token set, so a one-word page ("/pricing",
    titled "Pricing") scores 1.0 against any keyword containing that word — which
    is how "payroll software pricing" used to acquire /pricing as a genuine
    existing-page match. Jaccard refuses that.
    """
    aw, bw = _tokens(a), _tokens(b)
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)


def _topical_candidate_reason(
    *,
    primary_keyword: str,
    want: str,
    want_tokens: set[str],
    blob: str,
    title: str,
    page_keyword: str,
) -> str | None:
    """Is this page a plausible target for the keyword? Reason, or None.

    Requires a majority of the keyword's tokens (and at least two, for
    multi-word keywords) rather than the old "half, rounded down, minimum one".
    """
    if want and want in blob:
        return "keyword_topic_overlap"
    if want_tokens:
        need = max(1, (len(want_tokens) + 1) // 2)
        if len(want_tokens) >= 2:
            need = max(2, need)
        if len(want_tokens & _tokens(blob)) >= need:
            return "keyword_topic_overlap"
    if _jaccard(primary_keyword, title) >= 0.5 or _jaccard(primary_keyword, page_keyword) >= 0.5:
        return "title_or_keyword_overlap"
    return None


def collect_crawl_pages(
    *,
    website: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize crawled / audited pages into URL candidates.

    Prefers the Phase 3 ``site_sitemap`` inventory when present.
    """
    website = dict(website or {})
    audit = dict(content_audit or {})
    by_path: dict[str, dict[str, Any]] = {}

    def _add(row: dict[str, Any]) -> None:
        path = url_n(str(row.get("path") or row.get("url") or ""))
        if not path or path == "/":
            return
        existing = by_path.get(path)
        merged = dict(existing or {})
        merged.update({k: v for k, v in row.items() if v not in (None, "", [], {})})
        merged["path"] = path
        merged.setdefault("url", row.get("url") or path)
        by_path[path] = merged

    from app.services.site_sitemap import sitemap_pages

    for item in sitemap_pages(website):
        _add(item)

    for key in ("pages", "crawled_pages", "top_pages", "sample_urls"):
        raw = website.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    _add(item)
                elif item:
                    _add({"url": str(item), "title": str(item)})

    site_sitemap = website.get("site_sitemap")
    if isinstance(site_sitemap, dict):
        for item in site_sitemap.get("pages") or []:
            if isinstance(item, dict):
                _add(item)
            elif item:
                _add({"url": str(item)})
        for item in site_sitemap.get("urls") or []:
            if isinstance(item, str):
                _add({"url": item})

    crawl = website.get("crawl") or website.get("crawl_technical")
    if isinstance(crawl, dict):
        summary = crawl.get("summary") if isinstance(crawl.get("summary"), dict) else crawl
        for item in summary.get("discovered_urls") or []:
            if isinstance(item, str):
                _add({"url": item})
            elif isinstance(item, dict):
                _add(item)
        nested = summary.get("site_sitemap")
        if isinstance(nested, dict):
            for item in nested.get("pages") or []:
                if isinstance(item, dict):
                    _add(item)

    for key in ("inventory", "refresh_queue", "dispositions"):
        for row in audit.get(key) or []:
            if isinstance(row, dict):
                _add(row)

    return list(by_path.values())


def build_url_candidates(
    *,
    primary_keyword: str,
    crawled_pages: list[dict[str, Any]],
    suggested_url: str | None = None,
    limit: int = 40,
    include_suggested_stub: bool = True,
) -> list[dict[str, Any]]:
    """Return crawl pages that could plausibly map to this cluster.

    ``include_suggested_stub`` controls whether a *non-existent* suggested URL is
    returned as a placeholder candidate. Decision-making callers pass False: a
    URL the cluster merely proposed is not evidence about the live site, and
    letting it into the scored pool lets an invented page set the score band.
    """
    want = _norm_kw(primary_keyword)
    want_tokens = _tokens(primary_keyword)
    sug = url_n(suggested_url or "")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _maybe_add(page: dict[str, Any], *, reason: str) -> None:
        path = url_n(str(page.get("path") or page.get("url") or ""))
        if not path or path in seen:
            return
        seen.add(path)
        out.append({**page, "path": path, "candidate_reason": reason})

    if sug and sug != "/":
        for page in crawled_pages:
            if url_n(str(page.get("path") or page.get("url") or "")) == sug:
                _maybe_add(page, reason="suggested_url_exact")
                break
        if sug not in seen and include_suggested_stub:
            _maybe_add({"url": sug, "path": sug, "title": primary_keyword.title()}, reason="suggested_url_only")

    for page in crawled_pages:
        path = url_n(str(page.get("path") or page.get("url") or ""))
        title = str(page.get("title") or page.get("keyword") or "")
        slug = path.strip("/").replace("-", " ")
        kw = str(page.get("keyword") or page.get("primary_keyword") or "")
        blob = f"{title} {slug} {kw}".lower()
        reason = _topical_candidate_reason(
            primary_keyword=primary_keyword,
            want=want,
            want_tokens=want_tokens,
            blob=blob,
            title=title,
            page_keyword=kw,
        )
        if reason:
            _maybe_add(page, reason=reason)

    if not out:
        for page in crawled_pages[: min(limit, 12)]:
            _maybe_add(page, reason="crawl_pool_fallback")

    return out[:limit]


def score_semantic_match(primary_keyword: str, page: dict[str, Any]) -> float:
    """Primary keyword vs the page's title, slug and main heading (0-25).

    A weighted average of the three sources plus a bonus when they agree. A
    matching slug under an unrelated title and heading is a weak match; a page
    whose title, slug and heading all point at the keyword is a strong one.
    A source the crawl did not record (no title, no heading) is left out of the
    average rather than counted as a mismatch, but one source alone cannot
    earn the agreement bonus.
    """
    path = url_n(str(page.get("path") or page.get("url") or ""))
    texts = {
        "title": str(page.get("title") or ""),
        "slug": path.strip("/").replace("-", " ").replace("/", " "),
        "h1": str(page.get("h1") or ""),
    }
    present = {k: v for k, v in texts.items() if v.strip()}
    if not present:
        return 0.0
    ratios = {k: _overlap_ratio(primary_keyword, v) for k, v in present.items()}
    weight = sum(_SEMANTIC_WEIGHTS[k] for k in ratios)
    average = sum(_SEMANTIC_WEIGHTS[k] * r for k, r in ratios.items()) / weight
    agreeing = sum(1 for r in ratios.values() if r >= 0.75)
    agreement = (agreeing - 1) / (len(ratios) - 1) if len(ratios) > 1 and agreeing > 1 else 0.0
    return round(COMPONENT_MAX["semantic_match"] * (0.75 * average + 0.25 * agreement), 2)


_FORMAT_SIGNAL_WORDS = {
    "listicle": re.compile(r"\b(\d+|best|top)\b"),
    "comparison": re.compile(r"\b(vs|versus|compared|alternatives?)\b"),
}


def score_intent_match(
    cluster_intent: str,
    page: dict[str, Any],
    *,
    serp_summary: dict[str, Any] | None = None,
) -> float:
    """Align cluster intent with page type / IA cluster (0–15)."""
    intent = (cluster_intent or "informational").lower()
    url = str(page.get("url") or page.get("path") or "")
    title = str(page.get("title") or "")
    meta = classify_audit_page(url, title)
    page_cluster = str(page.get("cluster") or meta.get("cluster") or "").lower()
    page_type = str(page.get("page_type") or meta.get("page_type") or "").lower()
    affinity = _INTENT_PAGE_AFFINITY.get(intent, set())
    if page_cluster in affinity or page_type in affinity:
        score = 15.0
    elif (intent == "informational" and page_cluster in ("blog", "guides")) or (
        intent in ("commercial", "transactional") and page_cluster in ("services", "service_hub", "location")
    ):
        score = 12.0
    else:
        score = 4.0

    # A generic service/hub page can satisfy topical intent while still losing
    # the SERP to a dedicated comparison/listicle page (e.g. "best keyword
    # research tools" wants a listicle, not a generic /keyword-research/ page).
    # Only docked when the SERP was actually validated — never penalize on an
    # unvalidated/absent SERP summary.
    dominant_format = str((serp_summary or {}).get("dominant_format") or "").lower()
    pattern = _FORMAT_SIGNAL_WORDS.get(dominant_format)
    if (
        pattern
        and (serp_summary or {}).get("validated")
        and page_type in ("service", "hub", "subservice", "sub_service")
        and not pattern.search(f"{title} {url}".lower())
    ):
        score = min(score, 6.0)
    return score


def score_keyword_topic_match(
    primary_keyword: str,
    secondary_keywords: list[str],
    page: dict[str, Any],
) -> float | None:
    """Keyword coverage beyond the primary's own title/slug match (0-25), or
    None when there is nothing independent to measure.

    Two parts, each measured only when its data exists: the keyword the page
    itself targets (from the content audit) against the cluster's primary, and
    how many of the cluster's secondary keywords the page's text covers. The
    primary-vs-title/slug overlap is not counted again here; semantic matching
    already measures it.
    """
    title = str(page.get("title") or "")
    declared = str(page.get("keyword") or page.get("primary_keyword") or "")
    path = url_n(str(page.get("path") or page.get("url") or ""))
    slug = path.strip("/").replace("-", " ")
    blob = f"{title} {page.get('h1') or ''} {declared} {slug}"
    parts: list[tuple[float, float]] = []  # (weight, ratio)
    if declared.strip():
        parts.append((0.5, _overlap_ratio(primary_keyword, declared)))
    secondaries = [s for s in secondary_keywords[:6] if str(s).strip()]
    if secondaries:
        hits = sum(1 for sec in secondaries if _overlap_ratio(sec, blob) >= 0.5)
        parts.append((0.5, hits / len(secondaries)))
    if not parts:
        return None
    total_weight = sum(w for w, _r in parts)
    ratio = sum(w * r for w, r in parts) / total_weight
    return round(COMPONENT_MAX["keyword_topic_match"] * ratio, 2)


def site_traffic_stats(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """The site's own Search Console distribution, for relative thresholds.

    ``available`` is False when no page carries Search Console metrics, which
    means the ranking component is missing evidence, not negative evidence.
    """
    clicks: list[float] = []
    impressions: list[float] = []
    for page in pages or []:
        metrics = page.get("metrics") if isinstance(page, dict) else None
        if not isinstance(metrics, dict):
            continue
        if any(metrics.get(k) is not None for k in ("clicks", "impressions", "position")):
            clicks.append(float(metrics.get("clicks") or 0))
            impressions.append(float(metrics.get("impressions") or 0))
    return {
        "available": bool(clicks),
        "clicks": sorted(clicks),
        "impressions": sorted(impressions),
    }


def _percentile(value: float, ordered: list[float]) -> float:
    """Share of the site's pages this value beats or equals (0-1)."""
    if not ordered or value <= 0:
        return 0.0
    below = sum(1 for v in ordered if v <= value)
    return below / len(ordered)


def score_ranking_evidence(
    page: dict[str, Any],
    *,
    serp_summary: dict[str, Any] | None = None,
    client_domain: str | None = None,
    site_stats: dict[str, Any] | None = None,
) -> float | None:
    """Search Console metrics + SERP presence (0-20), or None when neither the
    site's Search Console data nor a SERP check was available.

    Clicks and impressions are judged against the site's own pages: a page
    among this site's strongest earns full marks whatever the site's total
    traffic. Position is absolute (page one is page one on any site).
    """
    stats = site_stats if site_stats is not None else site_traffic_stats([page])
    organic = [r for r in (serp_summary or {}).get("organic") or [] if isinstance(r, dict)]
    if not stats.get("available") and not organic:
        return None
    score = 0.0
    metrics = dict(page.get("metrics") or {})
    if stats.get("available"):
        clicks = float(metrics.get("clicks") or 0)
        impressions = float(metrics.get("impressions") or 0)
        score += 8 * _percentile(clicks, stats.get("clicks") or [])
        score += 4 * _percentile(impressions, stats.get("impressions") or [])
        position = metrics.get("position")
        if position is not None:
            try:
                pos = float(position)
                if pos <= 10:
                    score += 8
                elif pos <= 20:
                    score += 5
                elif pos <= 50:
                    score += 2
            except (TypeError, ValueError):
                pass
    path = url_n(str(page.get("path") or page.get("url") or ""))
    domain = (client_domain or "").lower().strip()
    for row in organic:
        row_url = url_n(str(row.get("url") or ""))
        row_dom = str(row.get("domain") or "").lower()
        if row_url == path or (domain and domain in row_dom and _overlap_ratio(path, row_url) >= 0.8):
            try:
                pos = int(row.get("position") or 99)
                score += max(0, 10 - min(pos, 10))
            except (TypeError, ValueError):
                score += 3
            break
    return round(min(COMPONENT_MAX["ranking_evidence"], score), 2)


def score_traffic_business(
    cluster: dict[str, Any],
    page: dict[str, Any],
    *,
    commercial: dict[str, Any] | None = None,
) -> float | None:
    """Opportunity + CDD business weight (0-15), or None when the cluster has
    no opportunity data and the client has no CDD business terms on file."""
    best_raw = cluster.get("best_score")
    traffic = cluster.get("est_traffic")
    raw_terms = cdd_terms_from_commercial(commercial)
    # The helper always returns a dict of lists; only real terms count as data.
    terms = raw_terms if any(raw_terms.values()) else None
    has_best = isinstance(best_raw, (int, float)) and not isinstance(best_raw, bool)
    has_traffic = isinstance(traffic, (int, float)) and not isinstance(traffic, bool)
    if not has_best and not has_traffic and not terms:
        return None
    score = 0.0
    best = float(best_raw) if has_best else 0.0
    if best >= 70:
        score += 5
    elif best >= 40:
        score += 3
    if has_traffic and traffic > 0:
        score += min(4, float(traffic) / 500)
    if terms:
        cdd = match_cdd_page(
            str(page.get("url") or page.get("path") or ""),
            str(page.get("title") or ""),
            terms,
        )
        score += min(6, float(cdd.get("cdd_priority") or 0) * 2)
    return round(min(COMPONENT_MAX["traffic_business"], score), 2)


def score_url_candidate(
    page: dict[str, Any],
    *,
    cluster: dict[str, Any],
    primary_keyword: str,
    secondary_keywords: list[str],
    cluster_intent: str,
    serp_summary: dict[str, Any] | None = None,
    commercial: dict[str, Any] | None = None,
    client_domain: str | None = None,
    suggested_url: str | None = None,
    site_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine the five signals into a URL score (0-100).

    Each component that had data is scored; one that did not (no Search
    Console, no SERP, no commercial data, nothing independent for keyword
    coverage) is left out, and the row is scored out of the points it could
    obtain. A strong match on a site without Search Console is therefore
    recognised as strong. ``suggested_url`` no longer adds a boost: a page at
    the suggested path already scores on its slug in semantic matching.
    """
    del suggested_url  # kept for callers; the exact-URL boost double-counted the slug
    path = url_n(str(page.get("path") or page.get("url") or ""))
    components: dict[str, float | None] = {
        "semantic_match": score_semantic_match(primary_keyword, page),
        "intent_match": score_intent_match(cluster_intent, page, serp_summary=serp_summary),
        "keyword_topic_match": score_keyword_topic_match(primary_keyword, secondary_keywords, page),
        "ranking_evidence": score_ranking_evidence(
            page, serp_summary=serp_summary, client_domain=client_domain, site_stats=site_stats
        ),
        "traffic_business": score_traffic_business(cluster, page, commercial=commercial),
    }
    available = [k for k, v in components.items() if v is not None]
    unavailable = [k for k, v in components.items() if v is None]
    obtainable = sum(COMPONENT_MAX[k] for k in available)
    raw = sum(float(components[k] or 0) for k in available)
    normalized = round(100 * raw / obtainable, 2) if obtainable else 0.0
    return {
        "url": page.get("url") or path,
        "path": path,
        "title": page.get("title"),
        "url_score": normalized,
        "score_raw": round(raw, 2),
        "score_obtainable": obtainable,
        "signal_coverage": round(obtainable / sum(COMPONENT_MAX.values()), 2),
        "components_available": available,
        "components_unavailable": unavailable,
        "score_breakdown": {k: (v if v is not None else None) for k, v in components.items()},
        "candidate_reason": page.get("candidate_reason"),
        "disposition": page.get("disposition"),
        "metrics": page.get("metrics"),
        # Site-map judgements about the page itself. Carried through so topic
        # classification can tell "right page, under-built" from "right page,
        # already good" without re-reading the inventory.
        "page_type": page.get("page_type") or page.get("cluster"),
        "content_quality": page.get("content_quality"),
        "word_count": page.get("word_count"),
        "last_modified": page.get("last_modified") or page.get("lastmod") or page.get("modified"),
        "canonical_url": page.get("canonical_url"),
        "potential_cannibalization": page.get("potential_cannibalization") or [],
    }


def band_from_score(score: float) -> str:
    if score >= _HIGH_THRESHOLD:
        return "HIGH"
    if score >= _MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def find_existing_page_url(scored: list[dict[str, Any]]) -> str | None:
    """Best on-site URL that genuinely matches the keyword (not crawl fallback).

    Kept as the single-value convenience over ``real_match_candidates``, which
    callers use when they also need the matched page's score and breakdown.
    """
    for candidate in real_match_candidates(scored):
        path = url_n(str(candidate.get("path") or candidate.get("url") or ""))
        if path and path != "/":
            return path
    return None


def resolve_sheet_url_columns(
    *,
    current_url: str | None,
    proposed_url: str | None,
    action: str | None = None,
    selected_url: str | None = None,
    create_url: str | None = None,
) -> tuple[str | None, str | None]:
    """Workbook rule: existing page → Current URL only; new page → Proposed URL only."""
    cur = url_n(current_url) if current_url else None
    prop = url_n(proposed_url) if proposed_url else None
    if cur and cur != "/":
        return cur, None
    if prop and prop != "/":
        return None, prop
    act = str(action or "").upper()
    sel = url_n(selected_url) if selected_url else None
    new_url = url_n(create_url) if create_url else None
    if act == "CREATE" or "CREATE" in act:
        return None, new_url or sel
    if sel and sel != "/":
        return sel, None
    # Nothing resolvable on either side (e.g. a cluster held for review with no
    # existing page and no proposed URL). Previously fell off the end returning
    # a bare None, which blew up at the `current_url, proposed_url = ...` unpack.
    return None, None


def action_from_band(band: str, *, has_existing_page: bool) -> str:
    if not has_existing_page:
        return "CREATE"
    if band == "HIGH":
        return "OPTIMIZE_EXISTING"
    return "REVIEW_MERGE_REDIRECT"


def real_match_candidates(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scored candidates that are genuine on-site matches for the keyword.

    Excludes the crawl-pool fallback (arbitrary pages returned when nothing
    matched) and any placeholder built from a merely *proposed* URL. Only these
    rows may drive the URL score, the band, or the competing-URL list — anything
    else attributes a score to a page that never matched.
    """
    return [
        c
        for c in scored
        if str(c.get("candidate_reason") or "") in _REAL_CRAWL_MATCH_REASONS
    ]


def find_existing_match_for_cluster(
    cluster: dict[str, Any],
    *,
    crawled_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cheap "does the live site already cover this cluster" lookup.

    Reuses the same scoring machinery as map_cluster_to_url() but skips the
    taxonomy/sheet-column work that only matters once Phase 6b commits to a
    final URL decision. Used after keyword clustering to classify clusters
    against the Phase 3 sitemap (plus any live enrichments) before URL mapping.

    The returned score/band describe the *matched* page, and the score breakdown
    is carried through so topic classification can tell "the right page, weakly
    optimized" apart from "the right-looking URL, wrong page type".
    """
    primary = identify_primary_keyword(cluster)
    secondaries = identify_secondary_keywords(cluster)
    intent = str(cluster.get("intent") or detect_intent(primary)).lower()
    candidates = build_url_candidates(
        primary_keyword=primary,
        crawled_pages=crawled_pages,
        suggested_url=cluster.get("recommended_url"),
        include_suggested_stub=False,
    )
    stats = site_traffic_stats(crawled_pages)
    scored = [
        score_url_candidate(
            page,
            cluster=cluster,
            primary_keyword=primary,
            secondary_keywords=secondaries,
            cluster_intent=intent,
            site_stats=stats,
        )
        for page in candidates
    ]
    scored.sort(key=lambda x: (-float(x.get("url_score") or 0), str(x.get("path") or "")))
    real = real_match_candidates(scored)
    best = real[0] if real else None
    existing_url = str((best or {}).get("path") or "") or None
    if existing_url == "/":
        existing_url, best = None, None
    match_score = float((best or {}).get("url_score") or 0)
    return {
        "matched": bool(existing_url),
        "matched_url": existing_url,
        "match_score": match_score,
        "match_band": band_from_score(match_score),
        # The score is out of the points obtainable for this match; the scale
        # travels with it so URL mapping never re-bands it as something else.
        "score_scale": "normalized_obtainable",
        "score_obtainable": (best or {}).get("score_obtainable"),
        "components_available": (best or {}).get("components_available") or [],
        "components_unavailable": (best or {}).get("components_unavailable") or [],
        "score_breakdown": (best or {}).get("score_breakdown") or {},
        "matched_page": {
            "url": best.get("url"),
            "title": best.get("title"),
            "page_type": best.get("page_type"),
            "content_quality": best.get("content_quality"),
            "word_count": best.get("word_count"),
            "last_modified": best.get("last_modified"),
            "canonical_url": best.get("canonical_url"),
            "potential_cannibalization": best.get("potential_cannibalization") or [],
        }
        if best
        else {},
        "candidates_considered": len(scored),
        "competing_urls": [
            str(c.get("path")) for c in real[1:4] if c.get("path") and c.get("path") != existing_url
        ],
        # When the runner-up scores nearly as well, the site itself has two
        # pages chasing one topic — that is site-side cannibalization, and it
        # must be visible before anything is mapped or created.
        "runner_up_url": str(real[1].get("path")) if len(real) > 1 else None,
        "runner_up_score": float(real[1].get("url_score") or 0) if len(real) > 1 else None,
    }


def _merge_inventory_pages(
    sitemap_pages: list[dict[str, Any]] | None,
    live_pages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer sitemap inventory; overlay live-scan title/H1 when the URL matches."""
    by_path: dict[str, dict[str, Any]] = {}

    def _path(page: dict[str, Any]) -> str:
        raw = str(page.get("path") or page.get("url") or "").strip()
        if not raw:
            return ""
        if raw.startswith("http"):
            from urllib.parse import urlparse

            return (urlparse(raw).path or "/").rstrip("/") or "/"
        return raw.rstrip("/") or "/"

    for page in sitemap_pages or []:
        if not isinstance(page, dict):
            continue
        path = _path(page)
        if path:
            by_path[path] = dict(page)
    for page in live_pages or []:
        if not isinstance(page, dict):
            continue
        path = _path(page)
        if not path:
            continue
        if path in by_path:
            merged = {**by_path[path], **{k: v for k, v in page.items() if v not in (None, "", [])}}
            by_path[path] = merged
        else:
            by_path[path] = dict(page)
    return list(by_path.values())


def classify_clusters_against_sitemap(
    clusters: list[dict[str, Any]],
    *,
    sitemap_pages: list[dict[str, Any]] | None = None,
    live_pages: list[dict[str, Any]] | None = None,
    inventory_complete: bool = True,
) -> dict[str, Any]:
    """After clustering, before URL mapping: classify every cluster against the
    existing site map.

    Thin wrapper that merges the Phase 3 sitemap with any live-scan enrichment
    and hands the work to ``topic_classification``, which owns the full status
    taxonomy (existing / needs-optimization / needs-consolidation / new /
    supporting / cannibalization / out-of-scope / uncertain), the reason for
    each call, and its confidence.
    """
    from app.services.topic_classification import classify_clusters_against_site_map

    pages = _merge_inventory_pages(sitemap_pages, live_pages)
    report = classify_clusters_against_site_map(
        [c for c in clusters if isinstance(c, dict)],
        pages=pages,
        inventory_complete=inventory_complete,
    )
    report["sitemap_page_count"] = len(sitemap_pages or [])
    report["live_page_count"] = len(live_pages or [])
    return report


# Topic status (from topic_classification) → URL mapping decision. `action`
# stays inside the three legacy values every downstream reader already switches
# on; the richer answer travels in `url_status` / `dedicated_url`.
_STATUS_TO_MAPPING = {
    "EXISTING_TOPIC": ("OPTIMIZE_EXISTING", "existing_url", True),
    "EXISTING_TOPIC_NEEDS_OPTIMIZATION": ("OPTIMIZE_EXISTING", "existing_url_needs_optimization", True),
    "EXISTING_TOPIC_NEEDS_CONSOLIDATION": ("REVIEW_MERGE_REDIRECT", "existing_url_needs_consolidation", True),
    "CANNIBALIZATION_RISK": ("REVIEW_MERGE_REDIRECT", "existing_url_needs_consolidation", False),
    "NEW_TOPIC": ("CREATE", "new_proposed_url", True),
    "SUPPORTING_TOPIC": ("OPTIMIZE_EXISTING", "no_dedicated_url", False),
    "IRRELEVANT": ("REVIEW_MERGE_REDIRECT", "no_dedicated_url", False),
}
# Fallback for clusters that never went through topic classification.
_LEGACY_DISPOSITION_TO_STATUS = {
    "existing_topic": "EXISTING_TOPIC",
    "existing_review": "EXISTING_TOPIC_NEEDS_CONSOLIDATION",
    "new_topic": "NEW_TOPIC",
    "supporting_topic": "SUPPORTING_TOPIC",
    "out_of_scope": "IRRELEVANT",
}


def _apply_topic_status(
    cluster: dict[str, Any],
    *,
    action: str,
    band: str,
    existing_page_url: str | None,
    has_existing: bool,
) -> dict[str, Any]:
    """Let the Phase 5 topic decision govern the URL action, not re-derive it.

    URL mapping executes a decision that has already been made against the site
    map; it does not get to decide the topic structure a second time here.
    """
    status = str(cluster.get("topic_status") or "").upper()
    if not status:
        legacy = str(cluster.get("topic_disposition") or "").lower()
        status = _LEGACY_DISPOSITION_TO_STATUS.get(legacy, "")
    # UNCERTAIN carries no URL opinion of its own — act on the provisional call
    # but keep the row flagged so nothing ships on a coin flip.
    effective = status
    if status == "UNCERTAIN":
        effective = str(cluster.get("provisional_topic_status") or "NEW_TOPIC").upper()

    prior_url = str(
        (cluster.get("existing_page_match") or {}).get("matched_url")
        or (cluster.get("sitemap_match") or {}).get("matched_url")
        or ""
    ).strip()

    mapped = _STATUS_TO_MAPPING.get(effective)
    if not mapped:
        return {
            "action": action,
            "existing_page_url": existing_page_url,
            "has_existing": has_existing,
            "dedicated_url": True,
            "url_status": "existing_url" if has_existing else "new_proposed_url",
            "topic_status": status or None,
            "topic_status_original": status or None,
            "competing_url": None,
            "trail": ["No topic classification; decided from the URL score band."],
            "needs_review": bool(cluster.get("topic_needs_review")),
        }

    new_action, url_status, dedicated = mapped
    trail: list[str] = [f"Topic classification: {status or effective}."]
    competing_url: str | None = None
    reclassified_to: str | None = None
    if effective in ("EXISTING_TOPIC", "EXISTING_TOPIC_NEEDS_OPTIMIZATION", "EXISTING_TOPIC_NEEDS_CONSOLIDATION"):
        if prior_url:
            existing_page_url = prior_url
        has_existing = bool(existing_page_url)
        if not has_existing:
            # Classified as existing but no URL survived — do not silently
            # create; surface it instead.
            new_action = "REVIEW_MERGE_REDIRECT"
            url_status = "no_dedicated_url"
            dedicated = False
            trail.append("Classified as existing, but no page URL survived; held for review.")
    elif effective == "NEW_TOPIC":
        if has_existing and band == "HIGH":
            # A strong live match means this was not a new topic: optimize the
            # page rather than create a second one beside it.
            trail.append(
                f"Topic classification said new, but {existing_page_url} is a strong live match "
                f"({band}); reclassified as an existing page needing optimization."
            )
            new_action, url_status, dedicated = (
                "OPTIMIZE_EXISTING",
                "existing_url_needs_optimization",
                True,
            )
            reclassified_to = "EXISTING_TOPIC_NEEDS_OPTIMIZATION"
        else:
            if has_existing:
                # The new-topic call holds. The weaker match is surfaced as a
                # competing URL instead of filling Current URL on a create row.
                competing_url = existing_page_url
                trail.append(
                    f"New topic kept; the {band} match {existing_page_url} is recorded as a competing URL, "
                    "not as the page to optimize."
                )
            existing_page_url = None
            has_existing = False
    elif effective in ("SUPPORTING_TOPIC", "CANNIBALIZATION_RISK", "IRRELEVANT"):
        if prior_url and not existing_page_url:
            existing_page_url = prior_url
        has_existing = bool(existing_page_url)
        if not has_existing:
            new_action = "REVIEW_MERGE_REDIRECT"

    return {
        "action": new_action,
        "existing_page_url": existing_page_url,
        "has_existing": has_existing,
        "dedicated_url": dedicated,
        "url_status": url_status,
        "topic_status": reclassified_to or status or effective,
        "topic_status_original": status or effective,
        "competing_url": competing_url,
        "trail": trail,
        "needs_review": bool(cluster.get("topic_needs_review")) or status == "UNCERTAIN",
    }


def map_cluster_to_url(
    cluster: dict[str, Any],
    *,
    crawled_pages: list[dict[str, Any]],
    serp_summary: dict[str, Any] | None = None,
    commercial: dict[str, Any] | None = None,
    client_domain: str | None = None,
    suggested_url: str | None = None,
    offer_index: dict[str, str] | None = None,
    site_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full URL mapping pipeline for one cluster."""
    stats = site_stats if site_stats is not None else site_traffic_stats(crawled_pages)
    primary = identify_primary_keyword(cluster)
    secondaries = identify_secondary_keywords(cluster)
    intent = str(cluster.get("intent") or detect_intent(primary)).lower()
    suggested = suggested_url or cluster.get("recommended_url")
    candidates = build_url_candidates(
        primary_keyword=primary,
        crawled_pages=crawled_pages,
        suggested_url=suggested,
        include_suggested_stub=False,
    )
    scored = [
        score_url_candidate(
            page,
            cluster=cluster,
            primary_keyword=primary,
            secondary_keywords=secondaries,
            cluster_intent=intent,
            serp_summary=serp_summary,
            commercial=commercial,
            client_domain=client_domain,
            suggested_url=suggested,
            site_stats=stats,
        )
        for page in candidates
    ]
    scored.sort(key=lambda x: (-float(x.get("url_score") or 0), str(x.get("path") or "")))
    # The score band must describe the page we would actually map to. Scoring
    # off scored[0] let a crawl-pool fallback page (or, before the stub was
    # removed, an invented URL) set a HIGH band for a cluster whose real match
    # scored 20 — and that band drove the OPTIMIZE vs REVIEW vs CREATE decision.
    real = real_match_candidates(scored)
    best = real[0] if real else None
    best_score = float(best["url_score"]) if best is not None else 0.0
    band = band_from_score(best_score)
    band_source = "live_candidates" if best is not None else "no_match"
    score_basis = {
        "available": list((best or {}).get("components_available") or []),
        "unavailable": list((best or {}).get("components_unavailable") or []),
        "obtainable": (best or {}).get("score_obtainable"),
        "coverage": (best or {}).get("signal_coverage"),
    }
    trail: list[str] = []
    if best is not None:
        trail.append(f"Best live match {best.get('path')} scored {best_score:g}/100 ({band}).")
    else:
        trail.append("No live page matched the primary keyword.")

    from app.services.create_content import path_for_page_type, resolve_page_type

    # Resolve type from cluster signals first so we don't invent a /services/ URL
    # that then forces every new page to look like a service page.
    prior_type = (
        cluster.get("page_type")
        or cluster.get("content_type")
        or (cluster.get("existing_page_match") or {}).get("page_type")
        or (serp_summary or {}).get("dominant_format")
    )
    page_type = resolve_page_type(
        {
            "keyword": primary,
            "intent": intent,
            "page_type": prior_type,
            "content_type": prior_type,
            "type": prior_type,
        }
    )
    typed_url = path_for_page_type(page_type, primary)
    raw_suggested = str(suggested or "")
    # A stored /pricing/ path is not evidence. Only a real price or buy query
    # keeps that folder; everything else follows the keyword.
    if page_type != "landing" and "/pricing/" in raw_suggested.lower():
        create_url = typed_url
    else:
        create_url = raw_suggested or typed_url

    existing_page_url = str((best or {}).get("path") or "") or None
    # Prefer Phase 5 topic classification when present
    prior_match = cluster.get("existing_page_match") or cluster.get("sitemap_match") or {}
    prior_url = str(
        prior_match.get("matched_url")
        or (cluster.get("sitemap_match") or {}).get("matched_url")
        or ""
    ).strip()
    if prior_url and not existing_page_url:
        existing_page_url = prior_url
        prior_score = _prior_score_on_this_scale(prior_match)
        if prior_score is not None:
            best_score = prior_score
            band = band_from_score(best_score)
            band_source = "prior_classification"
            score_basis = {
                "available": list(prior_match.get("components_available") or []),
                "unavailable": list(prior_match.get("components_unavailable") or []),
                "obtainable": prior_match.get("score_obtainable"),
                "coverage": None,
            }
            trail.append(
                f"No live candidate matched; the band comes from topic classification's prior match "
                f"{prior_url} ({best_score:g}/100, {band})."
            )
    has_existing = bool(existing_page_url)
    action = action_from_band(band, has_existing_page=has_existing)

    decision = _apply_topic_status(
        cluster,
        action=action,
        band=band,
        existing_page_url=existing_page_url,
        has_existing=has_existing,
    )
    action = decision["action"]
    existing_page_url = decision["existing_page_url"]
    has_existing = decision["has_existing"]
    trail.extend(decision.get("trail") or [])

    current_url, proposed_url = resolve_sheet_url_columns(
        current_url=existing_page_url,
        proposed_url=create_url if (not has_existing and decision["dedicated_url"]) else None,
        action=action,
        selected_url=existing_page_url,
        create_url=create_url if decision["dedicated_url"] else None,
    )
    final_url = current_url or proposed_url or (create_url if decision["dedicated_url"] else None)
    competing = [
        c["path"]
        for c in real[1:4]
        if c.get("path") and c.get("path") != final_url and c.get("path") != current_url
    ]
    if decision.get("competing_url") and decision["competing_url"] not in competing:
        competing.insert(0, decision["competing_url"])
    verb = "review and consolidate" if action == "REVIEW_MERGE_REDIRECT" else "optimize"
    trail.append(
        f"Initial action {action}: "
        + (f"{verb} {current_url}." if current_url else f"propose {proposed_url}." if proposed_url else "no dedicated URL.")
    )
    match_confidence = round(best_score / 100, 2) if band_source != "no_match" else 0.0

    metrics = primary_keyword_metrics(cluster)
    ia_page_type = resolve_page_type(
        {
            "url": current_url or proposed_url or create_url,
            "keyword": primary,
            "intent": intent,
            "page_type": prior_type or page_type,
            "content_type": prior_type or page_type,
            "type": prior_type or page_type,
        }
    )

    entry = {
        "cluster": cluster.get("name"),
        "primary_keyword": primary,
        "secondary_keywords": secondaries,
        "search_intent": intent,
        "serp_analysis": {
            "validated": bool((serp_summary or {}).get("validated")),
            "dominant_format": (serp_summary or {}).get("dominant_format"),
            "intent": (serp_summary or {}).get("intent") or intent,
        },
        # Only genuine matches are rendered. Crawl-pool filler that never
        # matched the keyword stays out of the candidate list.
        "url_candidates": real[:8],
        "selected_url": final_url if final_url else None,
        "url_score": best_score,
        "score_band": band,
        "band_source": band_source,
        "score_basis": score_basis,
        "action": action,
        # Confidence in the URL match, from the score out of obtainable points.
        # Signal coverage says how much evidence that score rests on; a row
        # can be confident on narrow evidence and the report says so.
        "match_confidence": match_confidence,
        "signal_coverage": score_basis.get("coverage"),
        "competing_urls": competing,
        "create_url": create_url,
        # --- URL Mapping entity: the decision, its basis, and its confidence --
        "cluster_id": cluster.get("cluster_id") or stable_cluster_id(cluster),
        "topic_status": decision["topic_status"],
        "url_status": decision["url_status"],
        "target_type": (
            "new_page" if decision["url_status"] == "new_proposed_url"
            else "none" if decision["url_status"] == "no_dedicated_url"
            else "existing_page"
        ),
        "target_url": final_url,
        "dedicated_url": decision["dedicated_url"],
        "mapping_status": decision["url_status"],
        "mapping_reason": _mapping_reason(cluster, decision, band=band, score=best_score, url=final_url),
        "cannibalization_risk": bool(
            cluster.get("cannibalization") or decision["url_status"] == "existing_url_needs_consolidation"
        ),
        "needs_human_review": decision["needs_review"],
        "confidence": match_confidence,
        # Topic classification's own confidence, kept separate.
        "topic_confidence": (
            float(cluster.get("topic_confidence"))
            if isinstance(cluster.get("topic_confidence"), (int, float))
            else None
        ),
        "topic_status_original": decision.get("topic_status_original"),
        "supporting_parent": (cluster.get("supporting_parent") or {}).get("parent_cluster"),
        "supporting_parent_id": (cluster.get("supporting_parent") or {}).get("parent_cluster_id"),
        "decision_trail": trail,
        "current_url": current_url,
        "proposed_url": proposed_url,
        "search_volume": metrics["volume"],
        "cpc": metrics["cpc"],
        "ia_page_type": ia_page_type,
        "est_products": _estimate_products(cluster, commercial),
        "pipeline_stages": [
            "primary_keyword",
            "search_intent",
            "serp_analysis",
            "website_url_crawl",
            "url_candidates",
            "semantic_matching",
            "intent_matching",
            "keyword_topic_match",
            "ranking_evidence",
            "traffic_business_data",
            "url_score",
            "score_band",
            "action",
        ],
    }
    _apply_sheet_columns(entry, cluster, commercial=commercial, pillar=cluster.get("pillar"))
    _retitle_modifier_variant(entry, primary, offer_index)
    entry["notes"] = _url_map_notes(entry, cluster)
    return entry


def _prior_score_on_this_scale(prior_match: dict[str, Any]) -> float | None:
    """Topic classification's prior match score, on this module's 0-100 scale.

    A match produced by find_existing_match_for_cluster already says so
    (``score_scale``); an older record scored out of a fixed 100 with every
    component counted, so it is read as raw points out of 100. None when no
    score was recorded at all (0 is a real score, not a missing one).
    """
    raw = prior_match.get("match_score")
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if prior_match.get("score_scale") == "normalized_obtainable":
        return round(max(0.0, min(100.0, value)), 2)
    obtainable = prior_match.get("score_obtainable")
    if isinstance(obtainable, (int, float)) and obtainable > 0:
        return round(max(0.0, min(100.0, 100 * value / float(obtainable))), 2)
    return round(max(0.0, min(100.0, value)), 2)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", (text or "").lower())
    return re.sub(r"[\s_]+", "-", s).strip("-") or "page"


_URL_STATUS_REASON = {
    "existing_url": "Mapped to the existing page — it already owns this topic and intent.",
    "existing_url_needs_optimization": "Mapped to the existing page; it is the right target but needs optimization.",
    "existing_url_needs_consolidation": "Existing page is a partial/competing target — consolidate or retarget before creating anything.",
    "new_proposed_url": "No existing page satisfies this cluster — new URL proposed.",
    "no_dedicated_url": "No dedicated URL: this cluster supports another page or is held for review.",
}


def _mapping_reason(
    cluster: dict[str, Any],
    decision: dict[str, Any],
    *,
    band: str,
    score: float,
    url: str | None,
) -> str:
    """Every mapping must say why. Prefers the Phase 5 topic reason (which cites
    the site-map evidence) and appends the Phase 6 scoring outcome."""
    bits: list[str] = []
    topic_reason = str(cluster.get("topic_reason") or "").strip()
    bits.append(topic_reason or _URL_STATUS_REASON.get(decision["url_status"], "Mapped by URL score."))
    if url:
        bits.append(f"URL {url} scored {score:g}/100 ({band}).")
    else:
        bits.append(f"No URL assigned (best on-site candidate {score:g}/100, {band}).")
    parent = (cluster.get("supporting_parent") or {}).get("parent_cluster")
    if parent:
        bits.append(f"Folds into '{parent}'.")
    return " ".join(bits)


def cluster_is_page_ready(cluster: dict[str, Any]) -> bool:
    """True when this cluster should become one URL.

    A cluster is one topic, one intent, and one page. Clusters the coherence
    gate dropped or sent back for review stay in the keyword report. They are
    not pages, so the URL map must not receive them.
    """
    if not isinstance(cluster, dict):
        return False
    if cluster.get("cluster_coherence_ok") is False:
        return False
    if str(cluster.get("cluster_sense_verdict") or "") in {"drop", "review"}:
        return False
    return True


def build_final_url_map(
    cluster_report: dict[str, Any],
    *,
    website: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
    serp_by_keyword: dict[str, dict[str, Any]] | None = None,
    commercial: dict[str, Any] | None = None,
    client_domain: str | None = None,
    pillar_structure: list[dict[str, Any]] | None = None,
    offer_synonyms: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Map every cluster to a final URL with score band and action.

    Order matters: proposals that already exist on the site become optimize
    rows first (so a supporting row never folds into a "new" parent that is
    live), then supporting rows resolve to their parents, same-offer pages
    fold together, collisions are checked across the whole map, every row
    gets its fields and URL hygiene verdict, and finally the consistency guard
    rejects any row whose action, status and URL columns disagree.
    """
    crawled = collect_crawl_pages(website=website, content_audit=content_audit)
    conventions = _site_url_conventions(crawled)
    stats = site_traffic_stats(crawled)
    serp_map = serp_by_keyword or {}
    offer_index = _offer_index(offer_synonyms)
    clusters = [
        c for c in cluster_report.get("clusters") or [] if cluster_is_page_ready(c)
    ]
    for cluster in clusters:
        cluster.setdefault("cluster_id", stable_cluster_id(cluster))

    # Phase 6a pillars, keyed on cluster id. Names in the incoming structure are
    # resolved to ids once, here; anything unresolvable is reported.
    name_to_id = {_norm_kw(str(c.get("name") or "")): c["cluster_id"] for c in clusters}
    pillar_by_id: dict[str, str] = {}
    link_warnings: list[str] = []
    for pillar in pillar_structure or []:
        pillar_name = str(pillar.get("pillar") or pillar.get("name") or "")
        for item in pillar.get("clusters") or []:
            if not isinstance(item, dict) or not pillar_name:
                continue
            cid = str(item.get("cluster_id") or "") or name_to_id.get(_norm_kw(str(item.get("name") or "")), "")
            if cid:
                pillar_by_id[cid] = pillar_name
            else:
                link_warnings.append(
                    f"Pillar '{pillar_name}' lists cluster '{item.get('name')}', which matches no cluster."
                )

    entries: list[dict[str, Any]] = []
    for cluster in clusters:
        primary = identify_primary_keyword(cluster)
        serp = serp_map.get(_norm_kw(primary)) or serp_map.get(primary)
        entry = map_cluster_to_url(
            cluster,
            crawled_pages=crawled,
            serp_summary=serp,
            commercial=commercial,
            client_domain=client_domain,
            offer_index=offer_index,
            site_stats=stats,
        )
        entry["market"] = _market_from(cluster, commercial)
        cid = str(entry.get("cluster_id") or "")
        if cid in pillar_by_id:
            entry["pillar"] = pillar_by_id[cid]
            entry["pillar_cluster_id"] = cid
            _apply_sheet_columns(entry, cluster, commercial=commercial, pillar=pillar_by_id[cid])
        elif cluster.get("pillar") and pillar_structure:
            entry.setdefault("link_warnings", []).append(
                f"Declared pillar '{cluster.get('pillar')}' is not in the pillar structure."
            )
        entries.append(entry)

    conversions = _convert_live_proposals(entries, conventions)
    _resolve_supporting_targets(entries)
    _fold_same_line_pages(entries, offer_index)
    collisions = conversions + _resolve_url_collisions(entries, conventions)
    apply_workbook_taxonomy(entries, commercial=commercial, offer_synonyms=offer_synonyms)
    _enrich_entries(entries, crawled, conventions)
    rejected = check_url_map_consistency(entries)
    for problem in rejected:
        row = next((e for e in entries if e.get("cluster_id") == problem["cluster_id"]), None)
        if row is not None:
            row["consistency_errors"] = problem["errors"]
            row["needs_human_review"] = True
            row.setdefault("decision_trail", []).append(
                "Rejected by the consistency guard: " + " ".join(problem["errors"])
            )

    # Row numbers tie the report's three tables (taxonomy, keywords, decision) together.
    for number, row in enumerate(entries, start=1):
        row["row_no"] = number

    by_action: dict[str, int] = {"OPTIMIZE_EXISTING": 0, "REVIEW_MERGE_REDIRECT": 0, "CREATE": 0, FOLD_INTO_NEW_PARENT: 0}
    for row in entries:
        key = row.get("action") or "CREATE"
        by_action[key] = by_action.get(key, 0) + 1
    by_url_status: dict[str, int] = {}
    for row in entries:
        key = str(row.get("url_status") or "unclassified")
        by_url_status[key] = by_url_status.get(key, 0) + 1
    duplicate_proposed = [c for c in collisions if c.get("type") == "proposed_vs_proposed"]

    return {
        "final_url_map": entries,
        "offer_synonyms": offer_synonyms or [],
        "crawl_page_count": len(crawled),
        "mapped_cluster_count": len(entries),
        "duplicate_proposed_urls": duplicate_proposed,
        "url_collisions": collisions,
        "inconsistent_rows": rejected,
        "link_warnings": link_warnings,
        "site_conventions": {
            "usual_depth": conventions["usual_depth"],
            "trailing_slash": conventions["trailing_slash"],
            "locales": sorted(conventions["locales"]),
        },
        "summary": {
            "optimize_existing": by_action["OPTIMIZE_EXISTING"],
            "review_merge_redirect": by_action["REVIEW_MERGE_REDIRECT"],
            "create": by_action["CREATE"],
            "fold_into_new_parent": by_action[FOLD_INTO_NEW_PARENT],
            "high_band": sum(1 for e in entries if e.get("score_band") == "HIGH"),
            "medium_band": sum(1 for e in entries if e.get("score_band") == "MEDIUM"),
            "low_band": sum(1 for e in entries if e.get("score_band") == "LOW"),
            "by_url_status": by_url_status,
            "no_dedicated_url": by_url_status.get("no_dedicated_url", 0),
            "cannibalization_risk": sum(1 for e in entries if e.get("cannibalization_risk")),
            "needs_human_review": sum(1 for e in entries if e.get("needs_human_review")),
            "duplicate_proposed_urls": len(duplicate_proposed),
            "url_collisions": len(collisions),
            "inconsistent_rows": len(rejected),
            "hygiene_review": sum(1 for e in entries if (e.get("url_hygiene") or {}).get("verdict") == "review"),
            "scored_without_search_console": sum(
                1 for e in entries if "ranking_evidence" in ((e.get("score_basis") or {}).get("unavailable") or [])
            ),
            "link_warnings": len(link_warnings) + sum(len(e.get("link_warnings") or []) for e in entries),
        },
    }


def _convert_live_proposals(
    entries: list[dict[str, Any]],
    conventions: dict[str, Any],
) -> list[dict[str, Any]]:
    """A proposal for a URL that already exists becomes an optimize decision,
    unless another row already optimizes that page (a collision, handled later)."""
    live = conventions.get("paths") or set()
    owned = {url_n(e["current_url"]) for e in entries if e.get("current_url")}
    out: list[dict[str, Any]] = []
    for entry in entries:
        proposed = entry.get("proposed_url")
        if not proposed or entry.get("dedicated_url") is False:
            continue
        p = url_n(proposed)
        if p not in live or p in owned:
            continue
        entry["current_url"] = p
        entry["proposed_url"] = None
        entry["selected_url"] = p
        entry["target_url"] = p
        entry["action"] = "OPTIMIZE_EXISTING"
        entry["url_status"] = "existing_url_needs_optimization"
        entry["mapping_status"] = "existing_url_needs_optimization"
        entry["target_type"] = "existing_page"
        entry["status"] = _sheet_status(p)
        note = f"Proposed URL {p} already exists on the site; converted to optimize that page."
        entry.setdefault("decision_trail", []).append(note)
        entry["mapping_reason"] = f"{entry.get('mapping_reason') or ''} {note}".strip()
        owned.add(p)
        out.append({
            "type": "proposed_exists_on_site",
            "url": p,
            "owner_cluster": entry.get("cluster"),
            "owner_cluster_id": entry.get("cluster_id"),
            "other_cluster": None,
            "ownership_rule": "a live page wins over a new proposal; the row now optimizes it",
        })
    return out


def _enrich_entries(
    entries: list[dict[str, Any]],
    crawled: list[dict[str, Any]],
    conventions: dict[str, Any],
) -> None:
    """Fields every row carries once the decisions are final."""
    by_path = {url_n(str(p.get("path") or p.get("url") or "")): p for p in crawled if isinstance(p, dict)}
    canonical_of = {
        path: url_n(str(page.get("canonical_url")))
        for path, page in by_path.items()
        if page.get("canonical_url")
    }
    by_id = {str(e.get("cluster_id")): e for e in entries if e.get("cluster_id")}
    hubs: dict[str, str] = {}
    for e in entries:
        url = e.get("current_url") or e.get("proposed_url")
        if url and e.get("page_type") == "Hub Page" and e.get("l1_category"):
            hubs.setdefault(str(e["l1_category"]).lower(), url_n(url))
    locales = conventions.get("locales") or set()
    for entry in entries:
        cur, prop = entry.get("current_url"), entry.get("proposed_url")
        target = url_n(cur or prop or entry.get("target_url") or "") if (cur or prop or entry.get("target_url")) else None

        # Existing page quality on rows that work with a live page.
        if cur:
            page = by_path.get(url_n(cur)) or {}
            cand = next((c for c in entry.get("url_candidates") or [] if url_n(str(c.get("path") or "")) == url_n(cur)), {})
            entry["existing_page_quality"] = {
                "word_count": page.get("word_count") if page.get("word_count") is not None else cand.get("word_count"),
                "content_quality": page.get("content_quality") or cand.get("content_quality"),
                "last_modified": page.get("last_modified") or page.get("lastmod") or cand.get("last_modified"),
            }

        # Consolidation: which URLs should redirect into the target.
        if cur and (
            entry.get("url_status") == "existing_url_needs_consolidation"
            or entry.get("action") == "REVIEW_MERGE_REDIRECT"
        ):
            sources: list[str] = []
            page = by_path.get(url_n(cur)) or {}
            for url in [*(entry.get("competing_urls") or []), *(page.get("potential_cannibalization") or [])]:
                path = url_n(str(url))
                if path and path != url_n(cur) and path not in sources:
                    sources.append(path)
            entry["redirect_sources"] = sources
        else:
            entry["redirect_sources"] = []

        # Canonical conflicts.
        conflicts: list[str] = []
        if prop:
            p = url_n(prop)
            for path, canon in canonical_of.items():
                if canon == p and path != p:
                    conflicts.append(f"{path} already declares {p} as its canonical.")
        if cur:
            c = url_n(cur)
            canon = canonical_of.get(c)
            if canon and canon != c:
                conflicts.append(f"{c} declares {canon} as its canonical, so optimizing {c} may not rank.")
        entry["canonical_conflict"] = bool(conflicts)
        entry["canonical_conflicts"] = conflicts

        # Locale / hreflang: only meaningful when the site serves several locales.
        segments = [s for s in (target or "").strip("/").split("/") if s]
        if locales and segments and segments[0] in locales:
            entry["locale"] = segments[0]
            entry["hreflang_group"] = "/" + "/".join(segments[1:]) if len(segments) > 1 else "/"
        else:
            entry["locale"] = None
            entry["hreflang_group"] = None

        # Internal link parents: which hub or pillar page should link down here.
        parents: list[str] = []
        parent_row = by_id.get(str(entry.get("parent_cluster_id") or ""))
        if parent_row is not None:
            purl = parent_row.get("current_url") or parent_row.get("proposed_url")
            if purl:
                parents.append(url_n(purl))
        hub = hubs.get(str(entry.get("l1_category") or "").lower())
        if hub and target and hub != target and hub not in parents:
            parents.append(hub)
        if target and len(segments) >= 2:
            folder = "/" + segments[0]
            if folder != target and folder not in parents and (folder in by_path or folder in conventions.get("paths", set())):
                parents.append(folder)
        entry["internal_link_parents"] = parents

        # URL hygiene on every proposal (flag, never rewrite).
        if prop:
            entry["url_hygiene"] = url_hygiene(prop, conventions)
            if entry["url_hygiene"]["verdict"] == "review":
                entry["needs_human_review"] = True
                entry.setdefault("decision_trail", []).append(
                    "URL hygiene: " + " ".join(entry["url_hygiene"]["problems"])
                )
        else:
            entry["url_hygiene"] = {"verdict": "n/a", "problems": []}

        entry["status"] = _sheet_status(cur)
        _set_priority(entry)
        effort, effort_basis = _effort(entry)
        entry["effort"] = effort
        entry["effort_basis"] = effort_basis
        entry.setdefault("decision_trail", []).append(f"Priority {entry['priority']}: {entry['priority_reason']}.")
        if not entry.get("notes"):
            entry["notes"] = _url_map_notes(entry, {"keywords": []})
        elif entry.get("action") != "REVIEW_MERGE_REDIRECT":
            # Notes were written before folds and conversions changed the action.
            kept = [b for b in str(entry["notes"]).split("; ") if b and b != _WEAK_MATCH_NOTE]
            entry["notes"] = "; ".join(kept) or "—"


def _resolve_supporting_targets(entries: list[dict[str, Any]]) -> None:
    """Point supporting clusters at the page their parent cluster owns.

    Links on the parent's cluster id, never its name (names are rewritten later
    in the pipeline). A parent with a live page gets the supporting row as an
    optimize-that-page row; a parent whose page does not exist yet gets a
    fold-into-new-parent row carrying the parent's id. A parent that cannot be
    found leaves the row as it was, with a warning.
    """
    by_id = {str(e.get("cluster_id")): e for e in entries if isinstance(e, dict) and e.get("cluster_id")}
    by_name = {str(e.get("cluster") or ""): e for e in entries if isinstance(e, dict) and e.get("cluster")}
    for entry in entries:
        parent_id = str(entry.get("supporting_parent_id") or "")
        parent_name = str(entry.get("supporting_parent") or "")
        if not parent_id and not parent_name:
            continue
        parent = by_id.get(parent_id) if parent_id else None
        if parent is None and not parent_id and parent_name in by_name:
            # Older classification without ids: resolve the name once, then
            # carry the id forward, and say how it was resolved.
            parent = by_name[parent_name]
            entry.setdefault("link_warnings", []).append(
                f"Supporting parent '{parent_name}' resolved by name (no id on record)."
            )
        if parent is None:
            entry.setdefault("link_warnings", []).append(
                f"Declared supporting parent {parent_id or parent_name!r} could not be resolved; "
                "the row keeps its own decision."
            )
            entry["needs_human_review"] = True
            continue
        entry["parent_cluster_id"] = parent.get("cluster_id")
        live_url = parent.get("current_url")
        new_url = parent.get("proposed_url") or (
            parent.get("selected_url") if parent.get("action") == "CREATE" else None
        )
        if live_url:
            entry["action"] = "OPTIMIZE_EXISTING"
            entry["url_status"] = "no_dedicated_url"
            entry["current_url"] = live_url
            entry["selected_url"] = live_url
            entry["target_url"] = live_url
            note = f"Supporting cluster: its keywords strengthen the parent's live page {live_url}."
        elif new_url:
            entry["action"] = FOLD_INTO_NEW_PARENT
            entry["url_status"] = "fold_into_new_parent"
            entry["current_url"] = None
            entry["selected_url"] = new_url
            entry["target_url"] = new_url
            note = (
                f"Supporting cluster: folds into the parent page {new_url}, which is yet to be created "
                f"(parent '{parent.get('cluster')}')."
            )
        else:
            entry.setdefault("link_warnings", []).append(
                f"Supporting parent '{parent.get('cluster')}' has no page of its own yet."
            )
            entry["needs_human_review"] = True
            continue
        entry["mapping_status"] = entry["url_status"]
        entry["dedicated_url"] = False
        entry["target_type"] = "parent_page"
        entry["proposed_url"] = None
        entry["status"] = _sheet_status(entry.get("current_url"))
        taxonomy = _taxonomy_levels(
            entry.get("current_url") or entry.get("target_url"),
            pillar=parent.get("l1_category"),
        )
        _set_taxonomy(entry, taxonomy)
        _set_priority(entry)
        entry.setdefault("decision_trail", []).append(note)
        entry["mapping_reason"] = f"{entry.get('mapping_reason') or ''} {note}".strip()


# --------------------------------------------------------------------------- map-wide rules
# Everything below runs once every row exists, because each rule compares a
# row with the others or with the live site.

FOLD_INTO_NEW_PARENT = "FOLD_INTO_NEW_PARENT"
_RESERVED_FOLDERS = frozenset(
    {
        "blog", "blogs", "news", "page", "tag", "tags", "category", "categories", "author",
        "feed", "search", "cart", "checkout", "account", "my-account", "wp-admin",
        "wp-content", "wp-includes", "wp-json", "amp",
    }
)
_SLUG_STOP_WORDS = frozenset({"the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "with", "at", "by"})
_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")
_MAX_URL_LENGTH = 75


def _site_url_conventions(crawled: list[dict[str, Any]]) -> dict[str, Any]:
    """How this site already writes URLs: usual depth, trailing slash, slugs."""
    depths: dict[int, int] = {}
    slash = no_slash = 0
    slugs: dict[str, list[str]] = {}
    paths: set[str] = set()
    locales: set[str] = set()
    for page in crawled or []:
        raw = str(page.get("url") or page.get("path") or "")
        path = urlparse(raw).path if "://" in raw else raw
        if not path or path == "/":
            continue
        if path.endswith("/"):
            slash += 1
        else:
            no_slash += 1
        norm = url_n(path)
        paths.add(norm)
        segments = [s for s in norm.strip("/").split("/") if s]
        if segments and _LOCALE_SEGMENT.match(segments[0]) and len(segments) > 1:
            locales.add(segments[0])
        depth = len(segments)
        depths[depth] = depths.get(depth, 0) + 1
        if segments:
            slugs.setdefault(segments[-1], []).append(norm)
    usual_depth = max(depths, key=lambda d: (depths[d], -d)) if depths else 1
    return {
        "usual_depth": usual_depth,
        "max_depth": max(depths) if depths else 1,
        "trailing_slash": slash > no_slash,
        "slug_index": slugs,
        "paths": paths,
        "locales": locales,
    }


def url_hygiene(proposed: str, conventions: dict[str, Any]) -> dict[str, Any]:
    """Check a proposed URL against the site's own conventions. Never rewrites it.

    Returns {"verdict": "ok" | "review", "problems": [...]}.
    """
    problems: list[str] = []
    norm = url_n(proposed)
    segments = [s for s in norm.strip("/").split("/") if s]
    if not segments:
        return {"verdict": "review", "problems": ["The proposal is the site root."]}
    usual = int(conventions.get("usual_depth") or 1)
    if len(segments) > max(usual + 1, int(conventions.get("max_depth") or 1)):
        problems.append(f"Depth {len(segments)} is deeper than the site's usual {usual}.")
    if len(norm) > _MAX_URL_LENGTH:
        problems.append(f"{len(norm)} characters long (aim for {_MAX_URL_LENGTH} or fewer).")
    words = segments[-1].split("-")
    stops = sorted({w for w in words if w in _SLUG_STOP_WORDS})
    if stops:
        problems.append(f"Slug carries stop words: {', '.join(stops)}.")
    parent_words = {w for seg in segments[:-1] for w in seg.split("-")}
    repeated = sorted({w for w in words if w in parent_words and w not in _SLUG_STOP_WORDS})
    if repeated:
        problems.append(f"Slug repeats its parent folder: {', '.join(repeated)}.")
    if str(proposed).strip().endswith("/") != bool(conventions.get("trailing_slash")) and len(norm) > 1:
        want = "with" if conventions.get("trailing_slash") else "without"
        problems.append(f"The site writes URLs {want} a trailing slash.")
    if len(segments) == 1 and segments[0] in _RESERVED_FOLDERS:
        problems.append(f"/{segments[0]}/ is a structural folder, not a page.")
    if re.search(r"/page/\d+$", norm):
        problems.append("Collides with a paginated archive path.")
    elsewhere = [p for p in (conventions.get("slug_index") or {}).get(segments[-1], []) if p != norm]
    if elsewhere:
        problems.append(f"Same slug already exists at {elsewhere[0]}.")
    return {"verdict": "review" if problems else "ok", "problems": problems}


def _opportunity_strength(entry: dict[str, Any], max_volume: float) -> float:
    """Composite for breaking ownership ties: opportunity (volume) and match strength."""
    import math

    volume = entry.get("combined_cluster_volume") or entry.get("search_volume") or 0
    try:
        volume = float(volume)
    except (TypeError, ValueError):
        volume = 0.0
    vol_norm = math.log1p(volume) / math.log1p(max_volume) if max_volume > 0 else 0.0
    return round(0.6 * vol_norm + 0.4 * float(entry.get("url_score") or 0) / 100, 4)


def _flag(entry: dict[str, Any], note: str) -> None:
    entry["cannibalization_risk"] = True
    entry["needs_human_review"] = True
    entry.setdefault("collisions", []).append(note)
    entry.setdefault("decision_trail", []).append(note)


def _resolve_url_collisions(
    entries: list[dict[str, Any]],
    conventions: dict[str, Any],
) -> list[dict[str, Any]]:
    """Every proposed URL against the live site, other rows' current URLs, and
    other proposals. Both sides of a collision are flagged; the rule that
    awarded ownership is recorded."""
    collisions: list[dict[str, Any]] = []
    live = conventions.get("paths") or set()
    current_owner: dict[str, dict[str, Any]] = {}
    for entry in entries:
        cur = entry.get("current_url")
        if cur and entry.get("action") != FOLD_INTO_NEW_PARENT:
            current_owner.setdefault(url_n(cur), entry)

    for entry in entries:
        proposed = entry.get("proposed_url")
        if not proposed or entry.get("dedicated_url") is False:
            continue
        p = url_n(proposed)
        owner = current_owner.get(p)
        if owner is not None and owner is not entry:
            rule = "the page is live and another row already optimizes it"
            _flag(entry, f"Proposes {p}, which cluster '{owner.get('cluster')}' already optimizes ({rule}).")
            _flag(owner, f"Cluster '{entry.get('cluster')}' proposes this row's live page {p} as a new URL.")
            collisions.append({
                "type": "proposed_vs_current",
                "url": p,
                "owner_cluster": owner.get("cluster"),
                "owner_cluster_id": owner.get("cluster_id"),
                "other_cluster": entry.get("cluster"),
                "other_cluster_id": entry.get("cluster_id"),
                "ownership_rule": rule,
            })
            continue
        if p in live:
            # The proposed page already exists: optimize it instead of creating it.
            entry["current_url"] = p
            entry["proposed_url"] = None
            entry["selected_url"] = p
            entry["target_url"] = p
            entry["action"] = "OPTIMIZE_EXISTING"
            entry["url_status"] = "existing_url_needs_optimization"
            entry["mapping_status"] = "existing_url_needs_optimization"
            entry["target_type"] = "existing_page"
            entry["status"] = _sheet_status(p)
            note = f"Proposed URL {p} already exists on the site; converted to optimize that page."
            entry.setdefault("decision_trail", []).append(note)
            entry["mapping_reason"] = f"{entry.get('mapping_reason') or ''} {note}".strip()
            current_owner[p] = entry
            collisions.append({
                "type": "proposed_exists_on_site",
                "url": p,
                "owner_cluster": entry.get("cluster"),
                "owner_cluster_id": entry.get("cluster_id"),
                "other_cluster": None,
                "ownership_rule": "a live page wins over a new proposal; the row now optimizes it",
            })

    by_url: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        proposed = entry.get("proposed_url")
        if proposed and entry.get("dedicated_url") is not False:
            by_url.setdefault(url_n(proposed), []).append(entry)
    for proposed, rows in by_url.items():
        if len(rows) < 2:
            continue
        max_volume = max(
            float(r.get("combined_cluster_volume") or r.get("search_volume") or 0) for r in rows
        )
        ranked = sorted(rows, key=lambda r: (-_opportunity_strength(r, max_volume), str(r.get("cluster") or "")))
        owner = ranked[0]
        rule = "higher combined opportunity and match strength"
        for other in ranked[1:]:
            _flag(other, f"Proposes {proposed}, also proposed by '{owner.get('cluster')}', which owns it ({rule}).")
        _flag(
            owner,
            f"Owns proposed URL {proposed}; also proposed by "
            + ", ".join(f"'{r.get('cluster')}'" for r in ranked[1:])
            + f" ({rule}).",
        )
        collisions.append({
            "type": "proposed_vs_proposed",
            "url": proposed,
            "proposed_url": proposed,
            "owner_cluster": owner.get("cluster"),
            "owner_cluster_id": owner.get("cluster_id"),
            "competing_clusters": [r.get("cluster") for r in ranked[1:]],
            "competing_cluster_ids": [r.get("cluster_id") for r in ranked[1:]],
            "ownership_rule": rule,
        })
    return collisions


def _attribution(cluster: dict[str, Any], primary: str) -> str | None:
    """The cluster's own narrowing dimension: a location or an audience."""
    pattern = str(cluster.get("modifier_pattern") or "")
    if pattern == "location" and cluster.get("modifier_location"):
        loc = str(cluster["modifier_location"])
        return None if loc == "near me" else loc.title()
    if pattern == "audience":
        match = re.search(r"\bfor\s+(.+)$", _norm_kw(primary))
        if match:
            return match.group(1).title()
    return None


_PAGE_TYPE_WEIGHT = {
    "Hub Page": 3.0,
    "Landing Page": 2.0,
    "Brand Collection": 2.0,
    "Collection": 2.0,
    "Sub-collection": 1.5,
    "Attribution Collection": 1.0,
}


def _priority_with_reason(
    page_type: str,
    *,
    live: bool,
    band: str,
    volume: Any = None,
    difficulty: Any = None,
) -> tuple[str, str]:
    """P0-P3 from structural importance and commercial value, with the reasoning."""
    parts: list[str] = []
    structural = _PAGE_TYPE_WEIGHT.get(page_type, 1.5)
    parts.append(f"{page_type} +{structural:g}")
    score = structural
    if live:
        score += 0.5
        parts.append("live +0.5")
    try:
        vol = float(volume) if volume not in (None, "") else None
    except (TypeError, ValueError):
        vol = None
    if vol is None:
        parts.append("volume not measured +0")
    else:
        points = 3 if vol >= 1000 else 2 if vol >= 200 else 1 if vol >= 50 else 0
        score += points
        parts.append(f"{int(vol):,}/mo +{points}")
    try:
        kd = float(difficulty) if difficulty not in (None, "") else None
    except (TypeError, ValueError):
        kd = None
    if kd is not None:
        points = 1 if kd <= 30 else -1 if kd > 60 else 0
        score += points
        parts.append(f"KD {kd:g} {points:+d}")
    if live and band in ("HIGH", "LOW"):
        points = 1 if band == "HIGH" else -1
        score += points
        parts.append(f"{band} match {points:+d}")
    priority = "P0" if score >= 6 else "P1" if score >= 4.5 else "P2" if score >= 3 else "P3"
    return priority, f"{', '.join(parts)} = {score:g} -> {priority}"


def _effort(entry: dict[str, Any]) -> tuple[str, str]:
    action = str(entry.get("action") or "")
    quality = entry.get("existing_page_quality") or {}
    if action == FOLD_INTO_NEW_PARENT:
        return "Low", "Its keywords are written into the parent page when that page is built."
    if entry.get("dedicated_url") is False and action != "CREATE":
        return "Low", "No page of its own; keywords strengthen another page."
    if action == "CREATE":
        return "High", "A new page to plan, write, design and link."
    if entry.get("redirect_sources"):
        return "High", "Merge content and redirect the competing URLs into the target."
    words = quality.get("word_count")
    thin = str(quality.get("content_quality") or "") in ("thin", "broken") or (
        isinstance(words, (int, float)) and words < 300
    )
    if thin:
        return "Medium", "The existing page is thin and needs substantial new content."
    return "Low", "On-page optimization of a page that already covers the topic."


def _consistency_errors(entry: dict[str, Any]) -> list[str]:
    """Action, status and URL columns that contradict each other."""
    errors: list[str] = []
    action = str(entry.get("action") or "")
    cur, prop = entry.get("current_url"), entry.get("proposed_url")
    if cur and prop:
        errors.append("Both a current URL and a proposed URL.")
    if entry.get("status") in ("LIVE", "NEW") and (entry.get("status") == "LIVE") != bool(cur):
        errors.append(f"Status {entry.get('status')} disagrees with the current URL column.")
    if action == "CREATE":
        if cur:
            errors.append("Create row points at an existing URL.")
        if not prop:
            errors.append("Create row proposes no URL.")
    elif action == "OPTIMIZE_EXISTING":
        if not cur:
            errors.append("Optimize row has no current URL.")
        if prop:
            errors.append("Optimize row also proposes a new URL.")
    elif action == "REVIEW_MERGE_REDIRECT":
        if prop:
            errors.append("Review/merge row proposes a new URL.")
    elif action == FOLD_INTO_NEW_PARENT:
        if cur or prop:
            errors.append("Fold-into-new-parent row has its own URL.")
        if not entry.get("parent_cluster_id"):
            errors.append("Fold-into-new-parent row has no parent id.")
    url_status = str(entry.get("url_status") or "")
    if url_status == "new_proposed_url" and action != "CREATE":
        errors.append(f"URL status new_proposed_url with action {action}.")
    if url_status.startswith("existing_url") and action == "CREATE":
        errors.append(f"URL status {url_status} with action CREATE.")
    return errors


def check_url_map_consistency(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The guard: every row whose columns contradict each other, with why."""
    return [
        {"cluster": e.get("cluster"), "cluster_id": e.get("cluster_id"), "errors": errs}
        for e in entries
        if (errs := _consistency_errors(e))
    ]


def apply_url_map_to_architecture(
    architecture: dict[str, Any],
    url_map_report: dict[str, Any],
) -> dict[str, Any]:
    """Merge final URL map into target_url_tree and cluster_ownership."""
    arch = dict(architecture)
    tree = list(arch.get("target_url_tree") or [])
    ownership = list(arch.get("cluster_ownership") or arch.get("cluster_owners") or [])
    existing_paths = {url_n(str(n.get("url") or n.get("path") or "")) for n in tree if isinstance(n, dict)}
    # Track which cluster currently owns each mapped path so a second cluster
    # resolving to the same URL is flagged as cannibalization instead of
    # silently overwriting the first cluster's attribution with no trace.
    claimed_by: dict[str, dict[str, Any]] = {}

    for entry in url_map_report.get("final_url_map") or []:
        if not isinstance(entry, dict):
            continue
        cluster_name = str(entry.get("cluster") or "").strip()
        selected = url_n(str(entry.get("selected_url") or ""))
        action = entry.get("action")
        score = float(entry.get("url_score") or 0)
        if action in ("CREATE", FOLD_INTO_NEW_PARENT) or not selected or selected == "/":
            continue
        if entry.get("consistency_errors"):
            # A row the consistency guard rejected must not drive the tree.
            continue

        prior = claimed_by.get(selected)
        if prior and prior["cluster"] != cluster_name:
            winner, loser = (
                (prior, {"cluster": cluster_name, "url_score": score})
                if prior["url_score"] >= score
                else ({"cluster": cluster_name, "url_score": score}, prior)
            )
            ownership.append(
                {
                    "cluster": loser["cluster"],
                    "canonical_owner_url": selected,
                    "owner_url": selected,
                    "competing_urls": [selected],
                    "disposition": "cannibalization_review",
                    "cannibalization": True,
                    "url_score": loser["url_score"],
                    "note": (
                        f"Cluster '{loser['cluster']}' also mapped to {selected}, already "
                        f"claimed by '{winner['cluster']}' (score {winner['url_score']} vs "
                        f"{loser['url_score']}) — flagged for human review, not auto-merged."
                    ),
                }
            )
            if winner is prior:
                # Existing owner outscored (or tied) the newcomer — keep its
                # attribution on the node and skip re-applying this entry.
                continue
            claimed_by[selected] = {"cluster": cluster_name, "url_score": score}
        else:
            claimed_by[selected] = {"cluster": cluster_name, "url_score": score}

        if selected in existing_paths:
            for node in tree:
                if isinstance(node, dict) and url_n(str(node.get("url") or node.get("path") or "")) == selected:
                    node["cluster"] = cluster_name or node.get("cluster")
                    node["primary_keyword"] = entry.get("primary_keyword")
                    node["keyword"] = entry.get("primary_keyword")
                    node["url_map_action"] = action
                    node["url_score"] = entry.get("url_score")
                    mapped_type = str(entry.get("ia_page_type") or "").strip()
                    if mapped_type:
                        node["page_type"] = mapped_type
                        node["type"] = mapped_type
            # Record ownership for existing-page mappings too — previously only
            # newly-created nodes got a cluster_ownership row, so OPTIMIZE_EXISTING
            # (the most common action on a mature site) was invisible to the
            # cannibalization/ownership stage check downstream.
            ownership.append(
                {
                    "cluster": cluster_name,
                    "canonical_owner_url": selected,
                    "owner_url": selected,
                    "competing_urls": entry.get("competing_urls") or [],
                    "disposition": "optimize" if action == "OPTIMIZE_EXISTING" else "review",
                    "url_score": entry.get("url_score"),
                    "score_band": entry.get("score_band"),
                    "note": f"Mapped to existing page — {action}",
                }
            )
            continue
        mapped_type = str(entry.get("ia_page_type") or "page").strip() or "page"
        tree.append(
            {
                "url": selected,
                "path": selected,
                "cluster": cluster_name,
                "primary_keyword": entry.get("primary_keyword"),
                "keyword": entry.get("primary_keyword"),
                "page_type": mapped_type,
                "type": mapped_type,
                "from_url_map": True,
                "url_map_action": action,
                "url_score": entry.get("url_score"),
                "parent": "/" + selected.strip("/").split("/")[0] if selected.count("/") > 1 else "/",
                "depth": max(1, selected.strip("/").count("/") + 1) if selected not in ("/", "") else 0,
            }
        )
        existing_paths.add(selected)
        ownership.append(
            {
                "cluster": cluster_name,
                "canonical_owner_url": selected,
                "owner_url": selected,
                "competing_urls": entry.get("competing_urls") or [],
                "disposition": "optimize" if action == "OPTIMIZE_EXISTING" else "review",
                "url_score": entry.get("url_score"),
                "score_band": entry.get("score_band"),
                "note": f"Mapped from crawl — {action}",
            }
        )

    arch["target_url_tree"] = tree
    arch["cluster_ownership"] = ownership
    arch["cluster_owners"] = ownership
    arch["final_url_map"] = url_map_report.get("final_url_map") or []
    arch["url_map_summary"] = url_map_report.get("summary") or {}
    return arch


def lookup_url_map_entry(
    url_map_report: dict[str, Any] | None,
    *,
    cluster: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any] | None:
    """Find a mapped entry by cluster name or primary keyword."""
    if not url_map_report:
        return None
    cluster_l = (cluster or "").strip().lower()
    kw_l = _norm_kw(keyword or "")
    for entry in url_map_report.get("final_url_map") or []:
        if not isinstance(entry, dict):
            continue
        if cluster_l and str(entry.get("cluster") or "").strip().lower() == cluster_l:
            return entry
        if kw_l and _norm_kw(str(entry.get("primary_keyword") or "")) == kw_l:
            return entry
    return None
