"""Phase 8 — Existing content audit service.

Applies the existing-content-audit disposition framework. With GSC two-period
data this is a full decay/cannibalisation audit; without it, the run is marked
qualitative and decay cannot be claimed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from app.agents.prompts import load_skill, load_skill_file


PHASE8_SKILL_DIR = "existing-content-audit"

# Ordered disposition labels (skill contract)
DISPOSITIONS = (
    "consolidate",
    "keep",
    "refresh",
    "retitle",
    "optimise",
    "delete_candidate",
    "noindex",
)


def _path(url: str) -> str:
    if not url:
        return "/"
    if "://" in url:
        return urlparse(url).path or "/"
    return url if url.startswith("/") else f"/{url}"


def _pages_from_website(website: dict[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    samples = website.get("sample_urls") or []
    for u in samples:
        if isinstance(u, str) and u.strip():
            pages.append({"url": u, "title": "", "source": "website_sample"})
        elif isinstance(u, dict) and (u.get("url") or u.get("path")):
            pages.append(
                {
                    "url": u.get("url") or u.get("path"),
                    "title": u.get("title") or "",
                    "words": u.get("words") or u.get("word_count"),
                    "source": "website_sample",
                }
            )
    crawl = website.get("crawl_technical") or website.get("crawl") or {}
    if isinstance(crawl, dict):
        summary = crawl.get("summary") if isinstance(crawl.get("summary"), dict) else crawl
        for key in ("discovered_urls", "status_samples", "pages"):
            for row in summary.get(key) or []:
                if isinstance(row, dict) and (row.get("url") or row.get("path")):
                    pages.append(
                        {
                            "url": row.get("url") or row.get("path"),
                            "title": row.get("title") or "",
                            "status": row.get("status"),
                            "words": row.get("words") or row.get("word_count"),
                            "source": "crawl",
                        }
                    )
                elif isinstance(row, str):
                    pages.append({"url": row, "title": "", "source": "crawl"})
    for key, val in website.items():
        if not isinstance(val, dict):
            continue
        summary = val.get("summary") if isinstance(val.get("summary"), dict) else None
        if not summary:
            continue
        for row in summary.get("pages") or summary.get("sample_urls") or []:
            if isinstance(row, dict) and (row.get("url") or row.get("path")):
                pages.append(
                    {
                        "url": row.get("url") or row.get("path"),
                        "title": row.get("title") or "",
                        "words": row.get("words") or row.get("word_count"),
                        "source": key,
                    }
                )
            elif isinstance(row, str):
                pages.append({"url": row, "title": "", "source": key})
    return pages


def _norm_kw(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _theme_lookup(
    search_demand: dict[str, Any], seo_strategy: dict[str, Any]
) -> dict[str, str]:
    """Map keyword -> theme name, grounded in Phase 5 clusters / Phase 6a pillars.

    Architecture v1.9, Step 08: "Audit and crawl output is clustered by theme,
    not just listed by URL" — reuses the same cluster/pillar structure Phase 5
    and Phase 6a already established rather than inventing a new grouping.
    """
    lookup: dict[str, str] = {}

    def _index(rows: Any, *, name_key: str = "name", parent_theme: str | None = None) -> None:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            theme = parent_theme or str(
                row.get(name_key) or row.get("pillar") or row.get("primary_keyword") or ""
            ).strip()
            if not theme:
                continue
            head = _norm_kw(row.get("primary_keyword") or row.get("keyword"))
            if head:
                lookup.setdefault(head, theme)
            for kw_row in row.get("keywords") or []:
                term = _norm_kw(kw_row.get("keyword") if isinstance(kw_row, dict) else kw_row)
                if term:
                    lookup.setdefault(term, theme)
            for child in row.get("clusters") or []:
                if isinstance(child, dict):
                    # A pillar's child clusters all roll up to the pillar's theme —
                    # that's the grouping Phase 6a chose (Option B), not each
                    # child's own cluster name.
                    _index([child], parent_theme=theme)

    # Phase 6a pillars take precedence (Option B — Phase 6a groups Phase 5's
    # clusters; see url_mapping.build_final_url_map's pillar_structure param).
    _index(seo_strategy.get("core_topics"), name_key="pillar")
    cluster_report = search_demand.get("cluster_report")
    if isinstance(cluster_report, dict):
        _index(cluster_report.get("clusters"))
    _index(search_demand.get("clusters"))
    return lookup


def _gsc_metrics_map(
    seo_strategy: dict[str, Any], search_demand: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Optional page metrics if upstream packs stashed GSC-like rows."""
    out: dict[str, dict[str, Any]] = {}
    for blob in (
        seo_strategy.get("gsc_pages"),
        seo_strategy.get("page_performance"),
        search_demand.get("page_performance"),
        search_demand.get("gsc_pages"),
    ):
        if not isinstance(blob, list):
            continue
        for row in blob:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or row.get("page") or "")
            if not url:
                continue
            out[_path(url)] = {
                "clicks": float(row.get("clicks") or 0),
                "impressions": float(row.get("impressions") or 0),
                "position": float(row.get("position") or 0),
                "ctr": float(row.get("ctr") or 0),
                "prior_clicks": float(row.get("prior_clicks") or 0),
            }
    return out


def _disposition_for(
    path: str,
    *,
    in_ia: bool,
    in_priority: bool,
    gap_keyword: str | None,
    thin_hint: bool,
    words: int | None,
    metrics: dict[str, Any] | None,
    qualitative: bool,
) -> tuple[str, str, str]:
    """Return (disposition, effort, reason). Ordered rules when metrics exist."""
    m = metrics or {}
    clicks = float(m.get("clicks") or 0)
    impr = float(m.get("impressions") or 0)
    pos = float(m.get("position") or 0)
    ctr = float(m.get("ctr") or 0)
    prior = float(m.get("prior_clicks") or 0)
    change = ((clicks - prior) / prior) if prior > 0 else None
    w = int(words or 0)

    if not qualitative and m:
        if clicks >= 50 and (change is None or change > -0.30):
            return "keep", "None", "≥50 clicks, no material decline"
        if prior >= 10 and change is not None and change <= -0.30:
            return (
                "refresh",
                "Medium",
                f"Decayed {change:.0%} ({prior:.0f} → {clicks:.0f} clicks) — highest ROI",
            )
        if pos and pos <= 5 and impr >= 100 and (ctr < 0.02 or (impr and clicks / impr < 0.02)):
            return "retitle", "Low", "Strong position, weak CTR — snippet problem"
        if pos and 8 <= pos <= 20 and impr >= 50:
            return "optimise", "Medium", f"Striking distance (pos {pos:.1f})"
        if impr >= 100 and clicks == 0:
            return "optimise", "Medium", "≥100 impressions, zero clicks — intent mismatch?"
        if clicks == 0 and impr == 0 and w and w < 300:
            return "delete_candidate", "Low", "Zero demand + thin — review queue only"
        if clicks == 0 and impr == 0 and (not w or w >= 300):
            return "noindex", "Low", "Zero demand but substantial — reversible"
        if impr > 0 and clicks < 5:
            return "consolidate", "High", "Some impressions, <5 clicks — too weak alone"

    # Qualitative heuristics (no GSC) — never claim decay
    low = path.lower()
    if thin_hint and not in_priority:
        return (
            "refresh" if in_ia else "consolidate",
            "Medium",
            "Thin/unclear ownership (qualitative)",
        )
    if any(x in low for x in ("/old-", "/legacy", "/archive")):
        return "noindex", "Low", "Archive/legacy path — prefer noindex over delete"
    if gap_keyword and not in_ia:
        return "refresh", "Medium", f"Strategy gap overlap: {gap_keyword}"
    if in_priority and in_ia:
        return "refresh", "Medium", "On priority queue + IA tree"
    if in_ia:
        return "keep", "None", "Mapped in approved IA"
    if in_priority:
        return "optimise", "Medium", "Priority keyword without clear IA home"
    if path.count("/") >= 4 and not in_ia:
        return "consolidate", "High", "Deep orphan-like path (qualitative)"
    return "keep", "None", "No stronger rule matched"


def _group_by_theme(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the flat inventory into theme concepts (Architecture v1.9, Step 08).

    Page-by-page detail stays in `inventory`; this keeps a large site
    navigable without truncating it — an 80-page site or a 6,000-page
    competitor both stay readable grouped by concept.
    """
    groups: dict[str, dict[str, Any]] = {}
    for item in inventory:
        theme = str(item.get("theme") or "Uncategorized")
        bucket = groups.setdefault(
            theme,
            {"theme": theme, "url_count": 0, "dispositions": {d: 0 for d in DISPOSITIONS}, "urls": []},
        )
        bucket["url_count"] += 1
        disp = str(item.get("disposition") or "keep")
        bucket["dispositions"][disp] = bucket["dispositions"].get(disp, 0) + 1
        if len(bucket["urls"]) < 10:
            bucket["urls"].append(
                {"path": item.get("path"), "title": item.get("title"), "disposition": disp}
            )
    ordered = sorted(groups.values(), key=lambda g: g["url_count"], reverse=True)
    for bucket in ordered:
        if bucket["theme"] != "Uncategorized" and bucket["url_count"] > len(bucket["urls"]):
            bucket["urls_truncated"] = True
    return ordered


def _specialist_handoffs() -> list[dict[str, str]]:
    return [
        {"concern": "Rewrite a REFRESH page", "route_to": "content-brief → create-content"},
        {"concern": "Titles/meta on RETITLE", "route_to": "on-page-seo"},
        {"concern": "Redirect map for CONSOLIDATE", "route_to": "site-architecture"},
        {"concern": "Structural cannibalisation / taxonomy", "route_to": "site-architecture"},
        {"concern": "noindex + crawl impact", "route_to": "technical-seo"},
        {"concern": "Internal links to refreshed URL", "route_to": "internal-linking"},
        {
            "concern": "Sitewide decay may be technical/rendering",
            "route_to": "technical-seo / rendering-audit",
        },
    ]


async def run_content_audit_plan(
    *,
    client_name: str,
    primary_url: str,
    website: dict[str, Any] | None = None,
    site_architecture: dict[str, Any] | None = None,
    seo_strategy: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
    technical_seo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = load_skill("content_audit")
    website = dict(website or {})
    ia = dict(site_architecture or {})
    strategy = dict(seo_strategy or {})
    demand = dict(search_demand or {})
    tech = dict(technical_seo or {})

    metrics_by_path = _gsc_metrics_map(strategy, demand)
    qualitative = not bool(metrics_by_path)
    theme_by_kw = _theme_lookup(demand, strategy)

    ia_paths: dict[str, dict[str, Any]] = {}
    for node in ia.get("target_url_tree") or []:
        if not isinstance(node, dict):
            continue
        p = _path(str(node.get("url") or node.get("path") or ""))
        ia_paths[p] = node

    priority_kw: dict[str, dict[str, Any]] = {}
    for row in strategy.get("priority_queue") or strategy.get("priority_pages") or []:
        if not isinstance(row, dict):
            continue
        kw = str(row.get("keyword") or row.get("title") or "").lower().strip()
        if kw:
            priority_kw[kw] = row
        sug = row.get("suggested_url")
        if sug:
            priority_kw[_path(str(sug))] = row

    gaps: list[str] = []
    for g in strategy.get("content_gaps") or []:
        if isinstance(g, dict) and g.get("keyword"):
            gaps.append(str(g["keyword"]).lower())

    inventory_src = _pages_from_website(website)
    for p, node in ia_paths.items():
        inventory_src.append(
            {
                "url": node.get("url") or p,
                "title": node.get("title") or node.get("keyword") or p,
                "keyword": node.get("keyword") or node.get("primary_keyword"),
                "source": "ia_tree",
            }
        )
    if not inventory_src:
        # Fail closed — do not invent /about /blog /services stubs
        return {
            "client_name": client_name,
            "primary_url": primary_url,
            "qualitative": True,
            "data_mode": "blocked",
            "blocked": True,
            "reason": (
                "No crawl/sample URLs or IA tree to audit. "
                "Re-run Website Situation or provide a crawl before content audit."
            ),
            "inventory": [],
            "cannibalization": [],
            "summary_counts": {d: 0 for d in DISPOSITIONS},
            "could_not_assess": [
                "No page inventory — crawl required",
                "Decay (no GSC)",
                "Cannibalisation (no page+query GSC)",
            ],
            "executive_summary": (
                f"Content Audit blocked for {client_name}: empty inventory. "
                "Do not invent pages to prune."
            ),
            "source": "content_audit",
            "note": "Blocked — empty inventory (no stub pages).",
        }

    seen: set[str] = set()
    inventory: list[dict[str, Any]] = []
    by_kw: dict[str, list[str]] = defaultdict(list)

    for row in inventory_src:
        url = str(row.get("url") or "")
        path = _path(url)
        if path in seen:
            continue
        seen.add(path)
        node = ia_paths.get(path) or {}
        kw = str(
            row.get("keyword") or node.get("keyword") or node.get("primary_keyword") or ""
        ).strip()
        kw_l = kw.lower()
        in_ia = path in ia_paths
        in_priority = path in priority_kw or kw_l in priority_kw
        gap_hit = next((g for g in gaps if g and (g in path.lower() or g in kw_l)), None)
        title = str(row.get("title") or node.get("title") or path)
        words = row.get("words")
        try:
            words_i = int(words) if words is not None else None
        except (TypeError, ValueError):
            words_i = None
        thin = (words_i is not None and words_i < 300) or len(title) < 8 or path in ("/", "")
        metrics = metrics_by_path.get(path)
        disposition, effort, reason = _disposition_for(
            path,
            in_ia=in_ia,
            in_priority=bool(in_priority),
            gap_keyword=gap_hit,
            thin_hint=thin,
            words=words_i,
            metrics=metrics,
            qualitative=qualitative,
        )
        theme = theme_by_kw.get(_norm_kw(kw)) or str(node.get("cluster") or "").strip() or "Uncategorized"
        item = {
            "url": url if "://" in url else path,
            "path": path,
            "title": title,
            "type": node.get("type") or node.get("page_type") or row.get("source") or "page",
            "keyword": kw or None,
            "theme": theme,
            "disposition": disposition,
            "effort": effort,
            "reason": reason,
            "in_ia": in_ia,
            "priority": bool(in_priority),
            "words": words_i,
            "metrics": metrics,
        }
        inventory.append(item)
        if kw_l:
            by_kw[kw_l].append(path)

    cannibalization: list[dict[str, Any]] = []
    cannibal_paths: set[str] = set()
    for kw, urls in by_kw.items():
        if len(urls) >= 2:
            cannibalization.append(
                {
                    "keyword": kw,
                    "query": kw,
                    "keep": urls[0],
                    "merge_in": urls[1],
                    "urls": urls[:6],
                    "action": "consolidate — fix ownership before refresh",
                    "impressions_at_risk": None,
                }
            )
            cannibal_paths.update(urls[1:])

    for item in inventory:
        if item["path"] in cannibal_paths and item["disposition"] != "consolidate":
            item["disposition"] = "consolidate"
            item["effort"] = "High"
            item["reason"] = "Competing with another URL on shared queries"

    counts = {d: 0 for d in DISPOSITIONS}
    for item in inventory:
        d = str(item.get("disposition") or "keep")
        counts[d] = counts.get(d, 0) + 1

    themes = _group_by_theme(inventory)

    refresh_queue = [i for i in inventory if i.get("disposition") == "refresh"]
    quick_wins = [i for i in inventory if i.get("disposition") in ("retitle", "optimise")]
    review_queue = [
        i for i in inventory if i.get("disposition") in ("delete_candidate", "noindex")
    ]

    could_not_assess = [
        x
        for x in [
            "Decay & prior-period comparison (no GSC two-period data — qualitative only)"
            if qualitative
            else None,
            "Query-level cannibalisation impressions (need page+query GSC export)"
            if qualitative
            else None,
            "Backlinks (not provided — never treat DELETE_CANDIDATE as auto-delete)",
            "Seasonality (confirm YoY if niche is seasonal)",
            "Word counts / thin content (crawl words missing)"
            if not any(i.get("words") for i in inventory)
            else None,
        ]
        if x
    ]
    if tech.get("js_rendering_risk"):
        could_not_assess.insert(
            0,
            "Sitewide decay may be rendering-related — check rendering-audit before editorial cull",
        )

    data_label = "GSC + crawl" if not qualitative else "qualitative (crawl/IA/strategy — no GSC)"
    executive_summary = (
        f"Content Audit: {client_name} — {len(inventory)} URLs across {len(themes)} theme(s), "
        f"data={data_label}. "
        "Work order: cannibalisation → refresh → retitle → optimise → consolidate → "
        "noindex → delete review. "
        + (
            "GSC prior period missing — decay cannot be assessed."
            if qualitative
            else "Performance dispositions applied from metrics."
        )
    )

    result = {
        "client_name": client_name,
        "primary_url": primary_url,
        "period": "current vs prior" if not qualitative else "n/a (qualitative)",
        "data_mode": "gsc" if not qualitative else "qualitative",
        "qualitative": qualitative,
        "executive_summary": executive_summary,
        "inventory": inventory,
        "themes": themes,
        "cannibalization": cannibalization[:15],
        "cannibalisation": cannibalization[:15],
        "refresh_queue": refresh_queue[:20],
        "quick_wins": quick_wins[:20],
        "review_queue": review_queue[:20],
        "summary_counts": counts,
        "gap_keywords_unmatched": [
            g
            for g in gaps[:12]
            if not any(
                g in str(i.get("keyword") or "").lower() or g in str(i.get("path") or "")
                for i in inventory
            )
        ],
        "could_not_assess": could_not_assess,
        "handoffs": _specialist_handoffs(),
        "work_order": [
            "cannibalisation",
            "refresh",
            "retitle",
            "optimise",
            "consolidate",
            "noindex",
            "delete_review",
        ],
        "thresholds": {
            "decay": "-30%",
            "min_prior_clicks": 10,
            "thin_words": 300,
            "striking_distance": "8–20",
            "cannibal_impressions": 50,
        },
        "skills_used": [PHASE8_SKILL_DIR],
        "skills_loaded": {
            PHASE8_SKILL_DIR: len(load_skill_file(PHASE8_SKILL_DIR) or ""),
            "content_audit": len(load_skill("content_audit") or ""),
        },
        "upstream": {
            "content_strategy": bool(strategy),
            "site_architecture": bool(ia),
            "search_demand": bool(demand),
        },
        "script_hint": (
            "python scripts/content_audit.py audit "
            "--current ... --prior ... --queries ... --crawl ..."
        ),
        "source": "content_audit",
        "note": (
            "Full content audit in chat — essentials on approve. "
            "DELETE_CANDIDATE is review-only; default improve/consolidate over delete."
        ),
        "intent_table_owner": "content-strategy Step 4",
        "prioritisation_matrix_owner": "content-strategy Step 5",
        "sibling_skill": "content-strategy",
    }

    from app.services.content_queue import build_combined_priority_queue

    result.update(
        build_combined_priority_queue(
            content_audit=result,
            content_strategy=strategy,
            site_architecture=ia,
        )
    )
    return result
