"""Merge Context.dev computed styles with the local CSS walk.

The model never sees a UI kit. It gets a measured token sheet. When a value
was not read, the sheet says NOT AVAILABLE — a guessed palette looks correct
and is the failure nobody catches.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.integrations import context_dev
from app.logging_config import get_logger
from app.services.design_tokens import design_markdown_logo, extract_design_tokens

log = get_logger("design_capture")

DESIGN_CAPTURE_VERSION = 3


def design_source_url(
    *,
    existing_page_url: str = "",
    parent_pillar_page_url: str = "",
    reference_design_source: str = "",
    client_website_url: str = "",
    profile_website_url: str = "",
) -> str:
    """Preference order for the page whose design the new content must wear."""
    for candidate in (
        existing_page_url,
        parent_pillar_page_url,
        reference_design_source,
        client_website_url,
        profile_website_url,
    ):
        url = str(candidate or "").strip()
        if url.startswith("http"):
            return url
    return ""


def _quote(value: str) -> str:
    text = str(value or "").replace('"', "")
    return f'"{text}"' if text else '"NOT AVAILABLE"'


def _type_line(node: Any) -> str:
    if not isinstance(node, dict):
        return "NOT AVAILABLE"
    family = node.get("fontFamily") or node.get("font") or ""
    size = node.get("fontSize") or ""
    weight = node.get("fontWeight") or ""
    if not (family or size):
        return "NOT AVAILABLE"
    return f"{family} {weight} {size}".strip()


def build_design_md(capture: dict[str, Any]) -> str:
    colors = capture.get("colors") if isinstance(capture.get("colors"), dict) else {}
    local = capture.get("local") if isinstance(capture.get("local"), dict) else {}
    guide = capture.get("styleguide") if isinstance(capture.get("styleguide"), dict) else {}
    typography = guide.get("typography") if isinstance(guide.get("typography"), dict) else {}
    headings = typography.get("headings") if isinstance(typography.get("headings"), dict) else {}
    structure = local.get("structure") if isinstance(local.get("structure"), dict) else {}
    lines = [
        "---",
        f"design_capture_version: {DESIGN_CAPTURE_VERSION}",
        f"source_url: {_quote(str(capture.get('url') or ''))}",
        f"accent: {_quote(str(colors.get('accent') or ''))}",
        f"background: {_quote(str(colors.get('background') or ''))}",
        f"text: {_quote(str(colors.get('text') or ''))}",
        "---",
        "",
        "## Overview",
        str(capture.get("overview") or "Measured from the live page. Values that were not read are NOT AVAILABLE."),
        "",
        "## Colors",
    ]
    if colors.get("accent") or colors.get("background") or colors.get("text"):
        for role in ("accent", "background", "text"):
            lines.append(f"- {role}: {_quote(str(colors.get(role) or 'NOT AVAILABLE'))}")
    else:
        lines.append("NOT AVAILABLE — neither Context.dev nor the local CSS walk found a color.")
    lines.extend(["", "## Typography", f"- h1: {_type_line(headings.get('h1'))}", f"- body: {_type_line(typography.get('p'))}"])
    fonts = capture.get("fonts") if isinstance(capture.get("fonts"), list) else []
    if fonts:
        top = fonts[0]
        lines.append(
            f"- most used: {top.get('font') or 'NOT AVAILABLE'} "
            f"({top.get('percent_words') or top.get('percent') or '?'}% of words)"
        )
    lines.extend(["", "## Layout", "## Elevation & Depth", "## Shapes", "## Components"])
    buttons = local.get("buttons") if isinstance(local.get("buttons"), list) else []
    components = guide.get("components") if isinstance(guide.get("components"), dict) else {}
    if components.get("button") or buttons:
        lines.append("Button styles were read from the live page. Reuse them; do not invent a new control.")
    else:
        lines.append("NOT AVAILABLE — no button styles were read.")
    lines.extend(["", "## Logo — use THIS, do not recreate it", *design_markdown_logo(local.get("logo") if isinstance(local.get("logo"), dict) else None)])
    lines.extend(["", "## The site's own design tokens"])
    named = local.get("custom_properties") if isinstance(local.get("custom_properties"), dict) else {}
    if named:
        for name, value in list(named.items())[:24]:
            lines.append(f"- {name}: {_quote(str(value))}")
    else:
        lines.append("NOT AVAILABLE — no CSS custom properties were readable.")
    lines.extend(["", "## Page structure — reproduce this sequence exactly"])
    bands = structure.get("bands") if isinstance(structure.get("bands"), list) else []
    lines.append(" → ".join(str(b) for b in bands) if bands else "NOT AVAILABLE")
    nav = structure.get("nav_labels") if isinstance(structure.get("nav_labels"), list) else []
    if nav:
        lines.append("Nav: " + ", ".join(str(label) for label in nav))
    return "\n".join(lines) + "\n"


def _style_colors(guide: dict[str, Any], local: dict[str, Any]) -> dict[str, str]:
    raw = guide.get("colors") if isinstance(guide.get("colors"), dict) else {}
    colors = {
        "accent": str(raw.get("accent") or ""),
        "background": str(raw.get("background") or ""),
        "text": str(raw.get("text") or ""),
    }
    if not colors["accent"]:
        colors["accent"] = str(local.get("accent") or "")
    if not colors["background"]:
        colors["background"] = str(local.get("background") or "")
    return colors


def _font_name(guide: dict[str, Any], fonts: list[dict[str, Any]]) -> str:
    typography = guide.get("typography") if isinstance(guide.get("typography"), dict) else {}
    headings = typography.get("headings") if isinstance(typography.get("headings"), dict) else {}
    h1 = headings.get("h1") if isinstance(headings.get("h1"), dict) else {}
    body = typography.get("p") if isinstance(typography.get("p"), dict) else {}
    family = str(h1.get("fontFamily") or body.get("fontFamily") or "")
    if family:
        return family.split(",")[0].strip().strip("\"'")
    if fonts:
        return str(fonts[0].get("font") or "").split(",")[0].strip()
    return ""


async def capture_page_design(url: str) -> dict[str, Any]:
    """One capture per run. Paid readers and the local walk fail independently."""
    target = (url or "").strip()
    reasons: list[str] = []
    if not target:
        reasons.append("missing_url")
        return _unavailable(target, reasons)

    async def _style() -> tuple[dict[str, Any] | None, str | None]:
        return await context_dev.extract_styleguide(target)

    async def _fonts() -> tuple[list[dict[str, Any]], str | None]:
        return await context_dev.extract_fonts(target)

    async def _shot(**kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
        return await context_dev.screenshot(target, **kwargs)

    style_res, font_res, full_res, hero_res, local = await asyncio.gather(
        _style(),
        _fonts(),
        _shot(full=True),
        _shot(full=False, width=1440, height=900),
        extract_design_tokens(target),
        return_exceptions=True,
    )

    styleguide: dict[str, Any] = {}
    fonts: list[dict[str, Any]] = []
    screenshots: list[dict[str, Any]] = []
    local_ok = local if isinstance(local, dict) else {"available": False, "error": "local_failed"}

    for label, result in (
        ("styleguide", style_res),
        ("fonts", font_res),
        ("screenshot_full", full_res),
        ("screenshot_hero", hero_res),
        ("local", local if not isinstance(local, dict) else None),
    ):
        if isinstance(result, Exception):
            reasons.append(f"{label}:{type(result).__name__}")
            log.warning("design_reader_failed", reader=label, error=type(result).__name__)

    if isinstance(style_res, tuple):
        guide, err = style_res
        if err:
            reasons.append(err)
        elif isinstance(guide, dict):
            styleguide = guide
    if isinstance(font_res, tuple):
        rows, err = font_res
        if err:
            reasons.append(err)
        fonts = rows
    for result in (full_res, hero_res):
        if isinstance(result, tuple):
            shot, err = result
            if err and err not in reasons:
                reasons.append(err)
            elif isinstance(shot, dict):
                screenshots.append(shot)

    colors = _style_colors(styleguide, local_ok)
    font = _font_name(styleguide, fonts)
    measured = bool(colors.get("accent") or font or local_ok.get("available"))
    capture = {
        "available": measured,
        "version": DESIGN_CAPTURE_VERSION,
        "url": target,
        "source": "measured" if measured else "unavailable",
        "styleguide": styleguide,
        "fonts": fonts,
        "screenshots": screenshots,
        "local": {k: v for k, v in local_ok.items() if k != "html"},
        "colors": colors,
        "font": font,
        "reasons": reasons,
        "overview": (
            "Computed styles from Context.dev where present; local CSS walk filled fields "
            "Context.dev does not return. Do not invent a missing color."
            if measured
            else "NOT AVAILABLE — " + (", ".join(reasons) or "no reader returned a design.")
        ),
    }
    capture["design_md"] = build_design_md(capture)
    log.info(
        "design_captured",
        url=target,
        measured=measured,
        credits=_credit_note(reasons),
    )
    return capture


def _credit_note(reasons: list[str]) -> int:
    if "context_dev_not_configured" in reasons:
        return 0
    return 17


def _unavailable(url: str, reasons: list[str]) -> dict[str, Any]:
    capture = {
        "available": False,
        "version": DESIGN_CAPTURE_VERSION,
        "url": url,
        "source": "unavailable",
        "colors": {},
        "font": "",
        "reasons": reasons,
        "overview": "NOT AVAILABLE — " + ", ".join(reasons),
        "screenshots": [],
        "local": {},
        "styleguide": {},
        "fonts": [],
    }
    capture["design_md"] = build_design_md(capture)
    return capture


def merge_site_design(wordpress: dict[str, Any] | None, capture: dict[str, Any] | None) -> dict[str, Any]:
    """Measured tokens win. WordPress chrome is kept when the local walk found none."""
    site = dict(wordpress or {})
    measured = capture or {}
    if not measured.get("available"):
        site["design_capture"] = {
            "available": False,
            "reasons": measured.get("reasons") or [],
            "design_md": measured.get("design_md") or "",
        }
        return site
    local = measured.get("local") if isinstance(measured.get("local"), dict) else {}
    colors = measured.get("colors") if isinstance(measured.get("colors"), dict) else {}
    site.update(
        {
            "available": True,
            "source": "measured",
            "primary_color": colors.get("accent") or site.get("primary_color") or "",
            "font": measured.get("font") or site.get("font") or "",
            "colors": {
                **(site.get("colors") or {}),
                **{k: v for k, v in colors.items() if v},
            },
            "header_html": local.get("header_html") or site.get("header_html") or "",
            "footer_html": local.get("footer_html") or site.get("footer_html") or "",
            "stylesheets": local.get("stylesheets") or site.get("stylesheets") or [],
            "chrome": bool(local.get("header_html") or local.get("footer_html") or site.get("header_html")),
            "design_capture": {
                "available": True,
                "version": measured.get("version"),
                "url": measured.get("url"),
                "design_md": measured.get("design_md") or "",
                "screenshots": [
                    shot for shot in (measured.get("screenshots") or [])
                    if isinstance(shot, dict) and shot.get("model_safe", True)
                ],
                "reasons": measured.get("reasons") or [],
                "logo_url": ((local.get("logo") or {}) if isinstance(local.get("logo"), dict) else {}).get("url") or "",
            },
        }
    )
    return site
