"""Radius OS PDF theme — colours and reusable layout primitives."""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

# Match frontend CSS tokens (styles.css)
INK = colors.HexColor("#071c1f")
MUTED = colors.HexColor("#3d565a")
PRIMARY = colors.HexColor("#028090")
PRIMARY_DEEP = colors.HexColor("#03565f")
PRIMARY_BG = colors.HexColor("#e6f5f3")
LINE = colors.HexColor("#d8e0e1")
CARD = colors.HexColor("#ffffff")
AMBER_BG = colors.HexColor("#faeeda")
ZEBRA = colors.HexColor("#f7fafa")

PAGE_W = 6.5 * inch  # A4 minus 0.75" margins
MARGIN = 0.75 * inch

# Numbered section titles rendered while a document is being built, for the
# contents list on the cover. Set by report_pdf around flow construction.
_CONTENTS: ContextVar[list[str] | None] = ContextVar("report_contents", default=None)


def esc(text: Any) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def scalar(value: Any) -> str:
    if value is None or value == "":
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


def truncate(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "Brand",
            parent=base["Normal"],
            fontSize=9,
            textColor=PRIMARY,
            spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontSize=22,
            leading=26,
            textColor=INK,
            spaceAfter=10,
            fontName="Helvetica-Bold",
        ),
        "cover_sub": ParagraphStyle(
            "CoverSub",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=base["Heading2"],
            fontSize=11,
            leading=14,
            textColor=INK,
            spaceBefore=14,
            spaceAfter=8,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=9.5,
            leading=13.5,
            textColor=INK,
            spaceAfter=6,
        ),
        "muted": ParagraphStyle(
            "Muted",
            parent=base["Normal"],
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            leftIndent=12,
            bulletIndent=4,
            textColor=INK,
            spaceAfter=3,
        ),
        "cell": ParagraphStyle(
            "Cell",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            textColor=INK,
        ),
        "cell_bold": ParagraphStyle(
            "CellBold",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            textColor=INK,
            fontName="Helvetica-Bold",
        ),
        "th": ParagraphStyle(
            "TH",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            textColor=PRIMARY_DEEP,
            fontName="Helvetica-Bold",
        ),
        "band_title": ParagraphStyle(
            "SectionTitle",
            parent=base["Normal"],
            fontSize=15,
            leading=19,
            textColor=INK,
            fontName="Helvetica-Bold",
        ),
        "report_title": ParagraphStyle(
            "ReportOpener",
            parent=base["Normal"],
            fontSize=21,
            leading=25,
            textColor=INK,
            fontName="Helvetica-Bold",
        ),
        "metric_value": ParagraphStyle(
            "MetricValue",
            parent=base["Normal"],
            fontSize=13,
            leading=16,
            textColor=INK,
            fontName="Helvetica-Bold",
        ),
        "metric_value_small": ParagraphStyle(
            "MetricValueSmall",
            parent=base["Normal"],
            fontSize=9.5,
            leading=12,
            textColor=INK,
            fontName="Helvetica-Bold",
        ),
        "summary": ParagraphStyle(
            "Summary",
            parent=base["Normal"],
            fontSize=10.5,
            leading=15,
            textColor=INK,
        ),
        "subsection": ParagraphStyle(
            "Subsection",
            parent=base["Heading3"],
            fontSize=10,
            leading=13,
            textColor=INK,
            spaceBefore=10,
            spaceAfter=6,
            fontName="Helvetica-Bold",
        ),
    }


def numbered_section(number: int, title: str) -> Paragraph:
    return Paragraph(esc(f"{number}. {title}"), styles()["section"])


def subsection(title: str) -> Paragraph:
    return Paragraph(esc(title), styles()["subsection"])


def note_block(label: str, text: str) -> Paragraph:
    st = styles()
    return Paragraph(f"<b>{esc(label)}</b> {esc(text)}", st["body"])


def data_table_weighted(
    headers: list[str],
    rows: list[list[Any]],
    weights: list[float],
    *,
    max_rows: int = 40,
) -> Table | Paragraph:
    st = styles()
    if not rows:
        return Paragraph("—", st["muted"])
    ncols = len(headers)
    total = sum(weights[:ncols]) or 1.0
    col_widths = [PAGE_W * (w / total) for w in weights[:ncols]]
    data: list[list] = [[Paragraph(esc(h), st["th"]) for h in headers]]
    def _cell(c: Any) -> Paragraph:
        # A tuple is several lines in one cell; later lines are secondary.
        if isinstance(c, tuple):
            lines = [esc(truncate(scalar(x), 200)) for x in c if x not in (None, "")]
            first, rest = (lines[0], lines[1:]) if lines else ("—", [])
            text = first + "".join(f'<br/><font color="#3d565a">{x}</font>' for x in rest)
            return Paragraph(text, st["cell"])
        return Paragraph(esc(truncate(scalar(c), 200)), st["cell"])

    for row in rows[:max_rows]:
        data.append([_cell(c) for c in row[:ncols]])
    table = Table(data, colWidths=col_widths, repeatRows=1, splitByRow=1)
    table.setStyle(_table_style())
    return table


def _table_style() -> TableStyle:
    """Light header, hairline rows, no vertical rules: read across, not down."""
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_BG),
            ("LINEBELOW", (0, 0), (-1, 0), 1, PRIMARY),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, LINE),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD, ZEBRA]),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )


AMBER_RULE = colors.HexColor("#b26b00")


def alert_block(label: str, text: str) -> Table:
    """A finding the reader must not miss: amber panel with a strong left rule."""
    st = styles()
    cell = Paragraph(f"<b>{esc(label)}</b> {esc(text)}", st["body"])
    table = Table([[cell]], colWidths=[PAGE_W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), AMBER_BG),
                ("LINEBEFORE", (0, 0), (0, -1), 3, AMBER_RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def group_header(title: str, subtitle: str = "", detail: str = "", *, tone: str | None = None) -> Table:
    """Header for one block inside a section (a cluster, a hub). An amber tone
    marks blocks that need different handling from the rest."""
    st = styles()
    lines = [[Paragraph(f"<b>{esc(title)}</b>", st["subsection"])]]
    if subtitle:
        lines.append([Paragraph(esc(subtitle), st["body"])])
    if detail:
        lines.append([Paragraph(esc(truncate(detail, 400)), st["muted"])])
    table = Table(lines, colWidths=[PAGE_W])
    rule = AMBER_RULE if tone == "alert" else PRIMARY
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), AMBER_BG if tone == "alert" else PRIMARY_BG),
                ("LINEBEFORE", (0, 0), (0, -1), 3, rule),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def numbered_list(items: Sequence[str], *, max_items: int = 60) -> list:
    st = styles()
    out: list = []
    for i, item in enumerate(items[:max_items], 1):
        out.append(Paragraph(f"{i}. {esc(item)}", st["body"]))
    if len(items) > max_items:
        out.append(Paragraph(f"<i>…and {len(items) - max_items} more steps</i>", st["muted"]))
    return out


_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$", re.DOTALL)


def header_band(title: str, subtitle: str = "", *, level: str = "section") -> list:
    """A section heading: teal number, ink title, teal rule beneath.

    ``level="report"`` opens a whole report in a bundle (larger, heavier rule).
    Numbered section titles are collected for the cover's contents list.
    """
    st = styles()
    match = _NUMBERED.match(title or "")
    if level == "report":
        text = esc(title)
        style, rule = st["report_title"], 2.5
    elif match:
        number, rest = match.groups()
        text = f'<font color="#028090">{esc(number)}</font>&nbsp;&nbsp;&nbsp;{esc(rest)}'
        style, rule = st["band_title"], 1.2
        contents = _CONTENTS.get()
        if contents is not None:
            contents.append(f"{number}. {rest}")
    else:
        text = esc(title)
        style, rule = st["band_title"], 1.2
    heading = Table([[Paragraph(text, style)]], colWidths=[PAGE_W])
    heading.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), rule, PRIMARY),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    flow: list = [heading]
    if subtitle:
        flow.append(Spacer(1, 5))
        flow.append(Paragraph(esc(subtitle), st["muted"]))
    flow.append(Spacer(1, 10))
    return flow


def summary_panel(text: str, *, lead: str = "") -> Table:
    """The executive summary: mist panel, teal left rule, slightly larger type."""
    st = styles()
    rows = []
    if lead:
        rows.append([Paragraph(f"<b>{esc(lead)}</b>", st["body"])])
    rows.append([Paragraph(esc(text), st["summary"])])
    panel = Table(rows, colWidths=[PAGE_W])
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PRIMARY_BG),
                ("LINEBEFORE", (0, 0), (0, -1), 3, PRIMARY),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return panel


def section_heading(text: str) -> Paragraph:
    return Paragraph(esc(text), styles()["section"])


def body(text: str) -> Paragraph:
    return Paragraph(esc(text), styles()["body"])


# A KPI cell holds a short figure. Agent payloads occasionally route a whole
# blob through here (a page-inventory CSV arrived as a "metric"), and in a
# narrow column that wraps into thousands of points of height. A single-row
# table cannot be split across pages, so reportlab aborts the entire export
# with LayoutError. Bound both axes: how many columns share a row, and how
# much text a cell may carry.
MAX_METRIC_COLS = 4
MAX_METRIC_CHARS = 120


def metric_strip(metrics: Sequence[tuple[str, Any]]) -> Table:
    """Key figures: value over label, on mist, with a teal rule above each.

    Wraps onto further rows past ``MAX_METRIC_COLS`` and truncates values, so a
    blob routed here cannot grow a single row taller than a page.
    """
    st = styles()
    per_row = max(1, min(MAX_METRIC_COLS, len(metrics) or 1))
    cell_w = PAGE_W / per_row

    def _cell(label: Any, value: Any) -> Table:
        text = truncate(scalar(value), MAX_METRIC_CHARS)
        style = st["metric_value"] if len(text) <= 14 else st["metric_value_small"]
        cell = Table(
            [
                [Paragraph(esc(text), style)],
                [Paragraph(esc(truncate(scalar(label), 60)), st["muted"])],
            ],
            colWidths=[cell_w - 6],
        )
        cell.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PRIMARY_BG),
                    ("LINEABOVE", (0, 0), (-1, 0), 2, PRIMARY),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (0, 0), 7),
                    ("BOTTOMPADDING", (0, 0), (0, 0), 1),
                    ("TOPPADDING", (0, 1), (0, 1), 0),
                    ("BOTTOMPADDING", (0, 1), (0, 1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return cell

    cells = [_cell(label, value) for label, value in metrics]
    # Chunk into rows of at most per_row, padding the last row so every row has
    # the same column count (reportlab requires a rectangular grid).
    grid: list[list[Any]] = []
    for start in range(0, len(cells), per_row):
        chunk = cells[start : start + per_row]
        chunk += [""] * (per_row - len(chunk))
        grid.append(chunk)
    if not grid:
        grid = [[""] * per_row]

    row = Table(grid, colWidths=[cell_w] * per_row, splitByRow=1)
    row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return row


def data_table(headers: list[str], rows: list[list[Any]], *, max_rows: int = 40) -> Table | Paragraph:
    st = styles()
    if not rows:
        return Paragraph("—", st["muted"])
    ncols = len(headers)
    col_w = PAGE_W / ncols
    data: list[list] = [
        [Paragraph(esc(h), st["th"]) for h in headers],
    ]
    for row in rows[:max_rows]:
        data.append(
            [
                Paragraph(esc(truncate(scalar(c), 200)), st["cell"])
                for i, c in enumerate(row[:ncols])
            ]
        )
    table = Table(data, colWidths=[col_w] * ncols, repeatRows=1, splitByRow=1)
    table.setStyle(_table_style())
    return table


def section_rule() -> Spacer:
    """Space between consecutive sections. Each heading carries its own rule,
    so a second line here would only double it."""
    return Spacer(1, 10)


def bullet_list(items: Sequence[str], *, max_items: int = 60) -> list:
    st = styles()
    out: list = []
    for item in items[:max_items]:
        out.append(Paragraph(f"• {esc(item)}", st["bullet"]))
    if len(items) > max_items:
        out.append(Paragraph(f"<i>…and {len(items) - max_items} more</i>", st["muted"]))
    return out


def cover_page(client: dict, report_title: str, exported_at: str = "") -> list:
    """Flowable cover (kept for callers that build their own document)."""
    st = styles()
    flow: list = [Paragraph("Radius OS", st["brand"]), Paragraph(esc(report_title), st["cover_title"])]
    for key in ("name", "primary_url", "industry"):
        if client.get(key):
            flow.append(Paragraph(esc(client[key]), st["cover_sub"]))
    if exported_at:
        flow.append(Paragraph(esc(exported_at), st["muted"]))
    flow.append(Spacer(1, 20))
    return flow


# ---------------------------------------------------------------- page drawing

PAGE_H = 11.69 * inch  # A4
FULL_W = 8.27 * inch


def collect_contents() -> tuple[list[str], Any]:
    """Start collecting numbered section titles; returns (list, reset token)."""
    contents: list[str] = []
    return contents, _CONTENTS.set(contents)


def stop_collecting(token: Any) -> None:
    _CONTENTS.reset(token)


def draw_cover(
    canv: Any,
    *,
    title: str,
    subtitle: str = "",
    client_name: str = "",
    client_url: str = "",
    prepared: str = "",
    contents: Sequence[str] = (),
    contents_label: str = "In this report",
) -> None:
    """The cover: a deep-teal block over the top half, details and contents below."""
    st = styles()
    block_h = PAGE_H * 0.52
    canv.saveState()
    canv.setFillColor(PRIMARY_DEEP)
    canv.rect(0, PAGE_H - block_h, FULL_W, block_h, stroke=0, fill=1)
    # A thin teal band where the block meets the page, the one accent.
    canv.setFillColor(PRIMARY)
    canv.rect(0, PAGE_H - block_h - 5, FULL_W, 5, stroke=0, fill=1)

    canv.setFillColor(colors.HexColor("#9fd8d2"))
    canv.setFont("Helvetica-Bold", 10)
    canv.drawString(MARGIN, PAGE_H - MARGIN - 4, "Radius OS")

    white_title = ParagraphStyle(
        "CoverBlockTitle", parent=st["cover_title"], fontSize=30, leading=35, textColor=colors.white, alignment=0
    )
    white_sub = ParagraphStyle("CoverBlockSub", parent=st["cover_sub"], fontSize=12, leading=16, textColor=colors.HexColor("#cfeeea"))
    y = PAGE_H - block_h + 0.75 * inch
    paras = []
    if client_url:
        paras.append(Paragraph(esc(client_url), white_sub))
    if client_name:
        paras.append(Paragraph(f"<b>{esc(client_name)}</b>", ParagraphStyle("CoverClient", parent=white_sub, fontSize=13, textColor=colors.white)))
    if subtitle:
        paras.append(Paragraph(esc(subtitle), white_sub))
    paras.append(Paragraph(esc(title), white_title))
    for p in paras:
        _w, h = p.wrap(PAGE_W, PAGE_H)
        p.drawOn(canv, MARGIN, y)
        y += h + 6

    # Details below the block.
    y = PAGE_H - block_h - 0.75 * inch
    label = ParagraphStyle("CoverLabel", parent=st["muted"], fontSize=8.5, leading=11)
    value = ParagraphStyle("CoverValue", parent=st["body"], fontSize=10.5, leading=14, spaceAfter=0)
    details = [("Prepared for", client_name), ("Website", client_url), ("Date", prepared)]
    col_w = PAGE_W / 3
    x = MARGIN
    for lab, val in details:
        if not val:
            continue
        lp, vp = Paragraph(esc(lab), label), Paragraph(esc(val), value)
        lp.wrap(col_w - 10, 40)
        lp.drawOn(canv, x, y)
        _w, vh = vp.wrap(col_w - 10, 60)
        vp.drawOn(canv, x, y - vh - 2)
        x += col_w
    y -= 0.75 * inch

    if contents:
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.6)
        canv.line(MARGIN, y + 10, MARGIN + PAGE_W, y + 10)
        head = Paragraph(f"<b>{esc(contents_label)}</b>", ParagraphStyle("CoverContentsHead", parent=st["body"], textColor=PRIMARY_DEEP))
        _w, hh = head.wrap(PAGE_W, 40)
        head.drawOn(canv, MARGIN, y - hh)
        y -= hh + 6
        item = ParagraphStyle("CoverContentsItem", parent=st["body"], fontSize=9.5, leading=13, spaceAfter=0)
        shown = list(contents)[:16]
        half = (len(shown) + 1) // 2 if len(shown) > 8 else len(shown)
        for col, chunk in enumerate((shown[:half], shown[half:])):
            yy = y
            for entry in chunk:
                p = Paragraph(esc(entry), item)
                _w, ph = p.wrap(PAGE_W / 2 - 12, 40)
                yy -= ph
                p.drawOn(canv, MARGIN + col * (PAGE_W / 2), yy)
                yy -= 2
        if len(contents) > 16:
            more = Paragraph(f"…and {len(contents) - 16} more sections", st["muted"])
            more.wrap(PAGE_W, 20)
            more.drawOn(canv, MARGIN, MARGIN + 30)

    canv.setFillColor(MUTED)
    canv.setFont("Helvetica", 8)
    canv.drawString(MARGIN, MARGIN - 18, "Prepared with Radius OS. Figures come from the data providers named in each section.")
    canv.restoreState()


def draw_page_frame(canv: Any, *, left: str, right: str, footer: str) -> None:
    """Running header and footer on every page after the cover."""
    canv.saveState()
    top = PAGE_H - MARGIN + 22
    canv.setFont("Helvetica-Bold", 8)
    canv.setFillColor(PRIMARY_DEEP)
    canv.drawString(MARGIN, top, truncate(left, 70))
    canv.setFont("Helvetica", 8)
    canv.setFillColor(MUTED)
    canv.drawRightString(MARGIN + PAGE_W, top, truncate(right, 50))
    canv.setStrokeColor(LINE)
    canv.setLineWidth(0.5)
    canv.line(MARGIN, top - 6, MARGIN + PAGE_W, top - 6)
    canv.drawString(MARGIN, MARGIN - 28, truncate(footer, 90))
    canv.restoreState()


class NumberedCanvas(rl_canvas.Canvas):
    """Adds "Page X of Y" to every page except the cover, once Y is known."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_pages: list[dict] = []

    def showPage(self) -> None:
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            if self._pageNumber > 1:
                self.saveState()
                self.setFont("Helvetica", 8)
                self.setFillColor(MUTED)
                self.drawRightString(MARGIN + PAGE_W, MARGIN - 28, f"Page {self._pageNumber} of {total}")
                self.restoreState()
            super().showPage()
        super().save()
