"""XML sitemap audit joined onto the page hierarchy and the Phase 6 URL tree.

Parses sitemap indexes and urlsets already fetched for a client. Does not
generate or publish a sitemap, and does not crawl every listed URL.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from app.integrations.web_fetch import fetch_url, is_indexable_html_url

URL_CAP = 50_000
_URL_BLOCK = re.compile(r"<url\b[^>]*>(.*?)</url>", re.IGNORECASE | re.DOTALL)
_SITEMAP_BLOCK = re.compile(r"<sitemap\b[^>]*>(.*?)</sitemap>", re.IGNORECASE | re.DOTALL)
_LOC = re.compile(r"<loc[^>]*>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_LASTMOD = re.compile(r"<lastmod[^>]*>\s*([^<\s]+)\s*</lastmod>", re.IGNORECASE)


def path_key(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    path = urlparse(raw).path if "://" in raw else (raw if raw.startswith("/") else f"/{raw}")
    path = path.lower() or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _file_name(url: str) -> str:
    name = urlparse(url).path.rstrip("/").split("/")[-1]
    return name or "sitemap.xml"


def _parse_day(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_sitemap_xml(text: str, *, source_url: str) -> dict[str, Any]:
    """One document. A .xml loc is a child sitemap only inside a sitemap index."""
    body = text or ""
    lower = body.lower()
    file_name = _file_name(source_url)
    if "<sitemapindex" in lower:
        children: list[str] = []
        for block in _SITEMAP_BLOCK.findall(body):
            found = _LOC.search(block)
            if found:
                children.append(found.group(1).strip())
        if not children:
            children = [m.strip() for m in _LOC.findall(body)]
        return {"kind": "index", "source_url": source_url, "file": file_name, "children": children, "urls": []}
    if "<urlset" in lower:
        urls: list[dict[str, Any]] = []
        for block in _URL_BLOCK.findall(body):
            loc = _LOC.search(block)
            if not loc:
                continue
            lastmod_match = _LASTMOD.search(block)
            loc_url = loc.group(1).strip()
            urls.append(
                {
                    "loc": loc_url,
                    "path": path_key(loc_url),
                    "lastmod": lastmod_match.group(1).strip() if lastmod_match else None,
                    "sitemap_file": file_name,
                    "issues": [],
                }
            )
        return {"kind": "urlset", "source_url": source_url, "file": file_name, "children": [], "urls": urls}
    return {
        "kind": "invalid",
        "source_url": source_url,
        "file": file_name,
        "children": [],
        "urls": [],
        "issue": "non_xml",
    }


def audit_documents(
    documents: list[dict[str, Any]],
    *,
    cap: int = URL_CAP,
    today: date | None = None,
) -> dict[str, Any]:
    """Build the audit from already-downloaded sitemap bodies. No HTTP."""
    today = today or date.today()
    issues: list[dict[str, Any]] = []
    urls: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    parsed_urlset = False

    for doc in documents:
        source = str(doc.get("url") or "")
        status_code = int(doc.get("status_code") or 0)
        text = str(doc.get("text") or "")
        if status_code and status_code != 200:
            issues.append(
                {
                    "code": "sitemap_http",
                    "sitemap_file": _file_name(source),
                    "detail": f"{source} returned HTTP {status_code}",
                }
            )
            continue
        parsed = parse_sitemap_xml(text, source_url=source)
        if parsed["kind"] == "invalid":
            issues.append(
                {
                    "code": "non_xml",
                    "sitemap_file": parsed["file"],
                    "detail": f"{source} is not a sitemap index or urlset",
                }
            )
            continue
        if parsed["kind"] == "index":
            files.append(
                {
                    "url": source,
                    "kind": "index",
                    "file": parsed["file"],
                    "child_count": len(parsed["children"]),
                    "children": parsed["children"],
                }
            )
            continue
        parsed_urlset = True
        file_urls = list(parsed["urls"])
        file_issues: list[str] = []
        if len(file_urls) >= cap:
            file_issues.append("over_50000")
            issues.append(
                {
                    "code": "over_50000",
                    "sitemap_file": parsed["file"],
                    "detail": f"{parsed['file']} has {len(file_urls)} URLs (cap {cap})",
                }
            )
        lastmods = [u.get("lastmod") for u in file_urls if u.get("lastmod")]
        if len(file_urls) >= 2 and len(lastmods) == len(file_urls) and len(set(lastmods)) == 1:
            file_issues.append("lastmod_identical")
            issues.append(
                {
                    "code": "lastmod_identical",
                    "sitemap_file": parsed["file"],
                    "detail": f"{parsed['file']} uses the same lastmod on every URL",
                }
            )
        files.append(
            {
                "url": source,
                "kind": "urlset",
                "file": parsed["file"],
                "url_count": len(file_urls),
                "issues": file_issues,
            }
        )
        for row in file_urls:
            loc = str(row["loc"])
            if loc in seen:
                continue
            seen.add(loc)
            row_issues = list(row.get("issues") or [])
            lastmod = row.get("lastmod")
            if not lastmod:
                row_issues.append("lastmod_missing")
            else:
                day = _parse_day(str(lastmod))
                if day and day > today:
                    row_issues.append("lastmod_future")
            row["issues"] = row_issues
            row["excluded_from_inventory"] = not is_indexable_html_url(loc)
            row["http_status"] = "not_measured"
            urls.append(row)

    return {
        "status": "available" if parsed_urlset or files else "not_available",
        "urls": urls,
        "issues": issues,
        "files": files,
        "summary": {
            "url_cap": cap,
            "url_count": len(urls),
            "indexable_url_count": sum(1 for u in urls if not u.get("excluded_from_inventory")),
            "issue_count": len(issues),
            "http_checked": 0,
            "non_200": 0,
            "redirects": 0,
            "noindex": 0,
            "important_missing": None,
        },
        "gsc_note": None,
    }


def apply_known_page_status(audit: dict[str, Any], pages: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Flag non-200, redirect, and noindex only for URLs already in the crawl. No new fetch."""
    by_path: dict[str, dict[str, Any]] = {}
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        key = path_key(str(page.get("url") or page.get("path") or ""))
        if key:
            by_path[key] = page
    checked = non_200 = redirects = noindex = 0
    for row in audit.get("urls") or []:
        if row.get("excluded_from_inventory"):
            continue
        page = by_path.get(str(row.get("path") or ""))
        if not page:
            row["http_status"] = "not_measured"
            continue
        status = page.get("status_code")
        if status is None:
            status = page.get("status")
        try:
            code = int(status) if status not in (None, "") else 0
        except (TypeError, ValueError):
            code = 0
        robots = str(page.get("robots") or page.get("meta_robots") or "").lower()
        flagged = "noindex" in robots or page.get("noindex") is True
        if code <= 0 and not flagged:
            row["http_status"] = "not_measured"
            continue
        checked += 1
        issues = list(row.get("issues") or [])
        if code >= 400:
            non_200 += 1
            issues.append("non_200")
            row["http_status"] = code
        elif 300 <= code < 400:
            redirects += 1
            non_200 += 1
            issues.append("redirect")
            row["http_status"] = code
        elif code:
            row["http_status"] = code
        if flagged:
            noindex += 1
            issues.append("noindex")
        row["issues"] = issues
    summary = dict(audit.get("summary") or {})
    summary.update(
        {"http_checked": checked, "non_200": non_200, "redirects": redirects, "noindex": noindex}
    )
    audit["summary"] = summary
    return audit


def _indexable_by_path(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in audit.get("urls") or []:
        if row.get("excluded_from_inventory"):
            continue
        key = str(row.get("path") or "")
        if key and key not in out:
            out[key] = row
    return out


def stamp_page_hierarchy(
    rows: list[dict[str, Any]],
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    listed = _indexable_by_path(audit)
    stamped: list[dict[str, Any]] = []
    missing = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        key = path_key(str(item.get("path") or item.get("url") or ""))
        found = listed.get(key)
        if found:
            item["in_xml_sitemap"] = True
            item["sitemap_file"] = found.get("sitemap_file")
            item["sitemap_issues"] = list(found.get("issues") or [])
        else:
            item["in_xml_sitemap"] = False
            item["sitemap_file"] = None
            item["sitemap_issues"] = ["missing_from_sitemap"]
            missing += 1
        stamped.append(item)
    summary = dict(audit.get("summary") or {})
    summary["important_missing"] = missing
    audit["summary"] = summary
    return stamped


def stamp_target_url_tree(
    nodes: list[dict[str, Any]],
    audit: dict[str, Any],
    *,
    existing_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Set in_sitemap, sitemap_gap, or planned_only. Does not move a URL or add a redirect."""
    listed = _indexable_by_path(audit)
    existing = {path_key(p) for p in (existing_paths or set()) if path_key(p)}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        key = path_key(str(node.get("url") or node.get("path") or ""))
        found = listed.get(key)
        is_existing = key in existing
        if found and is_existing:
            verdict = "in_sitemap"
        elif found:
            verdict = "in_sitemap"
        elif is_existing:
            verdict = "sitemap_gap"
        else:
            verdict = "planned_only"
        node["sitemap_verdict"] = verdict
        node["in_sitemap"] = verdict == "in_sitemap"
        node["sitemap_file"] = found.get("sitemap_file") if found else None
        node["sitemap_issues"] = list(found.get("issues") or []) if found else (
            ["missing_from_sitemap"] if verdict == "sitemap_gap" else []
        )
    return nodes


def build_sitemap_hierarchy(
    tree: list[dict[str, Any]],
    audit: dict[str, Any],
    *,
    hierarchy_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Parent chain of the URL tree plus the sitemap verdict. Unassigned locs match neither tree nor hierarchy."""
    known = {path_key(str(n.get("url") or n.get("path") or "")) for n in tree if isinstance(n, dict)}
    known |= {path_key(p) for p in (hierarchy_paths or set())}
    known.discard("")
    children_missing: dict[str, list[str]] = {}
    for node in tree:
        if not isinstance(node, dict) or node.get("sitemap_verdict") != "sitemap_gap":
            continue
        parent = path_key(str(node.get("parent") or "/")) or "/"
        children_missing.setdefault(parent, []).append(str(node.get("url") or node.get("path") or ""))
    nodes = []
    for node in tree:
        if not isinstance(node, dict):
            continue
        key = path_key(str(node.get("url") or node.get("path") or ""))
        nodes.append(
            {
                "url": node.get("url"),
                "parent": node.get("parent"),
                "depth": node.get("depth"),
                "page_type": node.get("page_type") or node.get("type"),
                "sitemap_verdict": node.get("sitemap_verdict"),
                "sitemap_file": node.get("sitemap_file"),
                "children_missing": children_missing.get(key, []),
            }
        )
    unassigned = [
        {"loc": row.get("loc"), "path": row.get("path"), "sitemap_file": row.get("sitemap_file")}
        for row in audit.get("urls") or []
        if not row.get("excluded_from_inventory") and str(row.get("path") or "") not in known
    ]
    return {"nodes": nodes, "unassigned": unassigned}


def sitemap_field_statuses(audit: dict[str, Any] | None) -> dict[str, tuple[str, str]]:
    """Checklist statuses for the Sitemap group. Unchecked HTTP stays not_measured."""
    if not audit or audit.get("status") != "available":
        return {}
    summary = dict(audit.get("summary") or {})
    issues = list(audit.get("issues") or [])
    codes = {str(i.get("code")) for i in issues if isinstance(i, dict)}
    lastmod_bad = bool(codes & {"lastmod_identical"}) or any(
        "lastmod_missing" in (u.get("issues") or []) or "lastmod_future" in (u.get("issues") or [])
        for u in audit.get("urls") or []
        if not u.get("excluded_from_inventory")
    )
    http_checked = int(summary.get("http_checked") or 0)

    def _http(issue_count: int, label: str) -> tuple[str, str]:
        if http_checked <= 0:
            return "not_measured", "Sitemap URLs were not status-checked against a crawled page."
        if issue_count:
            return "issue", label
        return "checked", "Crawled sitemap URLs had no matching problem."

    important = summary.get("important_missing")
    if important is None:
        important_status = ("not_measured", "Hierarchy was not joined to the sitemap this pass.")
    elif int(important) > 0:
        important_status = ("issue", f"{important} hierarchy URL(s) are missing from the sitemap.")
    else:
        important_status = ("checked", "Hierarchy URLs are present in the sitemap.")

    return {
        "indexable URLs": (
            "issue" if "non_xml" in codes or "sitemap_http" in codes else "checked",
            "Sitemap XML parsed.",
        ),
        "200 status": _http(int(summary.get("non_200") or 0) - int(summary.get("redirects") or 0), "A listed URL is not HTTP 200."),
        "canonical URLs": ("not_measured", "Canonical comparison is not part of the sitemap XML parse."),
        "no noindex URLs": _http(int(summary.get("noindex") or 0), "A listed URL is noindex."),
        "no redirects": _http(int(summary.get("redirects") or 0), "A listed URL redirects."),
        "no 404s": _http(
            sum(1 for u in audit.get("urls") or [] if "non_200" in (u.get("issues") or [])),
            "A listed URL returns an error status.",
        ),
        "lastmod accuracy": (
            "issue" if lastmod_bad else "checked",
            "lastmod is missing, identical across a file, or in the future."
            if lastmod_bad
            else "lastmod values were read from the sitemap.",
        ),
        "important pages included": important_status,
    }


def checklist_issues(audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Findings the Phase 7 issue list can carry. GSC errors are not copied here."""
    if not audit or audit.get("status") != "available":
        return []
    out: list[dict[str, Any]] = []
    for issue in audit.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        out.append(
            {
                "rule_id": f"sitemap_{issue.get('code')}",
                "category": "Sitemap",
                "title": str(issue.get("detail") or issue.get("code")),
                "severity": "Medium",
                "source": "sitemap_audit",
            }
        )
    for row in audit.get("urls") or []:
        for code in row.get("issues") or []:
            if code in {"lastmod_missing", "lastmod_future"}:
                continue
            out.append(
                {
                    "rule_id": f"sitemap_{code}",
                    "category": "Sitemap",
                    "title": f"Sitemap {row.get('path')}: {code}",
                    "severity": "Medium",
                    "source": "sitemap_audit",
                }
            )
    return out


async def fetch_sitemap_audit(start_url: str, *, cap: int = URL_CAP) -> dict[str, Any]:
    """Download robots Sitemap lines and child indexes, then parse. Does not fetch each page."""
    if not start_url.startswith("http"):
        start_url = "https://" + start_url
    origin = f"{urlparse(start_url).scheme}://{urlparse(start_url).netloc}"
    candidates = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
    try:
        robots = await fetch_url(f"{origin}/robots.txt", timeout=10)
        if robots.get("status_code") == 200 and robots.get("text"):
            for line in str(robots["text"]).splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    if sm:
                        candidates.insert(0, sm)
    except Exception:  # noqa: BLE001
        pass

    documents: list[dict[str, Any]] = []
    seen: set[str] = set()

    async def _pull(sm_url: str, depth: int) -> None:
        if depth > 2 or sm_url in seen or len(seen) > 24:
            return
        seen.add(sm_url)
        try:
            res = await fetch_url(sm_url, timeout=15)
        except Exception:  # noqa: BLE001
            documents.append({"url": sm_url, "status_code": 0, "text": ""})
            return
        text = str(res.get("text") or "")
        documents.append({"url": sm_url, "status_code": int(res.get("status_code") or 0), "text": text})
        parsed = parse_sitemap_xml(text, source_url=sm_url)
        if parsed["kind"] != "index":
            return
        for child in parsed["children"][:20]:
            await _pull(child, depth + 1)

    for candidate in dict.fromkeys(candidates):
        await _pull(candidate, 0)
    return audit_documents(documents, cap=cap)
