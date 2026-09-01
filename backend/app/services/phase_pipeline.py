"""Cross-phase pipeline definitions and handoff validation.

PHASE 5 — SEARCH DEMAND & KEYWORD RESEARCH
├── Keyword Research
├── Keyword Cleaning / Relevance Filtering
├── Topic Creation
├── Keyword Clustering
└── Search Intent / Cluster Understanding
        ↓
PHASE 6 — SEO STRATEGY & INFORMATION ARCHITECTURE
├── Cluster → URL Mapping
├── Existing URL matching
├── New URL identification
├── Site structure
└── Cannibalization decisions
        ↓
PHASE 8/9 — CONTENT PLANNING
├── Existing content → audit/optimization
└── New content → page planning
        ↓
PHASE 11 — ON-PAGE SEO
├── Title
├── H1/H2
├── Meta
├── Content
└── Internal Linking
"""

from __future__ import annotations

from typing import Any

PHASE_5_STAGES = [
    "keyword_research",
    "keyword_cleaning",
    "topic_creation",
    "keyword_clustering",
    "cluster_intent_understanding",
]

PHASE_6_STAGES = [
    "cluster_url_mapping",
    "existing_url_matching",
    "new_url_identification",
    "site_structure",
    "cannibalization_decisions",
]

PHASE_8_9_STAGES = [
    "existing_content_audit_optimization",
    "new_content_page_planning",
]

PHASE_11_STAGES = [
    "title",
    "h1_h2",
    "meta",
    "content",
    "internal_linking",
]

PHASE_CHAIN = {
    "phase_5": {"key": "search_demand", "stages": PHASE_5_STAGES, "next": "content_strategy"},
    "phase_6": {
        "key": "site_architecture",
        "stages": PHASE_6_STAGES,
        "also": ["content_strategy"],
        "next": "content_planning",
    },
    "phase_8_9": {"key": "content_planning", "stages": PHASE_8_9_STAGES, "next": "content_production"},
    "phase_11": {"key": "on_page_seo", "stages": PHASE_11_STAGES, "next": "publishing"},
}


def _stage_ok(name: str, ok: bool, *, detail: str | None = None) -> dict[str, Any]:
    return {"stage": name, "ok": ok, "detail": detail}


def validate_phase5_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Check Phase 5 sub-stages are present in a search_demand summary."""
    stages: list[dict[str, Any]] = []
    stages.append(
        _stage_ok(
            "keyword_research",
            bool(pack.get("keyword_dataset") or pack.get("seed_clusters") or pack.get("keyword_count")),
            detail=f"{pack.get('keyword_count', 0)} keywords",
        )
    )
    cleaning = dict(pack.get("keyword_cleaning") or {})
    cluster_report = dict(pack.get("cluster_report") or {})
    pipeline = dict(cluster_report.get("keyword_pipeline") or {})
    stages.append(
        _stage_ok(
            "keyword_cleaning",
            bool(cleaning.get("kept_count") or pipeline.get("stages") or cluster_report.get("keyword_cleaning")),
            detail=f"kept {cleaning.get('kept_count', '?')} of {cleaning.get('input_count', '?')}",
        )
    )
    stages.append(
        _stage_ok(
            "topic_creation",
            bool((pack.get("topic_plan") or {}).get("topic_ideas") or pack.get("topics")),
            detail=f"{len((pack.get('topic_plan') or {}).get('topic_ideas') or pack.get('topics') or [])} topics",
        )
    )
    stages.append(
        _stage_ok(
            "keyword_clustering",
            bool(cluster_report.get("clusters")),
            detail=f"{cluster_report.get('clusters_created', 0)} clusters",
        )
    )
    intent_pack = dict(pack.get("cluster_intent") or cluster_report.get("cluster_intent") or {})
    stages.append(
        _stage_ok(
            "cluster_intent_understanding",
            bool(intent_pack.get("pages") or cluster_report.get("cluster_validation")),
            detail=f"{intent_pack.get('page_count', len(cluster_report.get('clusters') or []))} clusters understood",
        )
    )
    ok = all(s["ok"] for s in stages[:4])  # intent enrichment is additive
    return {"phase": 5, "agent": "search_demand", "stages": stages, "ready_for_phase_6": ok}


def enrich_phase5_cluster_intent(
    cluster_report: dict[str, Any],
    *,
    serp_by_keyword: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach search intent + validation per cluster (Phase 5 capstone)."""
    from app.services.content_pipeline import identify_primary_keyword, run_clusters_page_pipeline

    batch = run_clusters_page_pipeline(
        cluster_report,
        serp_by_keyword=serp_by_keyword or {},
    )
    intents: list[dict[str, Any]] = []
    for cluster in cluster_report.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        primary = identify_primary_keyword(cluster)
        page = next(
            (p for p in batch.get("pages") or [] if p.get("primary_keyword") == primary),
            {},
        )
        intents.append(
            {
                "cluster": cluster.get("name"),
                "primary_keyword": primary,
                "intent": page.get("search_intent") or cluster.get("intent"),
                "content_type": page.get("content_type") or cluster.get("content_type"),
                "secondary_keywords": page.get("secondary_keywords") or [],
                "serp_content_type": page.get("serp_content_type"),
                "validation_ok": (page.get("validation") or {}).get("valid", True),
            }
        )
    return {
        "pages": batch.get("pages") or [],
        "page_count": batch.get("page_count") or len(intents),
        "cluster_validation": batch.get("cluster_validation") or cluster_report.get("cluster_validation"),
        "intents": intents,
    }


def enrich_phase5_pack(pack: dict[str, Any], *, serp_by_keyword: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Add cluster intent understanding + phase pipeline metadata to Phase 5 output."""
    if serp_by_keyword:
        # Raw SERP evidence only has validated/organic. summarize_serp() derives
        # dominant_format + intent from the organic titles (used by content_pipeline's
        # serp_content_type below, and by Phase 6's score_intent_match/serp_analysis) —
        # run it once here so every downstream reader sees the same enriched shape.
        from app.services.content_brief import summarize_serp

        serp_by_keyword = {
            kw: summarize_serp(raw, fallback_intent=None) if isinstance(raw, dict) else raw
            for kw, raw in serp_by_keyword.items()
        }
    cluster_report = dict(pack.get("cluster_report") or {})
    intent = enrich_phase5_cluster_intent(cluster_report, serp_by_keyword=serp_by_keyword)
    cluster_report["cluster_intent"] = intent
    pack["cluster_report"] = cluster_report
    pack["cluster_intent"] = intent
    # Persist the SERP evidence itself (not just the derived intent) so Phase 6
    # URL mapping (site_architecture.py -> build_final_url_map) can read
    # demand.get("serp_by_keyword") instead of silently scoring with no SERP
    # evidence at all.
    if serp_by_keyword:
        pack["serp_by_keyword"] = serp_by_keyword
    pack["phase_pipeline"] = validate_phase5_pack({**pack, "cluster_intent": intent})
    return pack


def validate_phase6_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Check Phase 6 IA sub-stages."""
    url_map = pack.get("final_url_map") or (pack.get("url_map_report") or {}).get("final_url_map") or []
    tree = pack.get("target_url_tree") or []
    ownership = pack.get("cluster_ownership") or pack.get("cluster_owners") or []
    summary = dict(pack.get("url_map_summary") or (pack.get("url_map_report") or {}).get("summary") or {})
    stages: list[dict[str, Any]] = []
    stages.append(
        _stage_ok(
            "cluster_url_mapping",
            bool(url_map),
            detail=f"{len(url_map)} mapped clusters",
        )
    )
    optimize = int(summary.get("optimize_existing") or 0)
    review = int(summary.get("review_merge_redirect") or 0)
    stages.append(
        _stage_ok(
            "existing_url_matching",
            optimize + review > 0 or bool(url_map),
            detail=f"{optimize} optimize, {review} review",
        )
    )
    create = int(summary.get("create") or 0)
    stages.append(
        _stage_ok(
            "new_url_identification",
            create > 0 or bool(tree),
            detail=f"{create} new URLs planned",
        )
    )
    stages.append(
        _stage_ok(
            "site_structure",
            bool(tree),
            detail=f"{len(tree)} nodes in target_url_tree",
        )
    )
    competing = sum(len(o.get("competing_urls") or []) for o in ownership if isinstance(o, dict))
    cannibal_flags = sum(
        1
        for row in url_map
        if isinstance(row, dict) and (row.get("competing_urls") or row.get("action") == "REVIEW_MERGE_REDIRECT")
    )
    # Real cross-cluster URL collisions, flagged by apply_url_map_to_architecture
    # when two different clusters resolve to the same URL (cannibalization=True
    # ownership rows) — distinct from "an ownership row merely exists", which was
    # trivially true and told a reviewer nothing about whether cannibalization
    # was actually checked.
    collisions = sum(1 for row in ownership if isinstance(row, dict) and row.get("cannibalization"))
    stages.append(
        _stage_ok(
            "cannibalization_decisions",
            bool(url_map),
            detail=(
                f"{collisions} cross-cluster URL collisions, {cannibal_flags} review-band "
                f"mappings, {competing} competing URLs noted across {len(ownership)} owners"
            ),
        )
    )
    ok = bool(tree) and bool(url_map or tree)
    return {"phase": 6, "agent": "site_architecture", "stages": stages, "ready_for_phase_8_9": ok}


def enrich_phase6_pack(blueprint: dict[str, Any]) -> dict[str, Any]:
    blueprint["phase_pipeline"] = validate_phase6_pack(blueprint)
    return blueprint


def validate_phase89_pack(report: dict[str, Any]) -> dict[str, Any]:
    """Check Content Planning split: existing optimization vs new pages."""
    pages = list(report.get("pages") or [])
    existing_actions = {"refresh", "no_action", "retire"}
    new_actions = {"create"}
    existing_rows = [p for p in pages if p.get("action") in existing_actions]
    new_rows = [p for p in pages if p.get("action") in new_actions]
    stages = [
        _stage_ok(
            "existing_content_audit_optimization",
            bool(existing_rows) or not pages,
            detail=f"{len(existing_rows)} pages to optimize/leave/retire",
        ),
        _stage_ok(
            "new_content_page_planning",
            bool(new_rows) or not pages,
            detail=f"{len(new_rows)} new pages planned",
        ),
    ]
    ok = bool(pages) and report.get("locked") is not False
    return {
        "phase": "8/9",
        "agent": "content_planning",
        "stages": stages,
        "existing_count": len(existing_rows),
        "new_count": len(new_rows),
        "ready_for_phase_11": ok and bool(pages),
    }


def enrich_phase89_pack(report: dict[str, Any]) -> dict[str, Any]:
    report["phase_pipeline"] = validate_phase89_pack(report)
    report["planning_split"] = {
        "existing_content": [
            {
                "url": p.get("url"),
                "action": p.get("action"),
                "content_action": p.get("content_action"),
                "primary_keyword": p.get("primary_keyword"),
            }
            for p in report.get("pages") or []
            if p.get("action") in ("refresh", "no_action", "retire")
        ],
        "new_content": [
            {
                "url": p.get("url"),
                "action": p.get("action"),
                "content_action": p.get("content_action"),
                "primary_keyword": p.get("primary_keyword"),
            }
            for p in report.get("pages") or []
            if p.get("action") == "create"
        ],
    }
    return report


def validate_phase11_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Check On-Page SEO sub-stages on the page package."""
    pages = list(pack.get("pages") or [])
    page = pages[0] if pages else {}
    stages: list[dict[str, Any]] = []
    title = page.get("title")
    stages.append(_stage_ok("title", bool(title if isinstance(title, str) else (title or {}).get("after"))))
    headings = page.get("headings") or {}
    stages.append(_stage_ok("h1_h2", bool(headings.get("h1") or page.get("h1"))))
    meta = page.get("meta_description")
    stages.append(_stage_ok("meta", bool(meta if isinstance(meta, str) else (meta or {}).get("after"))))
    stages.append(_stage_ok("content", bool(page.get("content") or page.get("body") or page.get("outline"))))
    links = pack.get("internal_links") or pack.get("internal_linking") or {}
    stages.append(
        _stage_ok(
            "internal_linking",
            bool(links if isinstance(links, list) else (links.get("links") or links.get("plan"))),
        )
    )
    ok = all(s["ok"] for s in stages[:3])
    return {"phase": 11, "agent": "on_page_seo", "stages": stages, "ready_for_publishing": ok}


def enrich_phase11_pack(pack: dict[str, Any]) -> dict[str, Any]:
    pack["phase_pipeline"] = validate_phase11_pack(pack)
    return pack


def phase_handoff_summary(*packs: dict[str, Any]) -> dict[str, Any]:
    """Build a cross-phase readiness summary from phase pack dicts."""
    out: dict[str, Any] = {}
    for pack in packs:
        pp = pack.get("phase_pipeline")
        if isinstance(pp, dict) and pp.get("agent"):
            out[str(pp["agent"])] = pp
    return out
