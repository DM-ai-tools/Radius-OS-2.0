"""Claude names SERP clusters. Naming only.

It never creates keywords, never moves a keyword between clusters, never
supplies a metric, and never picks the primary keyword: the primary and the
member roles are decided in one place (``keyword_cluster_roles``) from measured
metrics, and a primary suggested here is ignored. The cluster's primary is
shown to the model so the name fits the page's target.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import get_settings
from app.integrations.llm import _parse_json_content, keyword_analysis_chat, keyword_analysis_ready
from app.logging_config import get_logger

log = get_logger("keyword_cluster_naming")

_SYSTEM = """\
You name keyword clusters that are already grouped and already have a primary
keyword. For each cluster write a short, specific name for the page it becomes.

Return JSON only:
{"clusters":[{"id":1,"name":"..."}]}

Rules:
- Name the cluster around its primary keyword.
- Do not add, remove, or reassign keywords, and do not choose a different primary.
- Do not output search volume, CPC, competition, difficulty, or intent.
"""


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _members(cluster: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in cluster.get("keywords") or []:
        if isinstance(row, dict):
            keyword = str(row.get("keyword") or "").strip()
        else:
            keyword = str(row or "").strip()
        key = _norm(keyword)
        if keyword and key not in seen:
            seen.add(key)
            out.append(keyword)
    primary = str(cluster.get("primary_keyword") or "").strip()
    if primary and _norm(primary) not in seen:
        out.append(primary)
    return out


def apply_cluster_names(
    clusters: list[dict[str, Any]],
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Apply a Claude naming payload: names only. Unknown ids are dropped, and a
    suggested primary keyword is ignored (the role decider owns the primary)."""
    if not isinstance(payload, dict):
        return clusters
    by_id: dict[int, dict[str, Any]] = {}
    for row in payload.get("clusters") or []:
        if not isinstance(row, dict):
            continue
        try:
            by_id[int(row.get("id"))] = row
        except (TypeError, ValueError):
            continue
    named: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, 1):
        updated = dict(cluster)
        suggestion = by_id.get(index)
        if suggestion:
            label = str(suggestion.get("name") or "").strip()
            if label:
                updated["name"] = label[:120]
        named.append(updated)
    return named


async def name_clusters(
    clusters: list[dict[str, Any]],
    *,
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Name clusters with the model. ``audit`` (if given) records whether the
    names came from the model, which model, or why they did not."""
    settings = get_settings()
    audit = audit if audit is not None else {}
    audit.update({"ran": False, "model": None, "reason": None})
    if not clusters:
        audit["reason"] = "no clusters to name"
        return clusters
    if not keyword_analysis_ready():
        audit["reason"] = "no model key configured; names come from the rules"
        return clusters
    brief = []
    for index, cluster in enumerate(clusters, 1):
        brief.append(
            {
                "id": index,
                "primary_keyword": cluster.get("primary_keyword"),
                "keywords": _members(cluster)[:40],
            }
        )
    raw = await keyword_analysis_chat(
        system=_SYSTEM,
        user=json.dumps({"clusters": brief}),
        model=settings.skill_model,
        max_tokens=2048,
    )
    if not raw or not str(raw).strip():
        log.warning("cluster_naming_empty")
        audit["reason"] = "naming was skipped; names come from the rules"
        return clusters
    try:
        parsed = _parse_json_content(raw)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        log.warning("cluster_naming_unparsed")
        audit["reason"] = "the model's reply could not be read; names come from the rules"
        return clusters
    audit.update({"ran": True, "model": settings.skill_model})
    return apply_cluster_names(clusters, parsed)
