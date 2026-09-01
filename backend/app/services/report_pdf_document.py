"""Declarative phase-report PDF layouts (numbered sections, tables, summaries)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from reportlab.platypus import Spacer

from app.services.report_export import resolve_report_title
from app.services.report_pdf_normalize import normalize_report_payload
from app.services.report_pdf_theme import (
    body,
    bullet_list,
    data_table_weighted,
    header_band,
    metric_strip,
    note_block,
    numbered_list,
    numbered_section,
    subsection,
)

SKIP = frozenset(
    {
        "card_type",
        "agent_key",
        "actions",
        "required_role",
        "event_type",
        "type",
        "title",
    }
)

SUMMARY_KEYS = ("executive_summary", "summary", "overview", "headline", "note", "description")


@dataclass
class SectionSpec:
    title: str
    intro: str | None = None
    intro_field: str | None = None
    metrics: list[tuple[str, str]] = field(default_factory=list)
    table_source: str | None = None
    table_headers: list[str] = field(default_factory=list)
    table_keys: list[str] = field(default_factory=list)
    table_weights: list[float] = field(default_factory=list)
    bullets_source: str | None = None
    numbered_source: str | None = None
    note_label: str | None = None
    note_fields: list[str] = field(default_factory=list)
    max_rows: int = 25
    row_fn: Callable[[dict[str, Any]], list[Any]] | None = None


def _get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return cur if cur is not None else default


def _rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _meta_line(payload: dict[str, Any], client: dict) -> str:
    bits: list[str] = []
    for key in ("business_name", "client_name", "client", "industry"):
        if payload.get(key):
            bits.append(str(payload[key]))
    url = payload.get("primary_url") or payload.get("domain") or client.get("primary_url")
    if url:
        bits.append(str(url))
    geo = " · ".join(str(x) for x in [payload.get("geographic_focus"), payload.get("location_name")] if x)
    if geo:
        bits.append(f"Geo: {geo}")
    return " · ".join(bits)


def _summary_text(payload: dict[str, Any], client: dict) -> str | None:
    for key in SUMMARY_KEYS:
        val = payload.get(key)
        if val and str(val).strip():
            return str(val).strip()
    score = payload.get("score") or payload.get("overall_score")
    if score is not None:
        name = payload.get("client_name") or client.get("name") or "the client"
        return f"Phase report for {name}. Overall score: {score}/100."
    return None


def _infer_columns(rows: list[dict[str, Any]], limit: int = 6) -> tuple[list[str], list[str]]:
    preferred = [
        "keyword",
        "title",
        "name",
        "label",
        "topic",
        "cluster",
        "path",
        "url",
        "priority",
        "disposition",
        "status",
        "volume",
        "difficulty",
        "score",
        "action",
        "note",
        "reason",
        "type",
    ]
    keys: list[str] = []
    for row in rows[:10]:
        for k in row:
            if k in SKIP or k in keys:
                continue
            if isinstance(row.get(k), (list, dict)):
                continue
            keys.append(k)
        if len(keys) >= limit:
            break
    ordered = [k for k in preferred if k in keys]
    for k in keys:
        if k not in ordered:
            ordered.append(k)
    ordered = ordered[:limit]
    headers = [k.replace("_", " ").title() for k in ordered]
    return headers, ordered


def _table_rows(
    rows: list[dict[str, Any]],
    keys: list[str],
    row_fn: Callable[[dict[str, Any]], list[Any]] | None = None,
) -> list[list[Any]]:
    def _cell(value: Any) -> Any:
        if isinstance(value, list):
            return ", ".join(str(x) for x in value[:6])
        if isinstance(value, dict):
            return json.dumps(value, default=str)[:120]
        return value

    def _has_value(value: Any) -> bool:
        return value not in (None, "", [], {})

    out: list[list[Any]] = []
    for row in rows:
        if row_fn:
            cells = [_cell(v) for v in row_fn(row)]
        else:
            cells = [_cell(row.get(k)) for k in keys]
        if any(_has_value(c) for c in cells):
            out.append(cells)
    return out


def _auto_blueprint(payload: dict[str, Any]) -> list[SectionSpec]:
    sections: list[SectionSpec] = []
    n = 0
    for key, value in payload.items():
        if key in SKIP or key in SUMMARY_KEYS:
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            n += 1
            headers, keys = _infer_columns(_rows(value))
            sections.append(
                SectionSpec(
                    title=key.replace("_", " ").title(),
                    table_source=key,
                    table_headers=headers,
                    table_keys=keys,
                )
            )
        elif isinstance(value, dict) and value:
            scalar_items = [(k, str(v)) for k, v in value.items() if _get(value, k) is not None and not isinstance(v, (list, dict))]
            if scalar_items:
                n += 1
                sections.append(
                    SectionSpec(
                        title=key.replace("_", " ").title(),
                        metrics=[(k.replace("_", " ").title(), f"{key}.{k}") for k, _ in scalar_items[:8]],
                    )
                )
    return sections[:8]


# --- Per-report blueprints ---------------------------------------------------

def _content_strategy_sections() -> list[SectionSpec]:
    return [
        SectionSpec(
            title="Strategic Overview",
            intro_field="summary",
        ),
        SectionSpec(
            title="Core Topics & Authority Map",
            table_source="core_topics",
            table_headers=["Topic", "Intent", "Volume", "Difficulty", "Priority"],
            table_keys=["name", "intent", "volume", "difficulty", "priority"],
            table_weights=[1.4, 0.8, 0.7, 0.7, 0.8],
            max_rows=20,
            row_fn=lambda r: [
                r.get("name") or r.get("pillar") or r.get("topic"),
                r.get("intent"),
                r.get("volume") or r.get("opportunity_score"),
                r.get("difficulty") or r.get("kd"),
                r.get("priority") or r.get("priority_label"),
            ],
        ),
        SectionSpec(
            title="Content Pillars",
            table_source="pillars",
            table_headers=["Pillar", "Primary keyword", "Score", "Intent"],
            table_keys=["name", "topic", "volume", "intent"],
            table_weights=[1.2, 1.2, 0.7, 0.8],
            row_fn=lambda r: [
                r.get("name") or r.get("pillar"),
                r.get("topic") or r.get("primary_keyword"),
                r.get("volume") or r.get("opportunity_score"),
                r.get("intent"),
            ],
        ),
        SectionSpec(
            title="Priority Content Queue",
            intro="Prioritized content pieces ranked by opportunity and business impact.",
            table_source="priority_queue",
            table_headers=["Title", "Priority", "Volume", "Difficulty", "URL"],
            table_keys=["title", "priority", "volume", "difficulty", "suggested_url"],
            table_weights=[1.5, 0.7, 0.6, 0.6, 1.2],
            max_rows=30,
            row_fn=lambda r: [
                r.get("title") or r.get("keyword"),
                r.get("priority") or r.get("priority_label") or r.get("bucket"),
                r.get("volume"),
                r.get("difficulty"),
                r.get("suggested_url") or r.get("url"),
            ],
        ),
        SectionSpec(
            title="Combined Priority Queue",
            table_source="combined_priority_queue",
            table_headers=["Action", "Title", "Priority", "Source"],
            table_keys=["action", "title", "priority", "source"],
            table_weights=[0.8, 1.6, 0.7, 0.9],
            max_rows=25,
        ),
        SectionSpec(
            title="Content Gaps",
            table_source="content_gaps",
            table_headers=["Gap", "Opportunity", "Competitor", "Note"],
            table_keys=["topic", "opportunity", "competitor", "note"],
            table_weights=[1.2, 1.0, 1.0, 1.3],
            row_fn=lambda r: [
                r.get("topic") or r.get("keyword") or r.get("title"),
                r.get("opportunity") or r.get("action") or r.get("priority"),
                ", ".join(r.get("competitors") or [])
                if isinstance(r.get("competitors"), list)
                else r.get("competitor"),
                r.get("note") or r.get("reason") or r.get("rationale"),
            ],
        ),
        SectionSpec(
            title="12-Week Content Calendar",
            table_source="content_calendar",
            table_headers=["Week", "Month", "Title", "Keyword", "Type", "Priority"],
            table_keys=["week", "month", "title", "keyword", "type", "priority"],
            table_weights=[0.5, 0.9, 1.4, 1.0, 0.7, 0.7],
            max_rows=15,
            row_fn=lambda r: [
                r.get("week"),
                r.get("month"),
                r.get("title") or r.get("keyword"),
                r.get("keyword"),
                r.get("type") or r.get("content_type"),
                r.get("priority"),
            ],
        ),
        SectionSpec(
            title="Handoffs",
            table_source="handoffs",
            table_headers=["Deliverable", "Receiving phase / owner"],
            table_keys=["item", "receiving"],
            table_weights=[1.5, 2.0],
            row_fn=lambda r: [
                r.get("item") or r.get("concern") or r.get("deliverable"),
                r.get("receiving") or r.get("route_to") or r.get("owner"),
            ],
        ),
    ]


def _search_demand_sections() -> list[SectionSpec]:
    return [
        SectionSpec(
            title="Research Overview",
            metrics=[
                ("Clusters", "cluster_count"),
                ("Keywords", "keyword_count"),
                ("Quick wins", "quick_win_count"),
                ("Source", "source"),
            ],
        ),
        SectionSpec(
            title="Keyword Clusters",
            table_source="clusters",
            table_headers=["Cluster", "Primary keyword", "Volume", "Keywords", "Intent"],
            table_keys=["name", "primary_keyword", "total_volume", "keyword_count", "intent"],
            table_weights=[1.2, 1.2, 0.7, 0.6, 0.8],
            max_rows=25,
            row_fn=lambda r: [
                r.get("name") or r.get("cluster"),
                r.get("primary_keyword") or r.get("target_keyword"),
                r.get("total_volume") or r.get("est_traffic") or r.get("volume"),
                r.get("keyword_count") or len(r.get("keywords") or []),
                r.get("intent"),
            ],
        ),
        SectionSpec(
            title="Top Opportunities",
            table_source="opportunities",
            table_headers=["Keyword", "Volume", "KD", "Score", "Intent"],
            table_keys=["keyword", "volume", "difficulty", "opportunity_score", "intent"],
            table_weights=[1.4, 0.7, 0.5, 0.6, 0.8],
            max_rows=30,
            row_fn=lambda r: [
                r.get("keyword") or r.get("title"),
                r.get("volume"),
                r.get("difficulty") or r.get("kd"),
                r.get("opportunity_score") or r.get("score"),
                r.get("intent"),
            ],
        ),
        SectionSpec(
            title="Trend Plays",
            bullets_source="trend_plays",
        ),
        SectionSpec(
            title="Keywords to Avoid",
            bullets_source="avoid",
        ),
    ]


def _fmt_kw_list(raw: Any, n: int = 10) -> str:
    if not isinstance(raw, list) or not raw:
        return "—"
    return ", ".join(str(k) for k in raw[:n] if k) or "—"


def _site_architecture_sections() -> list[SectionSpec]:
    """DOCX doesn't use the PDF's custom site_architecture_blueprint renderer
    (that one is registered as a PDF-only SPECIAL_RENDERER), so without an
    explicit blueprint here the URL Mapping sheet would fall through to the
    generic auto-blueprint and render a nested dict badly instead of the two
    readable tables. Same two-table split as the PDF renderer.
    """
    return [
        SectionSpec(
            title="URL Mapping — Taxonomy & Status",
            table_source="url_map_report.final_url_map",
            table_headers=[
                "Lvl", "L1", "L2", "L3", "L4", "Current URL", "Proposed URL",
                "Status", "Page Type", "Priority",
            ],
            table_keys=[
                "level", "l1_category", "l2_subcategory", "l3_subsubcategory",
                "l4_attribution", "current_url", "proposed_url", "status",
                "page_type", "priority",
            ],
            max_rows=60,
        ),
        SectionSpec(
            title="URL Mapping — Keywords & Opportunity",
            table_source="url_map_report.final_url_map",
            table_headers=[
                "Primary Keyword", "Vol/mo", "CPC", "Secondary Keywords (5-10)",
                "Combined Vol", "Est. Products", "In Scope?", "Notes",
            ],
            table_keys=[
                "primary_keyword", "search_volume", "cpc", "secondary_keywords_sheet",
                "combined_cluster_volume", "est_products", "in_scope", "notes",
            ],
            row_fn=lambda u: [
                u.get("primary_keyword"),
                u.get("search_volume"),
                u.get("cpc"),
                _fmt_kw_list(u.get("secondary_keywords_sheet") or u.get("secondary_keywords")),
                u.get("combined_cluster_volume"),
                u.get("est_products") if u.get("est_products") is not None else "—",
                u.get("in_scope") or "—",
                u.get("notes") or "—",
            ],
            max_rows=60,
        ),
    ]


def _content_audit_sections() -> list[SectionSpec]:
    return [
        SectionSpec(
            title="Audit Scope & Methodology",
            intro_field="note",
            metrics=[
                ("Pages inventoried", "inventory_count"),
                ("Qualitative mode", "qualitative"),
            ],
        ),
        SectionSpec(
            title="Themed Inventory",
            table_source="themes",
            table_headers=["Theme", "URLs", "Top dispositions"],
            table_keys=["theme", "url_count", "dispositions"],
            table_weights=[1.3, 0.6, 2.6],
            row_fn=lambda r: [
                r.get("theme"),
                r.get("url_count"),
                ", ".join(
                    f"{k}: {v}" for k, v in (r.get("dispositions") or {}).items() if v
                )
                or "—",
            ],
        ),
        SectionSpec(
            title="Inventory & Disposition",
            table_source="inventory",
            table_headers=["Title", "Path", "Disposition", "Reason", "IA note"],
            table_keys=["title", "path", "disposition", "reason", "ia_note"],
            table_weights=[1.2, 1.0, 0.8, 1.3, 0.9],
            max_rows=35,
        ),
        SectionSpec(
            title="Refresh Priority Queue",
            table_source="refresh_queue",
            table_headers=["Title", "Path", "Priority", "Reason"],
            table_keys=["title", "path", "priority", "reason"],
            table_weights=[1.2, 1.0, 0.7, 1.6],
        ),
        SectionSpec(
            title="Cannibalization Risks",
            table_source="cannibalization",
            table_headers=["Topic", "URLs", "Recommendation"],
            table_keys=["topic", "urls", "recommendation"],
            table_weights=[1.0, 1.2, 1.5],
        ),
        SectionSpec(
            title="Handoffs",
            table_source="handoffs",
            table_headers=["Deliverable", "Receiving phase / owner"],
            table_keys=["item", "receiving"],
            table_weights=[1.5, 2.0],
        ),
    ]


def _technical_seo_sections() -> list[SectionSpec]:
    return [
        SectionSpec(
            title="Audit Summary",
            metrics=[
                ("Overall score", "score"),
                ("Severity", "severity"),
                ("Phase 6 connected", "phase6_connected"),
            ],
        ),
        SectionSpec(
            title="Findings by Theme",
            table_source="findings_by_theme",
            table_headers=["Theme", "Score", "Findings"],
            table_keys=["theme", "score", "findings"],
            table_weights=[1.0, 0.6, 2.9],
            row_fn=lambda r: [
                r.get("theme"),
                r.get("score"),
                "; ".join(str(x) for x in (r.get("findings") or [])[:4]),
            ],
        ),
        SectionSpec(
            title="Priority Fix Backlog",
            table_source="priority_backlog",
            table_headers=["Priority", "Issue", "Fix", "Source"],
            table_keys=["priority", "issue", "fix", "source"],
            table_weights=[0.7, 1.3, 1.5, 0.8],
            max_rows=25,
        ),
        SectionSpec(
            title="IA Actions from Phase 6",
            table_source="ia_actions",
            table_headers=["Action", "Detail", "Owner"],
            table_keys=["action", "detail", "owner"],
            table_weights=[1.0, 2.0, 0.8],
        ),
        SectionSpec(
            title="Specialist Routing",
            table_source="specialist_routing",
            table_headers=["Area", "Route to", "Reason"],
            table_keys=["area", "route", "reason"],
            table_weights=[1.0, 1.0, 1.8],
        ),
        SectionSpec(
            title="Not Measured",
            bullets_source="not_measured",
        ),
    ]


def _competitor_sections() -> list[SectionSpec]:
    return [
        SectionSpec(
            title="Market Landscape Overview",
            intro_field="summary",
            metrics=[
                ("Client maturity", "client_baseline.maturity_score"),
                ("Competitors scored", "competitor_count"),
            ],
        ),
        SectionSpec(
            title="Competitor Set",
            table_source="competitors",
            table_headers=["Name", "URL", "Cluster", "Source"],
            table_keys=["name", "url", "positioning_cluster", "source"],
            table_weights=[1.2, 1.4, 1.0, 0.8],
            row_fn=lambda r: [
                r.get("name"),
                r.get("url"),
                r.get("positioning_cluster") or r.get("cluster"),
                r.get("source"),
            ],
        ),
        SectionSpec(
            title="Tier Overview",
            table_source="tier_overview",
            table_headers=["Rank", "Name", "Tier", "Composite", "Threat"],
            table_keys=["rank", "name", "tier_name", "composite", "future_threat"],
            table_weights=[0.5, 1.2, 1.0, 0.7, 0.8],
        ),
        SectionSpec(
            title="Parameter Gaps",
            table_source="parameter_gaps",
            table_headers=["Parameter", "Client", "Top competitor", "Gap", "Action"],
            table_keys=["parameter", "client_score", "top_competitor", "gap", "action"],
            table_weights=[1.0, 0.6, 1.0, 0.5, 1.6],
            max_rows=20,
        ),
        SectionSpec(
            title="Benchmark Recommendations",
            bullets_source="recommendations.benchmark",
        ),
        SectionSpec(
            title="Differentiation Plays",
            bullets_source="recommendations.differentiate",
        ),
    ]


def _website_sections() -> list[SectionSpec]:
    return [
        SectionSpec(
            title="Website Situation Summary",
            intro_field="summary",
            metrics=[
                ("Pages found", "pages_found"),
                ("Indexable", "indexable"),
                ("Broken links", "broken_count"),
                ("Severity", "severity"),
            ],
        ),
        SectionSpec(
            title="Audit Tabs",
            table_source="audit_tabs",
            table_headers=["Tab", "Severity", "Status", "Note"],
            table_keys=["tab", "severity", "status", "note"],
            table_weights=[0.9, 0.7, 0.7, 2.4],
        ),
        SectionSpec(
            title="Technical Findings",
            table_source="issues",
            table_headers=["Issue", "Severity", "Count", "Note"],
            table_keys=["issue", "severity", "count", "note"],
            table_weights=[1.2, 0.7, 0.5, 1.9],
        ),
        SectionSpec(
            title="Page Audits",
            table_source="page_audits",
            table_headers=["URL", "Score", "Status", "Note"],
            table_keys=["url", "score", "status", "note"],
            table_weights=[1.5, 0.6, 0.7, 1.7],
            max_rows=20,
        ),
        SectionSpec(
            title="Sample URLs",
            bullets_source="sample_urls",
        ),
    ]


def _generic_phase_sections(card_type: str) -> list[SectionSpec]:
    """Fallback sections for tracking, discovery, publishing, etc."""
    if card_type.startswith("tracking_"):
        return [
            SectionSpec(title="Tracking Checkpoint", intro_field="summary"),
            SectionSpec(
                title="Status & Metrics",
                metrics=[("Status", "status"), ("Score", "score"), ("Gate", "gate")],
            ),
            SectionSpec(
                title="Findings",
                table_source="findings",
                table_headers=["Finding", "Severity", "Note"],
                table_keys=["finding", "severity", "note"],
                table_weights=[1.4, 0.7, 1.4],
            ),
            SectionSpec(title="Next steps", bullets_source="next_steps"),
        ]
    if card_type.startswith("discovery_"):
        return [
            SectionSpec(title="Discovery Phase", intro_field="summary"),
            SectionSpec(
                title="Completeness",
                metrics=[("Score", "completeness_score"), ("Status", "status")],
            ),
            SectionSpec(
                title="Fields captured",
                table_source="fields",
                table_headers=["Field", "Value", "Source"],
                table_keys=["field", "value", "source"],
                table_weights=[1.0, 2.0, 0.8],
            ),
        ]
    return []


REPORT_BLUEPRINTS: dict[str, list[SectionSpec]] = {
    "content_strategy_report": _content_strategy_sections(),
    "search_demand_report": _search_demand_sections(),
    "content_audit_report": _content_audit_sections(),
    "site_architecture_blueprint": _site_architecture_sections(),
    "technical_seo_report": _technical_seo_sections(),
    "competitor_landscape": _competitor_sections(),
    "website_audit": _website_sections(),
    "broken_link_report": [
        SectionSpec(title="Broken Link Summary", intro_field="summary"),
        SectionSpec(
            title="Broken links",
            table_source="broken_links",
            table_headers=["URL", "Status", "Source page"],
            table_keys=["url", "status", "source"],
            table_weights=[1.5, 0.6, 1.4],
        ),
    ],
    "seo_audit_report": [
        SectionSpec(title="SEO Audit Summary", intro_field="summary"),
        SectionSpec(
            title="Issues",
            table_source="issues",
            table_headers=["Issue", "Severity", "URL"],
            table_keys=["issue", "severity", "url"],
            table_weights=[1.4, 0.7, 1.4],
        ),
    ],
    "on_page_seo_report": [
        SectionSpec(title="On-Page SEO Summary", intro_field="summary"),
        SectionSpec(
            title="Page recommendations",
            table_source="pages",
            table_headers=["URL", "Title", "Score", "Priority fixes"],
            table_keys=["url", "title", "score", "fixes"],
            table_weights=[1.3, 1.0, 0.5, 1.7],
        ),
    ],
    "content_planning_report": [
        SectionSpec(title="Merge Overview", intro_field="note"),
        SectionSpec(
            title="Merged pages",
            table_source="pages",
            table_headers=["Rank", "URL", "Keyword", "Action", "Disposition", "Tier", "Funnel"],
            table_keys=[
                "priority_rank", "url_n", "primary_keyword", "action", "disposition",
                "priority_tier", "funnel",
            ],
            table_weights=[0.35, 1.05, 0.9, 0.6, 0.7, 0.6, 0.5],
            row_fn=lambda r: [
                r.get("priority_rank"),
                r.get("url_n") or r.get("path") or r.get("url"),
                r.get("primary_keyword") or r.get("keyword"),
                r.get("action"),
                r.get("disposition"),
                r.get("priority_tier"),
                r.get("funnel"),
            ],
        ),
        SectionSpec(
            title="Content Signals from Phase 5/6 — angle, business fit, competitors",
            table_source="content_signals",
            table_headers=["URL", "Angle", "Business fit", "Competitor domains"],
            table_keys=["url_n", "angle", "business_fit", "competitor_domains"],
            table_weights=[1.1, 0.8, 1.6, 1.6],
        ),
        SectionSpec(
            title="Excluded — route upstream",
            table_source="excluded",
            table_headers=["Pack", "URL", "Reason"],
            table_keys=["source_pack", "url_n", "reason"],
            table_weights=[0.8, 1.0, 2.2],
        ),
    ],
    "content_production_report": [
        SectionSpec(title="Production Overview", intro_field="note"),
        SectionSpec(
            title="Briefed Topics",
            table_source="briefs",
            table_headers=["Keyword", "URL", "Funnel", "Angle", "Status"],
            table_keys=["keyword", "url", "funnel", "angle", "status"],
            table_weights=[1.0, 1.1, 0.5, 0.8, 0.7],
            row_fn=lambda r: [
                r.get("keyword"),
                r.get("url") or r.get("path"),
                r.get("funnel"),
                r.get("angle") or "—",
                r.get("status"),
            ],
        ),
        SectionSpec(
            title="Funnel Balance",
            metrics=[
                ("TOFU", "funnel_balance.TOFU"),
                ("MOFU", "funnel_balance.MOFU"),
                ("BOFU", "funnel_balance.BOFU"),
            ],
        ),
        SectionSpec(title="Funnel Balance Warnings", bullets_source="funnel_balance_warnings"),
        SectionSpec(
            title="Draft stubs",
            table_source="drafts",
            table_headers=["Title", "Keyword", "Status", "URL"],
            table_keys=["title", "keyword", "status", "url"],
            table_weights=[1.4, 1.0, 0.7, 1.2],
        ),
    ],
    "publishing_report": [
        SectionSpec(title="Publishing Overview", intro_field="summary"),
        SectionSpec(
            title="Publish queue",
            table_source="queue",
            table_headers=["URL", "Action", "Status", "Note"],
            table_keys=["url", "action", "status", "note"],
            table_weights=[1.3, 0.8, 0.7, 1.5],
        ),
    ],
    "readiness_score": [
        SectionSpec(
            title="Readiness Assessment",
            metrics=[("Overall", "overall"), ("Threshold", "threshold"), ("Ready for Phase 5", "ready_for_phase5")],
        ),
        SectionSpec(title="Missing items", bullets_source="missing"),
    ],
}


def _resolve_blueprint(card_type: str, payload: dict[str, Any]) -> list[SectionSpec]:
    if card_type in REPORT_BLUEPRINTS:
        return REPORT_BLUEPRINTS[card_type]
    generic = _generic_phase_sections(card_type)
    if generic:
        return generic
    return _auto_blueprint(payload)


def _render_section(flow: list, n: int, spec: SectionSpec, payload: dict[str, Any]) -> bool:
    """Render one section. Returns False if section had no content."""
    blocks: list = []
    has_content = False

    intro = spec.intro
    if not intro and spec.intro_field:
        intro = _get(payload, spec.intro_field)
        if intro is not None:
            intro = str(intro)
    if intro:
        blocks.append(body(str(intro)))
        has_content = True

    if spec.metrics:
        metrics = []
        for label, path in spec.metrics:
            val = _get(payload, path)
            if val is not None and val != "":
                metrics.append((label, val))
        if metrics:
            blocks.append(metric_strip(metrics))
            has_content = True

    if spec.table_source:
        rows = _rows(_get(payload, spec.table_source))
        if rows:
            keys = spec.table_keys or _infer_columns(rows)[1]
            headers = spec.table_headers or [k.replace("_", " ").title() for k in keys]
            weights = spec.table_weights or [1.0] * len(headers)
            table_data = _table_rows(rows, keys, spec.row_fn)
            if table_data:
                blocks.append(
                    data_table_weighted(
                        headers,
                        table_data,
                        weights,
                        max_rows=spec.max_rows,
                    )
                )
                has_content = True

    if spec.bullets_source:
        raw = _get(payload, spec.bullets_source)
        items: list[str] = []
        if isinstance(raw, list):
            for x in raw:
                if isinstance(x, str):
                    items.append(x)
                elif isinstance(x, dict):
                    items.append(
                        " · ".join(
                            f"{k}: {v}"
                            for k, v in x.items()
                            if k not in SKIP and not isinstance(v, (list, dict))
                        )
                    )
        if items:
            blocks.extend(bullet_list(items))
            has_content = True

    if spec.numbered_source:
        raw = _get(payload, spec.numbered_source)
        if isinstance(raw, list) and raw:
            blocks.extend(numbered_list([str(x) for x in raw]))
            has_content = True

    if spec.note_fields:
        bits = [str(_get(payload, f)) for f in spec.note_fields if _get(payload, f)]
        if bits:
            blocks.append(note_block(spec.note_label or "Note:", " ".join(bits)))
            has_content = True

    if not has_content:
        return False

    flow.append(numbered_section(n, spec.title))
    flow.extend(blocks)
    flow.append(Spacer(1, 8))
    return True


def _append_auto_sections(
    flow: list,
    payload: dict[str, Any],
    blueprint: list[SectionSpec],
    section_num: int,
) -> int:
    covered = {s.table_source for s in blueprint if s.table_source}
    covered |= {s.bullets_source for s in blueprint if s.bullets_source}
    covered |= {s.numbered_source for s in blueprint if s.numbered_source}
    # A metrics path's top-level key (e.g. "funnel_balance.TOFU" -> "funnel_balance")
    # is already rendered explicitly — don't let it also fall through to auto-render.
    covered |= {path.split(".", 1)[0] for s in blueprint for _, path in s.metrics}

    for key, value in payload.items():
        if key in SKIP or key in SUMMARY_KEYS or key in covered:
            continue
        if isinstance(value, list) and value:
            if isinstance(value[0], dict):
                section_num += 1
                headers, keys = _infer_columns(_rows(value))
                spec = SectionSpec(
                    title=key.replace("_", " ").title(),
                    table_source=key,
                    table_headers=headers,
                    table_keys=keys,
                )
                if _render_section(flow, section_num, spec, payload):
                    covered.add(key)
                else:
                    section_num -= 1
            elif isinstance(value[0], str):
                section_num += 1
                spec = SectionSpec(title=key.replace("_", " ").title(), bullets_source=key)
                if _render_section(flow, section_num, spec, payload):
                    covered.add(key)
                else:
                    section_num -= 1
        elif isinstance(value, dict) and value:
            scalar_items = [
                (k, str(v))
                for k, v in value.items()
                if v not in (None, "", [], {}) and not isinstance(v, (list, dict))
            ]
            if len(scalar_items) >= 2:
                section_num += 1
                spec = SectionSpec(
                    title=key.replace("_", " ").title(),
                    metrics=[(k.replace("_", " ").title(), f"{key}.{k}") for k, _ in scalar_items[:8]],
                )
                if _render_section(flow, section_num, spec, payload):
                    covered.add(key)
                else:
                    section_num -= 1
    return section_num


def render_document_report(
    flow: list,
    report: dict,
    client: dict,
    *,
    show_title: bool = True,
) -> None:
    raw_payload = report.get("payload") if isinstance(report.get("payload"), dict) else {}
    card_type = str(raw_payload.get("card_type") or report.get("card_type") or "")
    payload = normalize_report_payload(card_type, raw_payload)
    title = resolve_report_title(report, client)
    meta = _meta_line(payload, client)

    if show_title:
        flow.extend(header_band(title, meta))
    elif meta:
        flow.append(body(meta))
        flow.append(Spacer(1, 6))

    flow.append(subsection("Executive Summary"))
    summary = _summary_text(payload, client)
    if summary:
        flow.append(body(summary))
    else:
        flow.append(body(f"Structured phase report for {client.get('name') or 'the client'}."))
    flow.append(Spacer(1, 10))

    blueprint = _resolve_blueprint(card_type, payload)
    section_num = 0
    for spec in blueprint:
        if _render_section(flow, section_num + 1, spec, payload):
            section_num += 1

    _append_auto_sections(flow, payload, blueprint, section_num)
