"""Content pipeline: clean keywords → cluster → title → approval-ready page pack.

CLEAN KEYWORDS          (keyword_pipeline.run_keyword_pipeline)
      ↓
KEYWORD CLUSTERS        (keyword_clustering.run_keyword_clustering)
      ↓
Cluster Validation
      ↓
Identify Primary Keyword
      ↓
Identify Secondary Keywords
      ↓
Determine Search Intent
      ↓
Determine SERP / Content Type
      ↓
Analyze Existing Page
      ↓
Analyze Ranking Competitors
      ↓
Extract Title Patterns
      ↓
Determine Title Angle
      ↓
Generate Title Candidates
      ↓
Score Candidates
      ↓
Select Best Title
      ↓
Validate Title
      ↓
Human / Agent Approval  (review.py — outside this module)
      ↓
Publish                 (publishing.py — outside this module)
"""

from __future__ import annotations

import re
from typing import Any

from app.services.headline_framework import (
    build_headline_options,
    determine_title_angle,
    extract_title_patterns,
    score_titles_against_serp,
    validate_title,
)
from app.services.keyword_clustering import validate_clusters
from app.services.keyword_opportunity import detect_intent

_STOP = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokens(kw: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(kw)) if t not in _STOP and len(t) > 1}


def identify_primary_keyword(cluster: dict[str, Any]) -> str:
    pkw = str(cluster.get("primary_keyword") or "").strip()
    if pkw:
        return pkw
    for row in cluster.get("keywords") or []:
        if isinstance(row, dict) and row.get("role") == "Primary":
            return str(row.get("keyword") or "").strip()
    kws = cluster.get("keywords") or []
    if kws and isinstance(kws[0], dict):
        return str(kws[0].get("keyword") or "").strip()
    return ""


def identify_secondary_keywords(cluster: dict[str, Any], *, limit: int = 8) -> list[str]:
    out: list[str] = []
    primary = _norm(identify_primary_keyword(cluster))
    for row in cluster.get("keywords") or []:
        if not isinstance(row, dict):
            continue
        kw = str(row.get("keyword") or "").strip()
        if not kw or _norm(kw) == primary:
            continue
        role = str(row.get("role") or "")
        if role in ("Secondary", "Supporting") or not role:
            out.append(kw)
    seen: set[str] = set()
    deduped: list[str] = []
    for kw in out:
        key = _norm(kw)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(kw)
        if len(deduped) >= limit:
            break
    return deduped


def primary_keyword_metrics(cluster: dict[str, Any]) -> dict[str, Any]:
    """Volume/CPC for the cluster's primary keyword — for the URL Mapping sheet's
    Search Volume and CPC columns, which need the metric attached to a specific
    keyword row, not just the keyword string identify_primary_keyword() returns.
    """
    primary = _norm(identify_primary_keyword(cluster))
    for row in cluster.get("keywords") or []:
        if isinstance(row, dict) and _norm(str(row.get("keyword") or "")) == primary:
            return {"volume": row.get("volume"), "cpc": row.get("cpc")}
    return {"volume": None, "cpc": None}


def combined_cluster_volume(cluster: dict[str, Any]) -> int | None:
    """Sum of every keyword's search volume in the cluster (Combined Cluster
    Volume column) — the total demand this URL is expected to capture across
    its primary + secondary + supporting keywords, not just the head term.
    """
    total = 0
    seen = False
    for row in cluster.get("keywords") or []:
        if not isinstance(row, dict):
            continue
        vol = row.get("volume")
        if isinstance(vol, (int, float)):
            total += int(vol)
            seen = True
    return total if seen else None


def analyze_ranking_competitors(
    cluster: dict[str, Any],
    serp_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    organic = list((serp_summary or {}).get("organic") or [])
    domains = list(cluster.get("competitor_domains") or [])
    positions: list[dict[str, Any]] = []
    for row in organic:
        if not isinstance(row, dict):
            continue
        dom = str(row.get("domain") or "").lower()
        positions.append(
            {
                "domain": dom,
                "position": row.get("position"),
                "title": row.get("title"),
                "url": row.get("url"),
                "known_competitor": dom in {d.lower() for d in domains} if domains else None,
            }
        )
    return {
        "competitor_domains": domains,
        "serp_positions": positions[:10],
        "known_in_serp": sum(1 for p in positions if p.get("known_competitor")),
    }


def analyze_existing_page(preflight: dict[str, Any] | None) -> dict[str, Any]:
    pre = dict(preflight or {})
    ia = dict(pre.get("ia") or {})
    return {
        "has_existing_page": bool(ia.get("url") or pre.get("existing_page")),
        "url": ia.get("url") or pre.get("url"),
        "page_type": ia.get("page_type") or pre.get("page_type"),
        "action": pre.get("action") or ia.get("action"),
        "match_confidence": pre.get("match_confidence") or ia.get("match_confidence"),
        "blockers": list(pre.get("blockers") or []),
    }


def run_cluster_page_pipeline(
    cluster: dict[str, Any],
    *,
    serp_summary: dict[str, Any] | None = None,
    preflight: dict[str, Any] | None = None,
    client_name: str | None = None,
    audience: str | None = None,
    industry: str | None = None,
    location: str | None = None,
    differentiation: str | None = None,
    page_type: str | None = None,
    outline_count: int | None = None,
    roadmap_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Run cluster → title stages for one page cluster."""
    validation = validate_clusters([cluster])
    primary = identify_primary_keyword(cluster)
    secondaries = identify_secondary_keywords(cluster)
    intent = str(
        cluster.get("intent")
        or (serp_summary or {}).get("intent")
        or detect_intent(primary)
    ).lower()
    content_type = str(cluster.get("content_type") or cluster.get("recommended_content") or "guide")
    serp = dict(serp_summary or {})
    dominant_format = serp.get("dominant_format") or "article"
    existing = analyze_existing_page(preflight)
    competitors = analyze_ranking_competitors(cluster, serp_summary=serp)
    title_patterns = extract_title_patterns(
        [
            str(r.get("title") or "")
            for r in (serp.get("organic") or [])
            if isinstance(r, dict)
        ]
        or list(serp.get("coverage_floor") or [])
    )
    angle = determine_title_angle(
        serp_summary=serp,
        intent=intent,
        content_type=content_type,
        title_patterns=title_patterns,
    )
    candidates = build_headline_options(
        primary,
        page_type=page_type or content_type,
        content_type=content_type,
        intent=intent,
        audience=audience,
        industry=industry,
        location=location,
        client_name=client_name,
        differentiation=differentiation,
        outline_count=outline_count,
        prefer_template=angle.get("prefer_template"),
        keep_scores=True,
        limit=8,
    )
    ranked = score_titles_against_serp(
        candidates,
        serp_summary=serp,
        primary_keyword=primary,
        intent=intent,
    )
    selected = ranked[0] if ranked else {}
    title_validation = validate_title(
        str(selected.get("title") or primary),
        keyword=primary,
        serp_summary=serp,
        roadmap_titles=roadmap_titles,
    )

    return {
        "cluster_name": cluster.get("name"),
        "validation": validation,
        "primary_keyword": primary,
        "secondary_keywords": secondaries,
        "search_intent": intent,
        "content_type": content_type,
        "serp_content_type": {
            "dominant_format": dominant_format,
            "validated": bool(serp.get("validated")),
            "intent": serp.get("intent") or intent,
        },
        "existing_page": existing,
        "ranking_competitors": competitors,
        "title_patterns": title_patterns,
        "title_angle": angle,
        "title_candidates": ranked,
        "selected_title": selected,
        "title_validation": title_validation,
        "approval_ready": bool(title_validation.get("ok")) and validation.get("valid", True),
        "pipeline_stages": [
            "keyword_clusters",
            "cluster_validation",
            "primary_keyword",
            "secondary_keywords",
            "search_intent",
            "serp_content_type",
            "existing_page",
            "ranking_competitors",
            "title_patterns",
            "title_angle",
            "title_candidates",
            "score_candidates",
            "select_title",
            "validate_title",
        ],
    }


def run_clusters_page_pipeline(
    cluster_report: dict[str, Any],
    *,
    serp_by_keyword: dict[str, dict[str, Any]] | None = None,
    preflight_by_keyword: dict[str, dict[str, Any]] | None = None,
    client_name: str | None = None,
    audience: str | None = None,
    industry: str | None = None,
    roadmap_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Run the page pipeline for every cluster in a cluster report."""
    serp_map = serp_by_keyword or {}
    pre_map = preflight_by_keyword or {}
    pages: list[dict[str, Any]] = []
    for cluster in cluster_report.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        pkw = identify_primary_keyword(cluster)
        pages.append(
            run_cluster_page_pipeline(
                cluster,
                serp_summary=serp_map.get(_norm(pkw)) or serp_map.get(pkw),
                preflight=pre_map.get(_norm(pkw)) or pre_map.get(pkw),
                client_name=client_name,
                audience=audience,
                industry=industry,
                roadmap_titles=roadmap_titles,
            )
        )
    report_validation = validate_clusters(cluster_report.get("clusters") or [])
    return {
        "cluster_validation": report_validation,
        "pages": pages,
        "pages_ready": sum(1 for p in pages if p.get("approval_ready")),
        "page_count": len(pages),
    }
