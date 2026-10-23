"""Phase 7 report assembly: twelve audit groups, quick wins, backlog, scorecard.

Turns the page sample (``technical_seo_sample``), PageSpeed (mobile + desktop)
and the other providers into explicit checks. Each check has a group,
a checklist field, a status (``pass`` / ``issue`` / ``not_measured``), the evidence,
affected URLs and a fix. Issues become backlog rows; statuses feed the coverage
matrix; groups get a score only when at least one field was measured.
"""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlparse

from app.services.technical_seo_schemas import (
    ALL_CATEGORIES,
    AUDIT_GROUPS,
    normalize_category,
)

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}
_SEV_PENALTY = {"Critical": 25, "High": 15, "Medium": 8, "Low": 3, "Info": 1}
GROUP_INDEX = {g: i for i, g in enumerate(ALL_CATEGORIES)}
BACKLOG_LIMIT = 50

# Fixes a reviewer can do in a template or setting, without a project behind them.
EASY_FIELDS = {
    ("On-page technical SEO", "title"),
    ("On-page technical SEO", "meta description"),
    ("On-page technical SEO", "H1"),
    ("On-page technical SEO", "image alt"),
    ("Canonicalisation", "canonical existence"),
    ("Canonicalisation", "canonical validity"),
    ("Indexation", "noindex"),
    ("Indexation", "robots blocking"),
    ("Crawlability", "robots.txt"),
    ("Crawlability", "sitemap"),
    ("URL structure", "HTTP/HTTPS"),
    ("URL structure", "www/non-www"),
    ("URL structure", "redirect chains"),
    ("Security", "HTTPS"),
    ("Security", "mixed content"),
    ("Security", "HSTS"),
    ("Security", "security headers"),
    ("Security", "exposed WordPress files"),
    ("Security", "XML-RPC"),
    ("Internal linking", "broken links"),
    ("Internal linking", "redirected internal links"),
    ("Mobile", "viewport"),
    ("Sitemap", "no redirects"),
    ("Sitemap", "no 404s"),
    ("Sitemap", "no noindex URLs"),
    ("Sitemap", "200 status"),
    ("Structured data", "validation"),
}
_EASY_TEXT = re.compile(
    r"missing title|title tag|meta description|missing h1|multiple h1|broken|4xx|404|noindex|"
    r"canonical|https|mixed content|hsts|viewport|sitemap|robots\.txt|redirect chain|alt text",
    re.IGNORECASE,
)


def chk(
    group: str,
    field: str,
    status: str,
    detail: str,
    *,
    check: str | None = None,
    urls: list[str] | tuple[str, ...] = (),
    severity: str = "Medium",
    fix: str = "",
) -> dict[str, Any]:
    return {
        "group": group,
        "field": field,
        "check": check or field,
        "status": status,
        "detail": detail,
        "affected_urls": list(dict.fromkeys(u for u in urls if u))[:25],
        "severity": severity if status == "issue" else "",
        "fix": fix if status == "issue" else "",
    }


def _pages(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [p for p in (sample or {}).get("pages") or [] if p.get("parsed")]


def _short(url: str) -> str:
    p = urlparse(url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def _norm_url(url: str) -> str:
    p = urlparse(url or "")
    return f"{(p.hostname or '').lower().removeprefix('www.')}{p.path.rstrip('/') or '/'}"


# --------------------------------------------------------------------------- on-page


def onpage_checks(
    sample: dict[str, Any] | None,
    *,
    seo_pages: list[dict[str, Any]] | None = None,
    duplicate_titles: list[dict[str, Any]] | None = None,
    duplicate_meta: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Checks plus a per-page table. Uses the direct sample and the crawl audit's
    pages (titles, meta and per-page issue lists)."""
    g = "On-page technical SEO"
    pages = _pages(sample)
    crawl = [p for p in seo_pages or [] if isinstance(p, dict)]
    rows: list[dict[str, Any]] = []
    if not pages and not crawl:
        for f in ("title", "meta description", "H1", "H2/H3", "image alt", "duplicate metadata", "thin content"):
            rows.append(chk(g, f, "not_measured", "No page HTML could be fetched this pass."))
        return rows, []

    def crawl_hits(*needles: str) -> list[str]:
        out = []
        for p in crawl:
            text = " ".join(str(i.get("issue") or "") for k in ("critical", "warnings") for i in p.get(k) or [] if isinstance(i, dict))
            if any(n in text.lower() for n in needles):
                out.append(str(p.get("url") or ""))
        return out

    n_pages = len({p["final_url"] for p in pages} | {str(c.get("url")) for c in crawl})
    no_title = [p["final_url"] for p in pages if not p["parsed"]["title"]] + crawl_hits("missing title")
    long_title = [p["final_url"] for p in pages if len(p["parsed"]["title"]) > 65]
    dup_title_urls = [u for d in duplicate_titles or [] for u in d.get("urls") or []]
    titles = [p["parsed"]["title"].lower() for p in pages if p["parsed"]["title"]]
    sample_dup_titles = sorted({t for t in titles if titles.count(t) > 1})
    if no_title:
        rows.append(chk(g, "title", "issue", f"{len(set(no_title))} page(s) have no title tag.", check="Title tag presence",
                        urls=no_title, severity="High", fix="Add a unique, descriptive <title> to every indexable template."))
    else:
        rows.append(chk(g, "title", "pass", f"All {n_pages} checked page(s) have a title tag.", check="Title tag presence"))
    if dup_title_urls or sample_dup_titles:
        rows.append(chk(g, "title", "issue",
                        f"{len(duplicate_titles or []) or len(sample_dup_titles)} title(s) shared by more than one page.",
                        check="Title tag uniqueness", urls=dup_title_urls or [p["final_url"] for p in pages if p["parsed"]["title"].lower() in sample_dup_titles],
                        severity="Medium", fix="Give each page its own title; duplicates split ranking signals and CTR."))
    else:
        rows.append(chk(g, "title", "pass", "No duplicate titles among the checked pages.", check="Title tag uniqueness"))
    if long_title:
        rows.append(chk(g, "title", "issue", f"{len(long_title)} title(s) over 65 characters may be cut off in results.",
                        check="Title length", urls=long_title, severity="Low", fix="Keep titles near 50-60 characters with the main term first."))

    no_meta = [p["final_url"] for p in pages if not p["parsed"]["meta_description"]] + crawl_hits("missing meta description")
    dup_meta_urls = [u for d in duplicate_meta or [] for u in d.get("urls") or []]
    if no_meta:
        rows.append(chk(g, "meta description", "issue", f"{len(set(no_meta))} page(s) have no meta description.",
                        check="Meta description presence", urls=no_meta, severity="Medium",
                        fix="Write a unique meta description (about 140-155 characters) for each key page."))
    else:
        rows.append(chk(g, "meta description", "pass", f"All {n_pages} checked page(s) have a meta description.",
                        check="Meta description presence"))
    if dup_meta_urls:
        rows.append(chk(g, "meta description", "issue", f"{len(duplicate_meta or [])} meta description(s) reused across pages.",
                        check="Meta description uniqueness", urls=dup_meta_urls, severity="Low",
                        fix="Write a distinct description per page."))
    else:
        rows.append(chk(g, "meta description", "pass", "No duplicate meta descriptions found.", check="Meta description uniqueness"))

    no_h1 = [p["final_url"] for p in pages if not p["parsed"]["h1"]] + crawl_hits("no h1")
    multi_h1 = [p["final_url"] for p in pages if len(p["parsed"]["h1"]) > 1] + crawl_hits("expected 1 h1")
    h1s = [p["parsed"]["h1"][0].lower() for p in pages if p["parsed"]["h1"]]
    dup_h1 = [p["final_url"] for p in pages if p["parsed"]["h1"] and h1s.count(p["parsed"]["h1"][0].lower()) > 1]
    if no_h1:
        rows.append(chk(g, "H1", "issue", f"{len(set(no_h1))} page(s) have no H1.", check="H1 presence", urls=no_h1,
                        severity="High", fix="Add one descriptive H1 that states the page's topic."))
    else:
        rows.append(chk(g, "H1", "pass", "Every checked page has an H1.", check="H1 presence"))
    if multi_h1 or dup_h1:
        rows.append(chk(g, "H1", "issue",
                        (f"{len(set(multi_h1))} page(s) have more than one H1. " if multi_h1 else "")
                        + (f"{len(dup_h1)} page(s) share the same H1 text." if dup_h1 else ""),
                        check="H1 uniqueness", urls=multi_h1 + dup_h1, severity="Medium",
                        fix="Use exactly one H1 per page, worded for that page."))
    else:
        rows.append(chk(g, "H1", "pass", "One unique H1 per checked page.", check="H1 uniqueness"))

    skipped = [(p["final_url"], i) for p in pages for i in p["parsed"]["heading_issues"]]
    if skipped:
        rows.append(chk(g, "H2/H3", "issue", f"{len(skipped)} heading-order problem(s), e.g. {skipped[0][1]}",
                        check="Heading hierarchy", urls=[u for u, _ in skipped], severity="Low",
                        fix="Nest headings in order (H1 > H2 > H3) without skipping levels."))
    elif pages:
        rows.append(chk(g, "H2/H3", "pass", "Headings are nested in order on the checked pages.", check="Heading hierarchy"))
    else:
        rows.append(chk(g, "H2/H3", "not_measured", "Heading order needs page HTML; only crawl summaries were available.",
                        check="Heading hierarchy"))

    images = sum(p["parsed"]["image_count"] for p in pages)
    missing = sum(p["parsed"]["images_missing_alt"] for p in pages)
    alt_pages = [p["final_url"] for p in pages if p["parsed"]["images_missing_alt"]] + crawl_hits("alt")
    if images:
        coverage = round(100 * (images - missing) / images)
        status = "issue" if coverage < 90 else "pass"
        rows.append(chk(g, "image alt", status, f"{coverage}% of {images} images have an alt attribute ({missing} without).",
                        check="Image alt text coverage", urls=alt_pages, severity="Medium" if coverage < 70 else "Low",
                        fix="Add descriptive alt text to content images; use alt=\"\" only for decorative ones."))
    elif alt_pages:
        rows.append(chk(g, "image alt", "issue", f"{len(alt_pages)} crawled page(s) report images without alt text.",
                        check="Image alt text coverage", urls=alt_pages, severity="Medium",
                        fix="Add descriptive alt text to content images."))
    else:
        rows.append(chk(g, "image alt", "pass", "No images without alt text on the checked pages.", check="Image alt text coverage"))

    if dup_title_urls or dup_meta_urls:
        rows.append(chk(g, "duplicate metadata", "issue",
                        f"{len(duplicate_titles or [])} duplicate title group(s), {len(duplicate_meta or [])} duplicate description group(s).",
                        check="Duplicate metadata", urls=dup_title_urls + dup_meta_urls, severity="Medium",
                        fix="Make titles and descriptions unique per page."))
    else:
        rows.append(chk(g, "duplicate metadata", "pass", "No duplicate titles or descriptions found.", check="Duplicate metadata"))

    home = (sample or {}).get("home_url") or ""
    thin = [p["final_url"] for p in pages if p["parsed"]["word_count"] < 200 and _norm_url(p["final_url"]) != _norm_url(home)]
    if thin:
        rows.append(chk(g, "thin content", "issue", f"{len(thin)} inner page(s) under 200 words of visible text.",
                        check="Thin content", urls=thin, severity="Low",
                        fix="Expand thin pages with useful content, or consolidate or noindex low-value ones."))
    elif pages:
        rows.append(chk(g, "thin content", "pass", "Checked inner pages carry 200+ words.", check="Thin content"))
    else:
        rows.append(chk(g, "thin content", "not_measured", "Word counts need page HTML.", check="Thin content"))

    table = [
        {
            "url": _short(p["final_url"]),
            "title": f"{len(p['parsed']['title'])} chars" if p["parsed"]["title"] else "missing",
            "h1": str(len(p["parsed"]["h1"])),
            "meta": f"{len(p['parsed']['meta_description'])} chars" if p["parsed"]["meta_description"] else "missing",
            "headings": "ok" if not p["parsed"]["heading_issues"] else f"{len(p['parsed']['heading_issues'])} issue(s)",
            "alt": (
                f"{round(100 * (p['parsed']['image_count'] - p['parsed']['images_missing_alt']) / p['parsed']['image_count'])}%"
                if p["parsed"]["image_count"] else "no images"
            ),
            "words": p["parsed"]["word_count"],
        }
        for p in pages
    ]
    return rows, table


def indexation_checks(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pages = _pages(sample)
    if not pages:
        return rows
    home = _norm_url((sample or {}).get("home_url") or "")
    meta_noindex = [p["final_url"] for p in pages if "noindex" in p["parsed"]["meta_robots"]]
    header_noindex = [p["final_url"] for p in pages if "noindex" in str((p.get("headers") or {}).get("x-robots-tag", "")).lower()]
    if meta_noindex:
        on_home = any(_norm_url(u) == home for u in meta_noindex)
        rows.append(chk("Indexation", "noindex", "issue", f"{len(meta_noindex)} sampled page(s) carry meta robots noindex.",
                        urls=meta_noindex, severity="Critical" if on_home else "High",
                        fix="Remove noindex from pages that should rank."))
    else:
        rows.append(chk("Indexation", "noindex", "pass", f"No noindex on the {len(pages)} sampled page(s)."))
    if header_noindex:
        rows.append(chk("Indexation", "robots blocking", "issue", "X-Robots-Tag: noindex header on sampled page(s).",
                        urls=header_noindex, severity="High", fix="Remove the X-Robots-Tag noindex header for indexable pages."))
    elif (sample or {}).get("headers_measured"):
        rows.append(chk("Indexation", "robots blocking", "pass", "No X-Robots-Tag noindex header on sampled pages."))
    return rows


# --------------------------------------------------------------------------- URL structure


def url_structure_checks(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    g = "URL structure"
    v = (sample or {}).get("variants") or {}
    home = (sample or {}).get("home_url") or ""
    home_host = (urlparse(home).hostname or "").lower()
    rows: list[dict[str, Any]] = []
    dup_urls: list[str] = []

    def _blocked(x: dict[str, Any]) -> bool:
        return bool(x.get("blocked")) or (not x.get("status") and not x.get("chain"))

    http = v.get("http") or {}
    if not http or _blocked(http):
        rows.append(chk(g, "HTTP/HTTPS", "not_measured", "The site did not answer the http:// probe from this server."))
    elif http.get("first_status") in (301, 308) and str(http.get("final_url", "")).startswith("https://"):
        rows.append(chk(g, "HTTP/HTTPS", "pass", f"http:// redirects permanently ({http['first_status']}) to https://."))
    elif http.get("first_status") in (302, 307) and str(http.get("final_url", "")).startswith("https://"):
        rows.append(chk(g, "HTTP/HTTPS", "issue", f"http:// redirects to https:// with a temporary {http['first_status']}.",
                        urls=[http["url"]], severity="Medium", fix="Make the http to https redirect a 301."))
    else:
        rows.append(chk(g, "HTTP/HTTPS", "issue", f"http:// answers {http.get('status')} without moving to https://.",
                        urls=[http["url"]], severity="Critical", fix="Redirect every http:// URL to https:// with a 301."))
        dup_urls.append(http.get("url", ""))

    other = v.get("other_host") or {}
    if not other:
        rows.append(chk(g, "www/non-www", "not_measured", "Host variant not probed."))
    elif other.get("error", "").startswith("unsafe_or_unresolvable"):
        rows.append(chk(g, "www/non-www", "pass", f"{urlparse(other['url']).hostname} does not resolve, so only one host exists."))
    elif _blocked(other):
        rows.append(chk(g, "www/non-www", "not_measured", "The other host blocked the probe from this server."))
    elif other.get("first_status") in (301, 308) and (urlparse(other.get("final_url", "")).hostname or "").lower() == home_host:
        rows.append(chk(g, "www/non-www", "pass", f"{urlparse(other['url']).hostname} redirects permanently to {home_host}."))
    elif other.get("first_status") in (302, 307):
        rows.append(chk(g, "www/non-www", "issue", f"The other host redirects with a temporary {other['first_status']}.",
                        urls=[other["url"]], severity="Medium", fix="Use a 301 from the non-preferred host to the preferred one."))
    elif other.get("status") == 200:
        dup_urls.append(other["url"])
        rows.append(chk(g, "www/non-www", "issue", f"Both {home_host} and {urlparse(other['url']).hostname} serve the site (200).",
                        urls=[other["url"]], severity="High", fix="301-redirect the non-preferred host to the preferred host."))
    else:
        rows.append(chk(g, "www/non-www", "pass", f"The other host answers {other.get('status')}; it does not duplicate the site."))

    def _variant(key: str, field: str, label: str) -> None:
        x = v.get(key) or {}
        sample_url = v.get("sample_url") or ""
        if not x:
            rows.append(chk(g, field, "not_measured", f"No inner page to test {label} on."))
            return
        if _blocked(x):
            rows.append(chk(g, field, "not_measured", f"The {label} probe was blocked by the site."))
            return
        canon = x.get("canonical") or ""
        if x.get("first_status") in (301, 308) or x.get("status") in (404, 410):
            rows.append(chk(g, field, "pass", f"{label.capitalize()} variant answers {x.get('first_status') or x.get('status')} (no duplicate)."))
        elif x.get("status") == 200 and canon and _norm_url(canon) == _norm_url(sample_url) and "?" not in canon:
            rows.append(chk(g, field, "pass", f"{label.capitalize()} variant serves 200 but canonicalises to {_short(sample_url)}."))
        elif x.get("status") == 200:
            dup_urls.append(x["url"])
            rows.append(chk(g, field, "issue",
                            f"{label.capitalize()} variant serves 200 " + ("without a canonical." if not canon else f"with canonical {_short(canon)}."),
                            urls=[x["url"]], severity="Medium",
                            fix={"slash": "Pick one trailing-slash form; 301 the other or canonicalise to it.",
                                 "upper": "301 mixed-case URLs to lowercase (or return 404) and canonicalise to lowercase.",
                                 "param": "Canonicalise parameter URLs to the clean URL; keep tracking parameters out of internal links."}[key]))
        else:
            rows.append(chk(g, field, "pass", f"{label.capitalize()} variant answers {x.get('status')}."))

    _variant("slash", "trailing slash", "trailing-slash")
    _variant("param", "parameters", "parameter")
    if (v.get("upper") or {}).get("url"):
        _variant("upper", "case sensitivity", "upper-case")
    else:
        rows.append(chk(g, "case sensitivity", "not_measured", "No inner URL with letters to test case on."))

    pages = (sample or {}).get("pages") or []
    chained = [p["url"] for p in pages if p.get("hops", 0) >= 2] + [
        x["url"] for x in (v.get(k) or {} for k in ("http", "other_host", "slash", "upper", "param")) if x and x.get("hops", 0) >= 2
    ]
    loops = [p["url"] for p in pages if p.get("loop")] + [x["url"] for x in (v.get(k) or {} for k in v) if isinstance(x, dict) and x.get("loop")]
    if loops:
        rows.append(chk(g, "redirect chains", "issue", f"{len(loops)} redirect loop(s).", urls=loops, severity="Critical",
                        fix="Break the loop so each URL ends at a 200 page."))
    if chained:
        rows.append(chk(g, "redirect chains", "issue", f"{len(chained)} URL(s) take 2+ redirect hops.", urls=chained,
                        severity="Medium", fix="Point each redirect straight at the final URL (one hop)."))
    if not loops and not chained:
        rows.append(chk(g, "redirect chains", "pass", "Sampled URLs and variants reach their page in at most one hop."))

    measurable = [x for k in ("other_host", "slash", "param", "upper") if (x := v.get(k)) and not _blocked(x)]
    if dup_urls:
        rows.append(chk(g, "duplicate URLs", "issue", f"{len(dup_urls)} URL spelling(s) serve duplicate copies.",
                        urls=dup_urls, severity="Medium", fix="Consolidate each duplicate spelling with a 301 or canonical."))
    elif not measurable:
        rows.append(chk(g, "duplicate URLs", "not_measured", "The URL-variant probes were blocked or had no inner page to test."))
    elif v:
        rows.append(chk(g, "duplicate URLs", "pass", "No duplicate URL spellings found in the probes."))
    return rows


# --------------------------------------------------------------------------- canonicals


def canonical_checks(
    sample: dict[str, Any] | None,
    *,
    gsc: dict[str, Any] | None,
    sitemap_urls: list[str] | None,
    duplicate_titles: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Checks, a per-page canonical table, and duplicate URL clusters."""
    g = "Canonicalisation"
    pages = [p for p in _pages(sample) if p.get("status") == 200 or p.get("parsed")]
    rows: list[dict[str, Any]] = []
    table: list[dict[str, Any]] = []
    missing, invalid, non_self, conflicts = [], [], [], []
    in_sitemap = {_norm_url(u) for u in sitemap_urls or []}
    for p in pages:
        url = p["final_url"]
        canon = p["parsed"]["canonical"]
        if not canon:
            missing.append(url)
            state = "missing"
        else:
            cp = urlparse(canon)
            if cp.scheme != "https" and url.startswith("https://") or not cp.hostname or "?" in canon:
                invalid.append(url)
                state = "invalid"
            elif _norm_url(canon) == _norm_url(url):
                state = "self"
            else:
                non_self.append(url)
                state = f"points to {_short(canon)}"
                if _norm_url(url) in in_sitemap:
                    conflicts.append(url)
        table.append({"url": _short(url), "canonical": _short(canon) if canon else "—", "state": state})
    if not pages:
        rows.append(chk(g, "canonical existence", "not_measured", "No page HTML could be fetched this pass."))
    elif missing:
        rows.append(chk(g, "canonical existence", "issue", f"{len(missing)} of {len(pages)} sampled page(s) have no canonical.",
                        urls=missing, severity="High", fix="Add a self-referencing rel=canonical to every indexable template."))
    else:
        rows.append(chk(g, "canonical existence", "pass", f"All {len(pages)} sampled page(s) declare a canonical."))
    with_canon = [t for t in table if t["state"] != "missing"]
    if pages and not with_canon:
        rows.append(chk(g, "canonical validity", "not_measured", "No canonical tags on the sampled pages to validate."))
        rows.append(chk(g, "canonical conflicts", "not_measured", "No canonical tags on the sampled pages to compare."))
    elif pages:
        if invalid:
            rows.append(chk(g, "canonical validity", "issue", f"{len(invalid)} canonical(s) are http, relative-broken or carry parameters.",
                            urls=invalid, severity="High", fix="Use absolute https canonicals to the clean URL."))
        else:
            selfc = sum(1 for t in table if t["state"] == "self")
            rows.append(chk(g, "canonical validity", "pass",
                            f"{selfc} page(s) self-canonicalise; {len(non_self)} point to another URL (check that is intended)."))
        if conflicts:
            rows.append(chk(g, "canonical conflicts", "issue", f"{len(conflicts)} sitemap URL(s) canonicalise elsewhere.",
                            urls=conflicts, severity="Medium",
                            fix="List only canonical URLs in the sitemap, or make these pages self-canonical."))
        else:
            rows.append(chk(g, "canonical conflicts", "pass", "No sampled sitemap URL canonicalises to a different page."))

    gsc = dict(gsc or {})
    if gsc.get("status") == "available":
        picked = [i for i in gsc.get("issues") or [] if isinstance(i, dict) and re.search(
            r"google chose different canonical|without user-selected canonical|alternate page", str(i.get("title") or ""), re.IGNORECASE)]
        if picked:
            urls = [u for i in picked for u in i.get("affected_urls") or []]
            rows.append(chk(g, "Google-selected canonical", "issue",
                            f"Search Console reports {len(picked)} canonical mismatch type(s).", urls=urls, severity="High",
                            fix="Align declared canonicals, internal links and the sitemap on the URL Google should index."))
        else:
            rows.append(chk(g, "Google-selected canonical", "pass", "Search Console reports no canonical mismatches."))
    else:
        rows.append(chk(g, "Google-selected canonical", "not_measured", "Needs Search Console (connect it in Phase 2)."))

    clusters = [
        {"cluster": f"Same title: {str(d.get('value'))[:70]}", "urls": list(d.get("urls") or [])[:6], "count": d.get("count")}
        for d in duplicate_titles or []
    ]
    if clusters:
        rows.append(chk(g, "duplicate content", "issue", f"{len(clusters)} cluster(s) of pages sharing a title (likely duplicates).",
                        urls=[u for c in clusters for u in c["urls"]], severity="Medium",
                        fix="Merge or differentiate each cluster; canonicalise true duplicates to one URL."))
    elif pages:
        rows.append(chk(g, "duplicate content", "pass", "No duplicate-title clusters found."))
    return rows, table, clusters


# --------------------------------------------------------------------------- JS rendering


def js_checks(sample: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    g = "JavaScript/rendering"
    rows: list[dict[str, Any]] = []
    table: list[dict[str, Any]] = []
    rendered = (sample or {}).get("rendered") or []
    pages = [p for p in _pages(sample) if p.get("html_source") == "direct"]
    if not pages:
        reason = (
            "The site blocked direct fetching, so the raw HTML a crawler receives could not be read."
            if _pages(sample) or (sample or {}).get("pages") else "No page HTML could be fetched this pass."
        )
        for f in ("raw vs rendered HTML", "JS links", "JS content", "JS metadata", "JS schema"):
            rows.append(chk(g, f, "not_measured", reason))
        return rows, table
    if not rendered:
        shells = [p["final_url"] for p in pages if p["parsed"]["app_shell"]]
        if shells:
            rows.append(chk(g, "raw vs rendered HTML", "issue",
                            "Raw HTML is an empty app shell with little text: content is likely rendered by JavaScript.",
                            urls=shells, severity="High",
                            fix="Server-render or pre-render the main content, links and metadata."))
        else:
            home = pages[0]["parsed"]
            rows.append(chk(g, "raw vs rendered HTML", "pass",
                            f"Raw HTML already carries {home['word_count']} words, {len(home['h1'])} H1 and "
                            f"{home['link_count']} links on the homepage (no rendered copy to compare)."))
        for f in ("JS links", "JS content", "JS metadata", "JS schema"):
            rows.append(chk(g, f, "not_measured", "A browser-rendered copy (Firecrawl) was not available to compare."))
        return rows, table

    worst: dict[str, list[str]] = {"words": [], "links": [], "h1": [], "meta": [], "schema": []}
    for item in rendered:
        raw, ren, url = item["raw"], item["rendered"], item["url"]
        if ren["word_count"] >= 150 and raw["word_count"] < 0.5 * ren["word_count"]:
            worst["words"].append(url)
        if ren["link_count"] >= 10 and raw["link_count"] < 0.5 * ren["link_count"]:
            worst["links"].append(url)
        if ren["h1"] and not raw["h1"]:
            worst["h1"].append(url)
        if (ren["title"] and not raw["title"]) or (ren["canonical"] and not raw["canonical"]) or (
            ren["meta_description"] and not raw["meta_description"]
        ):
            worst["meta"].append(url)
        if set(ren["structured_data"]["types"]) - set(raw["structured_data"]["types"]):
            worst["schema"].append(url)
        table.append({
            "url": _short(url),
            "words": f"{raw['word_count']} / {ren['word_count']}",
            "links": f"{raw['link_count']} / {ren['link_count']}",
            "h1": f"{len(raw['h1'])} / {len(ren['h1'])}",
            "verdict": "JavaScript-dependent" if (url in worst["words"] or url in worst["links"] or url in worst["h1"]) else "Server HTML complete",
        })
    rows.append(chk(g, "raw vs rendered HTML", "issue" if worst["words"] else "pass",
                    f"Raw HTML holds under half the rendered text on {len(worst['words'])} page(s)." if worst["words"]
                    else "Raw and rendered HTML carry comparable text.", urls=worst["words"], severity="High",
                    fix="Server-render the main body text."))
    rows.append(chk(g, "JS links", "issue" if worst["links"] else "pass",
                    "Navigation/body links appear only after JavaScript runs." if worst["links"] else "Links are present in raw HTML.",
                    urls=worst["links"], severity="High", fix="Render navigation links as <a href> in the server HTML."))
    rows.append(chk(g, "JS content", "issue" if worst["h1"] else "pass",
                    "The H1 exists only after JavaScript runs." if worst["h1"] else "Headings are in raw HTML.",
                    urls=worst["h1"], severity="High", fix="Put headings and main copy in the server HTML."))
    rows.append(chk(g, "JS metadata", "issue" if worst["meta"] else "pass",
                    "Title, description or canonical is added by JavaScript." if worst["meta"] else "Metadata is in raw HTML.",
                    urls=worst["meta"], severity="High", fix="Output title, meta description and canonical server-side."))
    rows.append(chk(g, "JS schema", "issue" if worst["schema"] else "pass",
                    "Structured data is injected by JavaScript." if worst["schema"] else "Structured data (if any) is in raw HTML.",
                    urls=worst["schema"], severity="Medium", fix="Emit JSON-LD in the server HTML."))
    return rows, table


# --------------------------------------------------------------------------- performance


_THRESHOLDS = {  # good, poor (Google's published boundaries)
    "lcp_ms": (2500, 4000),
    "inp_ms": (200, 500),
    "cls": (0.1, 0.25),
    "ttfb_ms": (800, 1800),
    "fcp_ms": (1800, 3000),
    "tbt_ms": (200, 600),
}
_FIELD_CAT = {"FAST": "Good", "AVERAGE": "Needs improvement", "SLOW": "Poor"}
_METRICS = (
    ("LCP", "lcp_ms", "Largest Contentful Paint"),
    ("INP", "inp_ms", "Interaction to Next Paint"),
    ("CLS", "cls", "Cumulative Layout Shift"),
    ("TTFB", "ttfb_ms", "Time to First Byte"),
    ("FCP", "fcp_ms", "First Contentful Paint"),
    ("TBT", "tbt_ms", "Total Blocking Time (lab only)"),
)


def _rate(key: str, value: float | None) -> str:
    if value is None:
        return ""
    good, poor = _THRESHOLDS[key]
    return "Good" if value <= good else "Poor" if value > poor else "Needs improvement"


def _metric(cwv: dict[str, Any] | None, key: str) -> tuple[float | None, str, str]:
    """(value, rating, source) preferring field data; TBT is lab-only by definition."""
    cwv = cwv or {}
    field = cwv.get("field") or {}
    lab = cwv.get("lab") or {}
    if key != "tbt_ms" and field.get(key) is not None:
        cat = field.get(key.replace("_ms", "") + "_category") if key != "cls" else field.get("cls_category")
        return field[key], _FIELD_CAT.get(str(cat), _rate(key, field[key])), "field"
    if lab.get(key) is not None:
        return lab[key], _rate(key, lab[key]), "lab"
    return None, "", ""


def _fmt(key: str, value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if key == "cls" else f"{round(value):,} ms"


def performance_checks(
    cwv_mobile: dict[str, Any] | None,
    cwv_desktop: dict[str, Any] | None,
    sample: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Checks, the six-metric table (mobile and desktop, field or lab), page-weight rows."""
    g = "Performance"
    rows: list[dict[str, Any]] = []
    metrics_table: list[dict[str, Any]] = []
    any_data = bool((cwv_mobile or {}).get("field") or (cwv_mobile or {}).get("lab") or (cwv_desktop or {}).get("field") or (cwv_desktop or {}).get("lab"))
    for label, key, name in _METRICS:
        mv, mr, ms = _metric(cwv_mobile, key)
        dv, dr, ds = _metric(cwv_desktop, key)
        if mv is None and dv is None:
            rows.append(chk(g, label, "not_measured",
                            "INP exists only as real-user field data; this URL has none." if key == "inp_ms" and any_data
                            else "PageSpeed Insights returned no value."))
        else:
            rating = mr or dr
            source = ", ".join(sorted({s for s in (ms, ds) if s}))
            status = "issue" if rating in ("Poor", "Needs improvement") else "pass"
            rows.append(chk(g, label, status,
                            f"{name}: mobile {_fmt(key, mv)}" + (f", desktop {_fmt(key, dv)}" if dv is not None else "")
                            + f" ({source} data, {rating or 'unrated'}).",
                            urls=[], severity="High" if rating == "Poor" else "Medium",
                            fix={"lcp_ms": "Speed up the largest element: compress and preload the hero image, cut render-blocking files.",
                                 "inp_ms": "Break up long JavaScript tasks and defer non-essential scripts.",
                                 "cls": "Reserve space for images, embeds and banners so the layout does not shift.",
                                 "ttfb_ms": "Cache pages at the server or CDN and cut back-end work per request.",
                                 "fcp_ms": "Inline critical CSS and defer render-blocking resources.",
                                 "tbt_ms": "Reduce and split main-thread JavaScript."}[key]))
        metrics_table.append({
            "metric": f"{label} — {name}",
            "mobile": _fmt(key, mv) + (f" ({ms})" if ms else ""),
            "desktop": _fmt(key, dv) + (f" ({ds})" if ds else ""),
            "rating": " / ".join(r for r in (mr, dr) if r) or "—",
        })
    passes = (cwv_mobile or {}).get("passes_core_web_vitals")
    if passes is None:
        rows.append(chk(g, "CWV", "not_measured",
                        "No real-user (CrUX) field data for this URL; the values above are lab data from one controlled run."
                        if any_data else "PageSpeed Insights returned no data this pass."))
    else:
        rows.append(chk(g, "CWV", "pass" if passes else "issue",
                        "Passes the Core Web Vitals assessment (mobile field data)." if passes
                        else "Fails the Core Web Vitals assessment (mobile field data).",
                        severity="High", fix="Fix the failing metrics above, starting with the poorest."))

    pages = [p for p in _pages(sample) if p.get("html_source") == "direct"]
    lab = (cwv_mobile or {}).get("lab") or {}
    weight: list[dict[str, Any]] = []
    if pages:
        heavy = [p["final_url"] for p in pages if p["html_bytes"] > 500_000]
        sizes = ", ".join(f"{_short(p['final_url'])} {round(p['html_bytes'] / 1024)} KB" for p in pages[:4])
        rows.append(chk(g, "HTML size", "issue" if heavy else "pass", f"HTML document size: {sizes}.",
                        urls=heavy, severity="Low", fix="Trim inline scripts, styles and markup; aim for under 500 KB of HTML."))
        home = pages[0]["parsed"]
        blocking = lab.get("render_blocking") or home["render_blocking_js"] + home["render_blocking_css"]
        savings = lab.get("render_blocking_savings_ms")
        js_in_head = len(home["render_blocking_js"])
        status = "issue" if (savings or 0) >= 300 or js_in_head >= 2 else "pass"
        rows.append(chk(g, "JS/CSS", status,
                        f"{len(blocking)} render-blocking file(s)"
                        + (f", about {round(savings)} ms to save (lab)" if savings else "")
                        + f"; {js_in_head} synchronous script(s) in <head>"
                        + (f"; unused JS {round((lab.get('unused_js_bytes') or 0) / 1024)} KB, CSS {round((lab.get('unused_css_bytes') or 0) / 1024)} KB" if lab else "")
                        + ".", urls=[str(b) for b in blocking][:10], severity="Medium",
                        fix="Add defer/async to scripts, inline critical CSS and load the rest without blocking render."))
        third = home["third_party_hosts"]
        rows.append(chk(g, "third-party scripts", "issue" if len(third) > 10 else "pass",
                        f"{len(third)} third-party script host(s) on the homepage"
                        + (f" ({', '.join(third[:8])}{'…' if len(third) > 8 else ''})" if third else "")
                        + (f"; Lighthouse counts {lab['third_party_count']} third-party entities" if lab.get("third_party_count") else "")
                        + ".", severity="Medium", fix="Remove unused tags and load the rest after the page is interactive."))
        weight = [{"url": _short(p["final_url"]), "html_kb": round(p["html_bytes"] / 1024), "scripts": p["parsed"]["script_count"],
                   "blocking": len(p["parsed"]["render_blocking_js"]) + len(p["parsed"]["render_blocking_css"]),
                   "third_party": len(p["parsed"]["third_party_hosts"]), "images": p["parsed"]["image_count"]} for p in pages]
    else:
        for f in ("HTML size", "JS/CSS", "third-party scripts"):
            rows.append(chk(g, f, "not_measured", "The raw HTML could not be fetched directly (blocked or unreachable)."))
    imgs = (sample or {}).get("image_sizes") or []
    big = [i for i in imgs if i["bytes"] > 300_000]
    if imgs or lab.get("image_savings_bytes"):
        detail = (f"{len(big)} of {len(imgs)} homepage image(s) over 300 KB" if imgs else "")
        if lab.get("image_savings_bytes"):
            detail += ("; " if detail else "") + f"Lighthouse estimates {round(lab['image_savings_bytes'] / 1024)} KB of image savings"
        rows.append(chk(g, "image size", "issue" if big or (lab.get("image_savings_bytes") or 0) > 150_000 else "pass",
                        detail + ".", urls=[i["url"] for i in big], severity="Medium",
                        fix="Serve WebP/AVIF at the displayed size, compress, and lazy-load below-the-fold images."))
    else:
        rows.append(chk(g, "image size", "not_measured", "No image byte sizes were available this pass."))
    return rows, metrics_table, weight


def mobile_checks(
    sample: dict[str, Any] | None,
    cwv_mobile: dict[str, Any] | None,
    rendering: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    g = "Mobile"
    rows: list[dict[str, Any]] = []
    pages = _pages(sample)
    if pages:
        vp = pages[0]["parsed"]["viewport"]
        missing = [p["final_url"] for p in pages if not p["parsed"]["viewport"]]
        rows.append(chk(g, "viewport", "issue" if missing else "pass",
                        f"{len(missing)} page(s) have no viewport meta." if missing else f"Viewport meta present ({vp[:60]}).",
                        urls=missing, severity="High", fix='Add <meta name="viewport" content="width=device-width, initial-scale=1">.'))
        responsive = "width=device-width" in vp.replace(" ", "").lower()
        rows.append(chk(g, "responsive", "pass" if responsive else "issue",
                        "Viewport scales to the device width (responsive layout)." if responsive
                        else "Viewport does not use width=device-width; the page may not adapt to phones.",
                        severity="High", fix="Use width=device-width and a responsive layout."))
    else:
        rows.append(chk(g, "viewport", "not_measured", "No page HTML could be fetched this pass."))
        rows.append(chk(g, "responsive", "not_measured", "No page HTML could be fetched this pass."))
    m = cwv_mobile or {}
    if m.get("field"):
        rows.append(chk(g, "mobile CWV", "pass" if m.get("passes_core_web_vitals") else "issue",
                        "Mobile field data " + ("passes" if m.get("passes_core_web_vitals") else "does not pass") + " Core Web Vitals.",
                        severity="High", fix="See the Performance section for the failing mobile metrics."))
    elif m.get("lab"):
        rows.append(chk(g, "mobile CWV", "pass", f"Mobile lab performance score {m['lab'].get('performance_score')} (no field data)."))
    else:
        rows.append(chk(g, "mobile CWV", "not_measured", "No mobile PageSpeed data this pass."))
    r = rendering or {}
    # A challenge or error page on either fetch would compare two non-pages.
    if r.get("status") == "available" and r.get("desktop_status") == 200 and r.get("mobile_status") == 200 and (r.get("desktop_links") or 0) > 0:
        link_gap = any("links vs desktop" in str(i) for i in r.get("parity_issues") or [])
        text_gap = any("content parity" in str(i) for i in r.get("parity_issues") or [])
        rows.append(chk(g, "mobile navigation", "issue" if link_gap else "pass",
                        f"Mobile fetch has {r.get('mobile_links')} links vs desktop {r.get('desktop_links')}.",
                        severity="Medium", fix="Serve the same navigation links to mobile users."))
        rows.append(chk(g, "content parity", "issue" if text_gap or r.get("parity_issues") else "pass",
                        "; ".join(r.get("parity_issues") or []) or "Mobile and desktop fetches carry the same content.",
                        severity="Medium", fix="Serve mobile users the same main content as desktop (mobile-first indexing)."))
    else:
        why = ("The site answered the mobile/desktop fetch with a block or challenge page."
               if r.get("status") == "available" else "Mobile vs desktop fetch was not available.")
        rows.append(chk(g, "mobile navigation", "not_measured", why))
        rows.append(chk(g, "content parity", "not_measured", why))
    rows.append(chk(g, "touch targets", "not_measured",
                    "Lighthouse no longer audits tap-target size; check buttons and links are at least 48 px by hand."))
    return rows


# --------------------------------------------------------------------------- structured data


def structured_data_checks(
    sample: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Checks, a per-page schema table, and the rich results the site qualifies for."""
    g = "Structured data"
    rows: list[dict[str, Any]] = []
    pages = _pages(sample)
    table: list[dict[str, Any]] = []
    eligible: list[str] = []
    for p in pages:
        sd = p["parsed"]["structured_data"]
        table.append({
            "url": _short(p["final_url"]),
            "blocks": sd["json_ld_blocks"],
            "types": ", ".join(sd["types"] + [f"{t} (microdata)" for t in sd["microdata_types"]]) or "none",
            "problems": "; ".join(sd["malformed"] + sd["missing_required"] + sd["duplicates"]) or "—",
        })
        for e in sd["rich_results_eligible"]:
            if e not in eligible:
                eligible.append(e)
    if not pages:
        for f in ("schema types", "validation", "duplication", "entity relationships", "required properties", "rich-result eligibility"):
            rows.append(chk(g, f, "not_measured", "No page HTML could be fetched this pass."))
    else:
        all_types = sorted({t for p in pages for t in p["parsed"]["structured_data"]["types"] + p["parsed"]["structured_data"]["microdata_types"]})
        none = [p["final_url"] for p in pages if not p["parsed"]["structured_data"]["types"] and not p["parsed"]["structured_data"]["microdata_types"]]
        home_none = pages[0]["final_url"] in none
        if not all_types:
            rows.append(chk(g, "schema types", "issue", "No JSON-LD or microdata on any sampled page.", urls=none, severity="Medium",
                            fix="Add Organization (or LocalBusiness) and WebSite JSON-LD sitewide, plus page-type schema."))
        else:
            rows.append(chk(g, "schema types", "issue" if home_none else "pass",
                            f"Types found: {', '.join(all_types)}." + (" The homepage has none." if home_none else ""),
                            urls=none if home_none else [], severity="Low",
                            fix="Add Organization and WebSite JSON-LD to the homepage."))
        malformed = [(p["final_url"], m) for p in pages for m in p["parsed"]["structured_data"]["malformed"]]
        rows.append(chk(g, "validation", "issue" if malformed else "pass",
                        f"{len(malformed)} malformed block(s): {malformed[0][1]}" if malformed else "All JSON-LD blocks parse as valid JSON with @context.",
                        urls=[u for u, _ in malformed], severity="High", fix="Fix the JSON syntax and add \"@context\": \"https://schema.org\"."))
        dups = [(p["final_url"], d) for p in pages for d in p["parsed"]["structured_data"]["duplicates"]]
        rows.append(chk(g, "duplication", "issue" if dups else "pass",
                        f"{dups[0][1]} on {len(dups)} page(s)." if dups else "No entity is declared twice on a page.",
                        urls=[u for u, _ in dups], severity="Low", fix="Declare each entity once per page (plugins often add a second copy)."))
        multi = [p for p in pages if p["parsed"]["structured_data"].get("top_level_entities", 0) >= 2]
        unlinked = [p["final_url"] for p in multi if not p["parsed"]["structured_data"]["entities_linked"]]
        rows.append(chk(g, "entity relationships", "issue" if unlinked else "pass",
                        f"{len(unlinked)} page(s) declare several entities without linking them by @id." if unlinked
                        else "Entities are linked by @id where several are declared." if multi else "Only single entities are declared.",
                        urls=unlinked, severity="Low", fix="Give Organization and WebSite an @id and reference it from publisher/provider."))
        missing = [(p["final_url"], m) for p in pages for m in p["parsed"]["structured_data"]["missing_required"]]
        rows.append(chk(g, "required properties", "issue" if missing else "pass",
                        f"{len(missing)} entity(ies) lack required properties, e.g. {missing[0][1]}." if missing
                        else "Detected entities carry their required properties.",
                        urls=[u for u, _ in missing], severity="Medium", fix="Add the missing properties listed per page."))
        rows.append(chk(g, "rich-result eligibility", "pass",
                        ("Eligible: " + "; ".join(eligible) + ". Eligibility is not a guarantee; Google decides per query.")
                        if eligible else "No rich result types are fully marked up yet."))
    return rows, table, eligible


# --------------------------------------------------------------------------- security


def security_checks(sample: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    g = "Security"
    rows: list[dict[str, Any]] = []
    sample = sample or {}
    pages = _pages(sample)
    home = sample.get("home_url") or ""
    http = (sample.get("variants") or {}).get("http") or {}
    if not home:
        rows.append(chk(g, "HTTPS", "not_measured", "The homepage could not be fetched."))
    elif not home.startswith("https://"):
        rows.append(chk(g, "HTTPS", "issue", "The homepage is served over http://.", urls=[home], severity="Critical",
                        fix="Install a certificate and serve every page over https://."))
    elif http and http.get("status") == 200 and not str(http.get("final_url", "")).startswith("https://"):
        rows.append(chk(g, "HTTPS", "issue", "https:// works, but http:// also serves pages without redirecting.",
                        urls=[http["url"]], severity="Critical", fix="Force https with a sitewide 301 from http://."))
    else:
        rows.append(chk(g, "HTTPS", "pass", "Homepage on https://" + (" and http:// redirects to it." if http.get("first_status") in (301, 308) else ".")))
    mixed = [(p["final_url"], m) for p in pages for m in p["parsed"]["mixed_content"]]
    if pages:
        rows.append(chk(g, "mixed content", "issue" if mixed else "pass",
                        f"{len(mixed)} http:// resource(s) on https pages, e.g. {mixed[0][1][:80]}." if mixed
                        else "No http:// images, scripts or styles on the sampled https pages.",
                        urls=[u for u, _ in mixed], severity="High", fix="Load every resource over https:// (update hard-coded http:// URLs)."))
    else:
        rows.append(chk(g, "mixed content", "not_measured", "No page HTML could be fetched this pass."))

    headers = pages[0]["headers"] if pages and sample.get("headers_measured") else None
    header_rows: list[dict[str, Any]] = []
    if headers is None:
        reason = "The site blocked this server's request (bot protection), so response headers could not be read." if pages or sample.get("pages") else "The homepage could not be fetched."
        rows.append(chk(g, "HSTS", "not_measured", reason))
        rows.append(chk(g, "security headers", "not_measured", reason))
    else:
        hsts = headers.get("strict-transport-security", "")
        age = re.search(r"max-age=(\d+)", hsts)
        if not hsts:
            rows.append(chk(g, "HSTS", "issue", "No Strict-Transport-Security header.", urls=[home], severity="Medium",
                            fix="Send Strict-Transport-Security: max-age=31536000; includeSubDomains."))
        elif not age or int(age.group(1)) < 15_552_000:
            rows.append(chk(g, "HSTS", "issue", f"HSTS max-age is short ({hsts[:60]}).", urls=[home], severity="Low",
                            fix="Raise max-age to at least 31536000 (one year)."))
        else:
            rows.append(chk(g, "HSTS", "pass", f"HSTS set ({hsts[:60]})."))
        csp = headers.get("content-security-policy", "")
        xfo = headers.get("x-frame-options", "")
        checks = {
            "Content-Security-Policy": bool(csp),
            "X-Frame-Options (or CSP frame-ancestors)": bool(xfo) or "frame-ancestors" in csp,
            "X-Content-Type-Options: nosniff": headers.get("x-content-type-options", "").lower() == "nosniff",
            "Referrer-Policy": bool(headers.get("referrer-policy")),
        }
        absent = [k for k, ok in checks.items() if not ok]
        rows.append(chk(g, "security headers", "issue" if absent else "pass",
                        ("Missing: " + ", ".join(absent) + ".") if absent else "CSP, frame, content-type and referrer headers are set.",
                        urls=[home] if absent else [], severity="Medium" if "Content-Security-Policy" in absent else "Low",
                        fix="Add the missing headers at the server or CDN."))
        header_rows = [{"header": k, "present": "yes" if ok else "no"} for k, ok in checks.items()]
        header_rows.insert(0, {"header": "Strict-Transport-Security", "present": "yes" if hsts else "no"})

    wp = sample.get("wordpress") or {}
    if not wp:
        for f in ("exposed WordPress files", "XML-RPC", "WordPress version exposure"):
            rows.append(chk(g, f, "not_measured", "The homepage HTML could not be read (blocked), so the platform was not identified."))
    elif not wp.get("is_wordpress"):
        for f in ("exposed WordPress files", "XML-RPC", "WordPress version exposure"):
            rows.append(chk(g, f, "pass", "Not a WordPress site (no wp-content or wp-includes assets)."))
    elif wp.get("probes_blocked"):
        for f in ("exposed WordPress files", "XML-RPC"):
            rows.append(chk(g, f, "not_measured", "WordPress site, but the site blocked the file probes from this server."))
        version = wp.get("version_exposed")
        rows.append(chk(g, "WordPress version exposure", "issue" if version else "pass",
                        f"Version disclosed: {version}" if version else "No WordPress version in the page source.",
                        severity="Low", fix="Remove the generator tag and strip ?ver= from core assets."))
    else:
        exposed = wp.get("exposed_files") or []
        rows.append(chk(g, "exposed WordPress files", "issue" if exposed else "pass",
                        f"{len(exposed)} config backup(s) are publicly readable." if exposed
                        else f"None of {len(wp.get('files_checked') or [])} common wp-config backup names are readable.",
                        urls=exposed, severity="Critical", fix="Delete the backup files now and rotate the database password and salts."))
        rows.append(chk(g, "XML-RPC", "issue" if wp.get("xmlrpc_enabled") else "pass",
                        "xmlrpc.php is enabled (brute-force and pingback abuse target)." if wp.get("xmlrpc_enabled")
                        else f"xmlrpc.php is not exposed (status {wp.get('xmlrpc_status')}).",
                        severity="Medium", fix="Disable XML-RPC (plugin or server rule) unless Jetpack or an app needs it."))
        version = wp.get("version_exposed")
        rows.append(chk(g, "WordPress version exposure", "issue" if version or wp.get("readme_exposed") else "pass",
                        ("Version disclosed: " + str(version) if version else "")
                        + ("; readme.html is public" if wp.get("readme_exposed") else "") or "WordPress version is not disclosed.",
                        severity="Low", fix="Remove the generator tag and readme.html; strip ?ver= from core assets."))
    return rows, header_rows


# --------------------------------------------------------------------------- issues, coverage, groups


def issues_from_checks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r["status"] != "issue":
            continue
        out.append({
            "rule_id": "SAMPLE_" + re.sub(r"[^A-Z0-9]+", "_", f"{r['group']}_{r['check']}".upper()).strip("_"),
            "category": r["group"],
            "field": r["field"],
            "title": f"{r['check']}: {r['detail']}"[:240],
            "description": r["detail"],
            "severity": r["severity"] or "Medium",
            "priority": 0,
            "affected_urls": r["affected_urls"],
            "affected_url_count": len(r["affected_urls"]),
            "recommended_action": r["fix"],
            "source": "page_sample",
            "status": "open",
        })
    return out


def field_statuses(rows: list[dict[str, Any]]) -> dict[tuple[str, str], tuple[str, str]]:
    """Strongest status per (group, field): issue > pass > not_measured."""
    rank = {"issue": 2, "pass": 1, "not_measured": 0}
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for r in rows:
        key = (r["group"], r["field"])
        status = "checked" if r["status"] == "pass" else r["status"]
        cur = out.get(key)
        if cur is None or rank[r["status"]] > rank["pass" if cur[0] == "checked" else cur[0]]:
            out[key] = (status, r["detail"][:200])
    return out


def infer_group(text: str, category: str | None = None) -> tuple[str, str]:
    """(group, field) for a free-text finding without a category."""
    from app.services.technical_seo_checklist import _FIELD_HINTS

    low_text = (text or "").lower()
    # Words that name one field outright, ahead of the looser checklist hints
    # (Indexation also lists "canonical", which would otherwise win by order).
    for needle, target in _SPECIFIC:
        if needle in low_text:
            return target

    if category:
        cat = normalize_category(category)
        if cat in GROUP_INDEX:
            low = text.lower()
            for f in dict(AUDIT_GROUPS)[cat]:
                if any(h in low for h in _FIELD_HINTS.get(f, (f.lower(),))):
                    return cat, f
            return cat, ""
    low = (text or "").lower()
    for group, fields in AUDIT_GROUPS:
        for f in fields:
            if any(h in low for h in _FIELD_HINTS.get(f, (f.lower(),))):
                return group, f
    return "Crawlability", ""


_SPECIFIC: tuple[tuple[str, tuple[str, str]], ...] = (
    ("canonical", ("Canonicalisation", "canonical existence")),
    ("viewport", ("Mobile", "viewport")),
    ("json-ld", ("Structured data", "schema types")),
    ("structured data", ("Structured data", "schema types")),
    ("missing h1", ("On-page technical SEO", "H1")),
    ("homepage missing h1", ("On-page technical SEO", "H1")),
    ("http does not redirect", ("URL structure", "HTTP/HTTPS")),
    ("http serves content", ("URL structure", "HTTP/HTTPS")),
    ("robots.txt", ("Crawlability", "robots.txt")),
    ("xml sitemap", ("Crawlability", "sitemap")),
)


def group_scores(issues: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """0-100 per group from issue severity (same formula as technical_seo_rules);
    no score when the group had no measured field this pass."""
    measured = {g["group"]: sum(1 for f in g["fields"] if f["status"] != "not_measured") for g in coverage.get("groups") or []}
    # Performance is scored only on real speed measurements, not on page weight alone.
    perf_fields = {"CWV", "LCP", "INP", "CLS", "TTFB", "FCP", "TBT"}
    for g in coverage.get("groups") or []:
        if g["group"] == "Performance":
            measured["Performance"] = sum(1 for f in g["fields"] if f["field"] in perf_fields and f["status"] != "not_measured")
    by_cat: dict[str, list[dict[str, Any]]] = {c: [] for c in ALL_CATEGORIES}
    for i in issues:
        by_cat.setdefault(normalize_category(str(i.get("category") or "")), []).append(i)
    out: dict[str, dict[str, Any]] = {}
    for cat in ALL_CATEGORIES:
        items = by_cat.get(cat) or []
        if not measured.get(cat):
            out[cat] = {"category": cat, "score": None, "status": "NOT_AVAILABLE", "issue_count": len(items),
                        "reason": "No field in this group was measured this pass."}
            continue
        penalty = sum(_SEV_PENALTY.get(str(i.get("severity")), 8) * min(1.0, math.log10(1 + max(1, int(i.get("affected_url_count") or 1)))) for i in items)
        out[cat] = {"category": cat, "score": max(0, min(100, round(100 - penalty))), "status": "available",
                    "issue_count": len(items), "reason": None}
    return out


def scorecard(scores: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for cat in ALL_CATEGORIES:
        s = scores.get(cat) or {}
        score = s.get("score")
        state = "not measured" if score is None else "healthy" if score >= 80 else "needs attention" if score >= 60 else "poor"
        rows.append({"group": cat, "score": score, "state": state, "issues": s.get("issue_count", 0)})
    line = " · ".join(f"{r['group']} {r['score'] if r['score'] is not None else '—'}" for r in rows)
    return rows, "Scorecard (0-100): " + line


def quick_wins(backlog: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """High/Critical items whose fix is a template or setting change."""
    out = []
    for b in backlog:
        if str(b.get("priority")) not in ("Critical", "High"):
            continue
        easy = (b.get("category"), b.get("field")) in EASY_FIELDS or bool(_EASY_TEXT.search(str(b.get("issue") or "")))
        if not easy or not b.get("fix"):
            continue
        out.append(b)
        if len(out) >= limit:
            break
    return out


def build_backlog(issues: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All issues plus other actions, one row each, grouped by audit group then severity."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i in issues:
        key = re.sub(r"\W+", " ", str(i.get("title") or "")).strip().lower()[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        group, field = (normalize_category(str(i.get("category"))), str(i.get("field") or "")) if i.get("category") else infer_group(str(i.get("title")))
        if not field:
            field = infer_group(str(i.get("title")), group)[1]
        rows.append({
            "category": group, "field": field, "priority": i.get("severity") or "Medium",
            "issue": i.get("title"), "fix": i.get("recommended_action") or "",
            "affected": i.get("affected_url_count") or len(i.get("affected_urls") or []) or "",
            "source": i.get("source") or "",
        })
    for e in extra:
        key = re.sub(r"\W+", " ", str(e.get("issue") or "")).strip().lower()[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        group, field = infer_group(str(e.get("issue")), e.get("category"))
        rows.append({"category": group, "field": field, "priority": e.get("priority") or "Medium", "issue": e.get("issue"),
                     "fix": e.get("fix") or "", "affected": e.get("affected_url_count") or "", "source": e.get("source") or ""})
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(str(r["priority"]), 9), GROUP_INDEX.get(r["category"], 99)))
    top = rows[:BACKLOG_LIMIT]
    top.sort(key=lambda r: (GROUP_INDEX.get(r["category"], 99), SEVERITY_ORDER.get(str(r["priority"]), 9)))
    return top


def build_groups(
    *,
    issues: list[dict[str, Any]],
    coverage: dict[str, Any],
    scores: dict[str, dict[str, Any]],
    checks: list[dict[str, Any]],
    coverage_notes: dict[str, str],
) -> list[dict[str, Any]]:
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for i in issues:
        by_cat.setdefault(normalize_category(str(i.get("category") or "")), []).append(i)
    cov = {g["group"]: g for g in coverage.get("groups") or []}
    out = []
    for cat in ALL_CATEGORIES:
        items = sorted(by_cat.get(cat) or [], key=lambda i: (SEVERITY_ORDER.get(str(i.get("severity")), 9), -int(i.get("affected_url_count") or 0)))
        fields = (cov.get(cat) or {}).get("fields") or []
        s = scores.get(cat) or {}
        out.append({
            "group": cat,
            "score": s.get("score"),
            "status": s.get("status"),
            "issue_count": len(items),
            "fields_measured": sum(1 for f in fields if f["status"] != "not_measured"),
            "fields_not_measured": sum(1 for f in fields if f["status"] == "not_measured"),
            "coverage_note": (
                f"Reduced coverage: {nm} of {len(fields)} fields not measured. " if (nm := sum(1 for f in fields if f["status"] == "not_measured")) else ""
            ) + coverage_notes.get(cat, ""),
            "issues": [
                {
                    "severity": i.get("severity"),
                    "issue": i.get("title"),
                    "affected": i.get("affected_url_count") or len(i.get("affected_urls") or []) or "",
                    "examples": ", ".join(_short(u) for u in (i.get("affected_urls") or [])[:3]),
                    "fix": i.get("recommended_action") or "",
                }
                for i in items[:25]
            ],
            "fields": [
                {"field": f["field"], "status": {"checked": "checked, OK", "issue": "issue found", "not_measured": "not measured"}.get(f["status"], f["status"]), "note": f["note"]}
                for f in fields
            ],
            "checks": [
                {"check": c["check"], "status": {"pass": "OK", "issue": "Issue", "not_measured": "Not measured"}[c["status"]],
                 "detail": c["detail"], "urls": ", ".join(_short(u) for u in c["affected_urls"][:4])}
                for c in checks if c["group"] == cat
            ],
        })
    return out
