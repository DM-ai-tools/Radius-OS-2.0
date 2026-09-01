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
    "Indexability": 1.3,
    "Status Codes": 1.1,
    "Internal Linking": 1.0,
    "Metadata": 0.9,
    "Canonicalization": 1.15,
    "Content": 0.85,
    "Redirects": 1.1,
    "Performance": 1.0,
}

ALL_CATEGORIES = (
    "Crawlability",
    "Indexability",
    "Status Codes",
    "Internal Linking",
    "Metadata",
    "Canonicalization",
    "Content",
)
