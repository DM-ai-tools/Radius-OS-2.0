"""Normalized Technical SEO data models (provider-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TechnicalSEOPage:
    url: str
    status_code: int | None = None
    indexable: bool | None = None
    canonical_url: str | None = None
    canonical_status_code: int | None = None
    title: str | None = None
    meta_description: str | None = None
    h1: str | None = None
    depth: int | None = None
    internal_link_count: int | None = None
    word_count: int | None = None
    is_redirect_loop: bool | None = None
    final_redirect_url: str | None = None
    duplicate_title_count: int | None = None
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TechnicalSEOIssue:
    rule_id: str
    category: str
    title: str
    description: str
    severity: str
    priority: int
    affected_urls: list[str] = field(default_factory=list)
    affected_url_count: int = 0
    recommended_action: str = ""
    source: str = "unknown"
    status: str = "open"
    ahrefs_issue_id: str | None = None
    ahrefs_importance: str | None = None
    confidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "priority": self.priority,
            "affected_urls": self.affected_urls[:50],
            "affected_url_count": self.affected_url_count,
            "recommended_action": self.recommended_action,
            "source": self.source,
            "status": self.status,
            "ahrefs_issue_id": self.ahrefs_issue_id,
            "ahrefs_importance": self.ahrefs_importance,
            "confidence": self.confidence,
        }


@dataclass
class CategoryScore:
    category: str
    score: int | None
    status: str  # available | NOT_AVAILABLE
    reason: str | None = None
    issue_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "score": self.score,
            "status": self.status,
            "reason": self.reason,
            "issue_count": self.issue_count,
        }


SEVERITY_WEIGHTS = {
    "Critical": 100,
    "High": 75,
    "Medium": 50,
    "Low": 25,
    "Info": 10,
}

CATEGORY_IMPACT = {
    "Crawlability": 1.2,
    "Indexation": 1.3,
    "URL structure": 1.1,
    "Canonicalisation": 1.15,
    "Sitemap": 1.1,
    "Internal linking": 1.0,
    "On-page technical SEO": 0.9,
    "JavaScript/rendering": 1.05,
    "Performance": 1.0,
    "Mobile": 0.95,
    "Structured data": 0.9,
    "Security": 1.1,
    # Legacy aliases (older packs / tests)
    "Indexability": 1.3,
    "Status Codes": 1.1,
    "Metadata": 0.9,
    "Content": 0.85,
    "Redirects": 1.1,
    "Canonicalization": 1.15,
    "Internal Linking": 1.0,
}

# Phase 7 targeting contract — every audit must report these fields.
AUDIT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Crawlability", ("robots.txt", "sitemap", "crawlable URLs", "blocked resources", "crawl depth", "orphan pages")),
    ("Indexation", ("GSC indexed/excluded", "noindex", "robots blocking", "canonical", "discovered but not indexed", "crawled but not indexed", "soft 404")),
    ("URL structure", ("HTTP/HTTPS", "www/non-www", "trailing slash", "parameters", "duplicate URLs", "case sensitivity", "redirect chains")),
    ("Canonicalisation", ("canonical existence", "canonical validity", "canonical conflicts", "Google-selected canonical", "duplicate content")),
    ("Sitemap", ("indexable URLs", "200 status", "canonical URLs", "no noindex URLs", "no redirects", "no 404s", "lastmod accuracy", "important pages included")),
    ("Internal linking", ("orphan pages", "crawl depth", "important pages", "contextual links", "anchor text", "broken links", "redirected internal links")),
    ("On-page technical SEO", ("title", "meta description", "H1", "H2/H3", "image alt", "duplicate metadata", "thin content")),
    ("JavaScript/rendering", ("raw vs rendered HTML", "JS links", "JS content", "JS metadata", "JS schema")),
    ("Performance", ("CWV", "LCP", "INP", "CLS", "TTFB", "FCP", "TBT", "HTML size", "JS/CSS", "image size", "third-party scripts")),
    ("Mobile", ("responsive", "viewport", "mobile CWV", "touch targets", "mobile navigation", "content parity")),
    ("Structured data", ("schema types", "validation", "duplication", "entity relationships", "required properties", "rich-result eligibility")),
    ("Security", ("HTTPS", "mixed content", "HSTS", "security headers", "exposed WordPress files", "XML-RPC", "WordPress version exposure")),
)

ALL_CATEGORIES = tuple(name for name, _fields in AUDIT_GROUPS)

LEGACY_CATEGORY_MAP = {
    "Indexability": "Indexation",
    "Status Codes": "URL structure",
    "Redirects": "URL structure",
    "Canonicalization": "Canonicalisation",
    "Internal Linking": "Internal linking",
    "Metadata": "On-page technical SEO",
    "Content": "On-page technical SEO",
}


def normalize_category(name: str | None) -> str:
    raw = str(name or "").strip() or "Crawlability"
    return LEGACY_CATEGORY_MAP.get(raw, raw)
