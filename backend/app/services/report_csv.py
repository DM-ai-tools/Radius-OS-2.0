"""Keyword research as a CSV: one row per keyword, ready for a spreadsheet.

Rows come from the Phase 5 report the download already serves: every clustered
keyword (with its cluster, role, hub and labels), then the orphans (with why
they have no cluster), then any remaining researched keyword that is not in the
cluster map. Nothing is computed here; a value the run did not measure reads
"N/A", and estimated volumes say so in their own column.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

CSV_CARD_TYPES = frozenset({"search_demand_report"})

COLUMNS = [
    "Keyword",
    "Status",
    "Cluster",
    "Role",
    "Content hub",
    "Page pattern",
    "Cluster primary keyword",
    "Recommended URL",
    "Search volume",
    "Volume status",
    "Keyword difficulty",
    "CPC",
    "Intent",
    "Intent source",
    "Intent confidence",
    "Funnel stage",
    "Funnel source",
    "Opportunity score",
    "Service",
    "Seed",
    "Why it has no cluster",
    "Also searched as",
]

_ORPHAN_REASON = {
    "single_low_value_keyword": "Did not fit a topic (single low-value keyword)",
    "no_topic_fit": "Did not fit a topic",
    "unassigned_by_model": "Not assigned to any cluster by the model",
    "dropped_for_relevance": "Dropped by the model as not relevant",
}
# A cell starting with one of these is run as a formula by spreadsheet apps.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _safe(value: Any) -> Any:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _volume_status(row: dict[str, Any]) -> str:
    if row.get("volume") is None:
        return "Not measured"
    if row.get("volume_estimated") or str(row.get("volume_status") or "").lower() == "estimated":
        return "Estimated"
    return "Measured"


def _label(text: Any) -> Any:
    return str(text).capitalize() if text else None


def _variants(row: dict[str, Any]) -> str | None:
    names = [str(v) for v in row.get("close_variants") or [] if v]
    return "; ".join(names) or None


def keyword_research_rows(payload: dict[str, Any]) -> list[list[Any]]:
    report = payload.get("cluster_report") if isinstance(payload.get("cluster_report"), dict) else {}
    dataset: dict[str, dict[str, Any]] = {}
    for row in (payload.get("keyword_dataset") or []) + (payload.get("keyword_table") or []):
        if isinstance(row, dict) and row.get("keyword"):
            dataset.setdefault(_norm(row["keyword"]), row)

    rows: list[list[Any]] = []
    seen: set[str] = set()

    def facts(member: dict[str, Any]) -> dict[str, Any]:
        return {**dataset.get(_norm(member.get("keyword")), {}), **{k: v for k, v in member.items() if v is not None}}

    for cluster in report.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        for member in cluster.get("keywords") or []:
            if not isinstance(member, dict) or not member.get("keyword"):
                continue
            key = _norm(member["keyword"])
            if key in seen:
                continue
            seen.add(key)
            f = facts(member)
            rows.append([
                member["keyword"], "Clustered", cluster.get("name"), member.get("role"),
                cluster.get("pillar_name") or "Standalone", cluster.get("modifier_label"),
                cluster.get("primary_keyword"), cluster.get("recommended_url"),
                f.get("volume"), _volume_status(f), f.get("difficulty"), f.get("cpc"),
                _label(f.get("intent")), f.get("intent_source"), f.get("intent_confidence"),
                f.get("funnel"), f.get("funnel_source"), f.get("opportunity_score"),
                f.get("service"), f.get("seed"), None, _variants(f),
            ])

    for orphan in report.get("orphans") or []:
        if not isinstance(orphan, dict) or not orphan.get("keyword"):
            continue
        key = _norm(orphan["keyword"])
        if key in seen:
            continue
        seen.add(key)
        f = facts(orphan)
        reason = _ORPHAN_REASON.get(str(orphan.get("exclusion_reason") or "")) or orphan.get("notes")
        rows.append([
            orphan["keyword"], "Orphan", None, None, None, None, None, None,
            f.get("volume"), _volume_status(f), f.get("difficulty"), f.get("cpc"),
            _label(f.get("intent")), f.get("intent_source"), f.get("intent_confidence"),
            f.get("funnel"), f.get("funnel_source"), f.get("opportunity_score"),
            f.get("service"), f.get("seed"), reason, _variants(f),
        ])

    for key, f in dataset.items():
        if key in seen:
            continue
        seen.add(key)
        rows.append([
            f.get("keyword"), "Not in cluster map", None, None, None, None, None, None,
            f.get("volume"), _volume_status(f), f.get("difficulty"), f.get("cpc"),
            _label(f.get("intent")), None, None, f.get("funnel"), None, f.get("opportunity_score"),
            f.get("service"), f.get("seed"),
            "Researched, but not part of the clustered keyword map (removed in cleaning or not shown)",
            _variants(f),
        ])
    return rows


def build_keyword_research_csv(payload: dict[str, Any]) -> bytes:
    """UTF-8 with a byte-order mark so Excel opens accents correctly."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in keyword_research_rows(payload):
        writer.writerow([_safe(v) for v in row])
    return buf.getvalue().encode("utf-8-sig")
