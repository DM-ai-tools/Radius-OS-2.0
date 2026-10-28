"""Declarative phase-report PDF layouts (numbered sections, tables, summaries)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from reportlab.platypus import KeepTogether, Spacer

from app.logging_config import get_logger
from app.services.report_export import resolve_report_title
from app.services.report_pdf_normalize import normalize_report_payload
from app.services.report_pdf_theme import (
    alert_block,
    body,
    bullet_list,
    data_table_weighted,
    group_header,
    header_band,
    metric_strip,
    note_block,
    numbered_list,
    section_rule,
    summary_panel,
)

log = get_logger("report_pdf_document")

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
class TableSpec:
    """An extra table inside a section, under its own small heading."""

    title: str
    source: str
    headers: list[str]
    keys: list[str]
    weights: list[float] = field(default_factory=list)
    max_rows: int = 40
    row_fn: Callable[[dict[str, Any]], list[Any]] | None = None


@dataclass
class GroupSpec:
    """Several titled tables in one section: one per cluster, hub, etc.

    ``source`` is a list of ``{"title", "subtitle", "detail", "tone", "rows",
    "more"}`` dicts, rows already formatted as cell lists.
    """

    source: str
    headers: list[str]
    weights: list[float] = field(default_factory=list)
    max_rows: int = 25


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
    max_rows: int = 40
    row_fn: Callable[[dict[str, Any]], list[Any]] | None = None
    # Applied to the table's rows before row_fn (e.g. numbering older saved rows).
    prepare_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None
    tables: list[TableSpec] = field(default_factory=list)
    # A finding that must stand out, shown first in the section.
    alert_field: str | None = None
    alert_label: str = "Warning:"
    groups: GroupSpec | None = None


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
            return json.dumps(value, default=str)[:200]
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
            title="Core Topics & Authority Map",
            table_source="core_topics",
            table_headers=["Topic", "Intent", "Volume", "Difficulty", "Priority"],
            table_keys=["name", "intent", "volume", "difficulty", "priority"],
            table_weights=[1.4, 0.8, 0.7, 0.7, 0.8],
            max_rows=40,
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
            title="Content Plan Summary",
            intro="One row per piece of work. The two tables below are the same rows in the same order.",
            metrics=[
                ("Work items", "content_plan_summary_view.total"),
                ("By action", "content_plan_summary_view.actions"),
                ("By priority", "content_plan_summary_view.priorities"),
                ("Scheduled", "content_plan_summary_view.scheduled"),
                ("Unscheduled", "content_plan_summary_view.unscheduled"),
                ("Target volume", "content_plan_summary_view.combined_volume"),
                ("By source", "content_plan_summary_view.sources"),
            ],
        ),
        SectionSpec(
            title="Content Plan — Work",
            intro="Row numbers match the targeting table.",
            table_source="content_plan",
            table_headers=["#", "Week", "Action", "Title", "Primary keyword", "Type", "Priority"],
            table_keys=["row", "week", "action", "title", "keyword", "content_type", "priority"],
            table_weights=[0.35, 0.4, 0.7, 1.5, 1.1, 0.7, 0.6],
            max_rows=200,
            row_fn=lambda r: [
                r.get("row"),
                r.get("week") or "—",
                r.get("work_label") or r.get("action"),
                r.get("title") or r.get("keyword"),
                r.get("primary_keyword") or r.get("keyword"),
                r.get("content_type"),
                r.get("priority"),
            ],
        ),
        SectionSpec(
            title="Content Plan — Targeting",
            intro="Same rows and numbering as the work table.",
            table_source="content_plan",
            table_headers=["#", "Volume", "Difficulty", "Intent", "Funnel", "Words", "URL", "Service"],
            table_keys=["row", "volume", "difficulty", "intent", "funnel", "est_words", "suggested_url", "service"],
            table_weights=[0.35, 0.55, 0.55, 0.7, 0.5, 0.45, 1.5, 0.8],
            max_rows=200,
            row_fn=lambda r: [
                r.get("row"),
                r.get("volume"),
                r.get("difficulty"),
                r.get("intent"),
                r.get("funnel"),
                r.get("est_words"),
                r.get("suggested_url") or r.get("existing_url") or r.get("url"),
                r.get("service"),
            ],
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
            title="Calendar",
            intro="Month pacing for the content plan. Each piece of work is listed once in the content plan, with its week.",
            table_source="content_calendar",
            table_headers=["Month", "Theme", "Pieces", "Actions", "Volume", "Services"],
            table_keys=["month", "theme", "pieces", "actions_label", "combined_volume", "services_label"],
            table_weights=[0.5, 0.9, 0.5, 1.3, 0.6, 1.2],
            max_rows=12,
            row_fn=lambda r: [
                r.get("month"),
                r.get("theme") or r.get("label"),
                r.get("pieces"),
                r.get("actions_label"),
                r.get("combined_volume"),
                r.get("services_label"),
            ],
        ),
        SectionSpec(
            title="Excluded",
            intro="Topics marked avoid. These are not pieces of work.",
            table_source="exclusions",
            table_headers=["Keyword or topic", "Reason"],
            table_keys=["keyword", "reason"],
            table_weights=[1.2, 2.2],
            max_rows=40,
            row_fn=lambda r: [
                r.get("keyword") or r.get("topic"),
                r.get("reason"),
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


def _cells(row: dict[str, Any]) -> list[Any]:
    return list(row.get("cells") or [])


def _keyword_cluster_sections() -> list[SectionSpec]:
    """Layered Keyword Clusters report (data from keyword_cluster_report)."""
    return [
        SectionSpec(
            title="Keyword Clustering Summary",
            alert_field="kc_accounting_alert",
            alert_label="Keyword accounting did not balance.",
            metrics=[
                ("Keywords in", "kc_summary.keywords_in"),
                ("Clusters", "kc_summary.clusters"),
                ("Clustered", "kc_summary.clustered"),
                ("Orphans", "kc_summary.orphans"),
                ("Accounting", "kc_summary.accounting"),
                ("Source", "kc_summary.source"),
                ("Content hubs", "kc_summary.hubs"),
                ("Size discipline", "kc_summary.size"),
            ],
            note_label="Verification:",
            note_fields=["kc_verification"],
        ),
        SectionSpec(
            title="Content Hubs",
            intro_field="kc_hubs_note",
            groups=GroupSpec(
                source="kc_hubs",
                headers=["Subtopic cluster", "Primary keyword", "Intent", "Stage", "Page pattern", "Volume"],
                weights=[1.4, 1.4, 0.9, 0.55, 0.95, 0.65],
            ),
        ),
        SectionSpec(
            title="Cluster Overview",
            table_source="kc_overview",
            table_headers=[
                "Cluster / URL", "Primary keyword", "Intent / stage",
                "Page pattern / content", "Size", "Volume", "Avg KD",
            ],
            table_keys=[],
            table_weights=[1.7, 1.35, 0.95, 1.25, 0.6, 0.65, 0.55],
            max_rows=60,
            row_fn=_cells,
        ),
        SectionSpec(
            title="Cluster Detail",
            intro_field="kc_details_note",
            groups=GroupSpec(
                source="kc_details",
                headers=["Keyword", "Role", "Volume", "KD", "Intent", "Stage", "Opp. score"],
                weights=[2.0, 0.8, 0.8, 0.45, 0.9, 0.55, 0.65],
            ),
        ),
        SectionSpec(
            title="Orphan Keywords",
            intro_field="kc_orphans_note",
            table_source="kc_orphans",
            table_headers=["Keyword", "Volume", "Intent", "Why it has no cluster"],
            table_keys=[],
            table_weights=[1.5, 0.7, 0.9, 2.4],
            max_rows=40,
            row_fn=_cells,
        ),
        SectionSpec(
            title="Content Roadmap",
            table_source="kc_roadmap",
            table_headers=["Priority", "Cluster", "Content type", "Target keyword", "Intent", "Stage", "Est. traffic"],
            table_keys=[],
            table_weights=[0.55, 1.4, 1.0, 1.3, 0.85, 0.5, 0.7],
            max_rows=20,
            row_fn=_cells,
        ),
        SectionSpec(
            title="Clustering Quality",
            intro_field="kc_quality_note",
            table_source="kc_quality",
            table_headers=["Issue", "Cluster", "What is wrong", "Risk"],
            table_keys=[],
            table_weights=[1.0, 1.3, 2.6, 0.8],
            max_rows=40,
            row_fn=_cells,
        ),
        SectionSpec(
            title="Clustering Provenance",
            metrics=[
                ("Grouped by", "kc_provenance.grouped_by"),
                ("Model clustering", "kc_provenance.model_clustering"),
                ("SERP check", "kc_provenance.serp_check"),
                ("Cluster names", "kc_provenance.naming"),
                ("Invented keywords rejected", "kc_provenance.invented_rejected"),
                ("Excluded in cleaning", "kc_provenance.excluded"),
            ],
            tables=[
                TableSpec(
                    title="Excluded during cleaning, by reason",
                    source="kc_exclusions",
                    headers=["Reason", "Keywords"],
                    keys=[],
                    weights=[3.0, 1.0],
                    row_fn=_cells,
                )
            ],
            bullets_source="kc_provenance_notes",
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
                # Surfaces the coverage-fallback warning when live keyword APIs
                # were unavailable, so estimated volumes in this report are not
                # mistaken for measured demand.
                ("Data quality", "keyword_data_quality.warning"),
            ],
        ),
        *_keyword_cluster_sections(),
        SectionSpec(
            title="Top Opportunities",
            table_source="opportunities",
            table_headers=["Keyword", "Volume", "KD", "Score", "Intent"],
            table_keys=["keyword", "volume", "difficulty", "opportunity_score", "intent"],
            table_weights=[1.4, 0.7, 0.5, 0.6, 0.8],
            max_rows=40,
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
            prepare_rows=_url_map_numbered,
            table_headers=[
                "#", "Lvl", "L1", "L2", "L3", "L4", "Current URL", "Proposed URL",
                "Status", "Page Type", "Priority",
            ],
            table_keys=[
                "row_no", "level", "l1_category", "l2_subcategory", "l3_subsubcategory",
                "l4_attribution", "current_url", "proposed_url", "status",
                "page_type", "priority",
            ],
            max_rows=60,
        ),
        SectionSpec(
            title="URL Mapping — Keywords & Opportunity",
            table_source="url_map_report.final_url_map",
            prepare_rows=_url_map_numbered,
            table_headers=[
                "#", "Primary Keyword", "Vol/mo", "CPC", "Secondary Keywords (5-10)",
                "Combined Vol", "Est. Products", "In Scope?", "Notes",
            ],
            table_keys=[
                "row_no", "primary_keyword", "search_volume", "cpc", "secondary_keywords_sheet",
                "combined_cluster_volume", "est_products", "in_scope", "notes",
            ],
            row_fn=lambda u: [
                u.get("row_no"),
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
        SectionSpec(
            title="URL Mapping — Decision & Evidence",
            intro=(
                "The same rows by number: what each row will do, the score and the signals it "
                "was based on, how confident the match is, the effort, the checks run against the "
                "site and the other proposals, the links and redirects, and the rules that fired."
            ),
            table_source="url_map_report.final_url_map",
            prepare_rows=_url_map_numbered,
            table_headers=_url_map_decision_headers(),
            table_keys=[],
            row_fn=_url_map_decision_row,
            max_rows=60,
        ),
    ]


def _url_map_numbered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services.url_map_report import numbered

    return numbered(rows)


def _url_map_decision_headers() -> list[str]:
    from app.services.url_map_report import DECISION_HEADERS

    return list(DECISION_HEADERS)


def _url_map_decision_row(entry: dict[str, Any]) -> list[Any]:
    from app.services.url_map_report import decision_row

    return decision_row(entry)


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
            table_headers=["Title", "Path", "Disposition", "Reason", "Keyword"],
            table_keys=["title", "path", "disposition", "reason", "keyword"],
            table_weights=[1.2, 1.0, 0.8, 1.3, 0.9],
            max_rows=40,
            row_fn=lambda r: [
                r.get("title"),
                r.get("path") or r.get("url"),
                r.get("disposition"),
                r.get("reason"),
                r.get("keyword") or r.get("ia_note"),
            ],
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
            row_fn=lambda r: [
                r.get("topic") or r.get("keyword") or r.get("query"),
                ", ".join(str(u) for u in r.get("urls") or [])
                if isinstance(r.get("urls"), list)
                else r.get("urls"),
                r.get("recommendation") or r.get("action"),
            ],
        ),
        SectionSpec(
            title="Handoffs",
            table_source="handoffs",
            table_headers=["Deliverable", "Receiving phase / owner"],
            table_keys=["item", "receiving"],
            table_weights=[1.5, 2.0],
        ),
    ]


# The twelve Phase 7 audit groups, in report order: (title, payload slug, extra tables).
_TECH_GROUP_EXTRAS: dict[str, list[TableSpec]] = {
    "on_page": [
        TableSpec("Sampled pages", "on_page_audit.pages",
                  ["Page", "Title", "H1s", "Meta description", "Headings", "Alt text", "Words"],
                  ["url", "title", "h1", "meta", "headings", "alt", "words"], [1.6, 0.7, 0.4, 0.8, 0.8, 0.6, 0.5]),
    ],
    "canonicalisation": [
        TableSpec("Canonical tags on sampled pages", "canonical_audit.pages", ["Page", "Canonical", "State"],
                  ["url", "canonical", "state"], [1.4, 1.4, 1.0]),
        TableSpec("Duplicate URL clusters", "canonical_audit.duplicate_clusters", ["Cluster", "Pages", "Count"],
                  ["cluster", "urls", "count"], [1.4, 2.2, 0.4]),
    ],
    "javascript_rendering": [
        TableSpec("Raw HTML vs browser-rendered (raw / rendered)", "js_rendering_audit.comparison",
                  ["Page", "Words", "Links", "H1", "Verdict"], ["url", "words", "links", "h1", "verdict"],
                  [1.4, 0.7, 0.7, 0.5, 1.0]),
    ],
    "performance": [
        TableSpec("Core Web Vitals and lab metrics (field = real users; lab = one controlled run)",
                  "performance_audit.metrics", ["Metric", "Mobile", "Desktop", "Rating"],
                  ["metric", "mobile", "desktop", "rating"], [1.6, 1.0, 1.0, 1.0]),
        TableSpec("Page weight (direct sample)", "performance_audit.page_weight",
                  ["Page", "HTML KB", "Scripts", "Render-blocking", "Third-party hosts", "Images"],
                  ["url", "html_kb", "scripts", "blocking", "third_party", "images"], [1.6, 0.6, 0.6, 0.8, 0.8, 0.6]),
    ],
    "structured_data": [
        TableSpec("Schema found per page", "structured_data_audit.pages", ["Page", "JSON-LD blocks", "Types", "Problems"],
                  ["url", "blocks", "types", "problems"], [1.2, 0.5, 1.4, 1.8]),
        TableSpec("Rich results the markup qualifies for", "structured_data_audit.eligible_rows", ["Rich result"],
                  ["result"], [1.0]),
    ],
    "security": [
        TableSpec("Response headers (homepage)", "security_audit.headers", ["Header", "Present"], ["header", "present"],
                  [2.0, 0.6]),
        TableSpec("WordPress exposure", "security_audit.wordpress_rows", ["Check", "Result"], ["check", "result"],
                  [1.4, 2.0]),
    ],
}
TECH_GROUP_SLUGS = (
    ("Crawlability", "crawlability"),
    ("Indexation", "indexation"),
    ("URL Structure", "url_structure"),
    ("Canonicalisation", "canonicalisation"),
    ("Sitemap", "sitemap"),
    ("Internal Linking", "internal_linking"),
    ("On-Page Technical SEO", "on_page"),
    ("JavaScript and Rendering", "javascript_rendering"),
    ("Performance (Core Web Vitals)", "performance"),
    ("Mobile and Usability", "mobile"),
    ("Structured Data", "structured_data"),
    ("Security", "security"),
)


def _tech_group_section(title: str, slug: str) -> SectionSpec:
    base = f"group.{slug}"
    return SectionSpec(
        title=title,
        intro_field=f"{base}.coverage_note",
        metrics=[
            ("Score", f"{base}.score_label"),
            ("Issues", f"{base}.issue_count"),
            ("Fields measured", f"{base}.fields_measured"),
            ("Not measured", f"{base}.fields_not_measured"),
        ],
        table_source=f"{base}.issues",
        table_headers=["Severity", "Issue", "Pages", "Examples", "Recommended fix"],
        table_keys=["severity", "issue", "affected", "examples", "fix"],
        table_weights=[0.6, 1.8, 0.4, 1.0, 1.7],
        max_rows=25,
        tables=[
            TableSpec("Checks run", f"{base}.checks", ["Check", "Result", "Detail", "URLs"],
                      ["check", "status", "detail", "urls"], [1.0, 0.6, 2.2, 1.2]),
            *_TECH_GROUP_EXTRAS.get(slug, []),
            TableSpec("Fields: checked vs not measured", f"{base}.fields", ["Field", "Status", "Note"],
                      ["field", "status", "note"], [1.0, 0.8, 2.6]),
        ],
    )


def _technical_seo_sections() -> list[SectionSpec]:
    return [
        SectionSpec(
            title="Audit Summary",
            metrics=[
                ("Overall score", "score"),
                ("Severity", "severity"),
                ("Coverage", "coverage_label"),
                ("Fields measured %", "audit_coverage.summary.coverage_pct"),
                ("Pages sampled", "page_sample.pages"),
                ("Phase 6 connected", "phase6_connected"),
            ],
            note_label="Data sources:",
            note_fields=["data_source_note"],
        ),
        SectionSpec(
            title="Quick Wins",
            intro="High-priority issues with a straightforward fix, most impactful first.",
            table_source="quick_wins",
            table_headers=["Priority", "Issue", "Fix", "Group", "Pages"],
            table_keys=["priority", "issue", "fix", "category", "affected"],
            table_weights=[0.6, 1.8, 1.8, 0.9, 0.4],
            max_rows=10,
        ),
        *[_tech_group_section(title, slug) for title, slug in TECH_GROUP_SLUGS],
        SectionSpec(
            title="Priority Backlog by Category",
            intro="Up to 50 actions, grouped by audit area, most severe first within each area.",
            table_source="priority_backlog",
            table_headers=["Category", "Priority", "Issue", "Fix", "Pages"],
            table_keys=["category", "priority", "issue", "fix", "affected"],
            table_weights=[0.9, 0.6, 1.7, 1.8, 0.4],
            max_rows=50,
        ),
        SectionSpec(
            title="IA Actions from Phase 6",
            table_source="ia_actions",
            table_headers=["Type", "Issue", "Fix", "Priority"],
            table_keys=["type", "issue", "fix", "priority"],
            table_weights=[0.6, 1.6, 1.8, 0.6],
            row_fn=lambda r: [
                r.get("type"),
                r.get("issue") or (f"{r.get('from')} → {r.get('to')}" if r.get("from") else None),
                r.get("fix") or (f"{r.get('status')} redirect" if r.get("from") else None),
                r.get("priority"),
            ],
        ),
        SectionSpec(
            title="Specialist Routing",
            table_source="specialist_routing",
            table_headers=["Area", "Route to", "Status"],
            table_keys=["finding_area", "route_to", "status"],
            table_weights=[1.8, 1.0, 1.0],
        ),
        SectionSpec(
            title="What We Checked",
            intro=(
                "Coverage of the twelve audit groups this pass. Not measured means no evidence was collected; "
                "it is never counted as a pass."
            ),
            metrics=[
                ("Fields", "audit_coverage.summary.fields"),
                ("Measured", "audit_coverage.summary.measured"),
                ("With issues", "audit_coverage.summary.with_issues"),
                ("Not measured", "audit_coverage.summary.not_measured"),
                ("Coverage %", "audit_coverage.summary.coverage_pct"),
            ],
            table_source="coverage_matrix",
            table_headers=["Group", "Fields", "Measured", "With issues", "Not measured", "What would complete it"],
            table_keys=["group", "fields", "measured", "with_issues", "not_measured", "needs"],
            table_weights=[1.1, 0.5, 0.6, 0.6, 0.7, 2.2],
            max_rows=12,
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
            table_headers=["Rank", "Name", "Website", "Tier", "Composite", "Threat"],
            table_keys=["rank", "name", "url", "tier_name", "composite", "future_threat"],
            table_weights=[0.4, 1.1, 1.3, 0.9, 0.6, 0.7],
        ),
        SectionSpec(
            title="Parameter Gaps",
            table_source="parameter_gaps",
            table_headers=["Parameter", "Client", "Top competitor", "Gap", "Action"],
            table_keys=["parameter", "client_score", "top_competitor", "gap", "action"],
            table_weights=[1.0, 0.6, 1.0, 0.5, 1.6],
            max_rows=40,
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
            max_rows=40,
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
        SectionSpec(
            title="SEO Audit Summary",
            intro_field="summary",
            metrics=[
                ("Overall score", "overall_score"),
                ("Score band", "score_band"),
                ("Pages analyzed", "pages_analyzed"),
            ],
        ),
        SectionSpec(
            title="Issues",
            table_source="issues",
            table_headers=["Issue", "Severity", "URL"],
            table_keys=["issue", "severity", "url"],
            table_weights=[1.4, 0.7, 1.4],
            row_fn=lambda r: [
                r.get("issue") or r.get("message") or r.get("title"),
                r.get("severity"),
                r.get("url") or r.get("ref"),
            ],
        ),
    ],
    "on_page_seo_report": [
        SectionSpec(title="On-Page SEO Summary", intro_field="summary"),
        SectionSpec(
            title="Page recommendations",
            table_source="pages",
            table_headers=["URL", "Title", "Score", "Keyword"],
            table_keys=["url", "title", "score", "keyword"],
            table_weights=[1.3, 1.0, 0.5, 1.7],
            row_fn=lambda r: [
                r.get("url") or r.get("path"),
                (r.get("title") or {}).get("after")
                if isinstance(r.get("title"), dict)
                else (r.get("title") or r.get("h1")),
                r.get("score") if r.get("score") is not None else r.get("current_score"),
                r.get("keyword") or r.get("fixes"),
            ],
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
            table_headers=["URL", "Title", "Keyword", "Status", "Note"],
            table_keys=["url", "title", "keyword", "status", "note"],
            table_weights=[1.2, 1.1, 0.9, 0.7, 1.2],
            row_fn=lambda r: [
                r.get("url"),
                r.get("title"),
                r.get("keyword"),
                r.get("status") or r.get("action"),
                r.get("note"),
            ],
        ),
    ],
    "readiness_score": [
        SectionSpec(
            title="Readiness Assessment",
            metrics=[("Overall", "overall"), ("Threshold", "threshold"), ("Ready for Phase 5", "ready_for_phase5")],
        ),
        SectionSpec(title="Phase scores", bullets_source="phase_scores"),
        SectionSpec(title="Missing items", bullets_source="missing"),
    ],
}


# Payload keys each report deliberately leaves out, with the reason. The section
# list says what a report shows; this says what it chose not to show, so deleting a
# section spec never brings the data back through the automatic sections. The keys
# stay in the payload because other consumers read them.
_PIPELINE = "Pipeline diagnostics for the app, not the client report."
_ECHOED = "Input carried from an earlier phase; that phase's report shows it."
_EXPORTED = "Raw working data; it is in the CSV / workbook export and the app."
_DUPLICATE = "Already shown by a curated section of this report."

REPORT_SUPPRESSED_KEYS: dict[str, dict[str, str]] = {
    "search_demand_report": {
        "clusters": "Keyword clusters have their own Keyword Clusters sections (kc_*).",
        "cluster_report": "Rendered as the Keyword Clusters sections (kc_*).",
        "topics": "Topic selection belongs to the content strategy report.",
        "topic_plan": "Topic selection belongs to the content strategy report.",
        "topic_selection_context": _PIPELINE,
        "timings": _PIPELINE,
        "skills_used": _PIPELINE,
        "providers_used": _PIPELINE,
        "provider_errors": _PIPELINE,
        "phase_pipeline": _PIPELINE,
        "keyword_pool_limits": _PIPELINE,
        "keyword_seeding": _PIPELINE,
        "keyword_cleaning": _PIPELINE,
        "relevance_audit": _PIPELINE,
        "live_site_scan": _PIPELINE,
        "competitor_page_stats": _PIPELINE,
        "sitemap_classification": _PIPELINE,
        "intent_balance": _PIPELINE,
        "products": _ECHOED,
        "services": _ECHOED,
        "competitors": _ECHOED,
        "competitor_names": _ECHOED,
        "competitor_domains": _ECHOED,
        "cdd_keywords": _ECHOED,
        "service_catalog": _ECHOED,
        "service_prioritization": _ECHOED,
        "keyword_dataset": _EXPORTED,
        "search_demand_analysis": _EXPORTED,
        "seed_clusters": _EXPORTED,
        "seed_keywords": _EXPORTED,
        "workbook": _EXPORTED,
        "competitor_page_keywords": _EXPORTED,
        "keyword_table": _DUPLICATE,
        "head_terms": _DUPLICATE,
        "strong_evergreen": _DUPLICATE,
        "best_opportunities": _DUPLICATE,
    },
    "content_strategy_report": {
        "workbook": _EXPORTED,
        "skills_used": _PIPELINE,
        "source_phase5_providers": _PIPELINE,
        "competitors": _ECHOED,
        "competitor_names": _ECHOED,
        "competitor_domains": _ECHOED,
        "selected_services": _ECHOED,
        "content_plan_summary": "Shown as Content Plan Summary (content_plan_summary_view).",
    },
    "competitor_landscape": {
        "target_business": _ECHOED,
        "discovery_sources": _PIPELINE,
    },
    "website_audit": {
        "tabs_run": _PIPELINE,
        "audit_ids": _PIPELINE,
    },
    "phase_validation_report": {
        "company_context_keys": _PIPELINE,
        "applicable_parameters": _PIPELINE,
    },
    "tracking_report": {
        "known_change_fields": "Form field definitions for the app's change dialog.",
    },
    "tracking_confirmation": {
        "known_change_fields": "Form field definitions for the app's change dialog.",
    },
}


def _resolve_blueprint(card_type: str, payload: dict[str, Any]) -> list[SectionSpec]:
    if card_type in REPORT_BLUEPRINTS:
        return REPORT_BLUEPRINTS[card_type]
    generic = _generic_phase_sections(card_type)
    if generic:
        return generic
    return _auto_blueprint(payload)


def _render_section(
    flow: list,
    n: int,
    spec: SectionSpec,
    payload: dict[str, Any],
    *,
    leading_rule: bool = False,
) -> bool:
    """Render one section. Returns False if section had no content."""
    blocks: list = []
    has_content = False

    if spec.alert_field and _get(payload, spec.alert_field):
        blocks.append(alert_block(spec.alert_label, str(_get(payload, spec.alert_field))))
        blocks.append(Spacer(1, 6))
        has_content = True

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
        if rows and spec.prepare_rows:
            rows = spec.prepare_rows(rows)
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

    if spec.groups:
        g = spec.groups
        for group in _rows(_get(payload, g.source)):
            rows = [r for r in group.get("rows") or [] if isinstance(r, list)]
            blocks.append(Spacer(1, 6))
            header = group_header(
                str(group.get("title") or ""),
                str(group.get("subtitle") or ""),
                str(group.get("detail") or ""),
                tone=group.get("tone"),
            )
            if rows:
                # A header never sits alone at the foot of a page.
                table = data_table_weighted(
                    g.headers, rows, g.weights or [1.0] * len(g.headers), max_rows=g.max_rows
                )
                blocks.append(KeepTogether([header, table]))
            else:
                blocks.append(header)
            if group.get("more"):
                blocks.append(body(f"…and {group['more']} more keyword(s) in this cluster."))
            has_content = True

    for extra in spec.tables:
        rows = _rows(_get(payload, extra.source))
        table_data = _table_rows(rows, extra.keys, extra.row_fn) if rows else []
        if table_data:
            blocks.append(Spacer(1, 4))
            blocks.append(note_block(extra.title, ""))
            blocks.append(
                data_table_weighted(
                    extra.headers,
                    table_data,
                    extra.weights or [1.0] * len(extra.headers),
                    max_rows=extra.max_rows,
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

    if leading_rule:
        flow.append(Spacer(1, 6))
        flow.append(section_rule())
        flow.append(Spacer(1, 8))
    flow.extend(header_band(f"{n}. {spec.title}"))
    flow.extend(blocks)
    flow.append(Spacer(1, 8))
    return True


AUTO_SECTION_LABEL = "Automatic section"
AUTO_SECTION_NOTE = (
    "Shown automatically: this data has no section in the report's design yet, so it "
    "is laid out generically. Curated sections above are unaffected."
)


def _covered_keys(blueprint: list[SectionSpec]) -> set[str]:
    covered = {s.table_source for s in blueprint if s.table_source}
    covered |= {s.groups.source for s in blueprint if s.groups}
    covered |= {t.source.split(".", 1)[0] for s in blueprint for t in s.tables}
    covered |= {s.bullets_source for s in blueprint if s.bullets_source}
    covered |= {s.numbered_source for s in blueprint if s.numbered_source}
    # A metrics path's top-level key (e.g. "funnel_balance.TOFU" -> "funnel_balance")
    # is already rendered explicitly — don't let it also fall through to auto-render.
    covered |= {path.split(".", 1)[0] for s in blueprint for _, path in s.metrics}
    # A dotted table source ("url_map_report.final_url_map") covers its top key too.
    covered |= {str(s.table_source).split(".", 1)[0] for s in blueprint if s.table_source}
    return covered


def suppressed_keys(card_type: str) -> frozenset[str]:
    """Payload keys this report type deliberately does not render."""
    return frozenset(REPORT_SUPPRESSED_KEYS.get(card_type, {}))


def auto_section_specs(
    card_type: str, payload: dict[str, Any], blueprint: list[SectionSpec]
) -> list[SectionSpec]:
    """Sections for payload keys the blueprint neither renders nor suppresses.

    The safety net for genuinely new data. A key a blueprint removed on purpose
    belongs in ``REPORT_SUPPRESSED_KEYS``; it never comes back through here.
    Every spec is titled and introduced as automatic, so a reader can tell it from
    a curated section.
    """
    covered = _covered_keys(blueprint) | suppressed_keys(card_type)
    specs: list[SectionSpec] = []
    for key, value in payload.items():
        if key in SKIP or key in SUMMARY_KEYS or key in covered:
            continue
        title = f"{AUTO_SECTION_LABEL}: {key.replace('_', ' ').title()}"
        if isinstance(value, list) and value:
            if isinstance(value[0], dict):
                headers, keys = _infer_columns(_rows(value))
                specs.append(
                    SectionSpec(
                        title=title,
                        intro=AUTO_SECTION_NOTE,
                        table_source=key,
                        table_headers=headers,
                        table_keys=keys,
                    )
                )
            elif isinstance(value[0], str):
                specs.append(SectionSpec(title=title, intro=AUTO_SECTION_NOTE, bullets_source=key))
        elif isinstance(value, dict) and value:
            scalar_items = [
                (k, str(v))
                for k, v in value.items()
                if v not in (None, "", [], {}) and not isinstance(v, (list, dict))
            ]
            if len(scalar_items) >= 2:
                specs.append(
                    SectionSpec(
                        title=title,
                        intro=AUTO_SECTION_NOTE,
                        metrics=[(k.replace("_", " ").title(), f"{key}.{k}") for k, _ in scalar_items[:8]],
                    )
                )
    return specs


def _spec_key(spec: SectionSpec) -> str:
    if spec.table_source:
        return spec.table_source
    if spec.bullets_source:
        return spec.bullets_source
    return spec.metrics[0][1].split(".", 1)[0] if spec.metrics else spec.title


def _append_auto_sections(
    flow: list,
    payload: dict[str, Any],
    blueprint: list[SectionSpec],
    section_num: int,
    *,
    card_type: str = "",
) -> int:
    rendered: list[str] = []
    for spec in auto_section_specs(card_type, payload, blueprint):
        if _render_section(flow, section_num + 1, spec, payload, leading_rule=True):
            section_num += 1
            rendered.append(_spec_key(spec))
    if rendered:
        # Loud on purpose: an automatic section is either new data that needs a
        # designed section, or a removed one that needs suppressing.
        log.warning("report_auto_sections_rendered", card_type=card_type or "(none)", keys=rendered)
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
        flow.extend(header_band(title, meta, level="report"))
    elif "Geo:" in meta:
        # The cover and running header already name the client and site.
        flow.append(body(f"Market: {meta.split('Geo:', 1)[1].strip()}"))
        flow.append(Spacer(1, 6))

    if payload.get("scorecard_line"):
        # One line, every audit group, before the narrative.
        flow.append(note_block("At a glance:", str(payload["scorecard_line"])))
        flow.append(Spacer(1, 4))
    summary = _summary_text(payload, client) or (
        f"Structured phase report for {client.get('name') or 'the client'}."
    )
    flow.append(summary_panel(summary, lead="Summary"))
    flow.append(Spacer(1, 12))

    blueprint = _resolve_blueprint(card_type, payload)
    section_num = 0
    for spec in blueprint:
        if _render_section(flow, section_num + 1, spec, payload, leading_rule=True):
            section_num += 1

    _append_auto_sections(flow, payload, blueprint, section_num, card_type=card_type)
