"""Checks on a draft before a preview, and on the page the agent delivers.

Each failed check on a delivered page becomes a review note shown with the preview.
The words matter most: the draft was checked for SEO, so a rewritten preview would
mislead the reviewer. The page checks mirror what a reviewer flagged on the first
real preview (2026-10-16): placeholders, broken links, missing head tags, no button.
"""

from __future__ import annotations

import html as htmllib
import re
from typing import Any

from app.services.site_preview.images import find_repeated_images

MIN_COPY_MATCH = 0.9

# Same markers publishing refuses (wp_content.PLACEHOLDER_MARKERS), plus the figure
# slots of images that were never generated.
PLACEHOLDERS = ("[AUTHOR INPUT REQUIRED", "[VERIFY", "[FIGURE ", "copy to be written before go-live", "Lorem ipsum")
# Sentences only the outline-and-template writer produces. A draft containing them is
# filler from a failed model call, not copy (see create_content.write_one_page).
TEMPLATE_FILLER = (
    "Priorities under this heading include",
    "keeps this section on",
    "explains the practical detail a buyer needs before they enquire",
    "Opening: direct answer to the query",
    "Match this page type — not a generic blog template",
)


_VERIFY_MARKER = re.compile(r"[ \t]*\[VERIFY\b[^\]\n]*\]?", re.IGNORECASE)


def draft_problems(markdown: str) -> list[str]:
    """Why a draft is not ready to preview. Empty means it is ready.

    ``[VERIFY]`` used to be written beside unsourced numbers. It is stripped
    here so a leftover marker cannot block the preview.
    """
    text = _VERIFY_MARKER.sub("", markdown or "")
    problems: list[str] = []
    filler = sum(text.count(f) for f in TEMPLATE_FILLER)
    if filler:
        problems.append(
            "it is template filler, not written copy (the writing model was unavailable when it "
            "was drafted). Draft the page again"
        )
    author = text.count("[AUTHOR INPUT REQUIRED")
    if author:
        problems.append(f"{author} \"[AUTHOR INPUT REQUIRED]\" placeholder(s) still need writing")
    for marker in ("copy to be written before go-live", "Lorem ipsum"):
        if marker.lower() in text.lower():
            problems.append(f"it contains \"{marker}\"")
    return problems


def strip_missing_figures(markdown: str) -> str:
    """Drop ``[FIGURE role] caption`` lines: slots of images that were never generated."""
    out = re.sub(r"(?im)^\s*\[FIGURE\s+[^\]]*\][^\n]*\n?", "", markdown or "")
    return re.sub(r"\n{3,}", "\n\n", out)


def _norm(text: str) -> str:
    text = htmllib.unescape(text or "").lower()
    text = re.sub(r"[‘’“”]", "'", text)
    return " ".join(re.findall(r"[a-z0-9']+", text))


def _visible_text(page: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1>", " ", page)
    return _norm(re.sub(r"<[^>]+>", " ", body))


def draft_parts(markdown: str) -> dict[str, list[str]]:
    """Headings, link addresses and body sentences of the draft."""
    headings = [m.group(2).strip() for m in re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*#*$", markdown)]
    links = [m.group(2).strip() for m in re.finditer(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)", markdown)]
    body = re.sub(r"(?m)^#{1,6}\s+.*$", " ", markdown)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    body = re.sub(r"[*_`>|#-]+", " ", body)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}", body) if len(s.split()) >= 5]
    return {"headings": headings, "links": links, "sentences": sentences}


def _head(page: str) -> str:
    m = re.search(r"(?is)<head\b.*?</head>", page)
    return m.group(0) if m else page[:20000]


def _meta(head: str, attr: str, name: str) -> bool:
    return bool(re.search(rf"""(?i)<meta\b[^>]*\b{attr}\s*=\s*["']{re.escape(name)}["'][^>]*>""", head))


def page_problems(page: str, *, client_name: str) -> list[str]:
    """Page-level issues: head tags, links, logo, call to action, placeholders."""
    notes: list[str] = []
    head = _head(page)
    missing = []
    if not re.search(r"""(?i)<link\b[^>]*\brel\s*=\s*["']canonical["']""", head):
        missing.append("canonical link")
    for prop in ("og:title", "og:description", "og:url", "og:image"):
        if not _meta(head, "property", prop):
            missing.append(prop)
    if not _meta(head, "name", "twitter:card"):
        missing.append("twitter:card")
    if not re.search(r"""(?i)<link\b[^>]*\brel\s*=\s*["'][^"']*\bicon\b""", head):
        missing.append("favicon")
    if not re.search(r"""(?i)<script\b[^>]*type\s*=\s*["']application/ld\+json["']""", page):
        missing.append("JSON-LD structured data")
    if missing:
        notes.append("Missing in <head>: " + ", ".join(missing) + ".")

    text = _visible_text(page)
    for marker in PLACEHOLDERS:
        if _norm(marker) and _norm(marker) in text:
            notes.append(f"Placeholder text \"{marker.strip()}\" is visible on the page.")

    hrefs = re.findall(r"""(?i)<a\b[^>]*\bhref\s*=\s*["']([^"']*)["']""", page)
    page_ids = sorted({h for h in hrefs if re.search(r"[?&]page_id=\d+", h)})
    if page_ids:
        notes.append(f"{len(page_ids)} link(s) use a WordPress ?page_id= address instead of the page's real address.")
    ids = set(re.findall(r"""(?i)\bid\s*=\s*["']([^"']+)["']""", page))
    dead = sorted({h for h in hrefs if h.startswith("#") and len(h) > 1 and h[1:] not in ids})
    if dead:
        notes.append("Link(s) to sections that are not on this page: " + ", ".join(dead[:5]) + ".")

    logos = [
        tag for tag in re.findall(r"<img\b[^>]*>", page, re.IGNORECASE)
        if re.search(r"logo", tag, re.IGNORECASE)
    ]
    first_name = (client_name or "").split()[0].lower() if client_name else ""
    for tag in logos[:2]:
        alt = re.search(r"""\balt\s*=\s*["']([^"']*)["']""", tag, re.IGNORECASE)
        alt_text = (alt.group(1) if alt else "").lower()
        if "logo" not in alt_text and (not first_name or first_name not in alt_text):
            notes.append(f"The logo's alt text does not name the logo or {client_name}: \"{alt_text or 'none'}\".")
            break

    tail = page[max(0, page.lower().rfind("<h2")):] if "<h2" in page.lower() else ""
    tail = re.split(r"(?i)<footer\b", tail)[0]
    if tail and not re.search(r"(?i)<a\b[^>]*\bhref\s*=\s*[\"'][^\"'#]", tail):
        notes.append("The closing section has no button or link to act on.")
    return notes


def review_notes(
    page: str,
    *,
    draft_markdown: str,
    title: str,
    report: dict[str, Any] | None,
    client_name: str = "",
) -> list[str]:
    """What a reviewer must know before trusting the preview. Empty means all checks passed."""
    notes: list[str] = []
    text = _visible_text(page)
    parts = draft_parts(draft_markdown)

    h1s = re.findall(r"(?is)<h1\b[^>]*>(.*?)</h1>", page)
    if len(h1s) != 1:
        notes.append(f"The page has {len(h1s)} h1 headings; it should have exactly one.")
    elif title and _norm(title) not in _norm(re.sub(r"<[^>]+>", " ", h1s[0])):
        notes.append("The h1 is not the draft's title.")

    missing_h = [h for h in parts["headings"] if _norm(h) and _norm(h) not in text]
    if missing_h:
        notes.append(f"{len(missing_h)} draft heading(s) missing or reworded: " + "; ".join(missing_h[:5]))

    hrefs = set(re.findall(r"""(?i)<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", page))
    missing_l = [link for link in parts["links"] if link not in hrefs and link.rstrip("/") not in {h.rstrip("/") for h in hrefs}]
    if missing_l:
        notes.append(f"{len(missing_l)} draft link(s) missing: " + ", ".join(missing_l[:5]))

    if parts["sentences"]:
        kept = sum(1 for s in parts["sentences"] if _norm(s) in text)
        share = kept / len(parts["sentences"])
        if share < MIN_COPY_MATCH:
            notes.append(f"Only {share:.0%} of the draft's sentences appear word for word; the copy was changed.")

    for repeat in find_repeated_images(page):
        notes.append(f"A photo is used {repeat['count']} times ({repeat['src'][:80]}).")

    notes.extend(page_problems(page, client_name=client_name))

    rep = report or {}
    verification = rep.get("verification") or {}
    if not (verification.get("desktop_compared") and verification.get("mobile_compared")):
        notes.append("The agent did not confirm comparing the page at both desktop and mobile widths.")
    content = rep.get("content_check") or {}
    if content.get("copy_changed"):
        notes.append("The agent reports changing the draft's copy: " + str(content.get("notes") or "")[:200])
    design = rep.get("design") or {}
    if rep and not (design.get("header_from_site") and design.get("footer_from_site")):
        notes.append("The agent did not confirm copying the site's header and footer.")
    for item in list(rep.get("unresolved") or [])[:6]:
        notes.append(f"Unresolved: {str(item)[:200]}")
    return notes
