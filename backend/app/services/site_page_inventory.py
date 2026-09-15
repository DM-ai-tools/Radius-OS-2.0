"""Site page inventory — Perplexity (OpenRouter) executor for site-page-inventory skill.

Builds a classified, reconciled URL inventory for Phase 3 sitemap when live HTTP
crawl is thin or WAF-blocked. Merges with discover_site_urls / Firecrawl when available.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from statistics import median
from typing import Any
from urllib.parse import urlparse

from app.agents.prompts import load_skill_file
from app.integrations.site_research import (
    _host_bare,
    _normalize_pages,
    _normalize_url,
    _perplexity_research,
    research_ready,
)
from app.logging_config import get_logger

log = get_logger("site_page_inventory")

_ARCHIVE_RE = re.compile(
    r"/(?:author|category|tag|tags|page)/\d*(?:/|$)|/page/\d+(?:/|$)",
    re.I,
)


def _page_type_from_signals(
    *,
    path: str,
    title: str,
    sitemap_bucket: str | None,
) -> str:
    bucket = (sitemap_bucket or "").lower()
    if "post" in bucket or "blog" in bucket:
        return "blog_article"
    low_path = (path or "/").lower()
    low_title = (title or "").lower()
    if low_path in ("/", ""):
        return "home"
    if any(x in low_path for x in ("/privacy", "/terms", "/cookie", "/legal", "/disclaimer")):
        return "legal"
    if any(x in low_path for x in ("/contact", "/book", "/consult", "/get-started", "/enquiry", "/inquiry")):
        return "conversion"
    if any(x in low_path for x in ("/about", "/team", "/careers", "/award")):
        return "company"
    if any(x in low_path for x in ("/case-stud", "/testimonial", "/success-stor", "/review")):
        return "proof"
    if any(x in low_path for x in ("/location", "/suburb", "/areas-we", "/near-me")):
        return "location"
    if any(x in low_path for x in ("/blog", "/news", "/insight", "/article")):
        return "blog_article"
    if any(x in low_path for x in ("/guide", "/resource", "/faq", "/learn")):
        return "guide"
    if "service" in low_title or low_path.count("/") == 1:
        return "service"
    if "page" in bucket:
        return "page"
    return "page"


def _inventory_system_prompt() -> str:
    skill = load_skill_file("site-page-inventory")
    head = (skill[:7000] if skill else "You are a site page inventory auditor.")
    return (
        head
        + "\n\nYou have live web access via Perplexity.\n"
        "Return ONLY valid JSON matching the schema in the user message.\n"
        "Do NOT invent URLs. Prefer sitemaps, navigation, and public site: evidence.\n"
        "Classify using CMS sitemap membership when known; else title/nav heuristics.\n"
        "Flag orphans, broken links, missing-from-sitemap, and empty-200 pages.\n"
    )


async def run_site_page_inventory(
    url: str,
    *,
    max_pages: int = 80,
    seed_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Execute site-page-inventory via Perplexity OpenRouter research model."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    parsed = urlparse(raw)
    target_host = (parsed.netloc or "").lower()
    bare = _host_bare(target_host)
    start = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    max_pages = max(1, min(int(max_pages or 80), 200))

    if not research_ready():
        return {
            "available": False,
            "error": "research_not_configured",
            "source": "perplexity_openrouter",
            "pages": [],
            "findings": [],
            "stats": {},
            "method_note": "OpenRouter / research_model not configured.",
        }

    system = _inventory_system_prompt()
    seed_hint = ""
    if seed_urls:
        seed_hint = (
            "Known seed URLs (include if still valid):\n"
            + "\n".join(f"- {u}" for u in seed_urls[:40])
            + "\n"
        )
    user = (
        f"Build a site page inventory for: {start}\n"
        f"Domain (same-host only): {bare}\n"
        f"Max pages: {max_pages}\n"
        f"{seed_hint}"
        "Return JSON:\n"
        "{\n"
        '  "method_note": "one paragraph: sources used, what was verified, limits",\n'
        '  "stats": {\n'
        '    "pages_found": 0,\n'
        '    "in_xml_sitemap": 0,\n'
        '    "orphans": 0,\n'
        '    "broken_links": 0,\n'
        '    "empty_200": 0,\n'
        '    "missing_from_sitemap": 0,\n'
        '    "archive_urls_excluded": 0\n'
        "  },\n"
        '  "findings": [\n'
        '    {"severity": "critical|warning|info", "finding": "...", "evidence": ["url or note"]}\n'
        "  ],\n"
        '  "pages": [\n'
        "    {\n"
        '      "url": "https://...",\n'
        '      "status_code": 200,\n'
        '      "title": "...",\n'
        '      "title_length": 0,\n'
        '      "meta_description": "...",\n'
        '      "desc_length": 0,\n'
        '      "h1": ["..."],\n'
        '      "h1_count": 1,\n'
        '      "h2_count": 0,\n'
        '      "word_count": 0,\n'
        '      "html_kb": 0,\n'
        '      "images": 0,\n'
        '      "canonical": "https://...",\n'
        '      "robots": "index,follow",\n'
        '      "in_xml_sitemap": true,\n'
        '      "sitemap_bucket": "page-sitemap|post-sitemap|unknown",\n'
        '      "page_type": "home|core_service|service|location|blog_article|guide|company|conversion|legal|proof|page",\n'
        '      "orphan": false,\n'
        '      "last_modified": null,\n'
        '      "issues": []\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Superset rule: include XML sitemap URLs PLUS linked pages not in the sitemap.\n"
        "Exclude /tag/ /author/ /category/ /page/N/ from pages[] (count in archive_urls_excluded).\n"
    )

    try:
        parsed_json, citations = await _perplexity_research(system, user)
    except Exception as exc:  # noqa: BLE001
        log.warning("site_page_inventory_failed", error=str(exc), url=start)
        return {
            "available": False,
            "error": str(exc)[:240],
            "source": "perplexity_openrouter",
            "pages": [],
            "findings": [],
            "stats": {},
            "method_note": f"Perplexity inventory failed: {exc}",
        }

    raw_pages = list((parsed_json or {}).get("pages") or [])
    same_host_citations = [
        c
        for c in citations
        if _host_bare(urlparse(c).netloc) == bare
        and not any(
            urlparse(c).path.lower().endswith(ext)
            for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".css", ".js")
        )
    ]

    # Seed known URLs so inventory is at least as wide as discovery
    for u in seed_urls or []:
        raw_pages.append({"url": u, "status_code": 200})

    normalized = _normalize_pages(
        raw_pages,
        target_host=target_host,
        max_pages=max_pages,
        extra_urls=same_host_citations[: max_pages // 2],
    )

    # Enrich with inventory-specific fields from raw match
    by_url = {
        str(_normalize_url(str(p.get("url") or ""), default_host=target_host) or "").rstrip("/").lower(): p
        for p in raw_pages
        if isinstance(p, dict)
    }
    pages: list[dict[str, Any]] = []
    archive_excluded = 0
    for row in normalized:
        url_n = str(row.get("url") or "")
        path = urlparse(url_n).path or "/"
        if _ARCHIVE_RE.search(path):
            archive_excluded += 1
            continue
        key = url_n.rstrip("/").lower()
        rich = by_url.get(key) or {}
        title = str(row.get("title") or rich.get("title") or "").strip()
        meta = str(row.get("meta_description") or rich.get("meta_description") or "").strip()
        h1 = row.get("h1") if isinstance(row.get("h1"), list) else []
        if not h1 and isinstance(rich.get("h1"), list):
            h1 = rich["h1"]
        page_type = str(rich.get("page_type") or "").strip() or _page_type_from_signals(
            path=path,
            title=title,
            sitemap_bucket=str(rich.get("sitemap_bucket") or ""),
        )
        issues = []
        if isinstance(rich.get("issues"), list):
            issues.extend(str(i) for i in rich["issues"] if i)
        if isinstance(row.get("issues"), list):
            issues.extend(str(i) for i in row["issues"] if i)
        status = int(row.get("status_code") or rich.get("status_code") or 200)
        word_count = rich.get("word_count")
        html_kb = rich.get("html_kb")
        try:
            word_count = int(word_count) if word_count is not None else None
        except (TypeError, ValueError):
            word_count = None
        try:
            html_kb = float(html_kb) if html_kb is not None else None
        except (TypeError, ValueError):
            html_kb = None
        if status == 200 and (
            (html_kb is not None and html_kb < 0.5)
            or (word_count is not None and word_count < 30 and not title)
        ):
            issues.append("empty_or_shell_200")
        pages.append(
            {
                **row,
                "path": path,
                "title": title or path,
                "title_length": int(rich.get("title_length") or len(title) or 0),
                "meta_description": meta,
                "desc_length": int(rich.get("desc_length") or len(meta) or 0),
                "h1": h1,
                "h1_count": int(rich.get("h1_count") or len(h1) or 0),
                "h2_count": int(rich.get("h2_count") or 0),
                "word_count": word_count,
                "html_kb": html_kb,
                "images": int(rich.get("images") or 0),
                "robots": str(rich.get("robots") or "").strip() or None,
                "in_xml_sitemap": bool(rich.get("in_xml_sitemap"))
                if "in_xml_sitemap" in rich
                else None,
                "sitemap_bucket": rich.get("sitemap_bucket"),
                "page_type": page_type,
                "orphan": bool(rich.get("orphan")) if "orphan" in rich else False,
                "last_modified": rich.get("last_modified"),
                "issues": sorted(set(issues)),
                "cluster": page_type if page_type != "blog_article" else "blog",
                "source": "perplexity_inventory",
            }
        )

    findings = [
        f
        for f in (parsed_json or {}).get("findings") or []
        if isinstance(f, dict) and f.get("finding")
    ]
    stats_in = dict((parsed_json or {}).get("stats") or {})
    empty_200 = sum(1 for p in pages if "empty_or_shell_200" in (p.get("issues") or []))
    orphans = sum(1 for p in pages if p.get("orphan"))
    broken = sum(1 for p in pages if int(p.get("status_code") or 200) >= 400)
    in_sm = sum(1 for p in pages if p.get("in_xml_sitemap") is True)
    missing_sm = sum(
        1
        for p in pages
        if p.get("in_xml_sitemap") is False and int(p.get("status_code") or 200) < 400
    )

    # Median words by page type
    by_type: dict[str, list[int]] = {}
    for p in pages:
        wc = p.get("word_count")
        if isinstance(wc, int) and wc > 0:
            by_type.setdefault(str(p.get("page_type") or "page"), []).append(wc)
    median_words = {k: int(median(v)) for k, v in by_type.items() if v}

    stats = {
        "pages_found": len(pages),
        "in_xml_sitemap": int(stats_in.get("in_xml_sitemap") or in_sm),
        "orphans": int(stats_in.get("orphans") or orphans),
        "broken_links": int(stats_in.get("broken_links") or broken),
        "empty_200": int(stats_in.get("empty_200") or empty_200),
        "missing_from_sitemap": int(stats_in.get("missing_from_sitemap") or missing_sm),
        "archive_urls_excluded": int(stats_in.get("archive_urls_excluded") or archive_excluded),
        "median_word_count_by_type": median_words,
    }

    method_note = str(
        (parsed_json or {}).get("method_note")
        or "Perplexity OpenRouter inventory — superset of public sitemap + linked pages."
    )

    return {
        "available": bool(pages),
        "error": None if pages else "empty_inventory",
        "source": "perplexity_openrouter",
        "skill": "site-page-inventory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_url": start,
        "pages": pages,
        "findings": findings,
        "stats": stats,
        "method_note": method_note,
        "csv": inventory_to_csv(pages),
    }


def inventory_to_csv(pages: list[dict[str, Any]]) -> str:
    """One CSV row per URL for download / workbook export."""
    buf = io.StringIO()
    fields = [
        "url",
        "page_type",
        "title",
        "title_length",
        "desc_length",
        "h1_count",
        "h2_count",
        "word_count",
        "html_kb",
        "images",
        "robots",
        "in_xml_sitemap",
        "orphan",
        "last_modified",
        "status_code",
        "issues",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for p in pages:
        row = {k: p.get(k) for k in fields}
        issues = p.get("issues") or []
        row["issues"] = "; ".join(str(i) for i in issues) if isinstance(issues, list) else issues
        writer.writerow(row)
    return buf.getvalue()


def inventory_pages_for_sitemap(inventory: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Shape inventory pages for ``build_client_sitemap`` / SEO audit consumers."""
    out: list[dict[str, Any]] = []
    for p in (inventory or {}).get("pages") or []:
        if not isinstance(p, dict) or not p.get("url"):
            continue
        page_type = str(p.get("page_type") or "page")
        cluster = "blog" if page_type == "blog_article" else page_type
        if cluster in ("core_service", "service"):
            cluster = "service" if page_type == "service" else "service_hub"
        out.append(
            {
                "url": p.get("url"),
                "path": p.get("path"),
                "title": p.get("title"),
                "status": p.get("status_code"),
                "cluster": cluster,
                "page_type": page_type,
                "canonical": p.get("canonical"),
                "word_count": p.get("word_count"),
                "h1": p.get("h1"),
                "meta_description": p.get("meta_description"),
                "issues": p.get("issues") or [],
                "orphan": p.get("orphan"),
                "in_xml_sitemap": p.get("in_xml_sitemap"),
                "source": "perplexity_inventory",
            }
        )
    return out
