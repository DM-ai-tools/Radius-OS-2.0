"""Claude names SERP clusters and picks a primary from members only.

It never creates keywords, never moves a keyword between clusters, and never
supplies a metric. A primary that is not already in the cluster is ignored.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import get_settings
from app.integrations.llm import _parse_json_content, _skill_chat
from app.logging_config import get_logger

log = get_logger("keyword_cluster_naming")

_SYSTEM = """\
You name keyword clusters that are already grouped. For each cluster pick a
short name and one primary keyword.

Return JSON only:
{"clusters":[{"id":1,"name":"...","primary_keyword":"exact member"}]}

Rules:
- primary_keyword must be copied from that cluster's keyword list.
- Do not add, remove, or reassign keywords.
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
    """Apply a Claude naming payload. Unknown ids and invented primaries are dropped."""
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
        members = {_norm(k): k for k in _members(cluster)}
        if suggestion:
            primary = _norm(suggestion.get("primary_keyword"))
            if primary in members:
                updated["primary_keyword"] = members[primary]
            label = str(suggestion.get("name") or "").strip()
            if label:
                updated["name"] = label[:120]
        named.append(updated)
    return named


async def name_clusters(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings()
    if not clusters or settings.use_mock_llm or not settings.anthropic_api_key:
        return clusters
    brief = []
    for index, cluster in enumerate(clusters, 1):
        brief.append(
            {
                "id": index,
                "keywords": _members(cluster)[:40],
            }
        )
    raw = await _skill_chat(
        system=_SYSTEM,
        user=json.dumps({"clusters": brief}),
        model=settings.skill_model,
        max_tokens=2048,
    )
    parsed = _parse_json_content(raw or "")
    if not isinstance(parsed, dict):
        log.warning("cluster_naming_unparsed")
        return clusters
    return apply_cluster_names(clusters, parsed)
