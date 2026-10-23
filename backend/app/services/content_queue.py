"""One content-plan list for strategy, audit, and architecture work.

Strategy rows are built first. Audit and architecture decisions are merged
into those same rows. A conflicting URL keeps the architecture decision.
A refresh for a topic replaces a new page for that topic. The result is one
list, not a second queue.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_WORK_LABELS = {
    "create": "Create page",
    "optimize": "Optimize existing page",
    "optimise": "Optimize existing page",
    "refresh": "Optimize existing page",
    "retitle": "Optimize existing page",
    "consolidate": "Consolidate duplicates",
    "redirect": "Handle redirect",
    "noindex": "Noindex page",
    "delete_candidate": "Review for removal",
}


def _path(url: str) -> str:
    if not url:
        return ""
    if "://" in url:
        return (urlparse(url).path or "/").rstrip("/") or "/"
    return (url if url.startswith("/") else f"/{url}").rstrip("/") or "/"


def _norm_kw(kw: str | None) -> str:
    return " ".join(str(kw or "").lower().split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm_kw(text)).strip("-")[:48]


def work_items(strategy: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The one work list. Older payloads are read until they are rebuilt."""
    payload = strategy or {}
    for key in ("content_plan", "priority_queue", "combined_priority_queue", "priority_pages"):
        raw = payload.get(key)
        if isinstance(raw, list) and any(isinstance(row, dict) for row in raw):
            return [row for row in raw if isinstance(row, dict)]
    return []


def strategy_origin_items(strategy: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pre-merge strategy rows, derived by source when the list has been merged."""
    items = work_items(strategy)
    if any(row.get("source") for row in items):
        return [row for row in items if row.get("source") == "content-strategy"]
    return items


def _effort_to_matrix(effort: str | None, disposition: str | None) -> str:
    """Map audit effort tags onto strategy prioritisation matrix (Step 5)."""
    e = str(effort or "").lower()
    d = str(disposition or "").lower()
    if d in ("retitle",) or e == "low" and d in ("optimise", "optimize"):
        return "Quick win"
    if d == "refresh":
        return "Big bet"
    if d in ("optimise", "optimize"):
        return "Quick win" if e == "low" else "Fill-in"
    if d in ("consolidate",):
        return "Big bet"
    if d in ("noindex", "delete_candidate"):
        return "Fill-in"
    if e == "none":
        return "Avoid"
    return "Fill-in"


def _work_label(action: str, content_type: str | None, explicit: str | None = None) -> str:
    if explicit:
        return str(explicit)
    kind = str(content_type or "").strip()
    if action == "create" and kind:
        pretty = kind.replace(" page", "").strip()
        if pretty:
            return f"Create {pretty} page"
    return _WORK_LABELS.get(action, action.replace("_", " ").title() or "Create page")


def _fill(item: dict[str, Any], **fields: Any) -> None:
    for key, value in fields.items():
        if value in (None, "", [], {}):
            continue
        if item.get(key) in (None, "", [], {}):
            item[key] = value


def _as_work_item(
    row: dict[str, Any],
    *,
    source: str,
    owner: str,
    action: str,
) -> dict[str, Any]:
    item = dict(row)
    action = action or "create"
    item["action"] = action
    item["source"] = item.get("source") or source
    item["owner"] = item.get("owner") or owner
    keyword = item.get("keyword") or item.get("primary_keyword")
    item["keyword"] = keyword
    item["primary_keyword"] = item.get("primary_keyword") or keyword
    item["title"] = item.get("title") or keyword or item.get("url") or item.get("path")
    item["work_label"] = _work_label(action, item.get("content_type"), item.get("work_label"))
    suggested = item.get("suggested_url") or item.get("url") or item.get("path")
    item["suggested_url"] = suggested
    if action in ("optimize", "optimise", "refresh", "retitle", "consolidate", "redirect", "noindex", "delete_candidate"):
        _fill(item, existing_url=item.get("existing_url") or item.get("url") or item.get("path") or suggested)
    return item


def _assign_identity(items: list[dict[str, Any]]) -> None:
    seen: dict[str, int] = {}
    for index, item in enumerate(items, start=1):
        item["rank"] = index
        keyword = _norm_kw(item.get("keyword") or item.get("title"))
        path = _path(str(item.get("existing_url") or item.get("suggested_url") or ""))
        stem = _slug(keyword or path or item.get("title") or "item") or "item"
        key = f"{item.get('source')}|{stem}"
        seen[key] = seen.get(key, 0) + 1
        item["id"] = f"work-{stem}" if seen[key] == 1 else f"work-{stem}-{seen[key]}"


def _ia_owned(ia: dict[str, Any]) -> set[str]:
    owned: set[str] = set()
    for node in ia.get("target_url_tree") or []:
        if isinstance(node, dict):
            path = _path(str(node.get("url") or node.get("path") or ""))
            if path:
                owned.add(path.lower())
    for row in ia.get("redirect_map") or []:
        if isinstance(row, dict):
            for key in ("from", "source", "to", "target"):
                path = _path(str(row.get(key) or ""))
                if path:
                    owned.add(path.lower())
    return owned


def build_content_plan(
    *,
    content_audit: dict[str, Any] | None,
    content_strategy: dict[str, Any] | None,
    site_architecture: dict[str, Any] | None = None,
    extra_gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge audit and architecture decisions into the strategy work list."""
    audit = dict(content_audit or {})
    strategy = dict(content_strategy or {})
    ia = dict(site_architecture or {})
    owned = _ia_owned(ia)

    items: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    by_kw: dict[str, dict[str, Any]] = {}
    by_path: dict[str, dict[str, Any]] = {}
    taken_over: list[dict[str, Any]] = []

    def remember(item: dict[str, Any]) -> None:
        keyword = _norm_kw(item.get("keyword"))
        if keyword:
            by_kw.setdefault(keyword, item)
        for key in ("suggested_url", "existing_url", "url", "path"):
            path = _path(str(item.get(key) or "")).lower()
            if path and path != "/":
                by_path.setdefault(path, item)

    def find(keyword: str | None, path: str | None) -> dict[str, Any] | None:
        nk = _norm_kw(keyword)
        np = _path(path or "").lower()
        if nk and nk in by_kw:
            return by_kw[nk]
        if np and np != "/" and np in by_path:
            return by_path[np]
        return None

    def overlay(
        item: dict[str, Any],
        *,
        owner: str,
        action: str,
        **fields: Any,
    ) -> None:
        previous = str(item.get("action") or "")
        architecture_locked = item.get("owner") == "site-architecture"
        if owner == "site-architecture" or not architecture_locked:
            if owner == "site-architecture" or item.get("owner") == item.get("source"):
                item["owner"] = owner
            if not architecture_locked:
                item["action"] = action
                item["work_label"] = fields.pop("work_label", None) or _work_label(
                    action, item.get("content_type"), None if owner != item.get("source") else item.get("work_label")
                )
            if previous in ("create", "") and action != "create" and item.get("source") == "content-strategy":
                taken_over.append(
                    {
                        "keyword": item.get("keyword"),
                        "suggested_url": item.get("suggested_url"),
                        "action": action,
                    }
                )
        _fill(item, **fields)
        if action in ("optimize", "optimise", "refresh", "retitle", "consolidate", "redirect"):
            _fill(item, existing_url=fields.get("existing_url") or fields.get("url"))
        remember(item)

    def add(row: dict[str, Any]) -> None:
        if find(row.get("keyword"), row.get("suggested_url") or row.get("existing_url") or row.get("url")):
            return
        items.append(row)
        remember(row)

    raw_rows = (
        strategy.get("priority_queue")
        or strategy.get("content_plan")
        or strategy.get("priority_pages")
        or []
    )
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("priority") or "") == "Avoid":
            exclusions.append(
                {
                    "keyword": raw.get("keyword") or raw.get("title"),
                    "topic": raw.get("title") or raw.get("keyword"),
                    "reason": raw.get("rationale") or raw.get("reason") or raw.get("note") or "Marked avoid",
                }
            )
            continue
        action = str(raw.get("action") or "create").lower()
        add(
            _as_work_item(
                raw,
                source=str(raw.get("source") or "content-strategy"),
                owner=str(raw.get("owner") or "content-strategy"),
                action=action,
            )
        )

    inventory = [row for row in (audit.get("inventory") or []) if isinstance(row, dict)]
    cannibal = [
        row
        for row in (audit.get("cannibalization") or audit.get("cannibalisation") or [])
        if isinstance(row, dict)
    ]

    for row in cannibal:
        keep = str(row.get("keep") or (row.get("urls") or [None])[0] or "")
        merge = str(row.get("merge_in") or "")
        urls = list(row.get("urls") or [])
        keep_path, merge_path = _path(keep).lower(), _path(merge).lower()
        architecture = keep_path in owned or merge_path in owned
        owner = "site-architecture" if architecture else "content-audit"
        keyword = str(row.get("keyword") or row.get("query") or "")
        note = (
            "Structural decision wins — URL change invalidates audit history"
            if architecture
            else "Performance cannibalisation — merge before new content"
        )
        existing = find(keyword, keep or merge)
        fields = {
            "existing_url": keep or keep_path,
            "merge_in": merge or (urls[1] if len(urls) > 1 else None),
            "disposition": "consolidate",
            "effort": "High",
            "priority": _effort_to_matrix("High", "consolidate"),
            "note": note,
            "url": keep or keep_path,
        }
        if existing:
            overlay(existing, owner=owner, action="redirect" if architecture else "consolidate", **fields)
            continue
        add(
            _as_work_item(
                {
                    "title": f"Merge cannibalisation: {keyword or keep_path}",
                    "keyword": keyword or None,
                    "priority": fields["priority"],
                    **fields,
                },
                source="site-architecture" if architecture else "content-audit",
                owner=owner,
                action="redirect" if architecture else "consolidate",
            )
        )

    def _audit_row(item: dict[str, Any], disposition: str, *, default_effort: str, note: str) -> None:
        path = str(item.get("path") or item.get("url") or "")
        keyword = str(item.get("keyword") or "")
        action = "optimize" if disposition in ("refresh", "retitle", "optimise", "optimize") else disposition
        if disposition == "consolidate" and _path(path).lower() in owned:
            action = "redirect"
        owner = "site-architecture" if _path(path).lower() in owned else "content-audit"
        source = "site-architecture" if owner == "site-architecture" and action == "redirect" else "content-audit"
        fields = {
            "title": item.get("title") or path,
            "keyword": keyword or None,
            "url": item.get("url") or path,
            "existing_url": item.get("url") or path,
            "suggested_url": item.get("url") or path,
            "disposition": disposition,
            "effort": item.get("effort") or default_effort,
            "priority": _effort_to_matrix(item.get("effort"), disposition),
            "reason": item.get("reason"),
            "note": note,
            "content_type": item.get("content_type"),
            "volume": item.get("volume"),
            "difficulty": item.get("difficulty"),
            "intent": item.get("intent"),
            "service": item.get("service"),
        }
        existing = find(keyword, path)
        if existing:
            overlay(existing, owner=owner, action=action, **fields)
            return
        if disposition == "consolidate" and any(
            str(current.get("action")) == "consolidate" and _path(str(current.get("existing_url") or "")) == _path(path)
            for current in items
        ):
            return
        add(_as_work_item(fields, source=source, owner=owner, action=action))

    for item in inventory:
        if str(item.get("disposition") or "").lower() == "refresh":
            _audit_row(item, "refresh", default_effort="Medium", note="Proven demand, decaying — prefer over new URL for this topic")

    for item in inventory:
        disposition = str(item.get("disposition") or "").lower()
        if disposition in ("retitle", "optimise", "optimize"):
            _audit_row(item, disposition, default_effort="Low", note="Audit quick win (retitle / striking distance)")

    for gap in list(strategy.get("content_gaps") or []) + list(extra_gaps or []):
        if not isinstance(gap, dict):
            continue
        keyword = str(gap.get("keyword") or "")
        if not keyword or find(keyword, gap.get("suggested_url")):
            continue
        add(
            _as_work_item(
                {
                    "title": gap.get("title") or f"New: {keyword}",
                    "keyword": keyword,
                    "suggested_url": gap.get("suggested_url"),
                    "priority": gap.get("priority") or "Fill-in",
                    "intent": gap.get("intent"),
                    "volume": gap.get("volume"),
                    "difficulty": gap.get("difficulty"),
                    "content_type": gap.get("content_type"),
                    "service": gap.get("service"),
                    "est_words": gap.get("est_words"),
                    "opportunity_score": gap.get("opportunity_score"),
                    "funnel": gap.get("funnel"),
                    "rationale": gap.get("rationale"),
                    "note": gap.get("rationale") or "Content gap vs competitors",
                },
                source="content-strategy",
                owner="content-strategy",
                action="create",
            )
        )

    for item in inventory:
        disposition = str(item.get("disposition") or "").lower()
        if disposition in ("consolidate", "noindex", "delete_candidate"):
            note = (
                "Review only — never auto-delete"
                if disposition == "delete_candidate"
                else "Index cleanup / consolidate"
            )
            _audit_row(item, disposition, default_effort="Low", note=note)

    for row in ia.get("redirect_map") or []:
        if not isinstance(row, dict):
            continue
        source_url = str(row.get("from") or row.get("source") or "")
        target_url = str(row.get("to") or row.get("target") or "")
        keyword = str(row.get("keyword") or "")
        existing = find(keyword, source_url)
        fields = {
            "existing_url": source_url,
            "suggested_url": target_url or None,
            "url": source_url,
            "note": row.get("note") or "Architecture redirect",
            "priority": row.get("priority") or "Fill-in",
            "title": row.get("title") or f"Redirect { _path(source_url) or source_url }",
            "keyword": keyword or None,
        }
        if existing:
            overlay(existing, owner="site-architecture", action="redirect", **fields)
            continue
        add(
            _as_work_item(
                fields,
                source="site-architecture",
                owner="site-architecture",
                action="redirect",
            )
        )

    work = []
    for item in items:
        if str(item.get("priority") or "") == "Avoid":
            exclusions.append(
                {
                    "keyword": item.get("keyword") or item.get("title"),
                    "topic": item.get("title") or item.get("keyword"),
                    "reason": item.get("rationale") or item.get("reason") or item.get("note") or "Marked avoid",
                }
            )
            continue
        work.append(item)
    _assign_identity(work)
    both = bool(audit.get("inventory") or audit.get("summary_counts")) and bool(
        strategy.get("priority_queue")
        or strategy.get("content_plan")
        or strategy.get("content_gaps")
        or strategy.get("pillars")
    )
    return {
        "content_plan": work,
        "exclusions": exclusions,
        "audit_overrides": taken_over,
        "queue_length": len(work),
        "both_skills_present": both,
        "routing_rule": (
            "strategy work first; audit and architecture merge into the same items; "
            "architecture wins a shared URL; refresh replaces a new page for the same topic"
        ),
        "vocabulary": {
            "intent_table_owner": "content-strategy Step 4",
            "prioritisation_matrix_owner": "content-strategy Step 5",
            "structural_cannibalisation_owner": "site-architecture",
            "performance_cannibalisation_owner": "content-audit",
            "url_conflict_winner": "site-architecture",
        },
        "note": (
            "One work list for the engagement. "
            "A refresh replaces a new page for the same topic."
            if both
            else "Strategy work only — run the content audit to merge what already exists."
        ),
    }


def _month_theme(month: int) -> str:
    return {1: "Foundation", 2: "Expansion", 3: "Authority"}.get(month, f"Month {month}")


def _volume_value(row: dict[str, Any]) -> float:
    try:
        return float(row.get("volume") or 0)
    except (TypeError, ValueError):
        return 0.0


def schedule_content_plan(items: list[dict[str, Any]], weeks: int = 12) -> list[dict[str, Any]]:
    """Write week, month, and month theme onto each work item, then roll up by month.

    One week per item. Topics are not repeated to pad empty weeks. Selected-service
    pages are scheduled first and are kept when they run past the default 12 weeks.
    """
    actionable = [p for p in items if p.get("priority") != "Avoid"]
    selected = [p for p in actionable if p.get("from_selected_service")]
    rest = [p for p in actionable if not p.get("from_selected_service")]
    ordered = selected + rest
    limit = len(selected) + min(len(rest), weeks)
    scheduled = ordered[: min(len(ordered), limit)]
    scheduled_ids = {id(item) for item in scheduled}
    for item in items:
        if id(item) not in scheduled_ids:
            item["week"] = None
            item["month"] = None
            item["month_theme"] = None
            item["month_week"] = None
    for index, item in enumerate(scheduled):
        week_num = index + 1
        month = ((week_num - 1) // 4) + 1
        item["week"] = week_num
        item["month"] = month
        item["month_week"] = ((week_num - 1) % 4) + 1
        item["month_theme"] = _month_theme(month)
    return month_rollup(items)


def month_rollup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Month pacing only. Titles stay on the work items."""
    buckets: dict[int, dict[str, Any]] = {}
    for item in items:
        month = item.get("month")
        if not isinstance(month, int):
            continue
        bucket = buckets.setdefault(
            month,
            {
                "month": month,
                "theme": item.get("month_theme") or _month_theme(month),
                "label": item.get("month_theme") or _month_theme(month),
                "pieces": 0,
                "actions": {},
                "combined_volume": 0,
                "services": [],
            },
        )
        bucket["pieces"] += 1
        action = str(item.get("action") or "create")
        bucket["actions"][action] = int(bucket["actions"].get(action) or 0) + 1
        bucket["combined_volume"] += _volume_value(item)
        service = str(item.get("service") or "").strip()
        if service and service not in bucket["services"]:
            bucket["services"].append(service)
    rollup = [buckets[key] for key in sorted(buckets)]
    for bucket in rollup:
        bucket["actions_label"] = ", ".join(
            f"{name} {count}" for name, count in bucket["actions"].items()
        ) or "—"
        bucket["services_label"] = ", ".join(bucket["services"]) or "—"
    return rollup


def order_content_plan(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scheduled work first, in week order, then unscheduled work by rank."""

    def sort_key(item: dict[str, Any]) -> tuple:
        week = item.get("week")
        rank = int(item.get("rank") or 0)
        if isinstance(week, int):
            return (0, week, rank)
        return (1, rank, 0)

    items.sort(key=sort_key)
    for index, item in enumerate(items, start=1):
        item["row"] = index
    return items


def summarize_content_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_action: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_source: dict[str, int] = {}
    scheduled = 0
    volume = 0.0
    for item in items:
        action = str(item.get("action") or "unspecified")
        priority = str(item.get("priority") or "unspecified")
        source = str(item.get("source") or "unspecified")
        by_action[action] = by_action.get(action, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        if isinstance(item.get("week"), int):
            scheduled += 1
        volume += _volume_value(item)
    return {
        "total": len(items),
        "by_action": by_action,
        "by_priority": by_priority,
        "by_source": by_source,
        "scheduled": scheduled,
        "unscheduled": len(items) - scheduled,
        "combined_volume": int(volume) if volume == int(volume) else round(volume, 1),
    }


def _summary_phrase(counts: dict[str, Any] | None) -> str:
    if not isinstance(counts, dict) or not counts:
        return "—"
    return ", ".join(f"{name} {count}" for name, count in counts.items())


def present_content_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """One render view. Legacy queues and calendars are folded in, then dropped."""
    out = dict(payload)
    items: list[dict[str, Any]] = []
    by_kw: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}

    def remember(item: dict[str, Any]) -> None:
        keyword = _norm_kw(item.get("keyword") or item.get("primary_keyword"))
        title = _norm_kw(item.get("title"))
        if keyword:
            by_kw.setdefault(keyword, item)
        if title:
            by_title.setdefault(title, item)

    def find(keyword: Any, title: Any) -> dict[str, Any] | None:
        keyword_key = _norm_kw(keyword)
        title_key = _norm_kw(title)
        if keyword_key and keyword_key in by_kw:
            return by_kw[keyword_key]
        if title_key and title_key in by_title:
            return by_title[title_key]
        return None

    def take(row: dict[str, Any]) -> dict[str, Any]:
        existing = find(row.get("keyword") or row.get("primary_keyword"), row.get("title"))
        if existing is None:
            existing = dict(row)
            items.append(existing)
            remember(existing)
            return existing
        for key, value in row.items():
            if value not in (None, "", [], {}) and existing.get(key) in (None, "", [], {}):
                existing[key] = value
        remember(existing)
        return existing

    source_rows = out.get("content_plan") or out.get("priority_queue") or out.get("priority_pages") or []
    for row in source_rows:
        if isinstance(row, dict):
            take(row)
    for row in out.get("combined_priority_queue") or []:
        if isinstance(row, dict):
            take(row)
    for row in out.get("priority_queue_strategy_only") or []:
        if isinstance(row, dict):
            take(row)

    calendar = out.get("content_calendar")
    week_rows: list[dict[str, Any]] = []
    if isinstance(calendar, list):
        for month in calendar:
            if not isinstance(month, dict):
                continue
            if isinstance(month.get("weeks"), list):
                theme = month.get("label") or month.get("theme")
                for week in month["weeks"]:
                    if isinstance(week, dict):
                        week_rows.append({**week, "month": month.get("month"), "month_theme": theme})
            elif month.get("title") or month.get("keyword"):
                week_rows.append(month)
    for week in week_rows:
        item = take(week)
        if week.get("week") is not None:
            item["week"] = week.get("week")
        if isinstance(week.get("month"), int):
            item["month"] = week.get("month")
        theme = week.get("month_theme") or (week.get("month") if isinstance(week.get("month"), str) else None)
        if theme:
            item["month_theme"] = theme
        _fill(
            item,
            content_type=week.get("content_type") or week.get("type"),
            priority=week.get("priority"),
            keyword=week.get("keyword"),
            title=week.get("title"),
            action=week.get("action"),
            suggested_url=week.get("suggested_url"),
        )

    work = [item for item in items if str(item.get("priority") or "") != "Avoid"]
    exclusions = [
        {
            "keyword": item.get("keyword") or item.get("title"),
            "topic": item.get("title") or item.get("keyword"),
            "reason": item.get("rationale") or item.get("reason") or item.get("note") or "Marked avoid",
        }
        for item in items
        if str(item.get("priority") or "") == "Avoid"
    ]
    exclusions.extend(row for row in (out.get("exclusions") or []) if isinstance(row, dict))
    if work and not any(item.get("id") for item in work):
        _assign_identity(work)
    order_content_plan(work)
    summary = out.get("content_plan_summary") if isinstance(out.get("content_plan_summary"), dict) else {}
    if not summary:
        summary = summarize_content_plan(work)
    rollup = month_rollup(work)
    out["content_plan"] = work
    out["content_calendar"] = rollup
    out["exclusions"] = exclusions
    out["content_plan_summary"] = summary
    out["content_plan_summary_view"] = {
        "total": summary.get("total", len(work)),
        "actions": _summary_phrase(summary.get("by_action")),
        "priorities": _summary_phrase(summary.get("by_priority")),
        "scheduled": summary.get("scheduled", 0),
        "unscheduled": summary.get("unscheduled", 0),
        "combined_volume": summary.get("combined_volume", 0),
        "sources": _summary_phrase(summary.get("by_source")),
    }
    for key in (
        "priority_queue",
        "priority_pages",
        "combined_priority_queue",
        "priority_queue_strategy_only",
        "calendar_priorities",
    ):
        out.pop(key, None)
    return out


def build_combined_priority_queue(
    *,
    content_audit: dict[str, Any] | None,
    content_strategy: dict[str, Any] | None,
    site_architecture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility name. Returns the one content plan, not a second queue."""
    return build_content_plan(
        content_audit=content_audit,
        content_strategy=content_strategy,
        site_architecture=site_architecture,
    )
