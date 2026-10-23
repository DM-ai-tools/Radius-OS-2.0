"""Everything the Manus agent is told for a site preview.

The job: rebuild the approved Phase 10 draft as a page that looks like it belongs on
the client's own website. The words are fixed (the draft was written and checked for
SEO); only the design comes from the client's site. Following the pattern that works
for Manus: a short task message, the full rules as an attached ``brief.md``, the
draft as ``draft.md``, one deliverable with a fixed name, a work method, a visual
self-check, a structured report, and no deploying anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HTML_FILENAME = "index.html"

NO_DEPLOY = "Do not deploy, publish or host anything; deliver the file only."


@dataclass
class PreviewBrief:
    client_name: str
    client_website: str
    design_page_url: str
    design_page_reason: str
    page_title: str
    page_url: str
    meta_description: str
    keyword: str
    draft_markdown: str
    image_files: list[dict[str, str]] = field(default_factory=list)  # {filename, alt, caption}
    remote_images: list[dict[str, str]] = field(default_factory=list)  # {src, alt}
    instructions: str = ""
    screenshot_names: list[str] = field(default_factory=list)


# Manus structured output: every property required, additionalProperties false.
def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_STR_LIST = {"type": "array", "items": _STR}

RESULT_SCHEMA: dict[str, Any] = _obj(
    {
        "html_filename": _STR,
        "summary": _STR,
        "design": _obj(
            {
                "reference_url": _STR,
                "header_from_site": _BOOL,
                "footer_from_site": _BOOL,
                "fonts": _STR_LIST,
                "colours": _STR_LIST,
            }
        ),
        "content_check": _obj(
            {
                "all_headings_kept": _BOOL,
                "all_links_kept": _BOOL,
                "all_images_placed": _BOOL,
                "copy_changed": _BOOL,
                "notes": _STR,
            }
        ),
        "verification": _obj({"desktop_compared": _BOOL, "mobile_compared": _BOOL, "notes": _STR}),
        "unresolved": _STR_LIST,
    }
)


def build_task_message(b: PreviewBrief) -> str:
    """The short text part of ``task.create`` (Manus caps message text at ~5,000 tokens)."""
    lines = [
        (
            f"Build a preview of a new page for {b.client_name}'s website: the attached draft "
            "(draft.md), laid out and styled exactly like the client's own site, as one "
            f"self-contained {HTML_FILENAME}."
        ),
        "",
        f"Client website: {b.client_website}",
        f"Design reference page on their site: {b.design_page_url}",
        f"Page: {b.page_title}" + (f" ({b.page_url})" if b.page_url else ""),
        "",
        (
            "The attached brief.md has every rule. Read it completely before you start and follow it exactly. "
            "The most important:"
        ),
        (
            "1. The words are final. Use draft.md's text exactly: every heading, paragraph, list, link and image, "
            "in order. Do not rewrite, shorten, add or translate copy."
        ),
        (
            "2. Everything else comes from the client's site: header, navigation, footer, fonts, colours, buttons, "
            "spacing, section styles. Measure them on the live site; do not guess."
        ),
        "3. The draft's H1 is the page's only h1; keep the heading levels.",
        "4. Place the attached images where draft.md puts them; never repeat a photo.",
        "5. Compare your page with the reference page at 1440px and 390px and fix differences.",
        (
            "6. Real links only (no ?page_id= or dead #anchors), the site's real logo with proper alt "
            "text, a button in the closing Next step section, and the head tags in brief.md "
            "(canonical, Open Graph, favicon, JSON-LD). Nothing may depend on JavaScript to appear."
        ),
        f"7. {NO_DEPLOY}",
        "",
        f"Attach the final page as {HTML_FILENAME}.",
    ]
    if b.instructions:
        lines += ["", "The user's instructions (follow them within the rules):", b.instructions[:1500]]
    if b.screenshot_names:
        lines += ["", "The user attached screenshots to show what they mean: " + ", ".join(b.screenshot_names)
                  + ". Look at them carefully."]
    return "\n".join(lines)


def build_brief(b: PreviewBrief) -> str:
    """The full rules, attached as ``brief.md``."""
    images = "\n".join(
        f"- `{img['filename']}`: alt text \"{img.get('alt', '')}\"" + (f", caption \"{img['caption']}\"" if img.get("caption") else "")
        for img in b.image_files
    ) or "- (none attached)"
    remote = "\n".join(f"- {img['src']} (alt \"{img.get('alt', '')}\")" for img in b.remote_images)
    user_requests = (
        f"\n## The user's requests\n\nFollow these first, within the hard rules:\n\n{b.instructions[:4000]}\n"
        if b.instructions
        else ""
    )
    return f"""# Site preview brief

## The job

Turn the attached `draft.md` into a page that looks like it already belongs on
{b.client_name}'s website ({b.client_website}). It is a preview for the team to
review before the page is published, so it must look like the real site: same
header, navigation, footer, fonts, colours, buttons and section styles. Deliver one
self-contained `{HTML_FILENAME}`.

The words are not yours to change. The draft was written and checked for search
(keywords, headings, internal links). Your work is the design.
{user_requests}
## The page

- Title (H1): {b.page_title}
- Address on the site: {b.page_url or "(new page)"}
- Meta description: {b.meta_description or "(none)"}
- Search keyword: {b.keyword or "(none)"}

## Design reference

- Reference page: {b.design_page_url} ({b.design_page_reason})
- Also look at the home page ({b.client_website}) and one or two inner pages of the
  same kind, to see which patterns repeat.

## How to work

1. Open the reference page at 1440px wide. Scroll the whole page, dismiss cookie
   banners, take full-page and per-section screenshots. Repeat at 390px.
2. Measure with `getComputedStyle`: fonts (family, sizes, weights, line heights),
   colours by role (body text, headings, links, primary and secondary buttons
   including text and hover, header, navigation, footer, section backgrounds,
   cards), content width, spacing between sections, border radii, image sizes.
3. Copy the site's header (logo, navigation links, header button) and footer
   (columns, links, contact details, social links) as a visitor sees them. Use the
   real logo file (SVG first, else the largest raster), never a favicon or a
   screenshot crop. Firecrawl can scrape what the browser misses.
4. Lay out the draft in the site's own content-page pattern: the way the reference
   page styles its hero or title area, body text, headings, lists, images,
   quotes, tables and calls to action.
5. Build the page, then check it visually (see below), fix, and attach it.

## The draft: rules for the words

- Use `draft.md` exactly. Every heading, paragraph, list item, table, link, call to
  action and FAQ stays, word for word, in the same order.
- Do not rewrite, shorten, summarise, add, translate or reorder copy. Do not add
  testimonials, statistics, claims or sections the draft does not have. Fixing an
  obvious typo is the only allowed change; report it under `content_check.notes`.
- The draft's H1 is the page's only `h1`. Keep every other heading at the level the
  draft gives it.
- Keep every link with its exact text and address. Internal links stay internal.
- The site's own header, navigation and footer text is not part of the draft and
  is copied from the live site.

## Head and page metadata

- `<title>`: the page title. `<meta name="description">`: the meta description.
- `<link rel="canonical" href="{b.page_url or b.client_website}">`.
- Open Graph and X card: `og:title`, `og:description`, `og:url` (the canonical
  address), `og:type` `website`, `og:image` (the hero image's address if it has a
  public one, else the site's own share image from its home page), and
  `twitter:card` `summary_large_image`.
- The site's favicon: copy the `<link rel="icon">` (and apple-touch-icon) tags from
  the live site, with absolute addresses.
- JSON-LD in one `<script type="application/ld+json">`: `Service` (or `Article` for
  a blog post) for this page, `FAQPage` with every question and answer if the draft
  has an FAQ, and `Organization` or `LocalBusiness` only with the name, address,
  phone and logo exactly as the live site shows them. Never invent a fact.

## Navigation, links and calls to action

- Navigation and footer links go to the client's real pages, by their final address:
  open each menu link and use the address it lands on. Never use `?page_id=` style
  addresses, and never a `#section` link that has no matching section on this page.
  A menu parent that only opens a submenu links to its own page if it has one,
  otherwise it is a button that opens the submenu.
- Keep the site's header button as the site shows it (text and destination).
- The draft's closing "Next step" section ends with a real button styled like the
  site's primary button, linking to the site's main booking or contact page (the
  page the header button goes to). Use the button text the site uses for that action.
- The logo, in the header and the footer, is the site's real logo image with
  `alt="{b.client_name} logo"`, linking to the home page. Never stand in a symbol,
  an emoji or text for the logo, and never reuse another image's alt text.

## Images

Attached image files, placed where `draft.md` references them by file name:
{images}
{("Remote images the draft already uses (keep their addresses):" + chr(10) + remote) if remote else ""}

- Put each image exactly where the draft references it, with its alt text. A
  caption only when the draft gives one; never print a file name or role as caption.
- Attached files have no public address, so embed them as data URIs, compressed:
  WebP or JPEG at quality about 80, at most 1600px on the long side.
- Images from the client's site (logo, icons, photos) keep their absolute https
  addresses; do not embed them.
- Never use the same photo twice. The logo may repeat.
- No other photos unless the reference page's pattern needs one (for example a hero
  image). Then use a photo from the client's own site, never a stock or generated one.

## Quality floor

- Body text at least 16px; line length 45-75 characters; WCAG AA contrast.
- Semantic HTML: one h1, ordered headings, real lists, `details/summary` for FAQs if
  the site uses accordions.
- No overlapping text at 1440, 1280, 1024, 768 or 390px; no horizontal scroll at
  390px; tap targets at least 44px.
- Everything works without JavaScript: no content or control may start hidden and
  only appear by script. Script may only add behaviour (a sticky header, a menu
  toggle); a back-to-top link, if the site has one, is a plain visible link.

## Visual check before delivering

Compare your page with the reference page side by side at 1440px and 390px:
header and footer identical; same fonts, sizes and colours by role; the content
area styled like the site's own pages; every heading, link and image of the draft
present and in order; no repeated photos; no broken images or dead links. Fix and
check again.

## Delivery

- One complete HTML file named `{HTML_FILENAME}`: inline CSS and JS, the draft's
  attached images as data URIs, every other image and link as an absolute https
  address on the client's site, Google Fonts allowed, no relative paths.
- Attach it to your final message and fill in the report.
- {NO_DEPLOY}
- Decide yourself by following this brief. Ask a question only if you are truly
  blocked; an assistant answers for the user.
"""


def build_edit_message(instructions: str, screenshot_names: list[str]) -> str:
    """A change request on the same task (it keeps what it learned about the site)."""
    lines = [
        "The user wants these changes to the page you delivered:",
        "",
        instructions[:4000],
        "",
        (
            f"Apply them to your latest {HTML_FILENAME} and keep everything else exactly as it is. "
            "Keep following brief.md: the draft's words stay exactly as they are. Re-check the changed "
            f"parts at 1440px and 390px, then attach the complete updated {HTML_FILENAME}. {NO_DEPLOY}"
        ),
    ]
    if screenshot_names:
        lines.insert(3, "Screenshots attached to show what they mean: " + ", ".join(screenshot_names) + ".")
    return "\n".join(lines)


NUDGE_MESSAGE = (
    f"Please attach the complete final page as a single self-contained {HTML_FILENAME} file "
    f"to your reply. {NO_DEPLOY}"
)


def repeat_fix_message(repeats: list[dict[str, Any]]) -> str:
    listed = "\n".join(
        f"- {r['src'][:120]} is used {r['count']} times" + (f" (alt: {', '.join(r['alts'][:3])})" if r.get("alts") else "")
        for r in repeats
    )
    return (
        "Your page uses the same photo more than once:\n"
        f"{listed}\n\n"
        "Keep each photo once. Replace every repeat with another image from the draft or the "
        "client's own site, or build that spot without a photo. Keep everything else exactly "
        f"as it is, re-check, and attach the complete {HTML_FILENAME}. {NO_DEPLOY}"
    )


def answer_context(b: PreviewBrief) -> str:
    """What the answering model knows about the job (no secrets, no draft text)."""
    return (
        f"A design agent is building a review preview of a new page for {b.client_name} "
        f"({b.client_website}), titled \"{b.page_title}\". It must lay out an approved draft "
        f"exactly, word for word, in the client's own site design, using {b.design_page_url} "
        "as the design reference, and deliver one self-contained index.html. The words may "
        "not change; the design comes from the client's live site; nothing may be deployed, "
        "published, emailed or bought, and no accounts or credentials may be connected."
    )
