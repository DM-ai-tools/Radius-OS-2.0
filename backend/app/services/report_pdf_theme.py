"""Radius OS PDF theme — colours and reusable layout primitives."""

from __future__ import annotations

from typing import Any, Sequence

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
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

PAGE_W = 6.5 * inch  # A4 minus 0.75" margins


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
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
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
            fontSize=9,
            leading=13,
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
            textColor=colors.white,
            fontName="Helvetica-Bold",
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
    max_rows: int = 25,
) -> Table | Paragraph:
    st = styles()
    if not rows:
        return Paragraph("—", st["muted"])
    ncols = len(headers)
    total = sum(weights[:ncols]) or 1.0
    col_widths = [PAGE_W * (w / total) for w in weights[:ncols]]
    data: list[list] = [[Paragraph(esc(h), st["th"]) for h in headers]]
    for row in rows[:max_rows]:
        data.append(
            [
                Paragraph(esc(truncate(scalar(c), 140 if i < 2 else 100)), st["cell"])
                for i, c in enumerate(row[:ncols])
            ]
        )
    table = Table(data, colWidths=col_widths, repeatRows=1, splitByRow=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_DEEP),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD, colors.HexColor("#f7fafa")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def numbered_list(items: Sequence[str], *, max_items: int = 12) -> list:
    st = styles()
    out: list = []
    for i, item in enumerate(items[:max_items], 1):
        out.append(Paragraph(f"{i}. {esc(item)}", st["body"]))
    if len(items) > max_items:
        out.append(Paragraph(f"<i>…and {len(items) - max_items} more steps</i>", st["muted"]))
    return out


def header_band(title: str, subtitle: str = "") -> list:
    st = styles()
    flow: list = []
    band = Table(
        [[Paragraph(esc(title), st["cover_title"])]],
        colWidths=[PAGE_W],
    )
    band.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PRIMARY_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -1), 2, PRIMARY),
            ]
        )
    )
    flow.append(band)
    if subtitle:
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(esc(subtitle), st["muted"]))
    flow.append(Spacer(1, 10))
    return flow


def section_heading(text: str) -> Paragraph:
    return Paragraph(esc(text), styles()["section"])


def body(text: str) -> Paragraph:
    return Paragraph(esc(text), styles()["body"])


def metric_strip(metrics: Sequence[tuple[str, Any]]) -> Table:
    """Horizontal KPI strip like cs-funnel-strip."""
    st = styles()
    cells = []
    for label, value in metrics:
        cell = Table(
            [
                [Paragraph(esc(label), st["muted"])],
                [Paragraph(f"<b>{esc(scalar(value))}</b>", st["body"])],
            ],
            colWidths=[PAGE_W / max(len(metrics), 1) - 4],
        )
        cell.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PRIMARY_BG),
                    ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        cells.append(cell)
    row = Table([cells], colWidths=[PAGE_W / max(len(cells), 1)] * len(cells))
    row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return row


def data_table(headers: list[str], rows: list[list[Any]], *, max_rows: int = 25) -> Table | Paragraph:
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
                Paragraph(esc(truncate(scalar(c), 100 if i == 0 else 80)), st["cell"])
                for i, c in enumerate(row[:ncols])
            ]
        )
    table = Table(data, colWidths=[col_w] * ncols, repeatRows=1, splitByRow=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_DEEP),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD, colors.HexColor("#f7fafa")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def bullet_list(items: Sequence[str], *, max_items: int = 12) -> list:
    st = styles()
    out: list = []
    for item in items[:max_items]:
        out.append(Paragraph(f"• {esc(item)}", st["bullet"]))
    if len(items) > max_items:
        out.append(Paragraph(f"<i>…and {len(items) - max_items} more</i>", st["muted"]))
    return out


def cover_page(client: dict, report_title: str, exported_at: str = "") -> list:
    st = styles()
    flow: list = []
    accent = Table([[""]], colWidths=[PAGE_W], rowHeights=[6])
    accent.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PRIMARY)]))
    flow.append(accent)
    flow.append(Spacer(1, 24))
    flow.append(Paragraph("RADIUS OS", st["brand"]))
    flow.append(Paragraph(esc(report_title), st["cover_title"]))
    if client.get("name"):
        flow.append(Paragraph(esc(client["name"]), st["cover_sub"]))
    if client.get("primary_url"):
        flow.append(Paragraph(esc(client["primary_url"]), st["cover_sub"]))
    if client.get("industry"):
        flow.append(Paragraph(esc(client["industry"]), st["cover_sub"]))
    if exported_at:
        flow.append(Spacer(1, 8))
        flow.append(Paragraph(esc(exported_at), st["muted"]))
    flow.append(Spacer(1, 20))
    return flow
