"""Deliverable size. A later run may fix or add work. It may not come back smaller."""

from __future__ import annotations

from typing import Any

# Lists that are the work product. Bookkeeping keys (validation history) are skipped
# because their names start with "_".
_BREADTH_KEYS = frozenset(
    {
        "clusters",
        "keywords",
        "keyword_dataset",
        "seed_keywords",
        "seeds",
        "services",
        "seed_clusters",
        "priority_queue",
        "target_url_tree",
        "pages",
        "inventory",
        "competitors",
        "topics",
        "topic_plan",
        "briefs",
        "findings",
        "sample_urls",
        "discovered_urls",
        "roadmap",
        "scorecards",
        "recommendations",
    }
)


def deliverable_breadth(value: Any, *, depth: int = 0) -> int:
    """How much work is in this deliverable. Zero means there is nothing to protect."""
    if depth > 8:
        return 0
    if isinstance(value, dict):
        total = 0
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            if isinstance(item, list) and key in _BREADTH_KEYS:
                total += len(item)
            total += deliverable_breadth(item, depth=depth + 1)
        return total
    if isinstance(value, list):
        return sum(deliverable_breadth(item, depth=depth + 1) for item in value[:300])
    return 0


def is_narrower(previous: Any, current: Any) -> bool:
    """True when a saved deliverable exists and the new one contains less of it."""
    previous_size = deliverable_breadth(previous)
    if previous_size <= 0:
        return False
    return deliverable_breadth(current) < previous_size
