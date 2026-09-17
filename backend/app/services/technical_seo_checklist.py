"""Phase 7 targeting checklist — 12 groups the technical SEO audit must cover.

Statuses are honest:
- issue: the report already has a finding for this field
- checked: a signal exists and no matching issue was raised
- not_measured: this pass did not collect evidence (never invent a pass)
"""

from __future__ import annotations

from typing import Any

from app.services.technical_seo_schemas import AUDIT_GROUPS, normalize_category

_HOMEPAGE_FIELDS = {
    "robots.txt",
    "canonical",
    "canonical existence",
    "title",
    "meta description",
    "H1",
    "HTTPS",
    "viewport",
    "responsive",
    "security headers",
}
_FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "robots.txt": ("robots.txt", "robots txt", "disallow"),
    "sitemap": ("sitemap",),
    "crawlable URLs": ("crawl", "blocked"),
    "blocked resources": ("blocked", "css", "javascript blocked"),
    "crawl depth": ("depth", "click depth"),
    "orphan pages": ("orphan",),
    "GSC indexed/excluded": ("gsc", "search console", "indexed", "excluded"),
    "noindex": ("noindex",),
    "robots blocking": ("robots", "x-robots"),
    "canonical": ("canonical",),
    "discovered but not indexed": ("discovered", "not indexed"),
    "crawled but not indexed": ("crawled", "not indexed"),
    "soft 404": ("soft 404", "soft-404"),
    "HTTP/HTTPS": ("https", "http", "mixed"),
    "www/non-www": ("www",),
    "trailing slash": ("trailing slash", "slash"),
    "parameters": ("parameter", "query string"),
    "duplicate URLs": ("duplicate url", "duplicate content"),
    "case sensitivity": ("case",),
    "redirect chains": ("redirect", "chain", "loop"),
    "canonical existence": ("canonical", "missing canonical"),
    "canonical validity": ("canonical",),
    "canonical conflicts": ("canonical conflict", "canonical"),
    "Google-selected canonical": ("google-selected", "selected canonical"),
    "duplicate content": ("duplicate",),
    "indexable URLs": ("sitemap", "indexable"),
    "200 status": ("4xx", "404", "200"),
    "canonical URLs": ("canonical",),
    "no noindex URLs": ("noindex", "sitemap"),
    "no redirects": ("redirect", "sitemap"),
    "no 404s": ("404", "4xx"),
    "lastmod accuracy": ("lastmod",),
    "important pages included": ("sitemap", "missing from sitemap"),
    "important pages": ("important", "hub"),
    "contextual links": ("anchor", "internal link"),
    "anchor text": ("anchor",),
    "broken links": ("broken", "4xx"),
    "redirected internal links": ("redirect", "internal link"),
    "title": ("title",),
    "meta description": ("meta description", "description"),
    "H1": ("h1",),
    "H2/H3": ("h2", "h3", "heading"),
    "image alt": ("alt", "image"),
    "duplicate metadata": ("duplicate title", "duplicate meta"),
    "thin content": ("thin",),
    "raw vs rendered HTML": ("render", "raw html", "javascript"),
    "JS links": ("js link", "javascript link", "render"),
    "JS content": ("js content", "rendered content"),
    "JS metadata": ("js metadata", "rendered title"),
    "JS schema": ("json-ld", "schema", "render"),
    "CWV": ("cwv", "core web vital"),
    "LCP": ("lcp",),
    "INP": ("inp",),
    "CLS": ("cls",),
    "TTFB": ("ttfb",),
    "FCP": ("fcp",),
    "TBT": ("tbt",),
    "HTML size": ("html size", "document size"),
    "JS/CSS": ("javascript", "css", "render-blocking"),
    "image size": ("image", "bytes"),
    "third-party scripts": ("third-party", "third party"),
    "responsive": ("responsive", "mobile"),
    "viewport": ("viewport",),
    "mobile CWV": ("mobile", "cwv"),
    "touch targets": ("touch",),
    "mobile navigation": ("mobile nav", "navigation"),
    "content parity": ("parity", "mobile"),
    "schema types": ("schema", "json-ld"),
    "validation": ("schema", "rich result"),
    "duplication": ("duplicate schema", "schema"),
    "entity relationships": ("entity", "schema"),
    "required properties": ("schema", "missing property"),
    "rich-result eligibility": ("rich result", "rich snippet"),
    "HTTPS": ("https", "http"),
    "mixed content": ("mixed content",),
    "HSTS": ("hsts",),
    "security headers": ("security header", "content-security", "x-frame"),
    "exposed WordPress files": ("wp-config", "wordpress", "xmlrpc"),
    "XML-RPC": ("xml-rpc", "xmlrpc"),
    "WordPress version exposure": ("wordpress version", "generator"),
}


def build_audit_coverage(
    *,
    issues: list[dict[str, Any]] | None = None,
    pages_available: bool = False,
    gsc_available: bool = False,
    cwv_available: bool = False,
    rendering_available: bool = False,
    sitemap_available: bool = False,
    homepage_checked: bool = False,
) -> dict[str, Any]:
    """Coverage matrix for the 12 targeting groups. Does not invent measurements."""
    rows = [i for i in (issues or []) if isinstance(i, dict)]
    blob = " ".join(
        f"{i.get('rule_id') or ''} {i.get('title') or ''} {normalize_category(str(i.get('category') or ''))}"
        for i in rows
    ).lower()

    groups: list[dict[str, Any]] = []
    measured = 0
    issues_found = 0
    not_measured = 0

    for group, fields in AUDIT_GROUPS:
        field_rows: list[dict[str, Any]] = []
        for field in fields:
            status, note = _field_status(
                field,
                blob=blob,
                group=group,
                pages_available=pages_available,
                gsc_available=gsc_available,
                cwv_available=cwv_available,
                rendering_available=rendering_available,
                sitemap_available=sitemap_available,
                homepage_checked=homepage_checked,
            )
            if status == "issue":
                issues_found += 1
                measured += 1
            elif status == "checked":
                measured += 1
            else:
                not_measured += 1
            field_rows.append({"field": field, "status": status, "note": note})
        groups.append(
            {
                "group": group,
                "category": normalize_category(group),
                "fields": field_rows,
                "issue_count": sum(1 for f in field_rows if f["status"] == "issue"),
                "not_measured_count": sum(1 for f in field_rows if f["status"] == "not_measured"),
            }
        )

    total = measured + not_measured
    return {
        "version": "phase7-targeting-v1",
        "groups": groups,
        "summary": {
            "fields": total,
            "measured": measured,
            "with_issues": issues_found,
            "not_measured": not_measured,
            "coverage_pct": round(100 * measured / total) if total else 0,
        },
    }


def _field_status(
    field: str,
    *,
    blob: str,
    group: str,
    pages_available: bool,
    gsc_available: bool,
    cwv_available: bool,
    rendering_available: bool,
    sitemap_available: bool,
    homepage_checked: bool,
) -> tuple[str, str]:
    hints = _FIELD_HINTS.get(field, (field.lower(),))
    if any(h in blob for h in hints):
        return "issue", "Matching finding in this audit."

    needs_pages = group in {
        "On-page technical SEO",
        "Internal linking",
        "Canonicalisation",
        "URL structure",
        "Sitemap",
    }
    needs_gsc = field in {
        "GSC indexed/excluded",
        "discovered but not indexed",
        "crawled but not indexed",
        "Google-selected canonical",
        "soft 404",
    }
    needs_cwv = group == "Performance" or field in {"CWV", "LCP", "INP", "CLS", "mobile CWV", "TTFB", "FCP", "TBT"}
    needs_render = group == "JavaScript/rendering" or field in {"content parity", "JS links", "JS content", "JS metadata", "JS schema"}
    needs_sitemap = group == "Sitemap" or field == "sitemap" or field == "lastmod accuracy"

    if needs_gsc and not gsc_available:
        return "not_measured", "Requires Search Console (Phase 2 connection)."
    if needs_cwv and not cwv_available:
        return "not_measured", "Requires PageSpeed / CrUX (cwv-measurement)."
    if needs_render and not rendering_available:
        return "not_measured", "Requires rendered vs raw HTML comparison."
    if needs_sitemap and not sitemap_available and not pages_available:
        return "not_measured", "Sitemap XML was not fetched this pass."
    if field in _HOMEPAGE_FIELDS and homepage_checked:
        return "checked", "Homepage sample collected; no issue raised for this field."
    if pages_available and needs_pages:
        return "checked", "Page inventory collected; no issue raised for this field."
    if gsc_available and needs_gsc:
        return "checked", "Search Console signals collected; no issue raised."
    if cwv_available and needs_cwv:
        return "checked", "CWV measurement collected; no issue raised."
    if rendering_available and needs_render:
        return "checked", "Render comparison collected; no issue raised."
    if sitemap_available and needs_sitemap:
        return "checked", "Sitemap signals collected; no issue raised."
    return "not_measured", "No evidence collected this pass."
