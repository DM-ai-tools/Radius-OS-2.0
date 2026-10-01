"""Diff two CDD field snapshots.

Comparison is normalized, not literal. A client re-exporting the same
spreadsheet reorders comma-separated lists and changes casing constantly — a
literal diff would report "everything changed" and mark every phase stale for
no reason, which would make the staleness signal worthless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Fields whose text is really a list of terms. Order and case carry no meaning.
LIST_LIKE_FIELDS: frozenset[str] = frozenset(
    {
        "business_keywords",
        "products",
        "products_for_promotion",
        "objectives",
        "competitors",
        "known_competitors",
        "lead_modes",
        "industry_targeting",
    }
)

_SPLIT_RE = re.compile(r"[,;\n|]+")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_field_value(key: str, value: Any) -> Any:
    """Comparable form of a field value.

    List-like fields become a set of terms; everything else a normalized string.
    """
    if value in (None, "", [], {}):
        return ""
    if key in LIST_LIKE_FIELDS:
        if isinstance(value, (list, tuple, set)):
            parts = [_norm_text(v) for v in value]
        else:
            parts = [_norm_text(p) for p in _SPLIT_RE.split(str(value))]
        return {p for p in parts if p}
    if isinstance(value, (list, tuple, set)):
        return {_norm_text(v) for v in value if _norm_text(v)}
    if isinstance(value, dict):
        return {k: _norm_text(v) for k, v in value.items()}
    return _norm_text(value)


def _is_empty(normalized: Any) -> bool:
    return normalized in ("", set(), {}, None)


@dataclass
class CddDiff:
    added: dict[str, Any] = field(default_factory=dict)
    removed: dict[str, Any] = field(default_factory=dict)
    # key -> (old, new), raw values for display
    modified: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    def changed_keys(self) -> list[str]:
        return sorted({*self.added, *self.removed, *self.modified})

    def summary(self, *, limit: int = 12) -> str:
        lines: list[str] = []
        for key, value in list(self.added.items())[:limit]:
            lines.append(f"+ {key}: {str(value)[:120]}")
        for key, value in list(self.removed.items())[:limit]:
            lines.append(f"- {key}: (was) {str(value)[:120]}")
        for key, (old, new) in list(self.modified.items())[:limit]:
            lines.append(f"~ {key}: {str(old)[:60]} -> {str(new)[:60]}")
        return "\n".join(lines) or "No changes."

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "modified": {k: {"old": o, "new": n} for k, (o, n) in self.modified.items()},
            "unchanged": self.unchanged,
            "changed_keys": self.changed_keys(),
            "has_changes": self.has_changes,
        }


def diff_cdd(old_fields: dict[str, Any] | None, new_fields: dict[str, Any] | None) -> CddDiff:
    """Compare two CDD field snapshots."""
    old = dict(old_fields or {})
    new = dict(new_fields or {})
    diff = CddDiff()

    for key in sorted({*old, *new}):
        old_norm = normalize_field_value(key, old.get(key))
        new_norm = normalize_field_value(key, new.get(key))
        old_empty, new_empty = _is_empty(old_norm), _is_empty(new_norm)

        if old_empty and new_empty:
            continue
        if old_empty and not new_empty:
            diff.added[key] = new.get(key)
            continue
        if new_empty and not old_empty:
            # A field cleared to blank is a removal, not a modification — the
            # requirement it expressed is gone.
            diff.removed[key] = old.get(key)
            continue
        if old_norm != new_norm:
            diff.modified[key] = (old.get(key), new.get(key))
        else:
            diff.unchanged.append(key)

    return diff
