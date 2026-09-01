"""Combined content-audit + content-strategy priority queue.

Implements the shared routing contract: one interleaved queue, refresh beats
new URL for the same topic, site-architecture wins URL conflicts.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def _path(url: str) -> str:
    if not url:
        return ""
    if "://" in url:
        return (urlparse(url).path or "/").rstrip("/") or "/"
    return (url if url.startswith("/") else f"/{url}").rstrip("/") or "/"


def _norm_kw(kw: str | None) -> str:
    return " ".join(str(kw or "").lower().split())


def _effort_to_matrix(effort: str | None, disposition: str | None) -> str:
    """Map audit effort tags onto strategy prioritisation matrix (Step 5)."""
    e = str(effort or "").lower()
    d = str(disposition or "").lower()
    if d in ("retitle",) or e == "low" and d in ("optimise", "optimize"):
        return "Quick win"
    if d == "refresh":
        return "Big bet"  # proven demand — invest to recover
    if d in ("optimise", "optimize"):
        return "Quick win" if e == "low" else "Fill-in"
    if d in ("consolidate",):
        return "Big bet"
    if d in ("noindex", "delete_candidate"):
        return "Fill-in"
    if e == "none":
        return "Avoid"
    return "Fill-in"


def build_combined_priority_queue(
    *,
    content_audit: dict[str, Any] | None,
    content_strategy: dict[str, Any] | None,
    site_architecture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one prioritised queue per routing contract combined-output rules."""
    audit = dict(content_audit or {})
    strategy = dict(content_strategy or {})
    ia = dict(site_architecture or {})

    # Structural cannibalisation / URL decisions from IA win over audit
    ia_owned: set[str] = set()
    for node in ia.get("target_url_tree") or []:
        if isinstance(node, dict):
            p = _path(str(node.get("url") or node.get("path") or ""))
            if p:
                ia_owned.add(p)
    for row in ia.get("redirect_map") or []:
        if isinstance(row, dict):
            for key in ("from", "source", "to", "target"):
                p = _path(str(row.get(key) or ""))
                if p:
                    ia_owned.add(p)

    inventory = [i for i in (audit.get("inventory") or []) if isinstance(i, dict)]
    cannibal = [
        c
        for c in (audit.get("cannibalization") or audit.get("cannibalisation") or [])
        if isinstance(c, dict)
    ]

    queue: list[dict[str, Any]] = []
    seen_topics: set[str] = set()  # keywords / paths claimed by audit items

    def _claim(kw: str | None, path: str | None) -> None:
        if kw:
            seen_topics.add(_norm_kw(kw))
        if path:
            seen_topics.add(_path(path).lower())

    def _claimed(kw: str | None, path: str | None) -> bool:
        nk = _norm_kw(kw)
        np = _path(path or "").lower()
        return (nk and nk in seen_topics) or (np and np in seen_topics)

    # 1. Cannibalisation merges (audit) — skip if IA already owns the URL decision
    for c in cannibal:
        keep = str(c.get("keep") or (c.get("urls") or [None])[0] or "")
        merge = str(c.get("merge_in") or "")
        urls = list(c.get("urls") or [])
        keep_p, merge_p = _path(keep), _path(merge)
        if keep_p in ia_owned or merge_p in ia_owned:
            owner = "site-architecture"
            note = "Structural/IA decision wins — URL change invalidates audit history"
        else:
            owner = "content-audit"
            note = "Performance cannibalisation — merge before new content"
        kw = str(c.get("keyword") or c.get("query") or "")
        queue.append(
            {
                "rank": len(queue) + 1,
                "source": "content-audit",
                "owner": owner,
                "action": "consolidate",
                "disposition": "consolidate",
                "title": f"Merge cannibalisation: {kw or keep_p}",
                "keyword": kw or None,
                "url": keep or keep_p,
                "merge_in": merge or (urls[1] if len(urls) > 1 else None),
                "priority": _effort_to_matrix("High", "consolidate"),
                "effort": "High",
                "note": note,
            }
        )
        _claim(kw, keep)
        _claim(None, merge)

    # 2. Refresh queue
    for item in inventory:
        if str(item.get("disposition") or "").lower() != "refresh":
            continue
        path = str(item.get("path") or item.get("url") or "")
        kw = str(item.get("keyword") or "")
        queue.append(
            {
                "rank": len(queue) + 1,
                "source": "content-audit",
                "owner": "content-audit",
                "action": "refresh",
                "disposition": "refresh",
                "title": item.get("title") or path,
                "keyword": kw or None,
                "url": item.get("url") or path,
                "priority": _effort_to_matrix(item.get("effort"), "refresh"),
                "effort": item.get("effort") or "Medium",
                "reason": item.get("reason"),
                "note": "Proven demand, decaying — prefer over new URL for this topic",
            }
        )
        _claim(kw, path)

    # 3. Quick wins: retitle + striking-distance optimise
    for item in inventory:
        disp = str(item.get("disposition") or "").lower()
        if disp not in ("retitle", "optimise", "optimize"):
            continue
        path = str(item.get("path") or item.get("url") or "")
        kw = str(item.get("keyword") or "")
        if _claimed(kw, path):
            continue
        queue.append(
            {
                "rank": len(queue) + 1,
                "source": "content-audit",
                "owner": "content-audit",
                "action": disp if disp != "optimize" else "optimise",
                "disposition": disp,
                "title": item.get("title") or path,
                "keyword": kw or None,
                "url": item.get("url") or path,
                "priority": _effort_to_matrix(item.get("effort"), disp),
                "effort": item.get("effort") or "Low",
                "reason": item.get("reason"),
                "note": "Audit quick win (retitle / striking distance)",
            }
        )
        _claim(kw, path)

    # 4. Genuine content gaps (strategy) — topics with no page / not claimed by refresh
    for item in strategy.get("priority_queue") or strategy.get("priority_pages") or []:
        if not isinstance(item, dict):
            continue
        kw = str(item.get("keyword") or "")
        path = str(item.get("suggested_url") or item.get("url") or item.get("path") or "")
        action = str(item.get("action") or "create").lower()
        # Same topic as refresh → refresh wins; drop new-content duplicate
        if _claimed(kw, path) or action in ("refresh", "consolidate"):
            continue
        queue.append(
            {
                "rank": len(queue) + 1,
                "source": "content-strategy",
                "owner": "content-strategy",
                "action": "create",
                "disposition": None,
                "title": item.get("title") or kw or path,
                "keyword": kw or None,
                "url": path or None,
                "intent": item.get("intent"),
                "priority": item.get("priority") or "Fill-in",
                "effort": "Medium",
                "business_fit": item.get("business_fit"),
                "est_words": item.get("est_words"),
                "image_suggestions": item.get("image_suggestions") or [],
                "note": "Genuine gap — no existing page owned for this topic",
            }
        )
        _claim(kw, path)

    for gap in strategy.get("content_gaps") or []:
        if not isinstance(gap, dict):
            continue
        kw = str(gap.get("keyword") or "")
        if not kw or _claimed(kw, None):
            continue
        queue.append(
            {
                "rank": len(queue) + 1,
                "source": "content-strategy",
                "owner": "content-strategy",
                "action": "create",
                "title": gap.get("title") or f"New: {kw}",
                "keyword": kw,
                "url": gap.get("suggested_url"),
                "priority": "Fill-in",
                "effort": "Medium",
                "note": gap.get("rationale") or "Content gap vs competitors",
            }
        )
        _claim(kw, None)

    # 5. Consolidations (non-cannibal) + index cleanup
    for item in inventory:
        disp = str(item.get("disposition") or "").lower()
        if disp not in ("consolidate", "noindex", "delete_candidate"):
            continue
        path = str(item.get("path") or item.get("url") or "")
        kw = str(item.get("keyword") or "")
        # Cannibal consolidates already added in step 1
        if disp == "consolidate" and _claimed(kw, path):
            continue
        if _path(path) in ia_owned and disp == "consolidate":
            continue
        if disp == "consolidate" and any(
            str(q.get("action")) == "consolidate" and _path(str(q.get("url") or "")) == _path(path)
            for q in queue
        ):
            continue
        queue.append(
            {
                "rank": len(queue) + 1,
                "source": "content-audit",
                "owner": "site-architecture" if _path(path) in ia_owned else "content-audit",
                "action": disp,
                "disposition": disp,
                "title": item.get("title") or path,
                "keyword": kw or None,
                "url": item.get("url") or path,
                "priority": _effort_to_matrix(item.get("effort"), disp),
                "effort": item.get("effort") or "Low",
                "reason": item.get("reason"),
                "note": (
                    "Review only — never auto-delete"
                    if disp == "delete_candidate"
                    else "Index cleanup / consolidate"
                ),
            }
        )
        _claim(kw, path)

    # Re-number ranks after filtering
    for i, row in enumerate(queue, start=1):
        row["rank"] = i

    both = bool(audit.get("inventory") or audit.get("summary_counts")) and bool(
        strategy.get("priority_queue") or strategy.get("content_gaps") or strategy.get("pillars")
    )
    return {
        "combined_priority_queue": queue,
        "queue_length": len(queue),
        "both_skills_present": both,
        "routing_rule": (
            "cannibalisation → refresh → audit quick wins → strategy gaps → "
            "consolidate/noindex/delete review"
        ),
        "vocabulary": {
            "intent_table_owner": "content-strategy Step 4",
            "prioritisation_matrix_owner": "content-strategy Step 5",
            "structural_cannibalisation_owner": "site-architecture",
            "performance_cannibalisation_owner": "content-audit",
            "url_conflict_winner": "site-architecture",
        },
        "note": (
            "ONE prioritised queue for the engagement. "
            "Never place new-content above a refresh for the same topic."
            if both
            else "Partial queue — run the sibling skill to complete the combined plan."
        ),
    }
