"""Report view of the keyword clustering result (Phase 5, PDF and DOCX).

Turns the engine's ``cluster_report`` into the layered Keyword Clusters
sections: summary, content hubs, overview, per-cluster detail, orphans,
roadmap, quality and provenance. Every figure comes from the cluster report;
nothing here computes a new metric. Missing values read "N/A", estimated
volumes are marked, and the report says which parts were verified against
real search results and which were inferred.
"""

from __future__ import annotations

from typing import Any

DETAIL_CLUSTER_CAP = 12
DETAIL_ROW_CAP = 25
ORPHAN_ROW_CAP = 40

_INTENT = {
    "informational": "Informational",
    "commercial": "Commercial",
    "transactional": "Transactional",
    "navigational": "Navigational",
}
_ORPHAN_REASON = {
    "single_low_value_keyword": "Did not fit a topic: a single low-value keyword with no related terms",
    "no_topic_fit": "Did not fit a topic",
    "unassigned_by_model": "Not assigned to any cluster by the model",
    "dropped_for_relevance": "Dropped by the model as not relevant",
}
_ISSUE_ORDER = [
    "mixed_intent_cluster",
    "duplicate_primary_keyword",
    "multiple_primary_roles",
    "missing_intent",
    "missing_content_type",
    "missing_primary_keyword",
    "single_keyword_cluster",
    "empty_keyword_list",
    "invalid_cluster_shape",
]
_ISSUE_TITLE = {
    "mixed_intent_cluster": "Mixed intent",
    "duplicate_primary_keyword": "Duplicate primary keyword",
    "multiple_primary_roles": "More than one primary",
    "missing_intent": "No intent",
    "missing_content_type": "No content type",
    "missing_primary_keyword": "No primary keyword",
    "single_keyword_cluster": "Single-keyword cluster",
    "empty_keyword_list": "No keywords",
    "invalid_cluster_shape": "Unreadable cluster",
}
# Reasons keyword_pipeline records; anything else is shown as written.
_EXCLUSION_REASON = {
    "lower_volume_duplicate": "Duplicate of a higher-volume keyword",
    "stale_year": "Mentions a past year",
    "url_noise": "A URL or domain, not a search",
    "noisy": "Junk or too short to mean anything",
    "empty": "Empty after cleaning",
    "competitor_brand": "A competitor's brand name",
    "client_brand_noise": "The client's own brand with no topic",
    "close_variant": "Same search as a kept keyword, worded differently",
}


_SERP_REASON = {
    "disabled": "turned off in settings",
    "locale_unresolved": "no resolved market for this client",
    "no_measured_head_terms": "no keyword had a measured volume",
    "rule_fallback": "model clustering failed, rule result used",
    "fewer than 3 keywords": "fewer than 3 keywords",
}


def _intent(value: Any) -> str:
    return _INTENT.get(str(value or "").lower(), str(value or "N/A"))


def _num(value: Any) -> Any:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else "N/A"


def _volume(row: dict[str, Any]) -> Any:
    v = row.get("volume")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "N/A"
    return f"~{int(v):,} (est.)" if row.get("volume_estimated") else int(v)


def _cluster_volume(cluster: dict[str, Any]) -> Any:
    v = cluster.get("est_traffic")
    if v is None:
        v = cluster.get("total_volume")
    return _num(v)


def _content_type(cluster: dict[str, Any]) -> str:
    return str(cluster.get("recommended_content") or cluster.get("content_type") or "N/A")


def _pattern(cluster: dict[str, Any]) -> str:
    return str(cluster.get("modifier_label") or "Core topic")


def _is_alert_pattern(cluster: dict[str, Any]) -> bool:
    # The two patterns most often mishandled: a comparison page and a local page
    # each need their own URL, never the core service page.
    return cluster.get("modifier_pattern") in ("comparison", "location")


def _count(cluster: dict[str, Any]) -> int:
    return int(cluster.get("keyword_count") or len(cluster.get("keywords") or []))


def _summary(report: dict[str, Any], clusters: list[dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
    accounting = report.get("keyword_accounting") or {}
    provenance = report.get("clustering_provenance") or {}
    size = report.get("size_discipline") or {}
    pillars = report.get("pillars") or []
    balanced = accounting.get("balanced")
    summary = {
        "keywords_in": _num(accounting.get("input_count", report.get("total_keywords"))),
        "clusters": len(clusters),
        "clustered": _num(accounting.get("clustered_count")),
        "orphans": _num(accounting.get("orphan_count", report.get("orphan_count"))),
        "accounting": (
            "Balanced" if balanced else (f"Not balanced: {accounting.get('unaccounted_count')} unaccounted"
                                         if accounting else "Not recorded")
        ),
        "source": provenance.get("source_label") or _legacy_source(report),
        "hubs": (
            f"{len(pillars)} hub(s) covering {sum(p.get('subtopic_count') or 0 for p in pillars)} clusters"
            if pillars
            else "None"
        ),
        "size": _size_text(size),
    }
    alert = None
    if accounting and not balanced:
        alert = (
            f"{accounting.get('unaccounted_count')} of {accounting.get('input_count')} keywords left "
            "clustering without a recorded reason. Treat this keyword map as incomplete until the "
            "run is repeated."
        )
    return summary, alert


def _size_text(size: dict[str, Any]) -> str:
    if not size:
        return "Not recorded"
    merged = int(size.get("merged_small_clusters") or 0)
    split = int(size.get("split_clusters") or 0)
    parts = []
    if merged:
        parts.append(f"{merged} small cluster(s) merged")
    if split:
        parts.append(f"{split} oversized cluster(s) split into {size.get('clusters_from_splits')}")
    if size.get("oversized_unsplit"):
        parts.append(f"{size['oversized_unsplit']} oversized with no clean split")
    return "; ".join(parts) if parts else f"No merges or splits needed (limit {size.get('split_threshold')} keywords)"


def _legacy_source(report: dict[str, Any]) -> str:
    source = str(report.get("source") or "")
    return {"keyword_clustering_skill": "Model clustering", "deterministic_rules": "Rule-based clustering"}.get(
        source, source or "Not recorded"
    )


def _verification_line(provenance: dict[str, Any], clusters: list[dict[str, Any]]) -> str | None:
    if not provenance:
        return None
    if provenance.get("serp_check_ran"):
        checked = provenance.get("serp_checked_clusters") or 0
        rest = len(clusters) - checked
        return (
            f"Membership of {checked} of {len(clusters)} clusters was checked against live Google results"
            + (f"; the other {rest} rest on {'the model' if provenance.get('grouping') == 'model' else 'wording rules'}." if rest else ".")
        )
    return (
        "No cluster was checked against live Google results in this run; every grouping rests on "
        f"{'the model' if provenance.get('grouping') == 'model' else 'wording rules'} and is inferred."
    )


def _overview(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Related values share a cell (name over URL, intent over stage, pattern
    # over content type) so all nine fields fit a portrait page legibly.
    return [
        {
            "cells": [
                (c.get("name"), c.get("recommended_url") or "N/A"),
                c.get("primary_keyword"),
                (_intent(c.get("intent")), c.get("funnel") or "N/A"),
                (_pattern(c), _content_type(c)),
                _count(c),
                _cluster_volume(c),
                c.get("avg_difficulty") if c.get("avg_difficulty") is not None else "N/A",
            ]
        }
        for c in clusters
    ]


def _detail_groups(clusters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    significant = [c for c in clusters if _count(c) >= 2]
    shown = significant[:DETAIL_CLUSTER_CAP]
    groups = []
    for c in shown:
        header = (
            f"{_intent(c.get('intent'))} intent, {c.get('funnel') or 'N/A'} stage. "
            f"Page pattern: {_pattern(c)}. Content: {_content_type(c)}. "
            f"URL: {c.get('recommended_url') or 'N/A'}."
        )
        if c.get("split_from"):
            header += f" Split from \"{c['split_from']}\" ({c.get('split_from_size') or '?'} keywords)."
        reason = (c.get("primary_selection") or {}).get("reason")
        members = [m for m in (c.get("keywords") or []) if isinstance(m, dict)]
        groups.append(
            {
                "title": str(c.get("name") or "Cluster"),
                "subtitle": header,
                "detail": f"Why this primary: {reason}" if reason else None,
                "tone": "alert" if _is_alert_pattern(c) else None,
                "rows": [
                    [
                        m.get("keyword"),
                        m.get("role") or "N/A",
                        _volume(m),
                        m.get("difficulty") if m.get("difficulty") is not None else "N/A",
                        _intent(m.get("intent")),
                        m.get("funnel") or "N/A",
                        m.get("opportunity_score") if m.get("opportunity_score") is not None else "N/A",
                    ]
                    for m in members[:DETAIL_ROW_CAP]
                ],
                "more": max(0, _count(c) - min(len(members), DETAIL_ROW_CAP)),
            }
        )
    note = (
        f"Showing {len(shown)} of {len(clusters)} clusters, highest priority first. "
        + (f"{len(clusters) - len(significant)} single-keyword clusters appear in the overview only. "
           if len(clusters) > len(significant) else "")
        + "Amber headers mark comparison and location pages, which need their own URL rather than "
        "sharing the core service page."
    )
    return groups, note


def _pillar_groups(report: dict[str, Any], clusters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    pillars = report.get("pillars") or []
    groups = []
    for p in pillars:
        vol = p.get("total_volume")
        groups.append(
            {
                "title": f"{p.get('name')} hub",
                "subtitle": (
                    f"Pillar page {p.get('recommended_url')} with {p.get('subtopic_count')} subtopic "
                    f"clusters, combined volume {f'{vol:,}/mo' if isinstance(vol, int) else 'N/A'}, "
                    f"covering {', '.join(p.get('funnel_stages') or [])}."
                ),
                "detail": None,
                "tone": None,
                "rows": [
                    [
                        s.get("name"),
                        s.get("primary_keyword"),
                        _intent(s.get("intent")),
                        s.get("funnel") or "N/A",
                        s.get("modifier_label") or "Core topic",
                        _num(s.get("volume")),
                    ]
                    for s in p.get("subtopics") or []
                ],
                "more": 0,
            }
        )
    standalone = [c for c in clusters if c.get("cluster_tier") != "subtopic"]
    if pillars:
        note = (
            "Each hub is one comprehensive pillar page, with every subtopic cluster beneath it as a "
            "supporting article. Hubs group clusters that share a head topic and together cover more "
            f"than one funnel stage. {len(standalone)} cluster(s) stand alone."
        )
    else:
        note = (
            "No content hubs: no head topic is shared by two or more clusters that cover more than one "
            f"funnel stage, so all {len(clusters)} clusters stand alone."
        )
    return groups, note


def _orphans(report: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    orphans = [o for o in report.get("orphans") or [] if isinstance(o, dict)]
    total = int(report.get("orphan_count") or len(orphans))
    rows = []
    for o in orphans[:ORPHAN_ROW_CAP]:
        reason = str(o.get("exclusion_reason") or "")
        text = _ORPHAN_REASON.get(reason) or str(o.get("notes") or "Did not fit a topic")
        if reason == "dropped_for_relevance" and o.get("notes"):
            text = f"{text}: {o['notes']}"
        rows.append({"cells": [o.get("keyword"), _volume(o), _intent(o.get("intent")), text]})
    if not total:
        return rows, None
    note = (
        f"{total} keyword(s) have demand but no cluster. They stay in the analysis rather than "
        "being hidden"
        + (f"; showing {len(rows)} of {total}." if total > len(rows) else ".")
    )
    return rows, note


def _roadmap(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sorted(
        (r for r in report.get("content_roadmap") or [] if isinstance(r, dict)),
        key=lambda r: r.get("priority") or 999,
    )
    return [
        {
            "cells": [
                r.get("priority"),
                r.get("cluster"),
                r.get("content_type") or "N/A",
                r.get("target_keyword"),
                _intent(r.get("intent")),
                r.get("funnel") or "N/A",
                _num(r.get("est_traffic")),
            ]
        }
        for r in rows
    ]


def _quality(report: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    issues = [i for i in ((report.get("cluster_validation") or {}).get("issues") or []) if isinstance(i, dict)]
    issues.sort(key=lambda i: _ISSUE_ORDER.index(i.get("issue")) if i.get("issue") in _ISSUE_ORDER else 99)
    singles = [i for i in issues if i.get("issue") == "single_keyword_cluster"]
    rows = []
    for i in issues:
        if i.get("issue") == "single_keyword_cluster":
            continue
        kind = str(i.get("issue") or "")
        if kind == "mixed_intent_cluster":
            share = i.get("intent_distribution") or {}
            what = (
                "Informational and transactional searches both carry real weight"
                + (f" ({share.get('informational')}% and {share.get('transactional')}% of the weighted mix)" if share else "")
                + "; one page cannot satisfy both."
            )
            risk = "Cannibalization"
        elif kind == "duplicate_primary_keyword":
            what = f"\"{i.get('keyword')}\" is also the primary keyword of another cluster."
            risk = "Cannibalization"
        elif kind == "multiple_primary_roles":
            what = "More than one member keyword is marked Primary."
            risk = "Unclear target"
        elif kind == "single_keyword_cluster":
            what = "Only one keyword; a thin page target."
            risk = "Low"
        else:
            what = _ISSUE_TITLE.get(kind, kind.replace("_", " ").capitalize()) + "."
            risk = "Incomplete"
        rows.append({"cells": [_ISSUE_TITLE.get(kind, kind), i.get("cluster") or "N/A", what, risk]})
    if singles:
        # Low risk and often numerous: one row, so the cannibalization flags stay visible.
        names = [str(i.get("cluster") or "") for i in singles]
        shown = ", ".join(names[:12]) + (f", and {len(names) - 12} more" if len(names) > 12 else "")
        rows.append(
            {
                "cells": [
                    "Single-keyword clusters",
                    f"{len(singles)} clusters",
                    f"Only one keyword each, so each is a thin page target: {shown}.",
                    "Low",
                ]
            }
        )
    serious = sum(1 for i in issues if i.get("issue") in ("mixed_intent_cluster", "duplicate_primary_keyword"))
    if not issues:
        note = "The cluster checks raised no issues."
    else:
        note = (
            f"{len(issues)} issue(s); {serious} predict keyword cannibalization (mixed intent or a "
            "duplicate primary) and are listed first."
            if serious
            else f"{len(issues)} low-risk issue(s); none predicts keyword cannibalization."
        )
    return rows, note


def _provenance(report: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    p = report.get("clustering_provenance") or {}
    serp_reason = p.get("serp_check_reason")
    metrics = {
        "grouped_by": "The model (Claude)" if p.get("grouping") == "model" else "Wording rules",
        "serp_check": (
            f"Ran on {p.get('serp_head_terms_checked')} head terms"
            if p.get("serp_check_ran")
            else f"Skipped: {_SERP_REASON.get(str(serp_reason), str(serp_reason or 'not run').replace('_', ' '))}"
        ),
        "naming": (
            f"Named by {p.get('naming_model')}" if p.get("naming_ran") else "Rule-based names"
        ),
        "model_clustering": (
            f"Ran ({p.get('model_clustering_model')})" if p.get("model_clustering_ran")
            else f"Did not run: {p.get('model_clustering_reason') or 'not recorded'}"
        ),
        "invented_rejected": p.get("invented_keywords_rejected", 0),
        "excluded": _num(p.get("cleaning_excluded_count", report.get("pipeline_excluded_count"))),
    }
    by_reason = p.get("cleaning_excluded_by_reason") or report.get("pipeline_excluded_by_reason") or {}
    exclusions = [
        {"cells": [_EXCLUSION_REASON.get(str(k), str(k).replace("_", " ").capitalize()), v]}
        for k, v in sorted(by_reason.items(), key=lambda kv: -int(kv[1] or 0))
    ]
    bullets = [f"Verified: {t}" for t in p.get("verified") or []] + [f"Inferred: {t}" for t in p.get("inferred") or []]
    return metrics, exclusions, bullets


def build_cluster_report_view(payload: dict[str, Any]) -> dict[str, Any]:
    """``kc_*`` keys for the Keyword Clusters report sections."""
    report = payload.get("cluster_report") if isinstance(payload.get("cluster_report"), dict) else {}
    clusters = [c for c in report.get("clusters") or [] if isinstance(c, dict)]
    if not clusters:
        # Older runs: only the flattened list exists. Show what it has.
        clusters = [c for c in payload.get("clusters") or [] if isinstance(c, dict)]
    if not clusters and not report:
        return {}
    summary, alert = _summary(report, clusters)
    detail, detail_note = _detail_groups(clusters)
    hubs, hubs_note = _pillar_groups(report, clusters)
    orphans, orphans_note = _orphans(report)
    quality, quality_note = _quality(report)
    prov_metrics, exclusions, prov_bullets = _provenance(report)
    return {
        "kc_summary": summary,
        "kc_accounting_alert": alert,
        "kc_verification": _verification_line(report.get("clustering_provenance") or {}, clusters),
        "kc_hubs": hubs,
        "kc_hubs_note": hubs_note,
        "kc_overview": _overview(clusters),
        "kc_details": detail,
        "kc_details_note": detail_note,
        "kc_orphans": orphans,
        "kc_orphans_note": orphans_note,
        "kc_roadmap": _roadmap(report),
        "kc_quality": quality,
        "kc_quality_note": quality_note,
        "kc_provenance": prov_metrics,
        "kc_exclusions": exclusions,
        "kc_provenance_notes": prov_bullets,
    }
