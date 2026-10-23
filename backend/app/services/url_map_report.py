"""Plain-language cells for the URL map tables (PDF and DOCX share these).

The URL map is shown as three tables over the same rows, tied together by the
row number: taxonomy and status, keywords and opportunity, and decision and
evidence. This module only formats what url_mapping decided.
"""

from __future__ import annotations

from typing import Any

from app.services.url_mapping import COMPONENT_LABELS

_OUTCOME = {
    "OPTIMIZE_EXISTING": "Optimize existing page",
    "REVIEW_MERGE_REDIRECT": "Review / merge / redirect",
    "CREATE": "Create new page",
    "FOLD_INTO_NEW_PARENT": "Fold into new parent page",
}
_URL_STATUS = {
    "existing_url": "existing page owns this",
    "existing_url_needs_optimization": "existing page needs optimizing",
    "existing_url_needs_consolidation": "competing pages to consolidate",
    "new_proposed_url": "new URL proposed",
    "no_dedicated_url": "no page of its own",
    "fold_into_new_parent": "parent page not built yet",
}


def outcome_text(entry: dict[str, Any]) -> str:
    action = _OUTCOME.get(str(entry.get("action") or ""), str(entry.get("action") or "—"))
    status = _URL_STATUS.get(str(entry.get("url_status") or ""))
    text = f"{action} ({status})" if status else action
    if entry.get("consistency_errors"):
        text += ". Rejected: " + " ".join(entry["consistency_errors"])
    return text


def score_text(entry: dict[str, Any]) -> str:
    basis = entry.get("score_basis") or {}
    available = [COMPONENT_LABELS.get(k, k) for k in basis.get("available") or []]
    missing = [COMPONENT_LABELS.get(k, k) for k in basis.get("unavailable") or []]
    score = entry.get("url_score")
    head = f"{score:g}/100 {entry.get('score_band') or ''}".strip() if isinstance(score, (int, float)) else "—"
    if entry.get("band_source") == "prior_classification":
        head += " (from prior classification)"
    elif entry.get("band_source") == "no_match":
        return f"{head}. No live page matched."
    parts = [head]
    if available:
        parts.append("based on " + ", ".join(available))
    if missing:
        parts.append("not available: " + ", ".join(missing))
    return "; ".join(parts) + "."


def confidence_text(entry: dict[str, Any]) -> str:
    match = entry.get("match_confidence")
    coverage = entry.get("signal_coverage")
    bits = []
    if isinstance(match, (int, float)):
        bits.append(f"match {round(match * 100)}%")
    if isinstance(coverage, (int, float)):
        bits.append(f"on {round(coverage * 100)}% of signals")
    topic = entry.get("topic_confidence")
    if isinstance(topic, (int, float)):
        bits.append(f"topic {round(topic * 100)}%")
    return ", ".join(bits) or "—"


def checks_text(entry: dict[str, Any]) -> str:
    bits: list[str] = []
    hygiene = entry.get("url_hygiene") or {}
    if hygiene.get("verdict") == "review":
        bits.append("URL: " + " ".join(hygiene.get("problems") or []))
    elif hygiene.get("verdict") == "ok":
        bits.append("URL hygiene OK.")
    bits.extend(entry.get("collisions") or [])
    bits.extend("Canonical: " + note for note in entry.get("canonical_conflicts") or [])
    bits.extend(entry.get("link_warnings") or [])
    return " ".join(bits) or "No conflicts."


def links_text(entry: dict[str, Any]) -> str:
    bits: list[str] = []
    if entry.get("redirect_sources"):
        bits.append("Redirect into target: " + ", ".join(entry["redirect_sources"][:4]))
    if entry.get("internal_link_parents"):
        bits.append("Linked from: " + ", ".join(entry["internal_link_parents"][:3]))
    quality = entry.get("existing_page_quality") or {}
    if quality:
        q = [
            f"{int(quality['word_count']):,} words" if isinstance(quality.get("word_count"), (int, float)) else None,
            str(quality["content_quality"]) if quality.get("content_quality") else None,
            f"updated {quality['last_modified']}" if quality.get("last_modified") else None,
        ]
        q = [x for x in q if x]
        if q:
            bits.append("Existing page: " + ", ".join(q))
    if entry.get("locale"):
        bits.append(f"Locale {entry['locale']}, hreflang group {entry.get('hreflang_group')}")
    elif entry.get("market"):
        bits.append(f"Market: {entry['market']}")
    return "; ".join(bits) or "—"


def score_cell(entry: dict[str, Any]) -> tuple[str, ...]:
    """The score text split into lines: the score, then its basis."""
    return tuple(part.strip().rstrip(".") + "." for part in score_text(entry).split("; ") if part.strip())


def trail_cell(entry: dict[str, Any], limit: int = 5) -> tuple[str, ...]:
    """The decision trail one step per line. The priority step is left out
    because the taxonomy table already shows the priority and its reason."""
    steps = [str(s) for s in entry.get("decision_trail") or [] if s and not str(s).startswith("Priority ")]
    if not steps:
        return ("—",)
    shown = [f"{i}. {step}" for i, step in enumerate(steps[:limit], 1)]
    if len(steps) > limit:
        shown.append(f"+{len(steps) - limit} more steps")
    return tuple(shown)


def trail_text(entry: dict[str, Any], limit: int = 4) -> str:
    steps = [str(s) for s in entry.get("decision_trail") or [] if s]
    if not steps:
        return "—"
    shown = steps[:limit]
    text = " ".join(f"{i}. {step}" for i, step in enumerate(shown, 1))
    if len(steps) > limit:
        text += f" (+{len(steps) - limit} more steps)"
    return text


def has_decision_evidence(entry: dict[str, Any]) -> bool:
    """Maps saved before 2026-10-22 carry none of the decision fields."""
    return any(entry.get(k) for k in ("decision_trail", "score_basis", "band_source"))


def numbered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with a row number; older saved maps are numbered by position."""
    return [
        row if row.get("row_no") else {**row, "row_no": index}
        for index, row in enumerate(rows, start=1)
    ]


def decision_row(entry: dict[str, Any]) -> list[Any]:
    if not has_decision_evidence(entry):
        # Nothing was recorded; an all-empty row is dropped by the table builders.
        return [None] * len(DECISION_HEADERS)
    return [
        entry.get("row_no"),
        outcome_text(entry),
        score_cell(entry),
        confidence_text(entry),
        f"{entry.get('effort') or '—'}" + (f": {entry['effort_basis']}" if entry.get("effort_basis") else ""),
        checks_text(entry),
        links_text(entry),
        trail_cell(entry),
    ]


DECISION_HEADERS = ["#", "Outcome", "Score and basis", "Confidence", "Effort", "Checks", "Links and redirects", "Decision trail"]
DECISION_WEIGHTS = [0.3, 1.0, 1.35, 1.1, 0.85, 1.15, 1.1, 2.0]
