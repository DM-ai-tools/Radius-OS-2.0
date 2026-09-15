"""Fallback IA pack when Phase 6 Site Architecture is not yet approved."""

from __future__ import annotations

import re
from typing import Any


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "page"


def _path_n(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        from urllib.parse import urlparse

        path = urlparse(raw).path or "/"
    else:
        path = raw if raw.startswith("/") else f"/{raw}"
    path = path.lower()
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def build_phase7_ia_fallback(
    *,
    primary_url: str,
    website: dict[str, Any] | None = None,
    demand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Minimal URL tree from Phase 3 website + Phase 5 clusters for Phase 7."""
    website = dict(website or {})
    demand = dict(demand or {})
    tree: list[dict[str, Any]] = []
    seen: set[str] = set()

    home = _path_n(primary_url) or "/"
    if home not in seen:
        seen.add(home)
        tree.append(
            {
                "url": home,
                "path": home if home.endswith("/") else f"{home}/",
                "page_type": "home",
                "depth": 0,
                "parent": None,
                "from_fallback_ia": True,
            }
        )

    clusters = list(
        (demand.get("cluster_report") or {}).get("clusters")
        or demand.get("clusters")
        or []
    )
    for cluster in clusters[:16]:
        if not isinstance(cluster, dict):
            continue
        kw = cluster.get("primary_keyword") or cluster.get("name")
        if not kw:
            continue
        path = f"/blog/{_slug(str(kw))}/"
        loc = _path_n(path)
        if not loc or loc in seen:
            continue
        seen.add(loc)
        tree.append(
            {
                "url": loc,
                "path": path,
                "page_type": "article",
                "type": "article",
                "keyword": kw,
                "primary_keyword": kw,
                "cluster": cluster.get("name"),
                "depth": 2,
                "parent": "/",
                "from_fallback_ia": True,
            }
        )

    current_state: dict[str, Any] = {"urls_crawled": 0}
    for key in ("crawl_technical", "seo_audit", "broken_links"):
        tab = website.get(key)
        if isinstance(tab, dict) and tab.get("status") not in (None, "not_run_this_session", "skipped"):
            current_state[key] = tab.get("status")
            if tab.get("summary"):
                current_state.setdefault("notes", []).append(f"{key}: {str(tab['summary'])[:120]}")

    bl = website.get("broken_links")
    if isinstance(bl, dict):
        count = bl.get("broken_count")
        if count is not None:
            current_state["broken_link_count"] = count

    return {
        "target_url_tree": tree,
        "current_state": current_state,
        "redirect_map": [],
        "source": "phase7_website_demand_fallback",
        "note": (
            "Fallback IA from Website Situation + Search Demand clusters. "
            "Run and approve Site Architecture for redirect map and depth remediation."
        ),
        "cluster_count": len(clusters),
        "fallback_url_nodes": len(tree),
    }
