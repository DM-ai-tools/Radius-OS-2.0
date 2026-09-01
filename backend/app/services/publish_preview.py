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
from typing import Any
from urllib.parse import urlparse

NEUTRAL_PRIMARY = "#334155"
NEUTRAL_FONT = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"


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
        return str(block.get("after") or "")
    return str(block or "")


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


def render_preview_html(
    *,
    client_name: str,
    page: dict[str, Any],
    content_html: str,
    brand: dict[str, Any],
    layout: dict[str, Any] | None = None,
    target_status: str = "draft",
) -> str:
    """A self-contained HTML document for reviewer eyeballs. Never written to the CMS."""
    style = _brand_style(brand)
    layout = layout or {}
    logo = (brand.get("logo") or {}).get("url") if brand.get("available") else None
    title = _title_of(page)
    meta = _meta_of(page)
    url = str(page.get("url") or "")

    notices: list[str] = []
    if style["brand_applied"] != "yes":
        notices.append(
            f"Brand assets unavailable ({_esc(brand.get('error') or 'not fetched')}) — "
            "neutral styling shown, not the client's real design."
        )
    if not layout.get("available"):
        notices.append("No live reference page scraped — layout is generic.")
    if target_status != "publish":
        notices.append(f"Target CMS status: <strong>{_esc(target_status)}</strong> — not live.")

    notice_html = "".join(f"<li>{n}</li>" for n in notices)
    swatches = "".join(
        f'<span class="sw" style="background:{_esc(c.get("hex"))}" title="{_esc(c.get("hex"))}"></span>'
        for c in (brand.get("palette") or [])[:6]
        if isinstance(c, dict) and c.get("hex")
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Preview — {_esc(title)}</title>
<style>
  :root {{ --brand: {style["primary"]}; }}
  body {{ margin:0; font-family:{style["font"]}; color:#0f172a; background:#f8fafc; }}
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
  .hdr {{ border-top:4px solid var(--brand); padding:16px 22px; display:flex;
          align-items:center; gap:12px; border-bottom:1px solid #eef2f7; }}
  .hdr img {{ max-height:34px; max-width:150px; }}
  .body {{ padding:22px; }}
  .body h1 {{ font-size:28px; margin:0 0 12px; color:var(--brand); }}
  .body h2 {{ font-size:19px; margin:22px 0 6px; }}
  .body h3 {{ font-size:15px; margin:14px 0 4px; }}
  .body p {{ line-height:1.6; color:#334155; }}
  .body [data-placeholder] {{ color:#94a3b8; }}
  .sw {{ display:inline-block; width:16px; height:16px; border-radius:3px;
         border:1px solid rgba(0,0,0,.15); margin-right:3px; vertical-align:middle; }}
</style></head>
<body>
<div class="bar"><span><strong>DRY-RUN PREVIEW</strong> — no CMS write has occurred</span>
<span>{_esc(client_name)} {swatches}</span></div>
{f'<div class="warn"><strong>Preview caveats</strong><ul>{notice_html}</ul></div>' if notices else ''}

<div class="serp">
  <div class="u">{_esc(url)}</div>
  <div class="t">{_esc(title)}</div>
  <div class="d">{_esc(meta)}</div>
</div>

<div class="page">
  <div class="hdr">
    {f'<img src="{_esc(logo)}" alt="{_esc(client_name)} logo">' if logo else f'<strong>{_esc(client_name)}</strong>'}
  </div>
  <div class="body">{content_html}</div>
</div>
</body></html>"""


def build_page_preview(
    *,
    client_name: str,
    page: dict[str, Any],
    brief: dict[str, Any] | None,
    brand: dict[str, Any],
    layout: dict[str, Any] | None,
    target_status: str,
    slug: str,
) -> dict[str, Any]:
    """Preview + the exact CMS payload, so reviewer and publisher never diverge."""
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
            target_status=target_status,
        ),
        "brand_applied": _brand_style(brand)["brand_applied"] == "yes",
        "has_written_copy": "data-placeholder" not in content_html,
    }
