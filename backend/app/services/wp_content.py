"""Publish-grade content rendering and pre-flight validation.

Separate from ``publish_preview.markdown_to_content_html`` on purpose. That renderer
is a *reviewer* view: it escapes everything, including inline markdown, so a human
sees exactly the literal draft text. What gets written to a CMS has the opposite
requirement — ``[text](url)`` must become a real link or the internal/external
linking the SEO package specifies simply does not exist on the published page.

Everything here is pure: markdown in, sanitized HTML out, or a payload in and a list
of validation issues out. No network, no CMS knowledge.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

# Text the pipeline emits for sections that were outlined but never written. If any
# of these reach a CMS payload the run is publishing a skeleton, not an article.
PLACEHOLDER_MARKERS = (
    'data-placeholder="true"',
    "copy to be written before go-live",
    "[AUTHOR INPUT REQUIRED]",
    "[VERIFY]",
    "TODO",
    "Lorem ipsum",
    "{{",
)

# Internal pipeline variables that must never appear in published copy.
_PIPELINE_LEAK = re.compile(
    r"\{\{[^}]+\}\}|\{'[a-z_]+':|<PLACEHOLDER|\[INSERT [A-Z ]+\]", re.IGNORECASE
)

# Schemes we allow in hrefs/srcs. Anything else (javascript:, vbscript:, file:)
# is dropped rather than published.
_SAFE_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

# Where create_content persists generated images. These are server-local paths that
# only become publishable once uploaded to the target's media library.
LOCAL_MEDIA_PREFIX = "/media/"

_ALLOWED_TAGS = frozenset(
    {
        "p", "br", "strong", "em", "code", "pre", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "a", "img", "figure", "figcaption",
        "table", "thead", "tbody", "tr", "th", "td", "div", "span",
    }
)


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def safe_href(raw: str) -> str | None:
    """Return the href if it is safe to publish, else None.

    Relative and root-relative links are kept as-is — they are how internal links
    are expressed before the site's domain is known.
    """
    href = str(raw or "").strip()
    if not href:
        return None
    if href.startswith(("/", "#", "./", "../")):
        return href
    scheme = (urlparse(href).scheme or "").lower()
    if not scheme:
        # Bare "example.com/page" — treat as https rather than dropping the link.
        return f"https://{href}" if "." in href.split("/")[0] else None
    return href if scheme in _SAFE_SCHEMES else None


def _inline(text: str) -> str:
    """Inline markdown → HTML, applied to already-escaped text.

    Escaping runs first, so ``&``/``<``/``>`` are already entities and the markdown
    punctuation that survives (``*``, ``[``, ``]``, backtick) is unambiguous.
    """
    out = _esc(text)

    def _link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        href = safe_href(html.unescape(target))
        if not href:
            return label  # unsafe scheme — keep the words, drop the link
        rel = ""
        if href.startswith(("http://", "https://")):
            rel = ' rel="noopener"'
        return f'<a href="{_esc(href)}"{rel}>{label}</a>'

    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*\*([^*]+)\*\*\*", r"<strong><em>\1</em></strong>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", out)
    return out


def _table_html(rows: list[str]) -> str:
    """Render a GitHub-style markdown table. ``rows`` excludes the divider line."""
    def _cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    if not rows:
        return ""
    head = _cells(rows[0])
    body = [_cells(r) for r in rows[1:]]
    thead = "".join(f"<th>{_inline(c)}</th>" for c in head)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>" for cells in body
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"


def _is_table_divider(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", line)) and "-" in line


def markdown_to_publish_html(
    markdown: str,
    *,
    images: list[dict[str, Any]] | None = None,
    media_base: str | None = None,
) -> str:
    """Draft markdown → CMS-ready HTML.

    Handles what the preview renderer does not: inline links, emphasis, inline code,
    ordered lists, blockquotes and tables. Images resolve against ``media_base`` and
    a figure with no usable source is omitted rather than shipped broken.
    """
    from app.services.publish_preview import draft_body_markdown

    body = draft_body_markdown(markdown)
    if not body:
        return ""

    queue: list[dict[str, Any]] = [i for i in (images or []) if isinstance(i, dict)]
    used_src: set[str] = set()

    def _abs_src(src: Any) -> str:
        s = str(src or "").strip()
        if not s:
            return ""
        if s.startswith(("http://", "https://")):
            return s
        base = str(media_base or "").rstrip("/")
        if base and s.startswith("/"):
            return f"{base}{s}"
        if s.startswith(LOCAL_MEDIA_PREFIX):
            # A locally-persisted draft image. Kept as-is so the publisher can
            # upload it and rewrite the src; anything still pointing here at write
            # time is stripped by strip_unresolved_images().
            return s
        # Any other relative or data: source cannot be resolved to something the
        # CMS can serve. Omit it rather than publishing a broken image.
        return ""

    def _src_key(src: Any) -> str:
        return str(src or "").strip().split("?")[0].rstrip("/")

    def _take(role: str | None = None, src: str | None = None) -> dict[str, Any] | None:
        if not queue:
            return None
        key = _src_key(src)
        if key:
            for idx, img in enumerate(queue):
                got = _src_key(img.get("src"))
                if got and (got == key or got.endswith(key) or key.endswith(got)):
                    used_src.add(got)
                    used_src.add(key)
                    return queue.pop(idx)
        if role:
            for idx, img in enumerate(queue):
                if str(img.get("role") or "").lower() == role.lower():
                    return queue.pop(idx)
        if src:
            return None
        return queue.pop(0)

    def _figure(src: Any, alt: str, caption: str) -> str:
        abs_src = _abs_src(src)
        if not abs_src:
            return ""  # never publish a broken image
        out = f'<figure><img src="{_esc(abs_src)}" alt="{_esc(alt)}" loading="lazy" />'
        if caption:
            out += f"<figcaption>{_inline(caption)}</figcaption>"
        return out + "</figure>"

    lines = body.split("\n")
    parts: list[str] = []
    para: list[str] = []
    ul: list[str] = []
    ol: list[str] = []
    quote: list[str] = []

    def flush_para() -> None:
        text = " ".join(para).strip()
        para.clear()
        if text:
            parts.append(f"<p>{_inline(text)}</p>")

    def flush_ul() -> None:
        if ul:
            parts.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in ul) + "</ul>")
            ul.clear()

    def flush_ol() -> None:
        if ol:
            parts.append("<ol>" + "".join(f"<li>{_inline(i)}</li>" for i in ol) + "</ol>")
            ol.clear()

    def flush_quote() -> None:
        if quote:
            inner = " ".join(quote).strip()
            parts.append(f"<blockquote><p>{_inline(inner)}</p></blockquote>")
            quote.clear()

    def flush_all() -> None:
        flush_para()
        flush_ul()
        flush_ol()
        flush_quote()

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            flush_all()
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_all()
            level = min(len(heading.group(1)), 6)
            parts.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        if line.startswith("> "):
            flush_para()
            flush_ul()
            flush_ol()
            quote.append(line[2:].strip())
            i += 1
            continue

        # Table: a header row followed by a |---|---| divider.
        if line.startswith("|") and i + 1 < len(lines) and _is_table_divider(lines[i + 1]):
            flush_all()
            rows = [line]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(lines[j].strip())
                j += 1
            parts.append(_table_html(rows))
            i = j
            continue

        if line.startswith("![") and "](" in line and line.endswith(")"):
            flush_all()
            alt_end = line.index("](")
            alt, src = line[2:alt_end], line[alt_end + 2 : -1]
            caption = ""
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if nxt.startswith("*") and nxt.endswith("*") and len(nxt) > 2:
                caption = nxt.strip("*")
                i += 1
            _take(src=src)
            key = _src_key(src)
            if key:
                used_src.add(key)
            fig = _figure(src, alt, caption)
            if fig:
                parts.append(fig)
            i += 1
            continue

        fig_m = re.match(r"^\[FIGURE\s+([^\]]+)\]\s*(.*)$", line, flags=re.IGNORECASE)
        if fig_m:
            flush_all()
            role, caption = fig_m.group(1).strip(), fig_m.group(2).strip()
            matched = _take(role) or {}
            fig = _figure(
                matched.get("src"),
                str(matched.get("alt") or role or "Figure"),
                caption or str(matched.get("caption") or ""),
            )
            if fig:
                parts.append(fig)
            i += 1
            continue

        ol_m = re.match(r"^\d+[.)]\s+(.*)$", line)
        if ol_m:
            flush_para()
            flush_ul()
            flush_quote()
            ol.append(ol_m.group(1).strip())
            i += 1
            continue

        if line.startswith(("- ", "* ", "+ ")):
            flush_para()
            flush_ol()
            flush_quote()
            ul.append(line[2:].strip())
            i += 1
            continue

        flush_ul()
        flush_ol()
        flush_quote()
        para.append(line)
        i += 1

    flush_all()

    for leftover in queue:
        key = _src_key(leftover.get("src"))
        if key and key in used_src:
            continue
        if key:
            used_src.add(key)
        fig = _figure(
            leftover.get("src"),
            str(leftover.get("alt") or leftover.get("role") or "Figure"),
            str(leftover.get("caption") or ""),
        )
        if fig:
            parts.append(fig)

    return "\n".join(p for p in parts if p)


# --- validation -------------------------------------------------------------


def strip_unresolved_images(content_html: str) -> tuple[str, int]:
    """Remove figures whose image never got a publicly-fetchable src.

    Called after media upload: anything still pointing at a server-local path would
    render as a broken image on the client's site, so the figure is dropped and the
    count reported rather than published.
    """
    removed = 0

    def _drop(match: re.Match[str]) -> str:
        nonlocal removed
        block = match.group(0)
        src = re.search(r'<img\b[^>]*\bsrc="([^"]*)"', block, re.IGNORECASE)
        if src and not src.group(1).startswith(("http://", "https://")):
            removed += 1
            return ""
        return block

    out = re.sub(r"<figure\b.*?</figure>", _drop, content_html or "", flags=re.DOTALL | re.IGNORECASE)

    def _drop_bare(match: re.Match[str]) -> str:
        nonlocal removed
        tag = match.group(0)
        src = re.search(r'\bsrc="([^"]*)"', tag, re.IGNORECASE)
        if src and not src.group(1).startswith(("http://", "https://")):
            removed += 1
            return ""
        return tag

    out = re.sub(r"<img\b[^>]*>", _drop_bare, out, flags=re.IGNORECASE)
    return out, removed


def find_placeholders(content_html: str) -> list[str]:
    """Placeholder markers present in the content, in the order they appear."""
    found = [m for m in PLACEHOLDER_MARKERS if m.lower() in (content_html or "").lower()]
    if _PIPELINE_LEAK.search(content_html or ""):
        found.append("pipeline_variable_leak")
    return found


def check_heading_structure(content_html: str) -> list[str]:
    """Structural heading problems: more than one H1, or a skipped level."""
    issues: list[str] = []
    levels = [int(m) for m in re.findall(r"<h([1-6])\b", content_html or "", re.IGNORECASE)]
    if levels.count(1) > 1:
        issues.append(f"{levels.count(1)} H1 headings — a page must have exactly one")
    previous = 0
    for level in levels:
        if previous and level > previous + 1:
            issues.append(f"heading level jumps from H{previous} to H{level}")
            break
        previous = level
    return issues


def check_markup(content_html: str) -> list[str]:
    """Cheap well-formedness checks — unclosed or unknown tags."""
    issues: list[str] = []
    stack: list[str] = []
    void = {"br", "img", "hr", "meta", "input"}
    for match in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>", content_html or ""):
        closing, tag, self_closing = match.group(1), match.group(2).lower(), match.group(3)
        if tag not in _ALLOWED_TAGS:
            issues.append(f"unexpected tag <{tag}>")
            continue
        if tag in void or self_closing:
            continue
        if closing:
            if not stack or stack[-1] != tag:
                issues.append(f"mismatched closing tag </{tag}>")
                if tag in stack:
                    while stack and stack.pop() != tag:
                        pass
            else:
                stack.pop()
        else:
            stack.append(tag)
    if stack:
        issues.append(f"unclosed tag(s): {', '.join(dict.fromkeys(stack))}")
    return issues[:6]


def check_links(content_html: str) -> list[str]:
    """Links that would publish broken or unsafe."""
    issues: list[str] = []
    for href in re.findall(r'<a\b[^>]*href="([^"]*)"', content_html or "", re.IGNORECASE):
        cleaned = html.unescape(href)
        if not cleaned.strip():
            issues.append("link with an empty href")
        elif safe_href(cleaned) is None:
            issues.append(f"unsafe link target: {cleaned[:60]}")
    return issues[:6]


def check_images(content_html: str) -> list[str]:
    issues: list[str] = []
    for tag in re.findall(r"<img\b[^>]*>", content_html or "", re.IGNORECASE):
        src = re.search(r'src="([^"]*)"', tag, re.IGNORECASE)
        alt = re.search(r'alt="([^"]*)"', tag, re.IGNORECASE)
        if not src or not src.group(1).strip():
            issues.append("image with no src")
        elif not src.group(1).startswith(("http://", "https://", "/")):
            issues.append(f"image src is not resolvable: {src.group(1)[:60]}")
        if not alt or not alt.group(1).strip():
            issues.append("image missing alt text")
    return issues[:6]


def validate_publish_payload(
    payload: dict[str, Any],
    *,
    min_content_chars: int = 400,
    require_written_copy: bool = True,
) -> dict[str, Any]:
    """Gate a CMS payload before it reaches WordPress.

    Returns ``{"ok", "errors", "warnings"}``. Errors block the write; warnings are
    reported and published. Nothing here talks to the network, so the caller can run
    it in preview mode and show a reviewer exactly what would block a real run.
    """
    errors: list[str] = []
    warnings: list[str] = []

    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "")
    slug = str(payload.get("slug") or "").strip()

    if not title:
        errors.append("title is empty")
    elif len(title) > 200:
        warnings.append(f"title is {len(title)} characters — WordPress may truncate it")

    if not content.strip():
        errors.append("content is empty")
    else:
        text_only = re.sub(r"<[^>]+>", " ", content)
        text_only = html.unescape(text_only)
        if len(text_only.strip()) < min_content_chars:
            errors.append(
                f"content is {len(text_only.strip())} characters of text — "
                f"below the {min_content_chars} minimum"
            )

    if not slug:
        errors.append("slug is empty")
    elif not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        errors.append(f"slug '{slug}' is not a valid WordPress slug")

    placeholders = find_placeholders(content)
    if placeholders:
        target = errors if require_written_copy else warnings
        target.append(
            "content still contains unwritten placeholders: " + ", ".join(placeholders[:4])
        )

    errors.extend(check_markup(content))
    errors.extend(check_links(content))
    warnings.extend(check_heading_structure(content))
    warnings.extend(check_images(content))

    excerpt = str(payload.get("excerpt") or "").strip()
    if not excerpt:
        warnings.append("no excerpt/meta description — the SERP snippet will be auto-generated")
    elif len(excerpt) > 160:
        warnings.append(f"meta description is {len(excerpt)} characters — over the 160 guideline")

    return {"ok": not errors, "errors": errors, "warnings": warnings}
