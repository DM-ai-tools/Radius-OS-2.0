"""Transparent priority scoring for Technical SEO issues."""

from __future__ import annotations

import math

from app.services.technical_seo_schemas import CATEGORY_IMPACT, SEVERITY_WEIGHTS, TechnicalSEOIssue


def compute_priority(
    *,
    severity: str,
    category: str,
    affected_url_count: int,
) -> int:
    """priority = severity_weight × impact_factor × log10(1 + affected_urls), capped 0-100."""
    weight = SEVERITY_WEIGHTS.get(severity, 50)
    impact = CATEGORY_IMPACT.get(category, 1.0)
    url_factor = math.log10(1 + max(0, affected_url_count))
    raw = weight * impact * (1 + url_factor)
    return max(1, min(100, round(raw)))


def apply_priorities(issues: list[TechnicalSEOIssue]) -> list[TechnicalSEOIssue]:
    for issue in issues:
        issue.priority = compute_priority(
            severity=issue.severity,
            category=issue.category,
            affected_url_count=issue.affected_url_count,
        )
    issues.sort(key=lambda i: (-i.priority, -i.affected_url_count, i.rule_id))
    return issues
