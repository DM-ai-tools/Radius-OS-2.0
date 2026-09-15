"""Classify crawled pages into IA hierarchy for Phase 3 SEO audit.

Business-first order (aligned with site-architecture page-type model):
  Home → service hub → service → sub-service → location → guides → blog → other

Primary focus: pages that match CDD commercial scope (products, products_for_promotion,
business_keywords, geographic_focus). Does not invent URLs.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

# Hierarchy-first cluster order for reports and UI
CLUSTER_ORDER = (
    "home",
    "service_hub",
    "services",
    "sub_services",
    "location",
    "guides",
    "blog",
    "other",
)

CLUSTER_LABELS = {
    "home": "Home",
    "service_hub": "Service hubs",
    "services": "Service pages",
    "sub_services": "Sub-service pages",
    "location": "Location pages",
    "guides": "Guides",
    "blog": "Blogs",
    "other": "Other pages",
}

# Lower = closer to money (for sort within same cluster)
HIERARCHY_LEVEL = {
    "home": 0,
    "service_hub": 1,
    "services": 2,
    "sub_services": 3,
    "location": 4,
    "guides": 5,
    "blog": 6,
    "other": 7,
}

_BLOG = {"blog", "blogs", "news", "articles", "insights", "resources", "posts", "journal"}
_GUIDE = {"guide", "guides", "how-to", "howto", "learn", "academy", "docs", "documentation", "kb", "help"}
_SERVICE = {"service", "services", "solution", "solutions", "offerings", "what-we-do", "products", "product"}
_LOCATION = {
    "location",
    "locations",
    "areas-we-serve",
    "service-areas",
    "service-area",
    "near-me",
    "cities",
    "regions",
}
_OTHER_FIRST = {
    "about",
    "about-us",
    "team",
    "company",
    "our-story",
    "who-we-are",
    "contact",
    "contact-us",
    "get-in-touch",
    "enquire",
    "enquiry",
    "quote",
    "privacy",
    "privacy-policy",
    "terms",
    "terms-of-service",
    "legal",
    "cookies",
    "disclaimer",
    "careers",
    "jobs",
    "login",
    "cart",
    "checkout",
    "search",
    "sitemap",
    "feed",
    "author",
    "tag",
    "category",
}


def _segments(url: str) -> list[str]:
    raw = (url or "").strip()
    if not raw:
        return []
    if "://" not in raw:
        raw = "https://site.example" + (raw if raw.startswith("/") else f"/{raw}")
    path = urlparse(raw).path or "/"
    return [s.lower() for s in path.strip("/").split("/") if s]


def _slugify_term(term: str) -> str:
    t = (term or "").strip().lower()
    t = re.sub(r"[^a-z0-9\s\-_/]", "", t)
    t = re.sub(r"[\s_/]+", "-", t).strip("-")
    return t


def cdd_terms_from_commercial(commercial: dict[str, Any] | None) -> dict[str, list[str]]:
    """Extract matchable CDD terms from commercial_scope / flattened CDD."""
    commercial = dict(commercial or {})
    buckets: dict[str, list[str]] = {
        "products": [],
        "products_for_promotion": [],
        "business_keywords": [],
        "geographic_focus": [],
    }

    def _extend(key: str, raw: Any) -> None:
        if isinstance(raw, str):
            parts = [x.strip() for x in re.split(r"[,;|/]+", raw) if x.strip()]
            buckets[key].extend(parts)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("product") or item.get("keyword") or item.get("label")
                    if name:
                        buckets[key].append(str(name).strip())
                elif str(item).strip():
                    buckets[key].append(str(item).strip())

    for key in buckets:
        _extend(key, commercial.get(key))
    # Nested CDD packs
    for nest in ("cdd", "client_intake", "marketing_context"):
        nested = commercial.get(nest)
        if isinstance(nested, dict):
            for key in buckets:
                _extend(key, nested.get(key))

    # Dedupe preserve order
    clean: dict[str, list[str]] = {}
    for key, vals in buckets.items():
        seen: set[str] = set()
        out: list[str] = []
        for v in vals:
            n = v.lower()
            if n and n not in seen:
                seen.add(n)
                out.append(v)
        clean[key] = out[:40]
    return clean


def _all_terms_flat(terms: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for key in ("products_for_promotion", "products", "business_keywords", "geographic_focus"):
        for t in terms.get(key) or []:
            n = t.lower()
            if n not in seen:
                seen.add(n)
                out.append(t)
    return out


def match_cdd_page(
    url: str,
    title: str = "",
    terms: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return which CDD terms a URL/title appears to cover. Never invents pages."""
    terms = terms or {}
    segs = _segments(url)
    path_blob = "/".join(segs)
    title_l = (title or "").lower()
    hay = f"{path_blob} {title_l}".replace("-", " ").replace("_", " ")
    matched: list[dict[str, str]] = []
    for bucket, vals in terms.items():
        for term in vals:
            slug = _slugify_term(term)
            phrase = term.lower().strip()
            if not phrase:
                continue
            hit = False
            if slug and (slug in path_blob or slug.replace("-", " ") in hay) or len(phrase) >= 4 and phrase in hay or slug and any(slug == s or slug in s or s in slug for s in segs if len(s) > 2):
                hit = True
            if hit:
                matched.append({"term": term, "bucket": bucket})
    # Prefer promotion products as stronger business signal
    promo_hits = [m for m in matched if m["bucket"] == "products_for_promotion"]
    money_hits = [m for m in matched if m["bucket"] in ("products_for_promotion", "products", "business_keywords")]
    return {
        "cdd_focus": bool(money_hits),
        "cdd_geo": any(m["bucket"] == "geographic_focus" for m in matched),
        "cdd_matches": matched[:8],
        "cdd_terms": [m["term"] for m in matched[:8]],
        "cdd_priority": 3 if promo_hits else (2 if money_hits else (1 if matched else 0)),
    }


def classify_audit_page(url: str, title: str = "") -> dict[str, str]:
    """Return cluster + page_type for a crawled URL. Does not invent pages."""
    segs = _segments(url)
    blob = f"{' '.join(segs)} {title}".lower()
    if not segs:
        return {
            "cluster": "home",
            "cluster_label": CLUSTER_LABELS["home"],
            "page_type": "home",
            "hierarchy_level": "0",
            "parent_type": "—",
        }
    head = segs[0]
    if head in _BLOG or "blog" in blob:
        depth = "hub" if len(segs) == 1 else "post"
        return {
            "cluster": "blog",
            "cluster_label": CLUSTER_LABELS["blog"],
            "page_type": depth,
            "hierarchy_level": str(HIERARCHY_LEVEL["blog"]),
            "parent_type": "Home" if depth == "hub" else "Blog hub",
        }
    if head in _GUIDE or "guide" in blob or "how to" in blob:
        depth = "hub" if len(segs) == 1 else "guide"
        return {
            "cluster": "guides",
            "cluster_label": CLUSTER_LABELS["guides"],
            "page_type": depth,
            "hierarchy_level": str(HIERARCHY_LEVEL["guides"]),
            "parent_type": "Home" if depth == "hub" else "Guides hub",
        }
    if head in _LOCATION or head in {"areas-we-serve", "service-areas"}:
        depth = "hub" if len(segs) == 1 else "location"
        return {
            "cluster": "location",
            "cluster_label": CLUSTER_LABELS["location"],
            "page_type": depth,
            "hierarchy_level": str(HIERARCHY_LEVEL["location"]),
            "parent_type": "Home" if depth == "hub" else "Location hub",
        }
    if head in _SERVICE:
        if len(segs) == 1:
            return {
                "cluster": "service_hub",
                "cluster_label": CLUSTER_LABELS["service_hub"],
                "page_type": "hub",
                "hierarchy_level": str(HIERARCHY_LEVEL["service_hub"]),
                "parent_type": "Home",
            }
        if len(segs) == 2:
            return {
                "cluster": "services",
                "cluster_label": CLUSTER_LABELS["services"],
                "page_type": "service",
                "hierarchy_level": str(HIERARCHY_LEVEL["services"]),
                "parent_type": "Service hub",
            }
        return {
            "cluster": "sub_services",
            "cluster_label": CLUSTER_LABELS["sub_services"],
            "page_type": "sub_service",
            "hierarchy_level": str(HIERARCHY_LEVEL["sub_services"]),
            "parent_type": "Service page",
        }
    if head in _OTHER_FIRST:
        return {
            "cluster": "other",
            "cluster_label": CLUSTER_LABELS["other"],
            "page_type": "page",
            "hierarchy_level": str(HIERARCHY_LEVEL["other"]),
            "parent_type": "Home",
        }
    # Agency-style top-level service URLs: /seo, /web-design
    if len(segs) == 1:
        return {
            "cluster": "services",
            "cluster_label": CLUSTER_LABELS["services"],
            "page_type": "service",
            "hierarchy_level": str(HIERARCHY_LEVEL["services"]),
            "parent_type": "Home",
        }
    # Nested top-level without /services/ prefix: /seo/local → sub-service
    if len(segs) >= 2 and segs[0] not in _BLOG | _GUIDE | _LOCATION | _OTHER_FIRST:
        return {
            "cluster": "sub_services",
            "cluster_label": CLUSTER_LABELS["sub_services"],
            "page_type": "sub_service",
            "hierarchy_level": str(HIERARCHY_LEVEL["sub_services"]),
            "parent_type": "Service page",
        }
    return {
        "cluster": "other",
        "cluster_label": CLUSTER_LABELS["other"],
        "page_type": "page",
        "hierarchy_level": str(HIERARCHY_LEVEL["other"]),
        "parent_type": "Home",
    }


def _audit_select_score(url: str, terms: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    """Higher = audit sooner / keep when max_pages is tight."""
    meta = classify_audit_page(url)
    cdd = match_cdd_page(url, "", terms)
    cluster = meta["cluster"]
    base = {
        "home": 10_000,
        "service_hub": 8_000,
        "services": 7_000,
        "sub_services": 6_500,
        "location": 5_500,
        "guides": 3_000,
        "blog": 2_000,
        "other": 1_000,
    }.get(cluster, 500)
    # CDD money pages jump ahead of same-tier content
    base += int(cdd["cdd_priority"]) * 1_200
    if cdd["cdd_geo"] and cluster == "location":
        base += 800
    # Prefer shallower paths within tier
    depth = len(_segments(url))
    base -= min(depth, 6) * 15
    return base, {**meta, **cdd}


def prioritize_urls_for_audit(
    urls: list[str],
    *,
    commercial: dict[str, Any] | None = None,
    max_pages: int = 40,
    home_url: str | None = None,
) -> list[str]:
    """Order discovered URLs for audit: home → CDD money pages → hierarchy → rest."""
    terms = cdd_terms_from_commercial(commercial)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    home_norm = (home_url or "").rstrip("/")

    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        score, _ = _audit_select_score(u, terms)
        # Explicit home boost
        path = urlparse(u).path or "/"
        if path in ("", "/") or (home_norm and u.rstrip("/") == home_norm):
            score = max(score, 20_000)
        scored.append((score, u))

    scored.sort(key=lambda x: (-x[0], x[1]))
    limit = max(1, int(max_pages or 40))
    return [u for _, u in scored[:limit]]


def attach_page_clusters(
    pages: list[dict[str, Any]],
    *,
    commercial: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    terms = cdd_terms_from_commercial(commercial)
    for page in pages:
        if not isinstance(page, dict):
            continue
        url = str(page.get("url") or page.get("path") or "")
        title = str(page.get("title") or "")
        meta = classify_audit_page(url, title)
        cdd = match_cdd_page(url, title, terms)
        page["cluster"] = meta["cluster"]
        page["cluster_label"] = meta["cluster_label"]
        page["page_type"] = meta["page_type"]
        page["hierarchy_level"] = int(meta.get("hierarchy_level") or HIERARCHY_LEVEL.get(meta["cluster"], 7))
        page["parent_type"] = meta.get("parent_type") or "—"
        page["cdd_focus"] = cdd["cdd_focus"]
        page["cdd_geo"] = cdd["cdd_geo"]
        page["cdd_matches"] = cdd["cdd_matches"]
        page["cdd_terms"] = cdd["cdd_terms"]
        page["business_weight"] = (
            3.0
            if meta["cluster"] == "home"
            else (3.0 if cdd["cdd_focus"] else (2.0 if meta["cluster"] in ("service_hub", "services", "sub_services") else 1.0))
        )
    # Sort pages for report: hierarchy then CDD focus then path
    def _sort_key(p: Any) -> tuple:
        if not isinstance(p, dict):
            return (99, 1, "")
        level = p.get("hierarchy_level")
        if not isinstance(level, int):
            try:
                level = int(level)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                level = 99
        return (
            level,
            0 if p.get("cdd_focus") else 1,
            str(p.get("path") or p.get("url") or ""),
        )

    pages.sort(key=_sort_key)
    return pages


def page_cluster_summary(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "scores": [], "pages": [], "label": CLUSTER_LABELS["other"], "cdd_count": 0}
    )
    for page in pages:
        if not isinstance(page, dict):
            continue
        key = str(page.get("cluster") or "other")
        row = buckets[key]
        row["label"] = page.get("cluster_label") or CLUSTER_LABELS.get(key, key)
        row["count"] += 1
        if page.get("cdd_focus"):
            row["cdd_count"] += 1
        score = page.get("overall_score")
        if isinstance(score, (int, float)):
            row["scores"].append(float(score))
        row["pages"].append(
            {
                "url": page.get("url"),
                "path": page.get("path"),
                "title": page.get("title"),
                "page_type": page.get("page_type"),
                "overall_score": page.get("overall_score"),
                "cdd_focus": page.get("cdd_focus"),
                "cdd_terms": page.get("cdd_terms") or [],
            }
        )
    out: list[dict[str, Any]] = []
    for key in CLUSTER_ORDER:
        if key not in buckets:
            continue
        row = buckets[key]
        scores = row["scores"]
        out.append(
            {
                "cluster": key,
                "label": row["label"],
                "count": row["count"],
                "cdd_count": row["cdd_count"],
                "avg_score": round(sum(scores) / len(scores)) if scores else None,
                "pages": row["pages"],
            }
        )
    for key, row in buckets.items():
        if key in CLUSTER_ORDER:
            continue
        scores = row["scores"]
        out.append(
            {
                "cluster": key,
                "label": row["label"],
                "count": row["count"],
                "cdd_count": row["cdd_count"],
                "avg_score": round(sum(scores) / len(scores)) if scores else None,
                "pages": row["pages"],
            }
        )
    return out


def build_page_hierarchy(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat hierarchy outline for the report (home → hubs → services → subs…)."""
    order = list(CLUSTER_ORDER)
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        if not isinstance(page, dict):
            continue
        by_cluster[str(page.get("cluster") or "other")].append(page)

    tree: list[dict[str, Any]] = []
    for key in order:
        for page in by_cluster.get(key) or []:
            tree.append(
                {
                    "level": int(page.get("hierarchy_level") or HIERARCHY_LEVEL.get(key, 7)),
                    "cluster": key,
                    "cluster_label": page.get("cluster_label") or CLUSTER_LABELS.get(key, key),
                    "page_type": page.get("page_type"),
                    "parent_type": page.get("parent_type"),
                    "path": page.get("path"),
                    "url": page.get("url"),
                    "title": page.get("title"),
                    "overall_score": page.get("overall_score"),
                    "cdd_focus": bool(page.get("cdd_focus")),
                    "cdd_terms": list(page.get("cdd_terms") or []),
                }
            )
    return tree


def cdd_coverage_gaps(
    pages: list[dict[str, Any]],
    commercial: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """CDD products/keywords with no matching crawled page — business opportunity only."""
    terms = cdd_terms_from_commercial(commercial)
    covered: set[str] = set()
    for page in pages:
        if not isinstance(page, dict):
            continue
        for t in page.get("cdd_terms") or []:
            covered.add(str(t).lower())
        for m in page.get("cdd_matches") or []:
            if isinstance(m, dict) and m.get("term"):
                covered.add(str(m["term"]).lower())

    gaps: list[dict[str, Any]] = []
    for bucket in ("products_for_promotion", "products", "business_keywords"):
        for term in terms.get(bucket) or []:
            if term.lower() in covered:
                continue
            gaps.append(
                {
                    "term": term,
                    "bucket": bucket,
                    "issue": (
                        f"No crawled page clearly maps to CDD {bucket.replace('_', ' ')} “{term}” — "
                        "confirm URL exists or treat as content/IA gap"
                    ),
                    "ref": "cdd_coverage",
                }
            )
    return gaps[:15]


def weighted_business_score(pages: list[dict[str, Any]]) -> int | None:
    """Weight home + CDD money pages higher than blog/other for site score."""
    weighted: list[tuple[float, float]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        score = page.get("overall_score")
        if not isinstance(score, (int, float)):
            continue
        w = float(page.get("business_weight") or 1.0)
        weighted.append((float(score), w))
    if not weighted:
        return None
    return round(sum(s * w for s, w in weighted) / sum(w for _, w in weighted))


def finalize_seo_audit_clusters(
    report: dict[str, Any],
    *,
    commercial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    commercial = dict(commercial or report.get("commercial_scope") or {})
    pages = [p for p in (report.get("pages") or []) if isinstance(p, dict)]
    attach_page_clusters(pages, commercial=commercial)
    report["pages"] = pages
    report["page_clusters"] = page_cluster_summary(pages)
    report["page_hierarchy"] = build_page_hierarchy(pages)
    report["cdd_focus_terms"] = cdd_terms_from_commercial(commercial)
    report["cdd_pages_count"] = sum(1 for p in pages if p.get("cdd_focus"))
    gaps = cdd_coverage_gaps(pages, commercial)
    report["cdd_coverage_gaps"] = gaps
    if gaps:
        opps = list(report.get("opportunities") or [])
        for g in gaps:
            opps.append({"issue": g["issue"], "ref": g["ref"]})
        report["opportunities"] = opps
    biz = weighted_business_score(pages)
    if biz is not None:
        report["business_weighted_score"] = biz
        # Prefer business-weighted as overall when CDD context exists
        if any(cdd_terms_from_commercial(commercial).values()):
            report["overall_score"] = biz
            if biz >= 90:
                report["score_band"] = "Excellent"
            elif biz >= 70:
                report["score_band"] = "Good"
            elif biz >= 50:
                report["score_band"] = "Needs significant work"
            else:
                report["score_band"] = "Critical SEO issues"
    report["audit_lens"] = "cdd_hierarchy"
    report["audit_focus_note"] = (
        "Prioritized Home → service hubs → service pages → sub-service pages, "
        "with primary attention on URLs matching CDD products, promotion list, "
        "business keywords, and geo."
    )
    return report
