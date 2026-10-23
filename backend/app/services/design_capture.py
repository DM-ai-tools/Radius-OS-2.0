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
    previous_page_url: str = "",
    existing_page_url: str = "",
    parent_pillar_page_url: str = "",
    reference_design_source: str = "",
    client_website_url: str = "",
    profile_website_url: str = "",
) -> str:
    """Preference order for the page whose design the new content must wear.

    Prefer the previous page on the internal-linking / publish plan when known,
    then an existing URL for refresh, then hub/parent, then home.
    """
    for candidate in (
        previous_page_url,
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


def _norm_path(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        from urllib.parse import urlparse

        return (urlparse(raw).path or "/").rstrip("/").lower() or "/"
    path = raw if raw.startswith("/") else f"/{raw}"
    return path.rstrip("/").lower() or "/"


def _abs_url(path_or_url: str, primary_url: str) -> str:
    raw = str(path_or_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    base = str(primary_url or "").rstrip("/")
    if not base:
        return ""
    return base + (raw if raw.startswith("/") else f"/{raw}")


def _norm_label(value: Any) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def resolve_previous_design_page(
    *,
    page_url: str = "",
    page_keyword: str = "",
    primary_url: str = "",
    on_page_seo: dict[str, Any] | None = None,
    content_planning: dict[str, Any] | None = None,
    topic_plan: dict[str, Any] | None = None,
    site_architecture: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Find the page whose live design this draft should copy.

    Evidence order:
    1. Internal linking plan — parent hub / strongest inbound ``from`` for this page
    2. Topic ``publishing_order`` — prior title mapped onto the locked roadmap
    3. Content planning roadmap — previous ``priority_rank`` sibling
    4. IA parent pillar
    """
    on_page = dict(on_page_seo or {})
    planning = dict(content_planning or {})
    plan = dict(topic_plan or {})
    ia = dict(site_architecture or {})
    current_path = _norm_path(page_url)
    current_kw = _norm_label(page_keyword)
    primary = str(primary_url or "").strip()

    def _match_row(row: dict[str, Any]) -> bool:
        path = _norm_path(str(row.get("url") or row.get("path") or ""))
        kw = _norm_label(
            row.get("keyword") or row.get("primary_keyword") or row.get("title") or ""
        )
        if current_path and path and path == current_path:
            return True
        if current_kw and kw and kw == current_kw:
            return True
        return False

    def _targets_this_draft(link: dict[str, Any]) -> bool:
        """True when this linking-plan edge points at the draft being previewed."""
        to_path = _norm_path(str(link.get("to") or link.get("target") or link.get("url") or ""))
        anchor = _norm_label(link.get("anchor") or link.get("keyword") or "")
        tail = to_path.strip("/").split("/")[-1] if to_path else ""
        kw_slug = current_kw.replace(" ", "-")
        if current_path and to_path and to_path == current_path:
            return True
        if current_kw and anchor and anchor == current_kw:
            return True
        if kw_slug and tail and tail == kw_slug:
            return True
        return False

    # --- 1) Internal linking plan (Phase 11) ---------------------------------
    linking = on_page.get("internal_linking") if isinstance(on_page.get("internal_linking"), dict) else {}
    links = list(linking.get("links") or on_page.get("internal_links") or [])
    inbound: list[dict[str, Any]] = []
    parent_candidate = ""
    for link in links:
        if not isinstance(link, dict):
            continue
        from_path = _norm_path(str(link.get("from") or ""))
        if not _targets_this_draft(link):
            continue
        reason = str(link.get("reason") or "").lower()
        if reason in {"parent_hub", "hub_to_spoke", "parent"} or "parent" in reason:
            parent_candidate = from_path
        inbound.append(link)
    if parent_candidate:
        return {
            "url": _abs_url(parent_candidate, primary),
            "reason": "internal_linking_parent",
            "path": parent_candidate,
        }
    if inbound:
        # Prefer a live inbound source over planned-only.
        live = next(
            (
                ln
                for ln in inbound
                if str(ln.get("target_status") or "").lower() in {"", "live"}
                or str(ln.get("from_status") or "").lower() == "live"
            ),
            inbound[0],
        )
        frm = _norm_path(str(live.get("from") or ""))
        if frm:
            return {
                "url": _abs_url(frm, primary),
                "reason": "internal_linking_inbound",
                "path": frm,
            }

    # Per-page packages sometimes nest linking under pages[]
    for page in on_page.get("pages") or on_page.get("queue") or []:
        if not isinstance(page, dict) or not _match_row(page):
            continue
        nested = page.get("internal_linking") if isinstance(page.get("internal_linking"), dict) else {}
        nested_links = list(nested.get("links") or page.get("internal_links") or [])
        for link in nested_links:
            if not isinstance(link, dict):
                continue
            to_path = _norm_path(str(link.get("to") or ""))
            from_path = _norm_path(str(link.get("from") or ""))
            if current_path and to_path == current_path and from_path:
                return {
                    "url": _abs_url(from_path, primary),
                    "reason": "page_internal_linking",
                    "path": from_path,
                }
        parent = str(page.get("parent") or "").strip()
        if parent:
            return {
                "url": _abs_url(parent, primary),
                "reason": "on_page_parent",
                "path": _norm_path(parent),
            }

    # --- 2) Topic publishing_order → prior title on the roadmap --------------
    roadmap = [
        r
        for r in (planning.get("pages") or planning.get("roadmap") or [])
        if isinstance(r, dict)
    ]
    order_raw = list(plan.get("publishing_order") or [])
    order_labels: list[str] = []
    for item in order_raw:
        if isinstance(item, dict):
            order_labels.append(
                _norm_label(item.get("title") or item.get("keyword") or item.get("url") or "")
            )
        else:
            order_labels.append(_norm_label(item))
    order_labels = [x for x in order_labels if x]

    def _row_label(row: dict[str, Any]) -> str:
        return _norm_label(
            row.get("title") or row.get("keyword") or row.get("primary_keyword") or ""
        )

    if order_labels and roadmap:
        idx = -1
        for i, label in enumerate(order_labels):
            if current_kw and label == current_kw:
                idx = i
                break
            for row in roadmap:
                if _match_row(row) and _row_label(row) == label:
                    idx = i
                    break
            if idx >= 0:
                break
        if idx > 0:
            prev_label = order_labels[idx - 1]
            for row in roadmap:
                if _row_label(row) == prev_label:
                    path = str(row.get("url") or row.get("path") or "")
                    if path:
                        return {
                            "url": _abs_url(path, primary),
                            "reason": "publishing_order_previous",
                            "path": _norm_path(path),
                        }

    # --- 3) Roadmap priority — previous ranked page --------------------------
    ranked = sorted(
        roadmap,
        key=lambda r: int(r.get("priority_rank") or 999),
    )
    for i, row in enumerate(ranked):
        if not _match_row(row):
            continue
        if i == 0:
            break
        prev = ranked[i - 1]
        path = str(prev.get("url") or prev.get("path") or "")
        if path:
            return {
                "url": _abs_url(path, primary),
                "reason": "roadmap_previous",
                "path": _norm_path(path),
            }

    # --- 4) This page's parent in the IA tree, then one site-wide pillar -----
    for node in ia.get("target_url_tree") or []:
        if not isinstance(node, dict):
            continue
        if not _match_row(node):
            continue
        parent = str(node.get("parent") or "").strip()
        if parent and parent != "—":
            return {
                "url": _abs_url(parent, primary),
                "reason": "ia_tree_parent",
                "path": _norm_path(parent),
            }
    parent_pillar = str(ia.get("parent_pillar_page_url") or "").strip()
    if parent_pillar.startswith("http"):
        return {
            "url": parent_pillar,
            "reason": "ia_parent_pillar",
            "path": _norm_path(parent_pillar),
        }

    return {"url": "", "reason": "", "path": ""}


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
