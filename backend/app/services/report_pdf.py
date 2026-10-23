"""Render structured phase reports as a downloadable PDF."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.report_export import resolve_report_title
from app.services.report_pdf_document import render_document_report
from app.services.report_pdf_site_architecture import render_site_architecture_blueprint
from app.services.report_pdf_theme import (
    NumberedCanvas,
    collect_contents,
    draw_cover,
    draw_page_frame,
    stop_collecting,
)

SPECIAL_RENDERERS = {
    "site_architecture_blueprint": render_site_architecture_blueprint,
}
DEFAULT_RENDERER = render_document_report

SKIP_KEYS = frozenset(
    {
        "card_type",
        "agent_key",
        "actions",
        "required_role",
        "event_type",
        "type",
    }
)

MAX_CELL_CHARS = 300
MAX_TABLE_CELL_CHARS = 200
MAX_LIST_ITEMS = 60
MAX_TABLE_ROWS = 40
MAX_DICT_FIELDS = 8
PREFERRED_LIST_KEYS = (
    "title",
    "name",
    "label",
    "topic",
    "keyword",
    "url",
    "path",
    "status",
    "priority",
    "score",
    "volume",
    "difficulty",
    "disposition",
    "action",
    "type",
    "phase",
)


def _cell_text(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return _truncate(json.dumps(value, default=str), MAX_TABLE_CELL_CHARS)
    return _truncate(_scalar_text(value), MAX_TABLE_CELL_CHARS)


def _dict_bullet(item: dict[str, Any]) -> str:
    keys = [k for k in PREFERRED_LIST_KEYS if k in item and k not in SKIP_KEYS]
    if not keys:
        keys = [k for k in item if k not in SKIP_KEYS and _is_scalar(item.get(k))][:MAX_DICT_FIELDS]
    parts: list[str] = []
    for k in keys[:MAX_DICT_FIELDS]:
        parts.append(f"{_label(k)}: {_cell_text(item.get(k))}")
    if not parts:
        return _truncate(json.dumps(item, default=str), MAX_CELL_CHARS)
    return " · ".join(parts)


def _esc(text: Any) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _truncate(text: str, limit: int = MAX_CELL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.1f}"
    return str(value)


def _label(key: str) -> str:
    return key.replace("_", " ").strip().title()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    ink = colors.HexColor("#071c1f")
    muted = colors.HexColor("#3d565a")
    primary_deep = colors.HexColor("#03565f")
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontSize=22,
            spaceAfter=14,
            textColor=ink,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["Normal"],
            fontSize=10,
            textColor=muted,
            spaceAfter=8,
        ),
        "h1": ParagraphStyle(
            "SectionH1",
            parent=base["Heading1"],
            fontSize=16,
            spaceBefore=16,
            spaceAfter=10,
            textColor=ink,
            fontName="Helvetica-Bold",
        ),
        "h2": ParagraphStyle(
            "SectionH2",
            parent=base["Heading2"],
            fontSize=11,
            spaceBefore=10,
            spaceAfter=6,
            textColor=primary_deep,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            leftIndent=14,
            bulletIndent=6,
            spaceAfter=2,
        ),
        "toc": ParagraphStyle(
            "TOC",
            parent=base["Normal"],
            fontSize=10,
            spaceAfter=4,
        ),
        "cover_center": ParagraphStyle(
            "CoverCenter",
            parent=base["Title"],
            fontSize=26,
            alignment=TA_CENTER,
            spaceAfter=20,
            textColor=ink,
            fontName="Helvetica-Bold",
        ),
    }


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    body = _styles()["body"]
    data = [
        [Paragraph(_esc(k), body), Paragraph(_esc(_truncate(v, MAX_CELL_CHARS)), body)]
        for k, v in rows
    ]
    table = Table(data, colWidths=[1.8 * inch, 4.7 * inch], repeatRows=0, splitByRow=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e6f5f3")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#03565f")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8e0e1")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _render_dict_list(flow: list, items: list[dict[str, Any]]) -> None:
    styles = _styles()
    for item in items[:MAX_LIST_ITEMS]:
        if not isinstance(item, dict):
            flow.append(Paragraph(f"• {_esc(_scalar_text(item))}", styles["bullet"]))
            continue
        flow.append(Paragraph(f"• {_esc(_dict_bullet(item))}", styles["bullet"]))
    if len(items) > MAX_LIST_ITEMS:
        flow.append(
            Paragraph(f"<i>…and {len(items) - MAX_LIST_ITEMS} more items</i>", styles["body"])
        )
    flow.append(Spacer(1, 4))


def _render_value(flow: list, value: Any, depth: int = 0) -> None:
    styles = _styles()
    if depth > 4:
        flow.append(Paragraph(_esc(_truncate(json.dumps(value, default=str))), styles["body"]))
        return

    if _is_scalar(value):
        flow.append(Paragraph(_esc(_scalar_text(value)), styles["body"]))
        return

    if isinstance(value, list):
        if not value:
            flow.append(Paragraph("—", styles["body"]))
            return
        if all(isinstance(x, dict) for x in value):
            _render_dict_list(flow, value)
            if len(value) > MAX_TABLE_ROWS:
                flow.append(
                    Paragraph(
                        f"<i>…and {len(value) - MAX_TABLE_ROWS} more rows</i>",
                        styles["body"],
                    )
                )
            return
        for i, item in enumerate(value[:MAX_LIST_ITEMS]):
            if isinstance(item, dict):
                flow.append(Paragraph(f"• {_esc(_dict_bullet(item))}", styles["bullet"]))
            else:
                flow.append(Paragraph(f"• {_esc(_scalar_text(item))}", styles["bullet"]))
        if len(value) > MAX_LIST_ITEMS:
            flow.append(
                Paragraph(f"<i>…and {len(value) - MAX_LIST_ITEMS} more items</i>", styles["body"])
            )
        flow.append(Spacer(1, 4))
        return

    if isinstance(value, dict):
        scalar_rows: list[tuple[str, str]] = []
        nested: list[tuple[str, Any]] = []
        for k, v in value.items():
            if k in SKIP_KEYS:
                continue
            if _is_scalar(v):
                scalar_rows.append((_label(k), _truncate(_scalar_text(v), MAX_CELL_CHARS)))
            else:
                nested.append((k, v))
        if scalar_rows:
            flow.append(_kv_table(scalar_rows))
            flow.append(Spacer(1, 6))
        for k, v in nested:
            flow.append(Paragraph(_esc(_label(k)), styles["h2"]))
            _render_value(flow, v, depth + 1)
        return


def _render_report_section(
    flow: list,
    report: dict,
    client: dict | None = None,
    *,
    show_title: bool = True,
) -> None:
    styles = _styles()
    payload = report.get("payload") or {}
    if not isinstance(payload, dict):
        flow.append(Paragraph(_esc(str(payload)), styles["body"]))
        return

    card_type = str(payload.get("card_type") or report.get("card_type") or "")
    renderer = SPECIAL_RENDERERS.get(card_type, DEFAULT_RENDERER)
    renderer(flow, report, client or {}, show_title=show_title)


def _prepared_label(bundle: dict) -> str:
    exported = bundle.get("exported_at") or ""
    if not exported:
        return datetime.now(UTC).strftime("%d %B %Y")
    try:
        return datetime.fromisoformat(str(exported).replace("Z", "+00:00")).strftime("%d %B %Y")
    except ValueError:
        return str(exported)


def build_reports_pdf(bundle: dict, *, single: bool = False) -> bytes:
    """Build a PDF containing all client reports, or one report when single=True.

    Page one is a drawn cover (title, client, date, contents); every later page
    carries a running header and a "Page X of Y" footer.
    """
    buf = BytesIO()
    client = bundle.get("client") or {}
    reports: list[dict] = bundle.get("reports") or []
    styles = _styles()
    client_name = str(client.get("name") or "Client")
    single_title = resolve_report_title(reports[0], client) if single and reports else ""
    doc_title = single_title if single_title else f"{client_name}: phase reports"

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=doc_title,
        author="Radius OS",
    )

    # Page one is drawn by draw_cover; the flow starts on page two.
    flow: list = [Spacer(1, 1), PageBreak()]
    contents, token = collect_contents()
    try:
        if single and reports:
            _render_report_section(flow, reports[0], client, show_title=False)
        else:
            for i, report in enumerate(reports):
                if i > 0:
                    flow.append(PageBreak())
                _render_report_section(flow, report, client, show_title=True)
    finally:
        stop_collecting(token)

    if not reports:
        flow.append(Paragraph("No reports generated yet.", styles["body"]))
        flow.append(
            Paragraph(
                "Run discovery, tracking, website audit, and downstream phases in chat, "
                "then download again.",
                styles["body"],
            )
        )

    if single and reports:
        cover_contents = contents
        contents_label = "In this report"
    else:
        cover_contents = [f"{i}. {resolve_report_title(r, client)}" for i, r in enumerate(reports, 1)]
        contents_label = f"{len(reports)} phase reports in this pack"
    readiness = (bundle.get("phase_statuses") or {}).get("overall_readiness_score")
    subtitle = str(client.get("industry") or "")
    if not single and readiness is not None:
        subtitle = f"Readiness {readiness:.0f}%" + (f", {subtitle}" if subtitle else "")

    def _first(canv, _doc) -> None:
        draw_cover(
            canv,
            title=single_title or "Phase reports",
            subtitle=subtitle,
            client_name=client_name,
            client_url=str(client.get("primary_url") or ""),
            prepared=_prepared_label(bundle),
            contents=cover_contents,
            contents_label=contents_label,
        )

    def _later(canv, _doc) -> None:
        draw_page_frame(
            canv,
            left=doc_title,
            right=str(client.get("primary_url") or client_name),
            footer=f"Prepared with Radius OS for {client_name}",
        )

    doc.build(flow, onFirstPage=_first, onLaterPages=_later, canvasmaker=NumberedCanvas)
    return buf.getvalue()
