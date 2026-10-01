"""Operator choices on the keyword report: drop focus, add competitor terms."""

from __future__ import annotations

from typing import Any

_ROW_LIST_KEYS = {
    "keyword_table",
    "keyword_dataset",
    "best_opportunities",
    "strong_evergreen",
    "head_terms",
    "trend_plays",
    "avoid",
    "keywords",
    "exact",
    "phrase",
    "related",
    "broad",
    "search_demand_analysis",
}


def norm_keyword(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _dropped_set(edits: dict[str, Any]) -> set[str]:
    raw = edits.get("deselected_keywords") or []
    if not isinstance(raw, list):
        return set()
    return {norm_keyword(item) for item in raw if norm_keyword(item)}


def _manual_rows(edits: dict[str, Any]) -> list[dict[str, Any]]:
    raw = edits.get("manual_competitor_keywords") or []
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            keyword, competitor = item, ""
        elif isinstance(item, dict):
            keyword = str(item.get("keyword") or "").strip()
            competitor = str(item.get("competitor") or item.get("domain") or "").strip()
        else:
            continue
        key = norm_keyword(keyword)
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "keyword": keyword,
                "source": "manual_competitor",
                "competitor": competitor or None,
                "competitor_domains": [competitor] if competitor else [],
                "gap_flag": True,
                "match_class": "related",
            }
        )
    return rows


def _keep_row(row: Any, dropped: set[str]) -> bool:
    if not isinstance(row, dict):
        return True
    key = norm_keyword(row.get("keyword") or row.get("primary_keyword") or "")
    return not key or key not in dropped


def _filter_tree(node: Any, dropped: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key in _ROW_LIST_KEYS and isinstance(value, list):
                node[key] = [row for row in value if _keep_row(row, dropped)]
            elif key == "supporting_keywords" and isinstance(value, list):
                node[key] = [
                    item
                    for item in value
                    if norm_keyword(item if isinstance(item, str) else (item or {}).get("keyword"))
                    not in dropped
                ]
            else:
                _filter_tree(value, dropped)
        primary = norm_keyword(node.get("primary_keyword") or "")
        if primary and primary in dropped:
            node["primary_keyword"] = None
            node["focus_dropped"] = True
    elif isinstance(node, list):
        for item in node:
            _filter_tree(item, dropped)


def apply_keyword_focus(summary: dict[str, Any], edits: dict[str, Any] | None) -> dict[str, Any]:
    """Drop deselected keywords and append manually added competitor keywords.

    ``edits`` comes from the keyword card on approve. Other edit keys are left
    untouched. Manual rows are inserted at the front of the keyword table so
    the shared-memory cap still keeps them.
    """
    current = dict(summary or {})
    if not edits:
        return current
    dropped = _dropped_set(edits)
    manual = [row for row in _manual_rows(edits) if norm_keyword(row["keyword"]) not in dropped]
    if dropped:
        _filter_tree(current, dropped)
    if manual:
        existing = {
            norm_keyword(row.get("keyword"))
            for row in (current.get("keyword_table") or [])
            if isinstance(row, dict)
        }
        fresh = [row for row in manual if norm_keyword(row["keyword"]) not in existing]
        current["keyword_table"] = [*fresh, *(current.get("keyword_table") or [])]
        dataset = list(current.get("keyword_dataset") or [])
        dataset_keys = {
            norm_keyword(row.get("keyword")) for row in dataset if isinstance(row, dict)
        }
        current["keyword_dataset"] = [
            *[row for row in fresh if norm_keyword(row["keyword"]) not in dataset_keys],
            *dataset,
        ]
    current["deselected_keywords"] = sorted(dropped)
    current["deselected_items"] = sorted(dropped)
    current["manual_competitor_keywords"] = [
        {"keyword": row["keyword"], "competitor": row.get("competitor") or ""}
        for row in manual
    ]
    return current


_IDENTITY_FIELDS = (
    "keyword",
    "primary_keyword",
    "url",
    "url_n",
    "proposed_url",
    "current_url",
    "path",
    "title",
    "name",
    "issue",
    "topic",
    "pillar",
    "page",
    "item",
    "broken_url",
    "source_page",
    "check",
)
_KEEP_LISTS = {"deselected_items", "deselected_keywords", "manual_competitor_keywords"}


def selection_keys(edits: dict[str, Any] | None) -> set[str]:
    """Normalized ids the operator unchecked on any stage card."""
    if not edits:
        return set()
    keys: set[str] = set()
    for field in ("deselected_items", "deselected_keywords"):
        raw = edits.get(field) or []
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                key = norm_keyword(item)
            elif isinstance(item, dict):
                key = ""
                for name in _IDENTITY_FIELDS:
                    key = norm_keyword(item.get(name))
                    if key:
                        break
            else:
                key = ""
            if key:
                keys.add(key)
    return keys


def _row_keys(row: Any) -> set[str]:
    if isinstance(row, str):
        key = norm_keyword(row)
        return {key} if key else set()
    if not isinstance(row, dict):
        return set()
    return {key for name in _IDENTITY_FIELDS if (key := norm_keyword(row.get(name)))}


def _filter_any(node: Any, dropped: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key in _KEEP_LISTS:
                continue
            if isinstance(value, list):
                kept = [item for item in value if not (_row_keys(item) & dropped)]
                node[key] = kept
                for item in kept:
                    if isinstance(item, (dict, list)):
                        _filter_any(item, dropped)
            elif isinstance(value, dict):
                _filter_any(value, dropped)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _filter_any(item, dropped)


def drop_focused_items(summary: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    """Remove unchecked rows and remember the choice. Does not touch manual adds."""
    current = dict(summary or {})
    if not keys:
        return current
    prior = selection_keys(
        {
            "deselected_items": current.get("deselected_items") or [],
            "deselected_keywords": current.get("deselected_keywords") or [],
        }
    )
    combined = prior | keys
    _filter_any(current, combined)
    current["deselected_items"] = sorted(combined)
    if current.get("deselected_keywords") is not None or "keyword_table" in summary:
        current["deselected_keywords"] = sorted(combined)
    return current


def focus_and_slim(summary: dict[str, Any] | None, edits: dict[str, Any] | None, slim) -> dict[str, Any]:
    """Apply an approve payload, drop unchecked rows, then slim for shared memory."""
    current = dict(summary or {})
    if edits:
        extra = {
            key: value
            for key, value in edits.items()
            if key not in _KEEP_LISTS
        }
        current.update(extra)
        keys = selection_keys(edits)
        if keys:
            current = drop_focused_items(current, keys)
    packed = dict(slim(current) or {})
    items = list(current.get("deselected_items") or [])[:200]
    if items:
        packed["deselected_items"] = items
    return packed
