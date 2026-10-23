"""Phase 12 preview — render the exact payload that would be sent to the CMS.

Architecture v1.9 step 12: "Only after approval does the system spend on a dry-run
preview — the exact payload that would be sent, with no live CMS writes — so the team
sees precisely what will change before it goes anywhere."

So the preview is built from the same content the publisher will send, styled with the
client's real brand (Brandfetch) and framed against a real page from their site
(Firecrawl). Where either is unavailable the preview says so on its face rather than
quietly rendering generic styling that misleads the reviewer.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse

NEUTRAL_PRIMARY = "#334155"
NEUTRAL_FONT = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"

_META_HEADINGS = (
    "review queue",
    "draft metadata",
    "differentiation delivered",
    "coverage check",
    "notes for the editor",
)


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _path(url: str) -> str:
    if not url:
        return "/"
    return urlparse(url).path or "/" if "://" in url else url


def _title_of(page: dict[str, Any]) -> str:
    block = page.get("title")
    if isinstance(block, dict):
        return str(block.get("after") or block.get("before") or "")
    return str(block or page.get("h1") or "")


def _meta_of(page: dict[str, Any]) -> str:
    block = page.get("meta_description")
    if isinstance(block, dict):
        return _display_meta(block.get("after") or "")
    return _display_meta(block or "")


def _display_meta(value: Any) -> str:
    """Human-readable meta for SERP preview — never show raw dict blobs."""
    from app.services.create_topic import format_audience_label

    if isinstance(value, dict):
        return (format_audience_label(value) or "")[:160]
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("{") and "primary" in text:
        return ""
    # Audience dicts sometimes get stringified into brief meta descriptions.
    text = re.sub(r"\s+for\s+\{.*", "", text, flags=re.DOTALL).strip()
    return text[:160]


def sanitize_meta_description(value: Any) -> str:
    """Public helper — use when persisting meta from briefs into drafts."""
    return _display_meta(value)


def build_content_html(page: dict[str, Any], brief: dict[str, Any] | None = None) -> str:
    """The body that would be written to the CMS.

    Built only from approved Phase 10/11 material — outline, H2s, FAQ. It deliberately
    does not invent paragraph copy: unwritten sections ship as clearly-marked
    placeholders so nobody mistakes an outline for a finished article.
    """
    brief = brief or {}
    headings = page.get("headings") if isinstance(page.get("headings"), dict) else {}
    h1 = str(headings.get("h1") or page.get("h1") or _title_of(page) or "")
    parts: list[str] = []
    if h1:
        parts.append(f"<h1>{_esc(h1)}</h1>")

    intro = str(brief.get("content_goal") or brief.get("meta_description") or _meta_of(page))
    if intro:
        parts.append(f"<p>{_esc(intro)}</p>")

    outline = brief.get("outline")
    sections: list[str] = []
    if isinstance(outline, list) and outline:
        for item in outline:
            if isinstance(item, dict):
                sections.append(str(item.get("heading") or item.get("title") or ""))
            elif item:
                sections.append(str(item))
    if not sections:
        sections = [str(h) for h in (headings.get("h2s") or []) if h]

    for section in [s for s in sections if s][:12]:
        parts.append(f"<h2>{_esc(section)}</h2>")
        parts.append(
            '<p data-placeholder="true"><em>Section outlined in the approved brief — '
            "copy to be written before go-live.</em></p>"
        )

    faq = brief.get("faq")
    if isinstance(faq, list) and faq:
        parts.append("<h2>FAQs</h2>")
        for row in faq[:6]:
            if not isinstance(row, dict):
                continue
            q = str(row.get("question") or row.get("q") or "")
            a = str(row.get("answer") or row.get("a") or "")
            if not q:
                continue
            parts.append(f"<h3>{_esc(q)}</h3>")
            if a:
                parts.append(f"<p>{_esc(a)}</p>")

    return "\n".join(parts)


def _brand_style(brand: dict[str, Any]) -> dict[str, str]:
    available = bool(brand.get("available"))
    primary = str(brand.get("primary_color") or "") if available else ""
    font_name = str((brand.get("font") or {}).get("name") or "") if available else ""
    return {
        "primary": primary or NEUTRAL_PRIMARY,
        "font": f'"{font_name}", {NEUTRAL_FONT}' if font_name else NEUTRAL_FONT,
        "brand_applied": "yes" if (available and (primary or font_name)) else "no",
    }


def preview_design_meta(brand: dict[str, Any], site_design: dict[str, Any] | None) -> dict[str, str]:
    """WordPress kit, then Brandfetch, then neutral. Never invents a colour."""
    design = site_design or {}
    brand_style = _brand_style(brand)
    brand_ok = brand_style["brand_applied"] == "yes"
    if str(design.get("source") or "") == "measured" and design.get("available"):
        primary = str(design.get("primary_color") or "")
        font_name = str(design.get("font") or "")
        if primary or font_name:
            return {
                "primary": primary or brand_style["primary"],
                "font": f'"{font_name}", {NEUTRAL_FONT}' if font_name else brand_style["font"],
                "brand_applied": "yes",
                "source": "measured",
                "fallback": "brandfetch" if (not primary and brand.get("primary_color")) else "",
                "filled": "color" if not primary and brand.get("primary_color") else "",
            }
        return {**brand_style, "source": "unavailable", "fallback": "", "filled": ""}
    if str(design.get("source") or "") == "firecrawl" and design.get("available"):
        primary = str(design.get("primary_color") or "") or brand_style["primary"]
        font_name = str(design.get("font") or "")
        return {
            "primary": primary or NEUTRAL_PRIMARY,
            "font": f'"{font_name}", {NEUTRAL_FONT}' if font_name else brand_style["font"],
            "brand_applied": "yes" if (str(design.get("primary_color") or "") or font_name) else "no",
            "source": "firecrawl",
            "fallback": "",
            "filled": "",
        }
    wp_ok = bool(design.get("available"))
    wp_color = str(design.get("primary_color") or "") if wp_ok else ""
    wp_font = str(design.get("font") or "") if wp_ok else ""
    brand_font = str((brand.get("font") or {}).get("name") or "") if brand.get("available") else ""
    filled: list[str] = []
    if wp_ok:
        primary = wp_color or brand_style["primary"]
        font = f'"{wp_font}", {NEUTRAL_FONT}' if wp_font else brand_style["font"]
        if not wp_color and brand.get("primary_color"):
            filled.append("color")
        if not wp_font and brand_font:
            filled.append("type")
        if not design.get("chrome") and (brand.get("logo") or {}).get("url"):
            filled.append("logo")
        return {
            "primary": primary,
            "font": font,
            "brand_applied": "yes",
            "source": "wordpress",
            "fallback": "brandfetch" if filled else "",
            "filled": ",".join(filled),
        }
    if brand_ok:
        return {**brand_style, "source": "brandfetch", "fallback": "", "filled": ""}
    return {**brand_style, "source": "neutral", "fallback": "", "filled": ""}


def _preview_style(brand: dict[str, Any], site_design: dict[str, Any] | None) -> dict[str, str]:
    return preview_design_meta(brand, site_design)


def _google_fonts_link(tokens: dict[str, Any]) -> str:
    families: list[str] = []
    for key in ("heading_font", "body_font"):
        name = str(tokens.get(key) or "").strip()
        if name and name not in families:
            families.append(name)
    if not families:
        return ""
    query = "&".join(
        "family=" + name.replace(" ", "+") + ":wght@400;500;600;700" for name in families
    )
    return f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?{query}&display=swap">'


def _page_design_css(tokens: dict[str, Any]) -> str:
    """CSS that paints the draft with the live page's measured type and color."""
    if not tokens:
        return ""
    body_font = str(tokens.get("body_font") or tokens.get("heading_font") or "")
    heading_font = str(tokens.get("heading_font") or body_font)
    background = str(tokens.get("background") or "")
    text = str(tokens.get("text") or "")
    muted = str(tokens.get("muted") or "")
    link = str(tokens.get("link") or tokens.get("primary") or "")
    primary = str(tokens.get("primary") or "")
    heading_color = text or primary
    rules = ["body.has-page-design .page { border: 0; }"]
    if background:
        rules.append(f"body.has-page-design {{ background:{background}; }}")
        rules.append(f"body.has-page-design .page, body.has-page-design .body {{ background:{background}; }}")
    if text:
        rules.append(f"body.has-page-design, body.has-page-design .body {{ color:{text}; }}")
    if body_font:
        stack = f'"{body_font}", {NEUTRAL_FONT}'
        rules.append(f"body.has-page-design, body.has-page-design .body {{ font-family:{stack}; }}")
    if tokens.get("body"):
        rules.append(f"body.has-page-design .body {{ font-size:{tokens['body']}; }}")
    if heading_font:
        hstack = f'"{heading_font}", {NEUTRAL_FONT}'
        rules.append(
            "body.has-page-design .body h1, body.has-page-design .body h2, "
            f"body.has-page-design .body h3 {{ font-family:{hstack}; }}"
        )
    if heading_color:
        rules.append(
            "body.has-page-design .body h1, body.has-page-design .body h2, "
            f"body.has-page-design .body h3 {{ color:{heading_color}; }}"
        )
    if tokens.get("h1"):
        rules.append(f"body.has-page-design .body h1 {{ font-size:{tokens['h1']}; line-height:1.15; }}")
    if tokens.get("h2"):
        rules.append(f"body.has-page-design .body h2 {{ font-size:{tokens['h2']}; line-height:1.2; }}")
    if tokens.get("h3"):
        rules.append(f"body.has-page-design .body h3 {{ font-size:{tokens['h3']}; }}")
    if tokens.get("weight"):
        rules.append(
            "body.has-page-design .body h1, body.has-page-design .body h2 "
            f"{{ font-weight:{tokens['weight']}; }}"
        )
    if link:
        rules.append(f"body.has-page-design .body a {{ color:{link}; }}")
    if muted:
        rules.append(f"body.has-page-design .body figcaption {{ color:{muted}; }}")
    if tokens.get("radius"):
        rules.append(f"body.has-page-design .body img, body.has-page-design .body figure {{ border-radius:{tokens['radius']}; }}")
    button_bg = str(tokens.get("button_bg") or "")
    button_text = str(tokens.get("button_text") or "")
    if button_bg and button_text:
        radius = tokens.get("radius") or "6px"
        rules.append(
            "body.has-page-design .body .draft-cta a {"
            f"display:inline-block; background:{button_bg}; color:{button_text}; "
            f"text-decoration:none; padding:12px 18px; border-radius:{radius};"
            "}"
        )
    return "\n  ".join(rules)


def render_preview_html(
    *,
    client_name: str,
    page: dict[str, Any],
    content_html: str,
    brand: dict[str, Any],
    layout: dict[str, Any] | None = None,
    site_design: dict[str, Any] | None = None,
    target_status: str = "draft",
    banner_label: str = "DRY-RUN PREVIEW",
    banner_subtitle: str = "no CMS write has occurred",
) -> str:
    """A self-contained HTML document for reviewer eyeballs. Never written to the CMS."""
    style = _preview_style(brand, site_design)
    layout = layout or {}
    design = site_design or {}
    logo = (brand.get("logo") or {}).get("url") if brand.get("available") else None
    tokens = design.get("preview_tokens") if isinstance(design.get("preview_tokens"), dict) else {}
    if not logo and str(tokens.get("logo") or "").startswith("http"):
        logo = str(tokens.get("logo"))
    page_css = _page_design_css(tokens)
    font_link = _google_fonts_link(tokens) if page_css else ""
    title = _title_of(page)
    meta = _meta_of(page)
    url = str(page.get("url") or "")

    notices: list[str] = []
    if style["source"] == "measured":
        notices.append(
            "Styled from the live page: Context.dev computed styles, plus a local CSS walk "
            "for tokens that API does not return. Missing colors are left unavailable, not invented."
        )
    elif style["source"] == "wordpress" and style.get("fallback") == "brandfetch":
        kit = f" kit {design.get('kit_id')}" if design.get("kit_id") else ""
        filled = style.get("filled") or "missing tokens"
        notices.append(
            f"Styled from the live WordPress site{kit}. "
            f"Brandfetch filled {filled.replace(',', ', ')}."
        )
    elif style["source"] == "wordpress":
        kit = f" kit {design.get('kit_id')}" if design.get("kit_id") else ""
        notices.append(
            f"Styled from the live WordPress site{kit}"
            + (" — header and footer included." if design.get("chrome") else ".")
        )
    elif style["source"] == "firecrawl":
        notices.append(
            "Fonts, colors, and type sizes were measured from the live page with Firecrawl."
        )
    elif style["source"] == "brandfetch":
        notices.append(
            "WordPress design was not available — Brandfetch fallback. "
            "This is the brand record, not the live page chrome."
        )
    elif style["brand_applied"] != "yes":
        notices.append(
            f"Brand assets unavailable ({_esc(brand.get('error') or design.get('error') or 'not fetched')}) — "
            "neutral styling shown, not the client's real design."
        )
    if design.get("preview_tokens") and style["source"] != "firecrawl":
        notices.append(
            "Article fonts and colors follow the live page measured by Firecrawl."
        )
    if not layout.get("available") and not design.get("chrome") and not design.get("preview_tokens"):
        notices.append("No live reference page scraped — layout is generic.")
    if target_status != "publish":
        notices.append(f"Target CMS status: <strong>{_esc(target_status)}</strong> — not live.")

    notice_html = "".join(f"<li>{n}</li>" for n in notices)
    kit_swatches = "".join(
        f'<span class="sw" style="background:{_esc(color)}" title="{_esc(name)}"></span>'
        for name, color in list((design.get("colors") or {}).items())[:6]
        if isinstance(color, str) and color.startswith("#")
    )
    swatches = kit_swatches or "".join(
        f'<span class="sw" style="background:{_esc(c.get("hex"))}" title="{_esc(c.get("hex"))}"></span>'
        for c in (brand.get("palette") or [])[:6]
        if isinstance(c, dict) and c.get("hex")
    )
    sheets = "".join(
        f'<link rel="stylesheet" href="{_esc(href)}">'
        for href in (design.get("stylesheets") or [])[:12]
        if isinstance(href, str) and href.startswith("http")
    )
    kit_class = f"elementor-kit-{_esc(design.get('kit_id'))}" if design.get("kit_id") else ""
    site_mode = bool(design.get("available"))
    shots = ((design.get("design_capture") or {}).get("screenshots") or []) if style["source"] == "measured" else []
    hero = next(
        (
            s.get("url")
            for s in shots
            if isinstance(s, dict) and s.get("url") and s.get("screenshot_type") != "fullPage"
        ),
        "",
    )
    reference_shot = (
        f'<p class="ref-shot"><img src="{_esc(hero)}" alt="Live page screenshot"></p>'
        if isinstance(hero, str) and hero.startswith("http")
        else ""
    )
    chrome_header = design.get("header_html") or ""
    if chrome_header and client_name:
        # Scraped logos often carry wrong partner/client alts (e.g. "Aimbo").
        chrome_header = re.sub(
            r'(?is)(<img\b[^>]*\balt\s*=\s*)(["\'])([^"\']*)\2',
            rf'\1\2{_esc(client_name)}\2',
            chrome_header,
            count=3,
        )
    chrome_footer = design.get("footer_html") or ""
    generic_header = (
        f'<img src="{_esc(logo)}" alt="{_esc(client_name)}" onerror="this.remove()">'
        f'<strong class="hdr-fallback">{_esc(client_name)}</strong>'
        if logo
        else f"<strong>{_esc(client_name)}</strong>"
    )

    html_doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Preview — {_esc(title)}</title>
{font_link}
{sheets}
<style>
  :root {{ --brand: {style["primary"]}; }}
  body.preview-root {{ margin:0; font-family:{style["font"]}; color:#0f172a; background:#fff; }}
  .bar {{ background:#0f172a; color:#e2e8f0; padding:10px 16px; font-size:13px;
          display:flex; gap:12px; align-items:center; justify-content:space-between; }}
  .bar strong {{ color:#fff; }}
  .warn {{ background:#fef3c7; border-bottom:1px solid #fde68a; padding:10px 16px;
           font-size:13px; color:#78350f; }}
  .warn ul {{ margin:4px 0 0 18px; padding:0; }}
  .serp {{ max-width:760px; margin:20px auto; background:#fff; border:1px solid #e2e8f0;
           border-radius:10px; padding:16px; }}
  .serp .u {{ color:#0f766e; font-size:12px; }}
  .serp .t {{ color:#1a0dab; font-size:19px; margin:2px 0; }}
  .serp .d {{ color:#475569; font-size:13px; }}
  .page {{ max-width:760px; margin:20px auto 60px; background:#fff; border:1px solid #e2e8f0;
           border-radius:10px; overflow:hidden; }}
  body.has-site .page {{ max-width:none; margin:0; border:0; border-radius:0; }}
  .hdr {{ border-top:4px solid var(--brand); padding:16px 22px; display:flex;
          align-items:center; gap:12px; border-bottom:1px solid #eef2f7; }}
  .hdr img {{ max-height:34px; max-width:150px; }}
  .hdr .hdr-fallback {{ font-size:18px; color:var(--brand); }}
  .body {{ padding:22px; max-width:760px; margin:0 auto; }}
  body.has-site .body {{ max-width:1100px; padding:28px 22px 48px; }}
  .body h1 {{ font-size:28px; margin:0 0 12px; color:var(--brand); }}
  .body h2 {{ font-size:19px; margin:22px 0 6px; }}
  .body h3 {{ font-size:15px; margin:14px 0 4px; }}
  .body p {{ line-height:1.6; }}
  .body ul {{ margin:8px 0 12px 20px; line-height:1.6; }}
  .body img {{ max-width:100%; height:auto; margin:12px 0; }}
  .body figure {{ margin:16px 0; }}
  .body figcaption {{ font-size:12px; color:#64748b; margin-top:6px; }}
  .body .draft-figure-placeholder {{
    display:flex; align-items:center; justify-content:center; min-height:160px;
    padding:16px; border-radius:8px; border:1px dashed #cbd5e1; background:#f8fafc;
    color:#64748b; font-size:13px; text-align:center;
  }}
  .body [data-placeholder] {{ color:#94a3b8; }}
  .sw {{ display:inline-block; width:16px; height:16px; border-radius:3px;
         border:1px solid rgba(0,0,0,.15); margin-right:3px; vertical-align:middle; }}
  {page_css}
</style></head>
<body class="preview-root{' has-site' if site_mode else ''}{' has-page-design' if page_css else ''} {kit_class}">
<div class="bar"><span><strong>{_esc(banner_label)}</strong> — {_esc(banner_subtitle)}</span>
<span>{_esc(client_name)} {swatches}</span></div>
{f'<div class="warn"><strong>Preview caveats</strong><ul>{notice_html}</ul></div>' if notices else ''}

<div class="serp">
  <div class="u">{_esc(url)}</div>
  <div class="t">{_esc(title)}</div>
  <div class="d">{_esc(meta)}</div>
</div>

<!--WP_HEADER-->
<div class="page">
  {'' if site_mode and chrome_header else f'<div class="hdr">{generic_header}</div>'}
  {reference_shot}
  <div class="body {kit_class}">{content_html}</div>
</div>
<!--WP_FOOTER-->
</body></html>"""
    html_doc = html_doc.replace("<!--WP_HEADER-->", chrome_header if site_mode else "")
    return html_doc.replace("<!--WP_FOOTER-->", chrome_footer if site_mode else "")


def build_page_preview(
    *,
    client_name: str,
    page: dict[str, Any],
    brief: dict[str, Any] | None,
    brand: dict[str, Any],
    layout: dict[str, Any] | None,
    target_status: str,
    slug: str,
    content_html: str | None = None,
    site_design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview + the exact CMS payload, so reviewer and publisher never diverge.

    ``content_html`` lets the caller supply the real written draft. When omitted the
    preview falls back to the outline skeleton, which is explicitly marked with
    placeholders — reviewer and publisher still see the identical payload either way.
    """
    if content_html is None:
        content_html = build_content_html(page, brief)
    schema = page.get("schema_json_ld")
    payload = {
        "title": _title_of(page),
        "slug": slug,
        "status": target_status,
        "excerpt": _meta_of(page),
        "content": content_html,
    }
    return {
        "url": page.get("url"),
        "path": _path(str(page.get("url") or "")),
        "slug": slug,
        "keyword": page.get("keyword"),
        "target_status": target_status,
        "cms_payload": payload,
        "cms_payload_json": json.dumps(payload, indent=2)[:20000],
        "schema_json_ld": schema if isinstance(schema, dict) else None,
        "preview_html": render_preview_html(
            client_name=client_name,
            page=page,
            content_html=content_html,
            brand=brand,
            layout=layout,
            site_design=site_design,
            target_status=target_status,
        ),
        "brand_applied": _preview_style(brand, site_design)["brand_applied"] == "yes",
        "design_source": _preview_style(brand, site_design)["source"],
        "design_fallback": _preview_style(brand, site_design).get("fallback") or None,
        "has_written_copy": "data-placeholder" not in content_html,
    }


def _is_meta_heading(text: str) -> bool:
    h = text.lower()
    return any(m in h for m in _META_HEADINGS)


_PUBLISH_MARKER = re.compile(
    r"[ \t]*\[(?:VERIFY|AUTHOR INPUT REQUIRED)\b[^\]\n]*\]?",
    re.IGNORECASE,
)


def strip_publish_markers(text: str) -> str:
    """Remove editor markers so they cannot be copied onto the page.

    The writer used to leave ``[VERIFY]`` beside unsourced numbers. The preview
    prints the draft as written, so those markers blocked every preview.
    """
    cleaned = _PUBLISH_MARKER.sub("", str(text or ""))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def draft_body_markdown(markdown: str) -> str:
    """Strip editorial/meta sections; keep publishable article body."""
    text = str(markdown or "").replace("\r\n", "\n")
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1].strip()
    lines = text.split("\n")
    out: list[str] = []
    skip_section = False
    seen_h1 = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            skip_section = _is_meta_heading(heading)
            if skip_section:
                continue
            out.append(line)
            continue
        if stripped.startswith("# "):
            if seen_h1:
                continue
            seen_h1 = True
            out.append(line)
            continue
        if skip_section:
            continue
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            continue
        out.append(line)
    return strip_publish_markers("\n".join(out))


def markdown_to_content_html(
    markdown: str,
    *,
    images: list[dict[str, Any]] | None = None,
    media_base: str | None = None,
) -> str:
    """Convert a Phase 10 draft markdown body to safe HTML for site preview."""
    body = draft_body_markdown(markdown)
    if not body:
        return '<p data-placeholder="true"><em>No publishable body found in this draft.</em></p>'

    image_queue: list[dict[str, Any]] = [
        i for i in (images or []) if isinstance(i, dict)
    ]
    used_src: set[str] = set()

    def _abs_src(src: str | None) -> str:
        s = str(src or "").strip()
        if not s:
            return ""
        if s.startswith("http://") or s.startswith("https://") or s.startswith("data:"):
            return s
        base = str(media_base or "").rstrip("/")
        if base and s.startswith("/"):
            return f"{base}{s}"
        return s

    def _src_key(src: Any) -> str:
        return str(src or "").strip().split("?")[0].rstrip("/")

    def _next_image(role_hint: str | None = None) -> dict[str, Any] | None:
        if not image_queue:
            return None
        if role_hint:
            for idx, img in enumerate(image_queue):
                if str(img.get("role") or "").lower() == role_hint.lower():
                    return image_queue.pop(idx)
        return image_queue.pop(0)

    def _consume_src(src: str) -> None:
        key = _src_key(src)
        if not key:
            return
        used_src.add(key)
        for idx, img in enumerate(image_queue):
            got = _src_key(img.get("src"))
            if got and (got == key or got.endswith(key) or key.endswith(got)):
                image_queue.pop(idx)
                return

    def _figure_html(
        *,
        src: str | None,
        alt: str,
        caption: str,
        role: str | None = None,
        status: str | None = None,
    ) -> str:
        abs_src = _abs_src(src)
        if abs_src:
            fig = f'<figure class="draft-figure"><img src="{_esc(abs_src)}" alt="{_esc(alt)}">'
            if caption:
                fig += f"<figcaption>{_esc(caption)}</figcaption>"
            return fig + "</figure>"
        label = "Image generation failed" if status == "failed" else "Image placeholder"
        if role:
            label = f"{label} · {_esc(role)}"
        fig = (
            f'<figure class="draft-figure draft-figure--placeholder">'
            f'<div class="draft-figure-placeholder">{label}</div>'
        )
        if caption:
            fig += f"<figcaption>{_esc(caption)}</figcaption>"
        return fig + "</figure>"

    lines = body.split("\n")
    parts: list[str] = []
    para_buf: list[str] = []
    list_buf: list[str] = []

    def flush_para() -> None:
        text = " ".join(para_buf).strip()
        para_buf.clear()
        if text:
            parts.append(f"<p>{_esc(text)}</p>")

    def flush_list() -> None:
        if not list_buf:
            return
        items = "".join(f"<li>{_esc(item)}</li>" for item in list_buf)
        list_buf.clear()
        parts.append(f"<ul>{items}</ul>")

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_list()
            i += 1
            continue
        if stripped.startswith("# "):
            flush_para()
            flush_list()
            parts.append(f"<h1>{_esc(stripped[2:].strip())}</h1>")
            i += 1
            continue
        if stripped.startswith("## "):
            flush_para()
            flush_list()
            parts.append(f"<h2>{_esc(stripped[3:].strip())}</h2>")
            i += 1
            continue
        if stripped.startswith("### "):
            flush_para()
            flush_list()
            parts.append(f"<h3>{_esc(stripped[4:].strip())}</h3>")
            i += 1
            continue
        img = stripped
        if img.startswith("![") and "](" in img and img.endswith(")"):
            flush_para()
            flush_list()
            alt_end = img.index("](")
            alt = img[2:alt_end]
            src = img[alt_end + 2 : -1]
            caption = ""
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("*") and lines[i + 1].strip().endswith("*"):
                caption = lines[i + 1].strip().strip("*")
                i += 1
            _consume_src(src)
            parts.append(_figure_html(src=src, alt=alt, caption=caption))
            i += 1
            continue
        fig_m = re.match(r"^\[FIGURE\s+([^\]]+)\]\s*(.*)$", stripped, flags=re.IGNORECASE)
        if fig_m:
            flush_para()
            flush_list()
            role = fig_m.group(1).strip()
            caption = fig_m.group(2).strip()
            matched = _next_image(role)
            src = (matched or {}).get("src")
            key = _src_key(src)
            if key:
                used_src.add(key)
            parts.append(
                _figure_html(
                    src=(matched or {}).get("src"),
                    alt=str((matched or {}).get("alt") or role or "Figure"),
                    caption=caption or str((matched or {}).get("caption") or (matched or {}).get("prompt") or ""),
                    role=str((matched or {}).get("role") or role),
                    status=str((matched or {}).get("status") or ("failed" if matched and not matched.get("src") else "")),
                )
            )
            i += 1
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_para()
            list_buf.append(stripped[2:].strip())
            i += 1
            continue
        flush_list()
        para_buf.append(stripped)
        i += 1

    flush_para()
    flush_list()
    # Leftover generated images (no FIGURE markers) — append after body.
    while image_queue:
        leftover = image_queue.pop(0)
        key = _src_key(leftover.get("src"))
        if key and key in used_src:
            continue
        if key:
            used_src.add(key)
        parts.append(
            _figure_html(
                src=leftover.get("src"),
                alt=str(leftover.get("alt") or leftover.get("role") or "Figure"),
                caption=str(leftover.get("caption") or leftover.get("prompt") or ""),
                role=str(leftover.get("role") or ""),
                status=str(leftover.get("status") or ""),
            )
        )
    return "\n".join(parts)
