"""Transparent priority scoring for Technical SEO issues."""

from __future__ import annotations

import math

from app.services.technical_seo_schemas import (
    CATEGORY_IMPACT,
    TechnicalSEOIssue,
)


# Each severity owns a band of the 1-100 scale; impact and URL count only order
# issues inside their band. The previous formula multiplied the three factors
# together and clamped the result, which overshot the cap for 56% of
# severity×category×url combinations — and once two issues both clamp to 100,
# severity no longer discriminates and the sort falls through to raw URL count.
# That ranked a Medium "duplicate title" on 10 URLs above a High "4xx page" on 5.
# Real audits report hundreds of URLs per issue, so saturation was the norm.
_SEVERITY_BAND_BASE = {
    "Critical": 80,
    "High": 62,
    "Medium": 44,
    "Low": 26,
    "Info": 8,
}
# Headroom inside a band. Kept below the 18-point gap between bases so the
# strongest member of one band can never overtake the weakest of the next.
_BAND_SPREAD = 17
# log10(1 + urls) saturates here; 1000+ affected URLs all earn full URL credit.
_URL_SCALE_CEILING = 3.0


def compute_priority(
    *,
    severity: str,
    category: str,
    affected_url_count: int,
) -> int:
    """Rank an issue on 1-100: severity picks the band, impact/URLs order within it."""
    base = _SEVERITY_BAND_BASE.get(severity)
    if base is None:
        # Unknown severity sits at the Medium band but never above a known one.
        base = _SEVERITY_BAND_BASE["Medium"]
    url_scale = min(
        1.0, math.log10(1 + max(0, affected_url_count)) / _URL_SCALE_CEILING
    )
    impact = CATEGORY_IMPACT.get(category, 1.0)
    lo, hi = min(CATEGORY_IMPACT.values()), max(CATEGORY_IMPACT.values())
    impact_scale = (impact - lo) / (hi - lo) if hi > lo else 0.0
    impact_scale = min(1.0, max(0.0, impact_scale))
    bonus = _BAND_SPREAD * (0.7 * url_scale + 0.3 * impact_scale)
    return max(1, min(100, round(base + bonus)))


def apply_priorities(issues: list[TechnicalSEOIssue]) -> list[TechnicalSEOIssue]:
    for issue in issues:
        issue.priority = compute_priority(
            severity=issue.severity,
            category=issue.category,
            affected_url_count=issue.affected_url_count,
        )
    issues.sort(key=lambda i: (-i.priority, -i.affected_url_count, i.rule_id))
    return issues
