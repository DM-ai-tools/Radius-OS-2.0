"""Map Ahrefs Site Audit issues + page data to normalized TechnicalSEOIssue rows."""

from __future__ import annotations

import math
import re
from typing import Any

from app.services.technical_seo_prioritization import apply_priorities
from app.services.technical_seo_schemas import (
    ALL_CATEGORIES,
    CategoryScore,
    TechnicalSEOIssue,
    TechnicalSEOPage,
)

# Ahrefs issue category → internal category
_AHREFS_CATEGORY_MAP: dict[str, str] = {
    "Internal pages": "Status Codes",
    "Indexability": "Indexability",
    "Links": "Internal Linking",
    "Redirects": "Crawlability",
    "Content": "Content",
    "Duplicates": "Metadata",
    "Sitemaps": "Crawlability",
    "Localization": "Indexability",
    "Social tags": "Metadata",
    "Usability and performance": "Performance",
    "Images": "Metadata",
    "JavaScript": "Crawlability",
    "CSS": "Crawlability",
    "External pages": "Crawlability",
    "AI Discoverability": "Indexability",
    "Other": "Crawlability",
}

# Ahrefs importance → base severity
_IMPORTANCE_SEVERITY = {
    "Error": "High",
    "Warning": "Medium",
    "Notice": "Low",
}

# Issue name patterns that elevate severity
_CRITICAL_PATTERNS = re.compile(
    r"5\d{2}|server error|noindex.*important|blocked.*index|redirect loop",
    re.I,
)
_HIGH_PATTERNS = re.compile(
    r"4\d{2}|broken|canonical conflict|noindex|not indexable|orphan|5xx|4xx",
    re.I,
)

_ISSUE_ACTIONS: dict[str, str] = {
    "broken": "Fix or remove links returning 4xx/5xx responses.",
    "redirect": "Resolve redirect chains; use single 301 hops to final URLs.",
    "canonical": "Align canonical tags so each indexable URL self-canonicalises or points to the correct primary URL.",
    "noindex": "Remove noindex from pages that should rank, or block via robots.txt if intentionally excluded.",
    "title": "Give each page a unique, descriptive title tag.",
    "meta description": "Write unique meta descriptions for key landing pages.",
    "h1": "Ensure each page has exactly one descriptive H1.",
    "duplicate": "Consolidate duplicate URLs via canonical or 301 redirects.",
    "orphan": "Add internal links from hub/category pages to orphan URLs.",
    "sitemap": "Fix sitemap entries — exclude redirects, 4xx, and noindex URLs.",
    "thin": "Expand thin pages with useful content or consolidate/noindex low-value URLs.",
}


def _map_category(ahrefs_category: str) -> str:
    return _AHREFS_CATEGORY_MAP.get(ahrefs_category, "Crawlability")


def _severity_for_issue(name: str, importance: str) -> str:
    if _CRITICAL_PATTERNS.search(name):
        return "Critical"
    if _HIGH_PATTERNS.search(name):
        return "High"
    return _IMPORTANCE_SEVERITY.get(importance, "Medium")


def _recommended_action(name: str) -> str:
    lowered = name.lower()
    for key, action in _ISSUE_ACTIONS.items():
        if key in lowered:
            return action
    return f"Review and remediate: {name}."


def issues_from_ahrefs(
    ahrefs_issues: list[dict[str, Any]],
    *,
    sample_urls_by_issue: dict[str, list[str]] | None = None,
) -> list[TechnicalSEOIssue]:
    """Convert Ahrefs /site-audit/issues rows to normalized issues."""
    samples = sample_urls_by_issue or {}
    out: list[TechnicalSEOIssue] = []
    for row in ahrefs_issues:
        if not isinstance(row, dict):
            continue
        issue_id = str(row.get("issue_id") or "").strip()
        name = str(row.get("name") or "Unknown issue").strip()
        if not issue_id:
            continue
        category = _map_category(str(row.get("category") or "Other"))
        importance = str(row.get("importance") or "Warning")
        count = int(row.get("crawled") or 0)
        severity = _severity_for_issue(name, importance)
        urls = samples.get(issue_id, [])
        out.append(
            TechnicalSEOIssue(
                rule_id=f"AHREFS_{issue_id}",
                category=category,
                title=name,
                description=(
                    f"Ahrefs Site Audit detected {count} affected URL(s) "
                    f"({importance.lower()} — {row.get('category', 'Other')})."
                ),
                severity=severity,
                priority=0,
                affected_urls=urls,
                affected_url_count=count,
                recommended_action=_recommended_action(name),
                source="ahrefs",
                ahrefs_issue_id=issue_id,
                ahrefs_importance=importance,
            )
        )
    return apply_priorities(out)


def issues_from_pages(pages: list[TechnicalSEOPage]) -> list[TechnicalSEOIssue]:
    """Supplemental rules on normalized page inventory (crawl or Ahrefs pages)."""
    issues: list[TechnicalSEOIssue] = []

    status_4xx = [p.url for p in pages if p.status_code and 400 <= p.status_code < 500]
    status_5xx = [p.url for p in pages if p.status_code and p.status_code >= 500]
    missing_title = [p.url for p in pages if p.status_code == 200 and not p.title]
    missing_h1 = [p.url for p in pages if p.status_code == 200 and not p.h1]
    dup_titles = [p.url for p in pages if (p.duplicate_title_count or 0) > 1]
    deep_pages = [p.url for p in pages if (p.depth or 0) >= 4]
    orphans = [
        p.url
        for p in pages
        if (p.internal_link_count == 0 and p.indexable is True and p.status_code == 200)
    ]
    redirect_loops = [p.url for p in pages if p.is_redirect_loop is True]
    thin = [p.url for p in pages if p.word_count is not None and p.word_count < 200]

    def _add(
        rule_id: str,
        category: str,
        title: str,
        severity: str,
        urls: list[str],
        action: str,
    ) -> None:
        if not urls:
            return
        issues.append(
            TechnicalSEOIssue(
                rule_id=rule_id,
                category=category,
                title=title,
                description=f"{len(urls)} page(s) matched rule {rule_id}.",
                severity=severity,
                priority=0,
                affected_urls=urls[:50],
                affected_url_count=len(urls),
                recommended_action=action,
                source="rule_engine",
            )
        )

    _add(
        "HTTP_5XX",
        "Status Codes",
        "Pages returning 5xx errors",
        "Critical",
        status_5xx,
        "Fix server errors on affected URLs immediately.",
    )
    _add(
        "HTTP_4XX",
        "Status Codes",
        "Pages returning 4xx errors",
        "High",
        status_4xx,
        "Fix or redirect 4xx URLs; remove broken internal links.",
    )
    _add(
        "MISSING_TITLE",
        "Metadata",
        "Missing title tag",
        "Medium",
        missing_title,
        "Add a unique title tag to each indexable page.",
    )
    _add(
        "MISSING_H1",
        "Metadata",
        "Missing H1",
        "Medium",
        missing_h1,
        "Add exactly one descriptive H1 per page.",
    )
    _add(
        "DUPLICATE_TITLE",
        "Metadata",
        "Duplicate title tags",
        "Medium",
        dup_titles,
        "Give each page a unique title.",
    )
    _add(
        "DEEP_CLICK_DEPTH",
        "Internal Linking",
        "Important pages at click depth 4+",
        "Medium",
        deep_pages,
        "Flatten hierarchy with hub links from higher-level pages.",
    )
    _add(
        "ORPHAN_PAGE",
        "Internal Linking",
        "Orphan indexable pages",
        "High",
        orphans,
        "Add internal links so crawlers can discover these pages.",
    )
    _add(
        "REDIRECT_LOOP",
        "Crawlability",
        "Redirect loops",
        "Critical",
        redirect_loops,
        "Break redirect loops; ensure chains terminate at a 200 response.",
    )
    _add(
        "THIN_CONTENT",
        "Content",
        "Thin content pages",
        "Low",
        thin,
        "Expand content or consolidate/noindex low-value pages.",
    )
    return apply_priorities(issues)


def merge_issues(
    primary: list[TechnicalSEOIssue],
    supplemental: list[TechnicalSEOIssue],
) -> list[TechnicalSEOIssue]:
    """Merge Ahrefs issues with rule-engine issues; prefer Ahrefs when rule_id overlaps."""
    ahrefs_titles = {i.title.lower() for i in primary}
    merged = list(primary)
    for issue in supplemental:
        if issue.title.lower() in ahrefs_titles:
            continue
        merged.append(issue)
    return apply_priorities(merged)


def compute_category_scores(
    issues: list[TechnicalSEOIssue],
    *,
    pages_available: bool,
    ahrefs_health_score: int | None,
) -> dict[str, dict[str, Any]]:
    """Category scores 0-100; unavailable categories marked NOT_AVAILABLE."""
    scores: dict[str, dict[str, Any]] = {}
    by_cat: dict[str, list[TechnicalSEOIssue]] = {c: [] for c in ALL_CATEGORIES}
    for issue in issues:
        by_cat.setdefault(issue.category, []).append(issue)

    for category in ALL_CATEGORIES:
        cat_issues = by_cat.get(category, [])
        if not pages_available and category in (
            "Metadata",
            "Internal Linking",
            "Content",
            "Canonicalization",
        ):
            scores[category] = CategoryScore(
                category=category,
                score=None,
                status="NOT_AVAILABLE",
                reason="Page-level data was not fetched from the provider.",
            ).to_dict()
            continue
        if not cat_issues:
            scores[category] = CategoryScore(
                category=category,
                score=100,
                status="available",
                issue_count=0,
            ).to_dict()
            continue
        penalty = 0.0
        for issue in cat_issues:
            sev_penalty = {
                "Critical": 25,
                "High": 15,
                "Medium": 8,
                "Low": 3,
                "Info": 1,
            }.get(issue.severity, 8)
            penalty += sev_penalty * min(1.0, math.log10(1 + issue.affected_url_count))
        score = max(0, min(100, round(100 - penalty)))
        scores[category] = CategoryScore(
            category=category,
            score=score,
            status="available",
            issue_count=len(cat_issues),
        ).to_dict()

    return scores


def overall_score(
    category_scores: dict[str, dict[str, Any]],
    *,
    ahrefs_health_score: int | None,
) -> int | None:
    """Weighted mean of available categories; prefer Ahrefs health when present."""
    if ahrefs_health_score is not None:
        return int(ahrefs_health_score)
    available = [
        s["score"]
        for s in category_scores.values()
        if s.get("status") == "available" and isinstance(s.get("score"), (int, float))
    ]
    if not available:
        return None
    return round(sum(available) / len(available))


def severity_summary(issues: list[TechnicalSEOIssue]) -> dict[str, int]:
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return counts
