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
    combined_cluster_volume,
    identify_primary_keyword,
    identify_secondary_keywords,
    primary_keyword_metrics,
)
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

_INTENT_PAGE_AFFINITY = {
    "transactional": {"service", "subservice", "sub_service", "service_hub", "services", "sub_services", "location", "product", "landing"},
    "commercial": {"service", "subservice", "sub_service", "services", "comparison", "guides", "location", "sub_services"},
    "informational": {"blog", "guides", "guide", "article", "hub", "spoke"},
    "navigational": {"home", "service_hub", "other"},
}


_STATUS_LABELS = {
    "OPTIMIZE_EXISTING": "Optimize Existing",
    "REVIEW_MERGE_REDIRECT": "Review / Merge / Redirect",
    "CREATE": "Create New",
}
_PRIORITY_LABELS = {"HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}
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


def _taxonomy_levels(url: str | None) -> dict[str, Any]:
    """L1-L4 taxonomy columns, derived from the actual/planned URL path segments
    — not invented labels. Works for any site: /services/seo/local (service
    business) and /electronics/laptops/gaming/16-inch (e-commerce) both resolve
    the same way. A shallower URL just leaves the deeper levels blank.
    """
    raw = str(url or "").strip()
    if not raw:
        return {"level": 0, "l1_category": None, "l2_subcategory": None, "l3_subsubcategory": None, "l4_attribution": None}
    path = urlparse(raw).path if "://" in raw else raw
    segments = [s.replace("-", " ").replace("_", " ").strip().title() for s in path.split("/") if s.strip()]
    return {
        "level": min(len(segments), 4),
        "l1_category": segments[0] if len(segments) >= 1 else None,
        "l2_subcategory": segments[1] if len(segments) >= 2 else None,
        "l3_subsubcategory": segments[2] if len(segments) >= 3 else None,
        "l4_attribution": segments[3] if len(segments) >= 4 else None,
    }


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
                return "Review"
            return "Yes"
    return "Yes"


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
        bits.append("Existing page is a weak match — confirm before merging")
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
    """Token overlap between keyword and page title + URL slug (0–25)."""
    path = url_n(str(page.get("path") or page.get("url") or ""))
    slug = path.strip("/").replace("-", " ").replace("/", " ")
    title = str(page.get("title") or page.get("keyword") or "")
    ratios = [
        _overlap_ratio(primary_keyword, title),
        _overlap_ratio(primary_keyword, slug),
        _overlap_ratio(primary_keyword, str(page.get("h1") or "")),
    ]
    return round(max(ratios) * 25, 2)


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
) -> float:
    """Primary + secondary keyword overlap (0–25)."""
    title = str(page.get("title") or "")
    kw = str(page.get("keyword") or page.get("primary_keyword") or "")
    path = url_n(str(page.get("path") or page.get("url") or ""))
    slug = path.strip("/").replace("-", " ")
    primary = max(
        _overlap_ratio(primary_keyword, title),
        _overlap_ratio(primary_keyword, kw),
        _overlap_ratio(primary_keyword, slug),
    )
    secondary_hits = 0
    for sec in secondary_keywords[:6]:
        if _overlap_ratio(sec, f"{title} {kw} {slug}") >= 0.5:
            secondary_hits += 1
    sec_bonus = min(8, secondary_hits * 2)
    return round(min(25, primary * 17 + sec_bonus), 2)


def score_ranking_evidence(
    page: dict[str, Any],
    *,
    serp_summary: dict[str, Any] | None = None,
    client_domain: str | None = None,
) -> float:
    """GSC metrics + SERP presence (0–20)."""
    score = 0.0
    metrics = dict(page.get("metrics") or {})
    clicks = float(metrics.get("clicks") or 0)
    impressions = float(metrics.get("impressions") or 0)
    position = metrics.get("position")
    if clicks > 0:
        score += min(8, clicks / 25)
    if impressions > 50:
        score += min(4, impressions / 500)
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
    for row in (serp_summary or {}).get("organic") or []:
        if not isinstance(row, dict):
            continue
        row_url = url_n(str(row.get("url") or ""))
        row_dom = str(row.get("domain") or "").lower()
        if row_url == path or (domain and domain in row_dom and _overlap_ratio(path, row_url) >= 0.8):
            try:
                pos = int(row.get("position") or 99)
                score += max(0, 10 - min(pos, 10))
            except (TypeError, ValueError):
                score += 3
            break
    return round(min(20, score), 2)


def score_traffic_business(
    cluster: dict[str, Any],
    page: dict[str, Any],
    *,
    commercial: dict[str, Any] | None = None,
) -> float:
    """Opportunity + CDD business weight (0–15)."""
    score = 0.0
    best = float(cluster.get("best_score") or 0)
    traffic = cluster.get("est_traffic")
    if best >= 70:
        score += 5
    elif best >= 40:
        score += 3
    if isinstance(traffic, (int, float)) and traffic > 0:
        score += min(4, float(traffic) / 500)
    terms = cdd_terms_from_commercial(commercial)
    cdd = match_cdd_page(
        str(page.get("url") or page.get("path") or ""),
        str(page.get("title") or ""),
        terms,
    )
    score += min(6, float(cdd.get("cdd_priority") or 0) * 2)
    return round(min(15, score), 2)


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
) -> dict[str, Any]:
    """Combine all signals into a URL score (0–100)."""
    path = url_n(str(page.get("path") or page.get("url") or ""))
    semantic = score_semantic_match(primary_keyword, page)
    intent = score_intent_match(cluster_intent, page, serp_summary=serp_summary)
    keyword = score_keyword_topic_match(primary_keyword, secondary_keywords, page)
    ranking = score_ranking_evidence(page, serp_summary=serp_summary, client_domain=client_domain)
    business = score_traffic_business(cluster, page, commercial=commercial)
    exact_boost = 0.0
    if suggested_url and url_n(suggested_url) == path:
        exact_boost = 20.0
    total = round(min(100, semantic + intent + keyword + ranking + business + exact_boost), 2)
    return {
        "url": page.get("url") or path,
        "path": path,
        "title": page.get("title"),
        "url_score": total,
        "score_breakdown": {
            "semantic_match": semantic,
            "intent_match": intent,
            "keyword_topic_match": keyword,
            "ranking_evidence": ranking,
            "traffic_business": business,
            "exact_url_boost": exact_boost,
        },
        "candidate_reason": page.get("candidate_reason"),
        "disposition": page.get("disposition"),
        "metrics": page.get("metrics"),
        # Site-map judgements about the page itself. Carried through so topic
        # classification can tell "right page, under-built" from "right page,
        # already good" without re-reading the inventory.
        "page_type": page.get("page_type") or page.get("cluster"),
        "content_quality": page.get("content_quality"),
        "word_count": page.get("word_count"),
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
    scored = [
        score_url_candidate(
            page,
            cluster=cluster,
            primary_keyword=primary,
            secondary_keywords=secondaries,
            cluster_intent=intent,
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
        "score_breakdown": (best or {}).get("score_breakdown") or {},
        "matched_page": {
            "url": best.get("url"),
            "title": best.get("title"),
            "page_type": best.get("page_type"),
            "content_quality": best.get("content_quality"),
            "word_count": best.get("word_count"),
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
            "needs_review": bool(cluster.get("topic_needs_review")),
        }

    new_action, url_status, dedicated = mapped
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
    elif effective == "NEW_TOPIC":
        if band == "LOW" or not has_existing:
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
        "topic_status": status or effective,
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
) -> dict[str, Any]:
    """Run the full URL mapping pipeline for one cluster."""
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
    best_score = float((best or {}).get("url_score") or 0)
    band = band_from_score(best_score)
    create_url = str(suggested or cluster.get("recommended_url") or f"/services/{_slug(primary)}")

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
        best_score = best_score or float(prior_match.get("match_score") or 0)
        band = band_from_score(best_score)
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

    # --- URL Mapping & Taxonomy sheet columns -------------------------------
    taxonomy = _taxonomy_levels(current_url or proposed_url)
    metrics = primary_keyword_metrics(cluster)
    from app.services.create_content import resolve_page_type

    page_type = resolve_page_type(
        {"url": current_url or proposed_url, "keyword": primary, "intent": intent}
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
        "url_candidates": scored[:8],
        "selected_url": final_url if final_url else None,
        "url_score": best_score,
        "score_band": band,
        "action": action,
        "match_confidence": round(best_score / 100, 2),
        "competing_urls": competing,
        "create_url": create_url,
        # --- URL Mapping entity: the decision, its basis, and its confidence --
        "cluster_id": cluster.get("cluster_id") or _slug(str(cluster.get("name") or primary)),
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
        "confidence": (
            float(cluster.get("topic_confidence"))
            if isinstance(cluster.get("topic_confidence"), (int, float))
            else round(best_score / 100, 2)
        ),
        "supporting_parent": (cluster.get("supporting_parent") or {}).get("parent_cluster"),
        # --- URL Mapping & Taxonomy sheet ------------------------------
        "level": taxonomy["level"],
        "l1_category": taxonomy["l1_category"],
        "l2_subcategory": taxonomy["l2_subcategory"],
        "l3_subsubcategory": taxonomy["l3_subsubcategory"],
        "l4_attribution": taxonomy["l4_attribution"],
        "current_url": current_url,
        "proposed_url": proposed_url,
        "status": _STATUS_LABELS.get(action, action),
        "search_volume": metrics["volume"],
        "cpc": metrics["cpc"],
        "secondary_keywords_sheet": identify_secondary_keywords(cluster, limit=10),
        "combined_cluster_volume": combined_cluster_volume(cluster),
        "page_type": page_type,
        "priority": _PRIORITY_LABELS.get(band, band),
        "est_products": _estimate_products(cluster, commercial),
        "in_scope": _in_scope_status(cluster),
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
    entry["notes"] = _url_map_notes(entry, cluster)
    return entry


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


def build_final_url_map(
    cluster_report: dict[str, Any],
    *,
    website: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
    serp_by_keyword: dict[str, dict[str, Any]] | None = None,
    commercial: dict[str, Any] | None = None,
    client_domain: str | None = None,
    pillar_structure: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Map every cluster to a final URL with score band and action.

    If pillar_structure (from Phase 6a) is provided, respects the pillar/cluster
    grouping when mapping URLs — ensures clusters within a pillar are not mapped
    to conflicting URLs or separate silos.
    """
    crawled = collect_crawl_pages(website=website, content_audit=content_audit)
    serp_map = serp_by_keyword or {}

    # Build a map of which clusters belong to which pillar (from Phase 6a)
    pillar_map: dict[str, str] = {}
    if pillar_structure:
        for pillar in pillar_structure:
            pillar_name = str(pillar.get("pillar") or pillar.get("name") or "")
            for cluster_in_pillar in pillar.get("clusters") or []:
                if isinstance(cluster_in_pillar, dict):
                    cluster_name = str(cluster_in_pillar.get("name") or "")
                    if cluster_name and pillar_name:
                        pillar_map[cluster_name] = pillar_name

    entries: list[dict[str, Any]] = []
    for cluster in cluster_report.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        primary = identify_primary_keyword(cluster)
        serp = serp_map.get(_norm_kw(primary)) or serp_map.get(primary)
        cluster_name = str(cluster.get("name") or "")
        entry = map_cluster_to_url(
            cluster,
            crawled_pages=crawled,
            serp_summary=serp,
            commercial=commercial,
            client_domain=client_domain,
        )
        # Preserve pillar structure from Phase 6a (Option B: Phase 6b respects Phase 6a grouping)
        if cluster_name in pillar_map:
            entry["pillar"] = pillar_map[cluster_name]
        entries.append(entry)

    _resolve_supporting_targets(entries)
    duplicate_proposed = _flag_duplicate_proposed_urls(entries)

    by_action = {"OPTIMIZE_EXISTING": 0, "REVIEW_MERGE_REDIRECT": 0, "CREATE": 0}
    for row in entries:
        by_action[row.get("action") or "CREATE"] = by_action.get(row.get("action") or "CREATE", 0) + 1

    by_url_status: dict[str, int] = {}
    for row in entries:
        key = str(row.get("url_status") or "unclassified")
        by_url_status[key] = by_url_status.get(key, 0) + 1

    return {
        "final_url_map": entries,
        "crawl_page_count": len(crawled),
        "mapped_cluster_count": len(entries),
        "duplicate_proposed_urls": duplicate_proposed,
        "summary": {
            "optimize_existing": by_action["OPTIMIZE_EXISTING"],
            "review_merge_redirect": by_action["REVIEW_MERGE_REDIRECT"],
            "create": by_action["CREATE"],
            "high_band": sum(1 for e in entries if e.get("score_band") == "HIGH"),
            "medium_band": sum(1 for e in entries if e.get("score_band") == "MEDIUM"),
            "low_band": sum(1 for e in entries if e.get("score_band") == "LOW"),
            "by_url_status": by_url_status,
            "no_dedicated_url": by_url_status.get("no_dedicated_url", 0),
            "cannibalization_risk": sum(1 for e in entries if e.get("cannibalization_risk")),
            "needs_human_review": sum(1 for e in entries if e.get("needs_human_review")),
            "duplicate_proposed_urls": len(duplicate_proposed),
        },
    }


def _resolve_supporting_targets(entries: list[dict[str, Any]]) -> None:
    """Point supporting clusters at the URL their parent cluster actually owns.

    map_cluster_to_url() only sees one cluster, so a supporting row leaves the
    batch with its own (or no) URL. Here — where every row is visible — the
    supporting row is repointed at the parent's page so its keywords strengthen
    that page instead of quietly becoming a second URL.
    """
    by_cluster = {
        str(e.get("cluster") or ""): e for e in entries if isinstance(e, dict) and e.get("cluster")
    }
    for entry in entries:
        parent_name = str(entry.get("supporting_parent") or "")
        if not parent_name:
            continue
        parent = by_cluster.get(parent_name)
        if not parent:
            continue
        parent_url = parent.get("selected_url") or parent.get("current_url") or parent.get("proposed_url")
        if not parent_url:
            continue
        entry["selected_url"] = parent_url
        entry["target_url"] = parent_url
        entry["current_url"] = parent.get("current_url")
        entry["proposed_url"] = None
        entry["action"] = "OPTIMIZE_EXISTING" if parent.get("current_url") else "REVIEW_MERGE_REDIRECT"
        entry["status"] = _STATUS_LABELS.get(entry["action"], entry["action"])
        entry["mapping_reason"] = (
            f"{entry.get('mapping_reason') or ''} Target resolved to parent page {parent_url}."
        ).strip()


def _flag_duplicate_proposed_urls(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two clusters proposing the same brand-new URL is cannibalization created
    by us, before the page even exists. Previously nothing checked this: the
    architecture merge skipped every CREATE row, so colliding proposals shipped.
    """
    by_url: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if not entry.get("dedicated_url", True):
            continue
        proposed = url_n(str(entry.get("proposed_url") or ""))
        if not proposed or proposed == "/":
            continue
        by_url.setdefault(proposed, []).append(entry)

    collisions: list[dict[str, Any]] = []
    for proposed, rows in by_url.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda e: -float(e.get("combined_cluster_volume") or 0))
        owner = rows[0]
        for loser in rows[1:]:
            loser["cannibalization_risk"] = True
            loser["needs_human_review"] = True
            loser["mapping_reason"] = (
                f"{loser.get('mapping_reason') or ''} Proposed URL {proposed} is already "
                f"proposed by cluster '{owner.get('cluster')}' — resolve before creating."
            ).strip()
        collisions.append(
            {
                "proposed_url": proposed,
                "owner_cluster": owner.get("cluster"),
                "competing_clusters": [r.get("cluster") for r in rows[1:]],
            }
        )
    return collisions


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
        if action == "CREATE" or not selected or selected == "/":
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
        tree.append(
            {
                "url": selected,
                "path": selected,
                "cluster": cluster_name,
                "primary_keyword": entry.get("primary_keyword"),
                "keyword": entry.get("primary_keyword"),
                "page_type": "service",
                "type": "service",
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
