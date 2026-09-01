"""Phase 7 companion checks — rendering, hreflang surface, internal linking, confidence."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.services.technical_seo_schemas import TechnicalSEOPage, TechnicalSEOIssue

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_HREFLANG_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]+hreflang=["\']([^"\']+)["\'][^>]+href=["\']([^"\']+)["\']',
    re.I,
)
_HREFLANG_RE_ALT = re.compile(
    r'<link[^>]+hreflang=["\']([^"\']+)["\'][^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']alternate["\']',
    re.I,
)


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _count_links(html: str) -> int:
    return len(re.findall(r'<a\s[^>]*href=', html or "", re.I))


def _extract_hreflang(html: str, base_url: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for pattern in (_HREFLANG_RE, _HREFLANG_RE_ALT):
        for lang, href in pattern.findall(html or ""):
            out.append({"hreflang": lang.strip(), "href": urljoin(base_url, href.strip())})
    return out


async def run_rendering_snapshot(url: str) -> dict[str, Any]:
    """Lightweight desktop vs mobile HTML fetch — not a full headless render audit."""
    seed = url if "://" in url else f"https://{url}"
    result: dict[str, Any] = {
        "status": "NOT_AVAILABLE",
        "url": seed,
        "parity_issues": [],
        "note": "",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            desktop = await client.get(seed, headers={"User-Agent": DESKTOP_UA})
            mobile = await client.get(seed, headers={"User-Agent": MOBILE_UA})
    except httpx.HTTPError as exc:
        result["note"] = f"Rendering snapshot failed: {str(exc)[:120]}"
        return result

    d_html = desktop.text or ""
    m_html = mobile.text or ""
    d_text = _strip_tags(d_html)
    m_text = _strip_tags(m_html)
    issues: list[str] = []

    d_len, m_len = len(d_text), len(m_text)
    if d_len > 200 and m_len < d_len * 0.6:
        issues.append(
            f"Mobile body text ~{m_len} chars vs desktop ~{d_len} — possible content parity gap"
        )
    d_links, m_links = _count_links(d_html), _count_links(m_html)
    if d_links > 5 and m_links < d_links * 0.5:
        issues.append(f"Mobile has {m_links} links vs desktop {d_links} — nav/link parity risk")

    d_title = re.search(r"<title[^>]*>([^<]+)</title>", d_html, re.I)
    m_title = re.search(r"<title[^>]*>([^<]+)</title>", m_html, re.I)
    if d_title and m_title and d_title.group(1).strip() != m_title.group(1).strip():
        issues.append("Title differs between desktop and mobile fetches")

    result.update(
        {
            "status": "available",
            "desktop_status": desktop.status_code,
            "mobile_status": mobile.status_code,
            "desktop_text_length": d_len,
            "mobile_text_length": m_len,
            "desktop_links": d_links,
            "mobile_links": m_links,
            "parity_issues": issues,
            "parity_ok": len(issues) == 0,
            "note": (
                "Lightweight UA fetch only — for CSR/SPA sites run full rendering-audit."
            ),
        }
    )
    return result


async def run_hreflang_surface_check(
    primary_url: str,
    *,
    sample_urls: list[str] | None = None,
    max_pages: int = 5,
) -> dict[str, Any]:
    """Surface hreflang presence + basic reciprocity on a small page sample."""
    urls = [primary_url] + list(sample_urls or [])
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique.append(u)
        if len(unique) >= max_pages:
            break

    annotations: dict[str, list[dict[str, str]]] = {}
    errors: list[str] = []
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            for page_url in unique:
                try:
                    resp = await client.get(
                        page_url if "://" in page_url else f"https://{page_url}"
                    )
                    base = str(resp.url)
                    tags = _extract_hreflang(resp.text or "", base)
                    if tags:
                        annotations[base] = tags
                except httpx.HTTPError as exc:
                    errors.append(f"{page_url}: {str(exc)[:80]}")
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "NOT_AVAILABLE",
            "reason": str(exc)[:200],
            "pages_checked": 0,
        }

    if not annotations:
        return {
            "status": "available",
            "pages_checked": len(unique),
            "hreflang_found": False,
            "issues": [],
            "note": "No hreflang annotations found on sampled pages (may be single-locale site).",
        }

    missing_returns: list[dict[str, str]] = []
    for source, tags in annotations.items():
        for tag in tags:
            target = tag["href"]
            target_tags = annotations.get(target, [])
            reciprocates = any(
                t["href"] == source or urlparse(t["href"]).path == urlparse(source).path
                for t in target_tags
            )
            if not reciprocates and target not in annotations:
                missing_returns.append(
                    {
                        "source": source,
                        "target": target,
                        "hreflang": tag["hreflang"],
                        "issue": "return tag not verified on target (target not in sample)",
                    }
                )

    issues = missing_returns[:20]
    return {
        "status": "available",
        "pages_checked": len(unique),
        "pages_with_hreflang": len(annotations),
        "hreflang_found": True,
        "annotations": {k: v[:10] for k, v in list(annotations.items())[:5]},
        "issues": issues,
        "issue_count": len(issues),
        "note": (
            "Surface check on sample only — full reciprocity matrix needs hreflang-validator."
        ),
    }


def build_internal_linking_snapshot(
    *,
    site_architecture: dict[str, Any] | None,
    pages: list[TechnicalSEOPage] | None,
) -> dict[str, Any]:
    """Orphan/deep pages from Ahrefs inventory + Phase 6 IA orphans."""
    ia = dict(site_architecture or {})
    orphans_ia = ia.get("orphans") or []
    if isinstance(orphans_ia, int):
        orphans_ia = [f"{orphans_ia} orphan candidates from IA crawl"]

    orphan_urls: list[str] = []
    deep_urls: list[str] = []
    low_link_urls: list[str] = []
    for p in pages or []:
        if p.indexable and p.status_code == 200:
            if p.internal_link_count == 0:
                orphan_urls.append(p.url)
            elif p.internal_link_count is not None and p.internal_link_count < 2:
                low_link_urls.append(p.url)
        if (p.depth or 0) >= 4:
            deep_urls.append(p.url)

    suggestions: list[dict[str, Any]] = []
    for url in orphan_urls[:15]:
        suggestions.append(
            {
                "from": "site hub / category parent",
                "to": url,
                "reason": "Orphan indexable page — no incoming internal links detected",
                "priority": "High",
            }
        )
    for url in deep_urls[:10]:
        suggestions.append(
            {
                "from": "higher-level hub",
                "to": url,
                "reason": f"Click depth {next((p.depth for p in pages or [] if p.url == url), '?')} — flatten with hub links",
                "priority": "Medium",
            }
        )

    return {
        "status": "available" if (pages or orphans_ia) else "NOT_AVAILABLE",
        "orphan_urls": orphan_urls[:25],
        "deep_urls": deep_urls[:25],
        "low_inlink_urls": low_link_urls[:25],
        "ia_orphan_notes": orphans_ia if isinstance(orphans_ia, list) else [],
        "link_suggestions": suggestions,
        "orphan_count": len(orphan_urls),
        "deep_count": len(deep_urls),
    }


def duplicate_groups_from_pages(
    pages: list[TechnicalSEOPage],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Duplicate title/meta groups from normalized page inventory."""
    title_groups: dict[str, list[str]] = {}
    meta_groups: dict[str, list[str]] = {}
    for p in pages:
        if p.title:
            title_groups.setdefault(p.title.lower(), []).append(p.url)
        if p.meta_description:
            meta_groups.setdefault(p.meta_description.lower(), []).append(p.url)
    dup_titles = [
        {"value": k, "urls": v, "count": len(v)}
        for k, v in title_groups.items()
        if len(v) >= 2
    ]
    dup_meta = [
        {"value": k[:80], "urls": v, "count": len(v)}
        for k, v in meta_groups.items()
        if len(v) >= 2
    ]
    return dup_titles[:15], dup_meta[:15]


def assign_issue_confidence(issue: dict[str, Any] | TechnicalSEOIssue) -> str:
    """Map issue to high/medium/low confidence for suggestion ledger."""
    if isinstance(issue, TechnicalSEOIssue):
        source = issue.source
        severity = issue.severity
        count = issue.affected_url_count
        ahrefs_id = issue.ahrefs_issue_id
    else:
        source = str(issue.get("source") or "")
        severity = str(issue.get("severity") or "Medium")
        count = int(issue.get("affected_url_count") or 0)
        ahrefs_id = issue.get("ahrefs_issue_id")

    if source == "ahrefs" and ahrefs_id and count > 0:
        return "high"
    if source == "rule_engine" and count > 0 and severity in ("Critical", "High"):
        return "high"
    if count > 0:
        return "medium"
    return "low"


def enrich_issues_with_confidence(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in issues:
        item = dict(row)
        item["confidence"] = assign_issue_confidence(item)
        out.append(item)
    return out


def build_suggestion_items(
    issues: list[dict[str, Any]],
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Structured change items for suggestion ledger (Step 10 style)."""
    items: list[dict[str, Any]] = []
    for issue in issues[:limit]:
        items.append(
            {
                "rule_id": issue.get("rule_id"),
                "url": (issue.get("affected_urls") or [None])[0],
                "change_type": issue.get("category") or "technical_seo",
                "priority": issue.get("priority"),
                "severity": issue.get("severity"),
                "confidence": issue.get("confidence") or assign_issue_confidence(issue),
                "title": issue.get("title"),
                "current_state": f"{issue.get('affected_url_count', 0)} URL(s) affected",
                "proposed_state": issue.get("recommended_action"),
                "status": "pending",
            }
        )
    return items


def cluster_issues_by_category(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        cat = str(issue.get("category") or "Other")
        grouped.setdefault(cat, []).append(issue)
    for cat in grouped:
        grouped[cat].sort(key=lambda i: (-int(i.get("priority") or 0), i.get("title") or ""))
    return grouped


def merge_sections(
    ahrefs_sections: dict[str, Any],
    tech_sections: dict[str, Any],
) -> dict[str, Any]:
    """Merge Ahrefs category sections with lightweight crawl sections."""
    merged = dict(ahrefs_sections)
    for key, payload in tech_sections.items():
        if key not in merged and isinstance(payload, dict):
            merged[key] = payload
            continue
        if not isinstance(payload, dict) or not isinstance(merged.get(key), dict):
            continue
        existing = merged[key]
        findings = list(existing.get("findings") or [])
        for f in payload.get("findings") or []:
            if f not in findings:
                findings.append(f)
        existing["findings"] = findings[:12]
        if existing.get("score") is None and payload.get("score") is not None:
            existing["score"] = payload.get("score")
    return merged


def build_score_comparison(
    current_score: int | None,
    previous_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not previous_summary or current_score is None:
        return None
    prev = previous_summary.get("score") or previous_summary.get("overall_score")
    if prev is None:
        return None
    try:
        prev_i = int(prev)
    except (TypeError, ValueError):
        return None
    delta = current_score - prev_i
    return {
        "previous_score": prev_i,
        "current_score": current_score,
        "delta": delta,
        "direction": "improved" if delta > 0 else "worsened" if delta < 0 else "unchanged",
        "previous_crawl_date": (
            (previous_summary.get("ahrefs_site_audit") or {}).get("crawl_date")
            or previous_summary.get("generated_at")
        ),
    }
