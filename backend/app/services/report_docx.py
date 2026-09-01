"""Render structured phase reports as a downloadable Word (.docx) document.

Reuses the same section blueprints, field normalization, and title resolution
as report_pdf.py / report_pdf_document.py — a blueprint fix (new section, new
column, a dropped-field fix) should apply to both formats without being
duplicated here. Only the actual document-building calls are format-specific.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from app.services.report_export import resolve_report_title
from app.services.report_pdf_document import (
    SectionSpec,
    _get,
    _meta_line,
    _resolve_blueprint,
    _rows,
    _summary_text,
    _table_rows,
)
from app.services.report_pdf_normalize import normalize_report_payload

INK = RGBColor(0x07, 0x1C, 0x1F)
MUTED = RGBColor(0x3D, 0x56, 0x5A)
PRIMARY = RGBColor(0x03, 0x56, 0x5F)

MAX_CELL_CHARS = 300


def _cell_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 1] + "…"


def _add_muted_paragraph(doc: Document, text: str, *, size: int = 10, center: bool = False) -> None:
    p = doc.add_paragraph()
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.font.color.rgb = MUTED


def _add_table(doc: Document, headers: list[str], rows: list[list[Any]]) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    try:
        table.style = "Light Grid Accent 1"
    except KeyError:
        table.style = "Table Grid"
    header_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        header_cells[i].text = h
        for run in header_cells[i].paragraphs[0].runs:
            run.font.bold = True
            run.font.size = Pt(9)
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row[: len(headers)]):
            cells[i].text = _cell_text(val)
            for run in cells[i].paragraphs[0].runs:
                run.font.size = Pt(9)


def _render_docx_section(doc: Document, section_num: int, spec: SectionSpec, payload: dict[str, Any]) -> bool:
    has_content = False
    heading_added = False

    def _ensure_heading() -> None:
        nonlocal heading_added
        if not heading_added:
            h = doc.add_heading(f"{section_num}. {spec.title}", level=2)
            for run in h.runs:
                run.font.color.rgb = PRIMARY
            heading_added = True

    intro = spec.intro
    if not intro and spec.intro_field:
        val = _get(payload, spec.intro_field)
        intro = str(val) if val is not None else None
    if intro:
        _ensure_heading()
        doc.add_paragraph(str(intro))
        has_content = True

    if spec.metrics:
        metrics = [(label, _get(payload, path)) for label, path in spec.metrics]
        metrics = [(label, val) for label, val in metrics if val is not None and val != ""]
        if metrics:
            _ensure_heading()
            p = doc.add_paragraph()
            p.add_run(" · ".join(f"{label}: {val}" for label, val in metrics)).font.size = Pt(10)
            has_content = True

    if spec.table_source:
        rows = _rows(_get(payload, spec.table_source))
        if rows:
            keys = spec.table_keys or []
            headers = spec.table_headers or [k.replace("_", " ").title() for k in keys]
            table_rows = _table_rows(rows, keys, spec.row_fn)
            if table_rows:
                _ensure_heading()
                _add_table(doc, headers, table_rows[: spec.max_rows])
                if len(table_rows) > spec.max_rows:
                    _add_muted_paragraph(doc, f"…and {len(table_rows) - spec.max_rows} more rows")
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
                            f"{k}: {v}" for k, v in x.items() if not isinstance(v, (list, dict))
                        )
                    )
        if items:
            _ensure_heading()
            for item in items:
                doc.add_paragraph(item, style="List Bullet")
            has_content = True

    if spec.numbered_source:
        raw = _get(payload, spec.numbered_source)
        if isinstance(raw, list) and raw:
            _ensure_heading()
            for item in raw:
                doc.add_paragraph(str(item), style="List Number")
            has_content = True

    if spec.note_fields:
        bits = [str(_get(payload, f)) for f in spec.note_fields if _get(payload, f)]
        if bits:
            _ensure_heading()
            note = doc.add_paragraph()
            note.add_run(f"{spec.note_label or 'Note:'} ").font.bold = True
            note.add_run(" ".join(bits))
            has_content = True

    return has_content


def _render_docx_report(doc: Document, report: dict, client: dict, *, show_title: bool = True) -> None:
    raw_payload = report.get("payload") if isinstance(report.get("payload"), dict) else {}
    card_type = str(raw_payload.get("card_type") or report.get("card_type") or "")
    payload = normalize_report_payload(card_type, raw_payload)
    title = resolve_report_title(report, client)
    meta = _meta_line(payload, client)

    if show_title:
        doc.add_heading(title, level=1)
    if meta:
        _add_muted_paragraph(doc, meta)

    doc.add_heading("Executive Summary", level=2)
    summary = _summary_text(payload, client)
    doc.add_paragraph(summary or f"Structured phase report for {client.get('name') or 'the client'}.")

    blueprint = _resolve_blueprint(card_type, payload)
    section_num = 0
    for spec in blueprint:
        if _render_docx_section(doc, section_num + 1, spec, payload):
            section_num += 1


def build_reports_docx(bundle: dict, *, single: bool = False) -> bytes:
    """Build a Word document containing all client reports, or one when single=True."""
    doc = Document()
    client = bundle.get("client") or {}
    reports: list[dict] = bundle.get("reports") or []

    if single and reports:
        report = reports[0]
        title = resolve_report_title(report, client)
        doc.add_heading("Radius OS", level=3)
        h = doc.add_heading(title, level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if client.get("primary_url"):
            _add_muted_paragraph(doc, str(client["primary_url"]), center=True)
        exported = bundle.get("exported_at") or ""
        if exported:
            try:
                dt = datetime.fromisoformat(str(exported).replace("Z", "+00:00"))
                _add_muted_paragraph(doc, f"Exported {dt.strftime('%d %B %Y')}", center=True)
            except ValueError:
                _add_muted_paragraph(doc, f"Exported {exported}", center=True)
        doc.add_page_break()
        _render_docx_report(doc, report, client, show_title=False)
    else:
        doc.add_heading("Radius OS", level=3)
        cover_title = f"{client.get('name') or 'Client'} — Phase Reports"
        h = doc.add_heading(cover_title, level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if client.get("primary_url"):
            _add_muted_paragraph(doc, str(client["primary_url"]), center=True)
        if client.get("industry"):
            _add_muted_paragraph(doc, str(client["industry"]), center=True)
        exported = bundle.get("exported_at") or ""
        if exported:
            try:
                dt = datetime.fromisoformat(str(exported).replace("Z", "+00:00"))
                _add_muted_paragraph(doc, f"Exported {dt.strftime('%d %B %Y')}", center=True)
            except ValueError:
                _add_muted_paragraph(doc, f"Exported {exported}", center=True)
        _add_muted_paragraph(doc, f"{len(reports)} phase reports", center=True)
        readiness = (bundle.get("phase_statuses") or {}).get("overall_readiness_score")
        if readiness is not None:
            _add_muted_paragraph(doc, f"Readiness: {readiness:.0f}%", center=True)
        doc.add_page_break()

        doc.add_heading("Contents", level=1)
        for i, report in enumerate(reports, 1):
            doc.add_paragraph(f"{i}. {resolve_report_title(report, client)}")
        doc.add_page_break()

        for i, report in enumerate(reports):
            if i > 0:
                doc.add_page_break()
            _render_docx_report(doc, report, client, show_title=True)

    if not reports:
        doc.add_paragraph("No reports generated yet.")
        doc.add_paragraph(
            "Run discovery, tracking, website audit, and downstream phases in chat — "
            "then download again."
        )

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
