"""Slim shared-memory packs vs full phase reports.

Full detailed reports stay on chat cards.
CDP / Team shared memory keeps only what later phases and operators need.
"""

from __future__ import annotations

from typing import Any


def _trim(v: Any, n: int = 120) -> str:
    s = str(v or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _kw_row(row: dict[str, Any], *, with_serp: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "keyword": row.get("keyword"),
        "volume": row.get("volume"),
        "difficulty": row.get("difficulty"),
        "intent": row.get("intent"),
        "opportunity_score": row.get("opportunity_score"),
        "bucket": row.get("bucket"),
        "gap_flag": bool(row.get("gap_flag")),
    }
    comps = row.get("competitor_domains") or []
    if comps:
        out["competitor_domains"] = list(comps)[:3]
    if row.get("business_fit") is not None:
        out["business_fit"] = row.get("business_fit")
    if with_serp:
        titles = row.get("serp_titles") or []
        if isinstance(titles, list) and titles:
            out["serp_titles"] = [
                {"title": _trim(t.get("title"), 80), "domain": t.get("domain")}
                for t in titles[:2]
                if isinstance(t, dict) and t.get("title")
            ]
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _slim_competitors(rows: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        item = {
            "name": row.get("name"),
            "url": row.get("url") or row.get("domain"),
            "domain": row.get("domain"),
            "cluster": row.get("cluster") or row.get("positioning_cluster"),
            "source": row.get("source"),
        }
        out.append({k: v for k, v in item.items() if v not in (None, "", [])})
    return out


def _slim_competitor_sites(rows: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        blogs = []
        for b in (row.get("blogs") or row.get("pages") or [])[:4]:
            if isinstance(b, dict) and (b.get("title") or b.get("path")):
                blogs.append(
                    {
                        "title": _trim(b.get("title"), 90),
                        "path": b.get("path"),
                        "kind": b.get("kind"),
                    }
                )
        item = {
            "name": row.get("name"),
            "domain": row.get("domain"),
            "page_count": row.get("page_count"),
            "blog_count": row.get("blog_count"),
            "hub_paths": list(row.get("hub_paths") or [])[:8],
            "blogs": blogs,
        }
        if row.get("error"):
            item["error"] = _trim(row.get("error"), 80)
        out.append({k: v for k, v in item.items() if v not in (None, "", [], {})})
    return out


def _slim_clusters(rows: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for c in rows[:limit]:
        if not isinstance(c, dict):
            continue
        kws = []
        raw_kws = c.get("keywords") or []
        if isinstance(raw_kws, list):
            for item in raw_kws[:8]:
                if isinstance(item, dict) and item.get("keyword"):
                    kws.append(
                        {
                            "keyword": item.get("keyword"),
                            "volume": item.get("volume"),
                            "difficulty": item.get("difficulty"),
                            "intent": item.get("intent"),
                            "role": item.get("role"),
                        }
                    )
                elif isinstance(item, str) and item.strip():
                    kws.append({"keyword": item.strip()})
        vol = c.get("total_volume")
        if vol is None:
            vol = c.get("est_traffic")
        kd = c.get("avg_difficulty")
        if kd is None:
            kd = c.get("difficulty")
        item = {
            "name": c.get("name"),
            "intent": c.get("intent"),
            "funnel": c.get("funnel"),
            "primary_keyword": c.get("primary_keyword"),
            "content_type": c.get("content_type") or c.get("recommended_content"),
            "keyword_count": c.get("keyword_count") or len(kws),
            # Dual keys so strategy/IA consumers keep working after slim
            "total_volume": vol,
            "est_traffic": vol,
            "avg_difficulty": kd,
            "difficulty": kd,
            "best_score": c.get("best_score") or c.get("opportunity_score"),
            "keywords": kws,
            "competitor_domains": list(c.get("competitor_domains") or [])[:3],
        }
        out.append({k: v for k, v in item.items() if v not in (None, "", [], {})})
    return out


def _slim_kw_bucket(rows: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    return [_kw_row(r) for r in (rows or [])[:limit] if isinstance(r, dict)]


def _slim_core_topic_clusters(clusters: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(clusters, list):
        return out
    for c in clusters[:limit]:
        if not isinstance(c, dict):
            continue
        supporting = []
        for s in (c.get("supporting") or [])[:4]:
            if isinstance(s, dict) and (s.get("keyword") or s.get("title")):
                supporting.append(
                    {
                        "title": _trim(s.get("title"), 60),
                        "keyword": s.get("keyword"),
                        "est_words": s.get("est_words"),
                    }
                )
        item = {
            "name": c.get("name"),
            "primary_keyword": c.get("primary_keyword"),
            "intent": c.get("intent"),
            "est_words": c.get("est_words"),
            "opportunity_score": c.get("opportunity_score") or c.get("best_score"),
            "supporting": supporting or None,
        }
        out.append({k: v for k, v in item.items() if v not in (None, "", [], {})})
    return out


def _slim_topic_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    ideas = []
    for t in (plan.get("topic_ideas") or [])[:10]:
        if not isinstance(t, dict):
            continue
        ideas.append(
            {
                "title": _trim(t.get("title"), 80),
                "keyword": t.get("keyword"),
                "intent": t.get("intent"),
                "funnel": t.get("funnel"),
                "volume": t.get("volume"),
                "opportunity_score": t.get("opportunity_score"),
                "gap_flag": bool(t.get("gap_flag")),
                "competitor_domains": list(t.get("competitor_domains") or [])[:3],
            }
        )
    out: dict[str, Any] = {
        "seed": plan.get("seed"),
        "topic_ideas": ideas,
        "cluster_map": plan.get("cluster_map") if isinstance(plan.get("cluster_map"), dict) else None,
        "publishing_order": list(plan.get("publishing_order") or [])[:10],
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def slim_search_demand_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    """Essentials for shared memory + Phase 6 — not the full chat report."""
    if not full:
        return {}
    clusters = _slim_clusters(full.get("clusters"))
    if not clusters and isinstance(full.get("cluster_report"), dict):
        clusters = _slim_clusters((full.get("cluster_report") or {}).get("clusters"))

    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "seed_keywords": list(full.get("seed_keywords") or [])[:15],
        "cdd_keywords": list(full.get("cdd_keywords") or [])[:12],
        "products": list(full.get("products") or full.get("services") or [])[:10],
        "services": list(full.get("services") or full.get("products") or [])[:10],
        "keyword_seeding": full.get("keyword_seeding")
        if isinstance(full.get("keyword_seeding"), dict)
        else None,
        "keyword_cleaning": (
            {
                "input_count": (full.get("keyword_cleaning") or {}).get("input_count"),
                "kept_count": (full.get("keyword_cleaning") or {}).get("kept_count"),
                "removed_count": (full.get("keyword_cleaning") or {}).get("removed_count"),
                "removed_by_reason": (full.get("keyword_cleaning") or {}).get("removed_by_reason")
                or {},
                "sample": list((full.get("keyword_cleaning") or {}).get("sample") or [])[:8],
            }
            if isinstance(full.get("keyword_cleaning"), dict)
            and (full.get("keyword_cleaning") or {}).get("input_count")
            else None
        ),
        "seed_clusters": [
            {
                "seed": c.get("seed"),
                "target": c.get("target"),
                "target_type": c.get("target_type"),
                "keyword_count": c.get("keyword_count"),
                "classes_missing": list(c.get("classes_missing") or []),
                "exact": [
                    {
                        "keyword": r.get("keyword"),
                        "volume": r.get("volume"),
                        "intent": r.get("intent"),
                        "match_class": "exact",
                    }
                    for r in (c.get("exact") or [])[:8]
                    if isinstance(r, dict)
                ],
                "phrase": [
                    {
                        "keyword": r.get("keyword"),
                        "volume": r.get("volume"),
                        "intent": r.get("intent"),
                        "match_class": "phrase",
                    }
                    for r in (c.get("phrase") or [])[:8]
                    if isinstance(r, dict)
                ],
                "related": [
                    {
                        "keyword": r.get("keyword"),
                        "volume": r.get("volume"),
                        "intent": r.get("intent"),
                        "match_class": "related",
                    }
                    for r in (c.get("related") or [])[:8]
                    if isinstance(r, dict)
                ],
                "broad": [
                    {
                        "keyword": r.get("keyword"),
                        "volume": r.get("volume"),
                        "intent": r.get("intent"),
                        "match_class": "broad",
                    }
                    for r in (c.get("broad") or [])[:6]
                    if isinstance(r, dict)
                ],
            }
            for c in (full.get("seed_clusters") or [])[:10]
            if isinstance(c, dict)
        ],
        "geographic_focus": full.get("geographic_focus"),
        "location_name": full.get("location_name"),
        "location_code": full.get("location_code"),
        "country": full.get("country"),
        "competitors": _slim_competitors(full.get("competitors")),
        "competitor_names": list(full.get("competitor_names") or [])[:8],
        "competitor_domains": list(full.get("competitor_domains") or [])[:8],
        "competitor_sites": _slim_competitor_sites(full.get("competitor_sites")),
        "best_opportunities": [
            _kw_row(r, with_serp=True)
            for r in (full.get("best_opportunities") or [])[:10]
            if isinstance(r, dict)
        ],
        "strong_evergreen": [
            _kw_row(r) for r in (full.get("strong_evergreen") or [])[:8] if isinstance(r, dict)
        ],
        "head_terms": [
            _kw_row(r) for r in (full.get("head_terms") or [])[:8] if isinstance(r, dict)
        ],
        "trend_plays": _slim_kw_bucket(full.get("trend_plays"), limit=8),
        "avoid": _slim_kw_bucket(full.get("avoid"), limit=8),
        "clusters": clusters,
        "topic_plan": _slim_topic_plan(full.get("topic_plan")),
        "topics": [
            {
                "role": t.get("role"),
                "pillar": t.get("pillar") or t.get("title") or t.get("name"),
                "primary_keyword": t.get("primary_keyword") or t.get("keyword"),
                "title": t.get("title"),
                "intent": t.get("intent"),
                "opportunity_score": t.get("opportunity_score"),
                "supporting_keywords": list(t.get("supporting_keywords") or [])[:8],
            }
            for t in (full.get("topics") or [])[:10]
            if isinstance(t, dict)
        ],
        "cluster_report": (
            {
                "clusters": clusters,
                "service_clusters": [
                    {
                        "service": group.get("service"),
                        "service_key": group.get("service_key"),
                        "seed_count": group.get("seed_count"),
                        "keyword_count": group.get("keyword_count"),
                        "total_volume": group.get("total_volume"),
                        "seeds": [
                            {
                                "seed_index": seed.get("seed_index"),
                                "seed_label": seed.get("seed_label"),
                                "seed": seed.get("seed"),
                                "target": seed.get("target"),
                                "target_type": seed.get("target_type"),
                                "assignment_reason": seed.get("assignment_reason"),
                                "keyword_count": seed.get("keyword_count"),
                                "class_counts": seed.get("class_counts"),
                                "classes_missing": seed.get("classes_missing"),
                                "keywords": list(seed.get("keywords") or [])[:30],
                            }
                            for seed in (group.get("seeds") or [])[:20]
                            if isinstance(seed, dict)
                        ],
                    }
                    for group in (
                        (full.get("cluster_report") or {}).get("service_clusters") or []
                    )
                    if isinstance(group, dict)
                ],
            }
            if clusters
            else None
        ),
        "keyword_count": full.get("keyword_count"),
        "providers_used": full.get("providers_used"),
        "note": _trim(
            full.get("note")
            or "Shared memory essentials — full keyword report is in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_seo_strategy_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "executive_summary": _trim(full.get("executive_summary"), 400),
        "target_audience": full.get("target_audience"),
        "geographic_focus": full.get("geographic_focus"),
        "location_name": full.get("location_name"),
        "competitor_names": list(full.get("competitor_names") or [])[:8],
        "competitor_domains": list(full.get("competitor_domains") or [])[:8],
        "competitors": _slim_competitors(full.get("competitors")),
        "core_topics": [
            {
                "pillar": c.get("pillar") or c.get("name"),
                "primary_keyword": c.get("primary_keyword"),
                "intent": c.get("intent"),
                "opportunity_score": c.get("opportunity_score"),
                "est_words": c.get("est_words"),
                "clusters": _slim_core_topic_clusters(c.get("clusters")),
            }
            for c in (full.get("core_topics") or full.get("pillars") or [])[:8]
            if isinstance(c, dict)
        ],
        "priority_queue": [
            {
                "title": p.get("title") or p.get("keyword"),
                "keyword": p.get("keyword"),
                "priority": p.get("priority") or p.get("priority_label"),
                "intent": p.get("intent"),
                "funnel": p.get("funnel"),
                "volume": p.get("volume"),
                "difficulty": p.get("difficulty"),
                "opportunity_score": p.get("opportunity_score"),
                "content_type": p.get("content_type"),
                "est_words": p.get("est_words"),
                "suggested_url": p.get("suggested_url"),
                "action": p.get("action"),
                "beat_competitors": list(p.get("beat_competitors") or [])[:3] or None,
                "image_suggestions": list(p.get("image_suggestions") or [])[:12] or None,
            }
            for p in (full.get("priority_queue") or full.get("priority_pages") or [])[:12]
            if isinstance(p, dict)
        ],
        "priority_pages": [
            {
                "title": p.get("title") or p.get("keyword"),
                "keyword": p.get("keyword"),
                "priority": p.get("priority"),
                "suggested_url": p.get("suggested_url") or p.get("url"),
            }
            for p in (full.get("priority_pages") or full.get("priority_queue") or [])[:10]
            if isinstance(p, dict)
        ],
        "url_ia_plan": [
            {
                "url": u.get("url") or u.get("path"),
                "keyword": u.get("keyword"),
                "type": u.get("type") or u.get("page_type"),
            }
            for u in (full.get("url_ia_plan") or [])[:12]
            if isinstance(u, dict)
        ],
        "combined_priority_queue": [
            {
                "rank": q.get("rank"),
                "action": q.get("action"),
                "title": _trim(q.get("title"), 80),
                "keyword": q.get("keyword"),
                "source": q.get("source"),
                "priority": q.get("priority"),
            }
            for q in (full.get("combined_priority_queue") or [])[:15]
            if isinstance(q, dict)
        ],
        "both_skills_present": bool(full.get("both_skills_present")),
        "content_audit_applied": bool(full.get("content_audit_applied")),
        "content_gaps": [
            {
                "keyword": g.get("keyword"),
                "action": g.get("action"),
                "competitors": list(g.get("competitors") or [])[:3],
                "volume": g.get("volume"),
            }
            for g in (full.get("content_gaps") or full.get("competitor_content_gaps") or [])[:8]
            if isinstance(g, dict)
        ],
        "calendar_priorities": list(full.get("calendar_priorities") or [])[:6],
        "note": _trim(
            full.get("note") or "Shared memory essentials — full strategy report is in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_site_architecture_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    cs = full.get("current_state") if isinstance(full.get("current_state"), dict) else {}
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "executive_summary": _trim(full.get("executive_summary"), 400),
        "geographic_focus": full.get("geographic_focus"),
        "location_name": full.get("location_name"),
        "competitor_names": list(full.get("competitor_names") or [])[:8],
        "competitor_domains": list(full.get("competitor_domains") or [])[:8],
        "current_state": {
            k: cs.get(k)
            for k in (
                "urls_crawled",
                "max_click_depth",
                "depth_4_plus",
                "orphans",
                "avg_click_depth",
            )
            if cs.get(k) is not None
        }
        or None,
        "target_url_tree": [
            {
                "url": n.get("url") or n.get("path"),
                "path": n.get("path") or n.get("url"),
                "type": n.get("type") or n.get("page_type"),
                "keyword": n.get("keyword") or n.get("primary_keyword"),
                "parent": n.get("parent"),
                "depth": n.get("depth"),
                "title": _trim(n.get("title"), 60) or None,
            }
            for n in (full.get("target_url_tree") or [])[:30]
            if isinstance(n, dict)
        ],
        "redirect_map": [
            {
                "from": r.get("from") or r.get("source"),
                "to": r.get("to") or r.get("target"),
                "status": r.get("status") or 301,
            }
            for r in (full.get("redirect_map") or [])[:25]
            if isinstance(r, dict) and (r.get("from") or r.get("source"))
        ],
        "robots_facet_policy": _trim(full.get("robots_facet_policy"), 200) or None,
        "faceted_navigation": _trim(full.get("faceted_navigation"), 160)
        if isinstance(full.get("faceted_navigation"), str)
        else full.get("faceted_navigation"),
        "cluster_ownership": [
            {
                "cluster": o.get("cluster") or o.get("name"),
                "owner_url": o.get("owner_url") or o.get("url"),
                "intent": o.get("intent"),
            }
            for o in (full.get("cluster_ownership") or [])[:12]
            if isinstance(o, dict)
        ],
        "competitor_ia": [
            {
                "name": s.get("name"),
                "domain": s.get("domain"),
                "blog_count": s.get("blog_count"),
                "page_count": s.get("page_count"),
                "hub_paths": list(s.get("hub_paths") or [])[:6],
            }
            for s in (full.get("competitor_ia") or [])[:4]
            if isinstance(s, dict)
        ],
        "competitor_ia_notes": _trim(full.get("competitor_ia_notes"), 300) or None,
        "gate": full.get("gate"),
        "note": _trim(
            full.get("note")
            or "Shared memory essentials — full IA blueprint is in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_technical_seo_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    broken = full.get("broken_links")
    broken_count = full.get("broken_link_count")
    if broken_count is None and isinstance(broken, dict):
        broken_count = broken.get("broken_count")
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "score": full.get("score") or full.get("overall_score"),
        "severity": full.get("severity"),
        "themes": [
            {
                "theme": t.get("theme") or t.get("name"),
                "score": t.get("score"),
                "finding_count": t.get("finding_count") or len(t.get("findings") or []),
            }
            for t in (full.get("findings_by_theme") or full.get("themes") or [])[:8]
            if isinstance(t, dict)
        ],
        "priority_backlog": [
            {
                "priority": b.get("priority"),
                "issue": _trim(b.get("issue") or b.get("title"), 120),
                "fix": _trim(b.get("fix") or b.get("action"), 120),
            }
            for b in (full.get("priority_backlog") or full.get("priority_fixes") or [])[:10]
            if isinstance(b, dict)
        ],
        "broken_link_count": broken_count,
        "ia_actions": list(full.get("ia_actions") or full.get("redirect_actions") or [])[:8],
        "ia_notes": [
            _trim(n, 120) for n in (full.get("ia_notes") or [])[:6] if isinstance(n, str)
        ],
        "phase6_connected": bool(full.get("phase6_connected")),
        "upstream_phase": full.get("upstream_phase") or "site_architecture",
        "not_measured": [
            {
                "item": _trim(n.get("item"), 100),
                "requires": n.get("requires"),
            }
            for n in (full.get("not_measured") or [])[:6]
            if isinstance(n, dict)
        ],
        "specialist_routing": [
            {
                "route_to": r.get("route_to"),
                "status": r.get("status"),
            }
            for r in (full.get("specialist_routing") or [])[:8]
            if isinstance(r, dict)
        ],
        "js_rendering_risk": bool(full.get("js_rendering_risk")),
        "note": _trim(
            full.get("note") or "Shared memory essentials — full technical SEO report is in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_content_audit_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "summary_counts": full.get("summary_counts")
        if isinstance(full.get("summary_counts"), dict)
        else None,
        "data_mode": full.get("data_mode"),
        "qualitative": bool(full.get("qualitative")),
        "themes": [
            {
                "theme": t.get("theme"),
                "url_count": t.get("url_count"),
                "dispositions": t.get("dispositions"),
            }
            for t in (full.get("themes") or [])[:12]
            if isinstance(t, dict)
        ],
        "inventory": [
            {
                "url": i.get("url") or i.get("path"),
                "disposition": i.get("disposition"),
                "title": _trim(i.get("title"), 80),
                "keyword": i.get("keyword"),
                "theme": i.get("theme"),
                "reason": _trim(i.get("reason"), 80),
            }
            for i in (full.get("inventory") or [])[:20]
            if isinstance(i, dict)
        ],
        "refresh_queue": [
            {"url": i.get("url") or i.get("path"), "reason": _trim(i.get("reason"), 80)}
            for i in (full.get("refresh_queue") or [])[:8]
            if isinstance(i, dict)
        ],
        "review_queue": [
            {"url": i.get("url") or i.get("path"), "disposition": i.get("disposition")}
            for i in (full.get("review_queue") or [])[:8]
            if isinstance(i, dict)
        ],
        "cannibalization": [
            {
                "keyword": c.get("keyword") or c.get("query"),
                "action": c.get("action"),
                "keep": c.get("keep"),
                "merge_in": c.get("merge_in"),
                "urls": list(c.get("urls") or [])[:4],
            }
            for c in (full.get("cannibalization") or full.get("cannibalisation") or [])[:6]
            if isinstance(c, dict)
        ],
        "could_not_assess": list(full.get("could_not_assess") or [])[:6],
        "combined_priority_queue": [
            {
                "rank": q.get("rank"),
                "action": q.get("action"),
                "title": _trim(q.get("title"), 80),
                "keyword": q.get("keyword"),
                "source": q.get("source"),
            }
            for q in (full.get("combined_priority_queue") or [])[:15]
            if isinstance(q, dict)
        ],
        "both_skills_present": bool(full.get("both_skills_present")),
        "handoffs": [
            {"concern": h.get("concern"), "route_to": h.get("route_to")}
            for h in (full.get("handoffs") or [])[:6]
            if isinstance(h, dict)
        ],
        "note": _trim(
            full.get("note") or "Shared memory essentials — full content audit is in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_content_planning_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    rows = [
        {
            "url": r.get("url") or r.get("path") or r.get("url_n"),
            "url_n": r.get("url_n"),
            "keyword": r.get("keyword") or r.get("primary_keyword"),
            "primary_keyword": r.get("primary_keyword") or r.get("keyword"),
            "action": r.get("action"),
            "title": r.get("title"),
            "disposition": r.get("disposition"),
            "decision_basis": r.get("decision_basis"),
            "existing_content_check": r.get("existing_content_check"),
            "new_content_justification": r.get("new_content_justification"),
            "funnel": r.get("funnel"),
            "intent": r.get("intent"),
            "page_type": r.get("page_type"),
            "content_type": r.get("content_type") or r.get("content_type_label"),
            "supporting_keywords": list(
                r.get("supporting_keywords") or r.get("secondary_keywords") or []
            )[:8],
            "depth": r.get("depth"),
            "parent": r.get("parent") or r.get("parent_url_n"),
            "priority_tier": r.get("priority_tier"),
            "priority_rank": r.get("priority_rank"),
            "cannibal_conflict": r.get("cannibal_conflict"),
            "status": r.get("status") or "planned",
        }
        for r in (full.get("pages") or full.get("roadmap") or [])[:20]
        if isinstance(r, dict)
    ]
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "locked": full.get("locked"),
        "lock_reason": full.get("lock_reason"),
        "pages": rows,
        "roadmap": rows,
        "excluded": [
            {"url_n": e.get("url_n"), "reason": e.get("reason"), "source_pack": e.get("source_pack")}
            for e in (full.get("excluded") or [])[:12]
            if isinstance(e, dict)
        ],
        "summary": full.get("summary") if isinstance(full.get("summary"), dict) else None,
        "note": _trim(
            full.get("note") or "Shared memory essentials — full content roadmap is in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_content_production_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "briefs": [
            {
                "url": b.get("url"),
                "keyword": b.get("keyword"),
                "funnel": b.get("funnel"),
                "intent": b.get("search_intent") or b.get("intent"),
                "writer_ready": bool(b.get("writer_ready")),
                "title": _trim(b.get("title"), 80),
            }
            for b in (full.get("briefs") or [])[:10]
            if isinstance(b, dict)
        ],
        "drafts": [
            {
                "url": d.get("url"),
                "title": _trim(d.get("title"), 80),
                "funnel": d.get("funnel"),
                "status": d.get("status") or "stub",
            }
            for d in (full.get("drafts") or [])[:10]
            if isinstance(d, dict)
        ],
        "note": _trim(
            full.get("note") or "Shared memory essentials — full briefs/drafts are in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_on_page_seo_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "pages": [
            {
                "url": p.get("url") or p.get("page_url"),
                "title": _trim((p.get("title") or {}).get("after") if isinstance(p.get("title"), dict) else p.get("title"), 80),
                "h1": _trim(
                    (p.get("headings") or {}).get("h1")
                    if isinstance(p.get("headings"), dict)
                    else p.get("h1"),
                    80,
                ),
                "keyword": p.get("keyword") or p.get("target_keyword"),
                "schema_types": list(p.get("schema_types") or [])[:3],
            }
            for p in (full.get("pages") or full.get("queue") or [])[:12]
            if isinstance(p, dict)
        ],
        "internal_links": [
            {
                "from": ln.get("from") or ln.get("source"),
                "to": ln.get("to") or ln.get("target"),
                "anchor": _trim(ln.get("anchor"), 60),
                "reason": ln.get("reason"),
                "target_status": ln.get("target_status"),
            }
            for ln in (full.get("internal_links") or (full.get("internal_linking") or {}).get("links") or [])[:15]
            if isinstance(ln, dict)
        ],
        "orphans": [
            {"url": o.get("url"), "suggested_from": o.get("suggested_from")}
            for o in (full.get("orphans") or (full.get("internal_linking") or {}).get("orphans") or [])[:8]
            if isinstance(o, dict)
        ],
        "skills_used": list(full.get("skills_used") or [])[:6] or None,
        "trademark_blocked": [
            {"url": b.get("url"), "field": b.get("field"), "term": b.get("term")}
            for b in (full.get("trademark_blocked") or [])[:12]
            if isinstance(b, dict)
        ],
        "note": _trim(
            full.get("note") or "Shared memory essentials — full on-page package is in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_publishing_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "generated_at": full.get("generated_at"),
        "mode_requested": full.get("mode_requested"),
        "mode_effective": full.get("mode_effective"),
        "publish_queue": [
            {
                "url": q.get("url"),
                "status": q.get("status") or "simulated",
                "cms": q.get("cms") or "mock",
                "post_id": q.get("post_id"),
                "link": q.get("link"),
                "downgraded": q.get("downgraded"),
                "error": q.get("error"),
            }
            for q in (full.get("publish_queue") or [])[:12]
            if isinstance(q, dict)
        ],
        "verification": [
            {
                "slug": v.get("slug"),
                "verified": v.get("verified"),
                "stored_status": v.get("stored_status"),
                "action_required": v.get("action_required"),
            }
            for v in (full.get("verification") or [])[:12]
            if isinstance(v, dict)
        ],
        "capability_blockers": [
            {"blocker": b.get("blocker"), "detail": _trim(b.get("detail"), 120)}
            for b in (full.get("capability_blockers") or [])[:5]
            if isinstance(b, dict)
        ],
        "design": {
            k: (full.get("design") or {}).get(k)
            for k in ("brand_available", "brand_name", "primary_color", "reference_url")
            if (full.get("design") or {}).get(k) is not None
        }
        or None,
        "indexnow_url_count": len(
            ((full.get("indexnow_preview") or {}).get("urlList") or [])
            if isinstance(full.get("indexnow_preview"), dict)
            else []
        ),
        "gsc_recrawl": list(full.get("gsc_recrawl") or [])[:10],
        "qa_checklist": list(full.get("qa_checklist") or [])[:8],
        "note": _trim(
            full.get("note")
            or "Shared memory essentials — mock publish package; no live CMS write. Full report in chat.",
            200,
        ),
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}


def slim_competitor_memory(full: dict[str, Any] | None) -> dict[str, Any]:
    if not full:
        return {}
    baseline = full.get("client_baseline_maturity")
    if baseline is None and isinstance(full.get("client_baseline"), dict):
        baseline = (full.get("client_baseline") or {}).get("maturity_score")
    pack: dict[str, Any] = {
        "_memory_slim": True,
        "analysis_mode": full.get("analysis_mode"),
        "client_baseline_maturity": baseline,
        "competitors": _slim_competitors(full.get("competitors") or full.get("competitor_set")),
        "tier_overview": [
            {
                "name": t.get("name"),
                "tier": t.get("tier"),
                "tier_name": t.get("tier_name"),
                "score": t.get("score") or t.get("maturity_score"),
            }
            for t in (full.get("tier_overview") or full.get("tiers") or [])[:10]
            if isinstance(t, dict)
        ],
        "recommendations": full.get("recommendations")
        if isinstance(full.get("recommendations"), dict)
        else None,
        "note": "Shared memory essentials — full competitor scorecards are in chat.",
    }
    return {k: v for k, v in pack.items() if v not in (None, "", [], {})}
