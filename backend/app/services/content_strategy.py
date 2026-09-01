"""Content Strategy skill — topical authority, gaps, priority queue, 12-week calendar."""

from __future__ import annotations

import re
from typing import Any

from app.integrations.llm import synthesize_json, synthesize_text
from app.services.create_topic import funnel_balance, funnel_from_intent as _funnel_from_intent
from app.services.topic_naming import specific_page_title
from app.agents.prompts import skill_system_preamble


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "page"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _est_words(role: str, intent: str | None = None) -> int:
    if role == "pillar":
        return 3000
    if role == "cluster":
        return 1500
    if intent == "transactional":
        return 800
    return 1000


def _priority_label(row: dict[str, Any]) -> str:
    vol = row.get("volume")
    kd = row.get("difficulty")
    score = float(row.get("opportunity_score") or 0)
    bucket = str(row.get("bucket") or "")
    if bucket == "avoid" or score < 30:
        return "Avoid"
    low_kd = kd is not None and float(kd) < 35
    high_vol = vol is not None and float(vol) >= 1000
    high_kd = kd is not None and float(kd) >= 60
    if low_kd and score >= 50:
        return "Quick win"
    if high_vol and high_kd:
        return "Big bet"
    if (vol is None or float(vol) < 300) and (kd is None or float(kd) < 40):
        return "Fill-in"
    if score >= 55:
        return "Quick win"
    if high_vol:
        return "Big bet"
    return "Fill-in"


def _intent(row: dict[str, Any]) -> str:
    raw = str(row.get("intent") or "").lower()
    if raw in ("informational", "commercial", "transactional", "navigational"):
        return raw
    kw = _norm(str(row.get("keyword") or row.get("primary_keyword") or ""))
    if any(x in kw for x in ("buy", "pricing", "price", "cost", "hire", "demo")):
        return "transactional"
    if any(x in kw for x in ("best", "vs", "versus", "alternative", "review", "top ")):
        return "commercial"
    return "informational"


def image_suggestions_for(
    keyword: str,
    content_type: str,
    intent: str,
    *,
    industry: str | None = None,
    location: str | None = None,
) -> list[dict[str, Any]]:
    """Topic-specific hero + supporting image prompts for the priority queue."""
    kw = (keyword or "").strip() or "this topic"
    place = (location or "").strip() or "the client's market"
    vert = (industry or "").strip() or "this industry"
    ct = (content_type or "blog").lower()
    hero = {
        "role": "hero",
        "prompt": (
            f"Photorealistic editorial photograph for an article about '{kw}' in {vert}, {place}. "
            "Show a real workplace or customer scene that clearly belongs to this topic. "
            "No stock-photo smiles, no logos, no readable text, no watermarks, no collage."
        ),
    }
    if ct == "comparison" or " vs " in kw.lower():
        supporting = {
            "role": "supporting",
            "prompt": (
                f"Clean comparison infographic for '{kw}': two or three labeled columns, "
                "simple icons, white background, no fake brand logos, no tiny unreadable text."
            ),
        }
    elif ct == "listicle":
        supporting = {
            "role": "supporting",
            "prompt": (
                f"Numbered visual overview of the main considerations for '{kw}' as a simple "
                "diagram on a light background. No stock photography, no logos."
            ),
        }
    elif ct == "landing" or (intent or "").lower() == "transactional":
        supporting = {
            "role": "supporting",
            "prompt": (
                f"Simple process diagram of how a {vert} firm delivers '{kw}' for a client in "
                f"{place}: 3–4 labeled steps, flat vector style, no logos."
            ),
        }
    else:
        supporting = {
            "role": "supporting",
            "prompt": (
                f"Original process or architecture diagram illustrating '{kw}' for {vert} in "
                f"{place}. Clean labeled parts, white background, not stock, no logos."
            ),
        }
    return [hero, supporting]


def _content_type(intent: str, keyword: str = "") -> str:
    kw = _norm(keyword)
    if " vs " in kw or " versus " in kw:
        return "comparison"
    if kw.startswith("best ") or kw.startswith("top "):
        return "listicle"
    if intent == "transactional":
        return "landing"
    if intent == "commercial":
        return "guide"
    if kw.startswith("how to"):
        return "guide"
    return "blog"


def _suggested_path(keyword: str, intent: str, content_type: str) -> str:
    """Intent-aware URL path — do not force every idea under /blog/."""
    slug = _slug(keyword)
    i = (intent or "").lower()
    ct = (content_type or "").lower()
    if i == "transactional" or ct == "landing":
        if any(x in _norm(keyword) for x in ("pricing", "price", "cost")):
            return f"/pricing/{slug}/" if "pricing" not in slug else f"/{slug}/"
        return f"/services/{slug}/"
    if i == "commercial" or ct in ("comparison", "listicle"):
        return f"/compare/{slug}/" if ct == "comparison" else f"/guides/{slug}/"
    if i == "navigational":
        return f"/{slug}/"
    if ct == "guide":
        return f"/guides/{slug}/"
    return f"/blog/{slug}/"


def _cluster_volume(c: dict[str, Any]) -> Any:
    return c.get("est_traffic") if c.get("est_traffic") is not None else c.get("total_volume")


def _cluster_difficulty(c: dict[str, Any]) -> Any:
    return c.get("avg_difficulty") if c.get("avg_difficulty") is not None else c.get("difficulty")


def _title_from_keyword(
    kw: str,
    content_type: str,
    *,
    intent: str | None = None,
    industry: str | None = None,
    location: str | None = None,
    audience: str | None = None,
    client_name: str | None = None,
) -> str:
    return specific_page_title(
        kw,
        content_type=content_type,
        intent=intent,
        industry=industry,
        location=location,
        page_type=content_type,
        audience=audience,
        client_name=client_name,
    )


def build_authority_map(
    *,
    pillars: list[dict[str, Any]],
    report_clusters: list[dict[str, Any]],
    topic_plan: dict[str, Any] | None,
    industry: str | None = None,
    location: str | None = None,
) -> list[dict[str, Any]]:
    core: list[dict[str, Any]] = []
    if pillars:
        for p in pillars[:6]:
            if not isinstance(p, dict):
                continue
            pkw = str(p.get("primary_keyword") or p.get("name") or "")
            cluster_children = []
            # Attach matching keyword clusters as subtopics
            for c in report_clusters:
                if not isinstance(c, dict):
                    continue
                ckw = _norm(str(c.get("primary_keyword") or ""))
                if not ckw:
                    continue
                if pkw and (_norm(pkw) in ckw or ckw in _norm(pkw) or _norm(str(c.get("name") or "")) in _norm(str(p.get("name") or ""))):
                    support = [
                        {
                            "title": _title_from_keyword(
                                str(k.get("keyword")),
                                "blog",
                                intent=c.get("intent"),
                                industry=industry,
                                location=location,
                            ),
                            "keyword": k.get("keyword"),
                            "est_words": _est_words("supporting", c.get("intent")),
                        }
                        for k in (c.get("keywords") or [])
                        if isinstance(k, dict) and k.get("role") != "Primary" and k.get("keyword")
                    ][:5]
                    cluster_children.append(
                        {
                            "name": c.get("name"),
                            "primary_keyword": c.get("primary_keyword"),
                            "est_words": _est_words("cluster", c.get("intent")),
                            "supporting": support,
                            "recommended_url": c.get("recommended_url"),
                            "intent": c.get("intent"),
                        }
                    )
            # Fallback supporting from pillar keywords
            if not cluster_children:
                for sk in (p.get("supporting_keywords") or [])[:4]:
                    cluster_children.append(
                        {
                            "name": str(sk),
                            "primary_keyword": sk,
                            "est_words": _est_words("cluster"),
                            "supporting": [],
                        }
                    )
            core.append(
                {
                    "pillar": p.get("name") or pkw,
                    "primary_keyword": pkw,
                    "est_words": _est_words("pillar"),
                    "intent": p.get("intent"),
                    "opportunity_score": p.get("opportunity_score"),
                    "clusters": cluster_children[:6],
                }
            )
    elif report_clusters:
        # Treat top clusters as pillars
        for c in report_clusters[:4]:
            if not isinstance(c, dict):
                continue
            support = [
                {
                    "title": _title_from_keyword(
                        str(k.get("keyword")),
                        "blog",
                        intent=c.get("intent"),
                        industry=industry,
                        location=location,
                    ),
                    "keyword": k.get("keyword"),
                    "est_words": _est_words("supporting"),
                }
                for k in (c.get("keywords") or [])
                if isinstance(k, dict) and k.get("role") != "Primary"
            ][:5]
            core.append(
                {
                    "pillar": c.get("name"),
                    "primary_keyword": c.get("primary_keyword"),
                    "est_words": _est_words("pillar"),
                    "intent": c.get("intent"),
                    "opportunity_score": c.get("best_score"),
                    "clusters": [
                        {
                            "name": c.get("name"),
                            "primary_keyword": c.get("primary_keyword"),
                            "est_words": _est_words("cluster", c.get("intent")),
                            "supporting": support,
                            "recommended_url": c.get("recommended_url"),
                        }
                    ],
                }
            )
    elif topic_plan and topic_plan.get("topic_ideas"):
        ideas = [t for t in topic_plan["topic_ideas"] if isinstance(t, dict)]
        pillar_idea = ideas[0] if ideas else None
        if pillar_idea:
            core.append(
                {
                    "pillar": pillar_idea.get("title"),
                    "primary_keyword": pillar_idea.get("keyword"),
                    "est_words": _est_words("pillar"),
                    "intent": pillar_idea.get("intent"),
                    "clusters": [
                        {
                            "name": t.get("title"),
                            "primary_keyword": t.get("keyword"),
                            "est_words": _est_words("cluster", t.get("intent")),
                            "supporting": [],
                        }
                        for t in ideas[1:7]
                    ],
                }
            )
    return core


def _metric_index(
    *buckets: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map normalised keyword → richest volume/KD/score row from ranked buckets."""
    by_kw: dict[str, dict[str, Any]] = {}
    for rows in buckets:
        for r in rows:
            if not isinstance(r, dict):
                continue
            kw = str(r.get("keyword") or r.get("primary_keyword") or "").strip()
            if not kw:
                continue
            key = _norm(kw)
            prev = by_kw.get(key)
            if not prev:
                by_kw[key] = r
                continue
            # Prefer the row with the higher opportunity score, then volume.
            try:
                prev_score = float(prev.get("opportunity_score") or 0)
            except (TypeError, ValueError):
                prev_score = 0.0
            try:
                cur_score = float(r.get("opportunity_score") or 0)
            except (TypeError, ValueError):
                cur_score = 0.0
            if cur_score > prev_score:
                by_kw[key] = r
                continue
            try:
                prev_vol = float(prev.get("volume") or 0)
            except (TypeError, ValueError):
                prev_vol = 0.0
            try:
                cur_vol = float(r.get("volume") or 0)
            except (TypeError, ValueError):
                cur_vol = 0.0
            if cur_vol > prev_vol:
                by_kw[key] = r
    return by_kw


def _queue_row_from_source(
    *,
    kw: str,
    domain: str,
    industry: str | None,
    location: str | None,
    source: dict[str, Any],
    idea: dict[str, Any] | None = None,
    from_phase5: bool = False,
    force_priority: str | None = None,
) -> dict[str, Any]:
    idea = idea or {}
    intent = _intent({**source, **idea, "keyword": kw})
    ctype = str(idea.get("content_type") or source.get("content_type") or "").strip() or _content_type(
        intent, kw
    )
    pri = force_priority or _priority_label({**source, **idea, "keyword": kw})
    if pri == "Avoid" and source.get("bucket") != "avoid" and not from_phase5:
        if float(source.get("opportunity_score") or idea.get("opportunity_score") or 0) >= 35:
            pri = "Fill-in"
    path = _suggested_path(kw, intent, ctype)
    carried_title = str(idea.get("title") or source.get("title") or "").strip()
    secondaries = list(
        idea.get("secondary_keywords")
        or idea.get("supporting_keywords")
        or source.get("secondary_keywords")
        or source.get("supporting_keywords")
        or []
    )[:8]
    return {
        "title": carried_title
        or _title_from_keyword(
            kw,
            ctype,
            intent=intent,
            industry=industry,
            location=location,
        ),
        "from_phase5_topic": bool(from_phase5 and carried_title),
        "angle": idea.get("angle") or source.get("angle"),
        "keyword": kw,
        "primary_keyword": kw,
        "secondary_keywords": secondaries,
        "supporting_keywords": secondaries,
        "intent": intent,
        "funnel": idea.get("funnel")
        or source.get("funnel")
        or _funnel_from_intent(intent, kw),
        "priority": pri,
        "est_words": _est_words(
            "pillar" if pri == "Big bet" else "cluster",
            intent,
        ),
        "content_type": ctype,
        "volume": source.get("volume") if source.get("volume") is not None else idea.get("volume"),
        "difficulty": source.get("difficulty")
        if source.get("difficulty") is not None
        else idea.get("difficulty"),
        "opportunity_score": source.get("opportunity_score")
        if source.get("opportunity_score") is not None
        else idea.get("opportunity_score"),
        "business_fit": source.get("business_fit") or idea.get("business_fit"),
        "suggested_url": f"https://{domain}{path}",
        "beat_competitors": source.get("competitor_domains")
        or idea.get("competitor_domains")
        or [],
        "rationale": source.get("rationale") or idea.get("rationale"),
        "bucket": "phase5_topic" if from_phase5 else (source.get("bucket") or "best"),
        "match_class": idea.get("match_class") or source.get("match_class"),
        "image_suggestions": idea.get("image_suggestions")
        or image_suggestions_for(
            kw,
            ctype,
            intent,
            industry=industry,
            location=location,
        ),
    }


def build_priority_queue(
    *,
    best: list[dict[str, Any]],
    evergreen: list[dict[str, Any]],
    trends: list[dict[str, Any]],
    avoid: list[dict[str, Any]],
    report_clusters: list[dict[str, Any]],
    domain: str,
    industry: str | None = None,
    location: str | None = None,
    topic_plan: dict[str, Any] | None = None,
    topics: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build Phase 6 priority queue.

    When Phase 5 Create Topic produced ``topic_ideas``, those rows are the spine
    (order preserved). Ranked ``best_opportunities`` only supply volume/KD metrics
    and optional fill-ins — they must not displace approved topics.
    """
    metrics = _metric_index(best, evergreen, trends, avoid)
    for c in report_clusters:
        if not isinstance(c, dict):
            continue
        pkw = str(c.get("primary_keyword") or "").strip()
        if not pkw:
            continue
        key = _norm(pkw)
        if key not in metrics:
            metrics[key] = {
                "keyword": pkw,
                "volume": _cluster_volume(c),
                "difficulty": _cluster_difficulty(c),
                "opportunity_score": c.get("best_score") or c.get("opportunity_score"),
                "intent": c.get("intent"),
                "competitor_domains": c.get("competitor_domains") or [],
                "bucket": "cluster",
            }

    # Ordered Phase 5 ideas (deduped). Also accept flat ``topics`` if plan empty.
    ideas: list[dict[str, Any]] = []
    seen_ideas: set[str] = set()
    for idea in list((topic_plan or {}).get("topic_ideas") or []) + list(topics or []):
        if not isinstance(idea, dict):
            continue
        kw = str(idea.get("keyword") or idea.get("primary_keyword") or "").strip()
        title = str(idea.get("title") or "").strip()
        if not kw or not title:
            continue
        # Skip when "keyword" is clearly a pasted title / brand CTA (not a query).
        if len(kw) > 80 or " - " in kw and kw.lower().endswith("click trends"):
            continue
        nkw = _norm(kw)
        if nkw in seen_ideas:
            continue
        seen_ideas.add(nkw)
        ideas.append(idea)

    out: list[dict[str, Any]] = []
    seen_kw: set[str] = set()

    if ideas:
        for idea in ideas:
            kw = str(idea.get("keyword") or idea.get("primary_keyword") or "").strip()
            nkw = _norm(kw)
            if nkw in seen_kw:
                continue
            source = dict(metrics.get(nkw) or {})
            source.setdefault("bucket", "phase5_topic")
            # Boost approved topics so calendar/queue treat them as primary work.
            if source.get("opportunity_score") is None:
                source["opportunity_score"] = idea.get("opportunity_score") or 75
            row = _queue_row_from_source(
                kw=kw,
                domain=domain,
                industry=industry,
                location=location,
                source=source,
                idea=idea,
                from_phase5=True,
                force_priority="Quick win"
                if float(source.get("opportunity_score") or 75) >= 55
                else "Big bet",
            )
            out.append(row)
            seen_kw.add(nkw)

        # Optional fill-ins from ranked buckets — only keywords Phase 5 did not cover.
        fill: list[dict[str, Any]] = []
        for bucket, rows in (
            ("best", best),
            ("evergreen", evergreen),
            ("trend", trends),
        ):
            for r in rows:
                if not isinstance(r, dict) or not r.get("keyword"):
                    continue
                nkw = _norm(str(r.get("keyword")))
                if nkw in seen_kw:
                    continue
                fill.append({**r, "bucket": r.get("bucket") or bucket})
        fill.sort(
            key=lambda x: (
                -(float(x.get("opportunity_score") or 0)),
                -(float(x.get("volume") or 0)),
            )
        )
        for r in fill[: max(0, 25 - len(out))]:
            kw = str(r.get("keyword") or "").strip()
            nkw = _norm(kw)
            if not kw or nkw in seen_kw:
                continue
            row = _queue_row_from_source(
                kw=kw,
                domain=domain,
                industry=industry,
                location=location,
                source=r,
                idea={},
                from_phase5=False,
                force_priority="Fill-in",
            )
            out.append(row)
            seen_kw.add(nkw)
        return out[:25]

    # No Phase 5 topic plan — fall back to ranked buckets (legacy path).
    pool: list[dict[str, Any]] = []
    for bucket, rows in (
        ("best", best),
        ("evergreen", evergreen),
        ("trend", trends),
        ("avoid", avoid),
    ):
        for r in rows:
            if isinstance(r, dict) and r.get("keyword"):
                pool.append({**r, "bucket": r.get("bucket") or bucket})
    for c in report_clusters:
        if not isinstance(c, dict):
            continue
        pkw = str(c.get("primary_keyword") or "")
        if pkw and _norm(pkw) not in {_norm(str(r.get("keyword") or "")) for r in pool}:
            pool.append(
                {
                    "keyword": pkw,
                    "volume": _cluster_volume(c),
                    "difficulty": _cluster_difficulty(c),
                    "opportunity_score": c.get("best_score") or c.get("opportunity_score"),
                    "intent": c.get("intent"),
                    "bucket": "best",
                    "competitor_domains": c.get("competitor_domains") or [],
                }
            )

    queue: list[dict[str, Any]] = []
    for r in pool:
        kw = str(r.get("keyword") or "").strip()
        if not kw:
            continue
        queue.append(
            _queue_row_from_source(
                kw=kw,
                domain=domain,
                industry=industry,
                location=location,
                source=r,
                idea={},
                from_phase5=False,
            )
        )

    order = {"Quick win": 0, "Big bet": 1, "Fill-in": 2, "Avoid": 3}
    queue.sort(
        key=lambda x: (
            order.get(str(x.get("priority")), 9),
            -(float(x.get("opportunity_score") or 0)),
            -(float(x.get("volume") or 0)),
        )
    )
    for item in queue:
        k = _norm(str(item.get("keyword") or ""))
        if not k or k in seen_kw:
            continue
        seen_kw.add(k)
        out.append(item)
    return out[:25]


def build_calendar(priority_queue: list[dict[str, Any]], weeks: int = 12) -> list[dict[str, Any]]:
    """1 article/week minimum schedule across 12 weeks / 3 months."""
    actionable = [p for p in priority_queue if p.get("priority") != "Avoid"]
    if not actionable:
        actionable = priority_queue[:weeks]
    months: list[dict[str, Any]] = []
    labels = {1: "Foundation", 2: "Expansion", 3: "Authority"}
    idx = 0
    for m in range(1, 4):
        month_weeks = []
        for w in range(1, 5):
            if idx >= len(actionable):
                # recycle fill-ins
                item = actionable[idx % max(1, len(actionable))] if actionable else None
            else:
                item = actionable[idx]
            idx += 1
            if not item:
                continue
            week_num = (m - 1) * 4 + w
            if week_num > weeks:
                break
            role = "pillar" if w == 1 and m == 1 else item.get("content_type") or "cluster"
            month_weeks.append(
                {
                    "week": week_num,
                    "month_week": w,
                    "title": item.get("title"),
                    "keyword": item.get("keyword"),
                    "content_type": role,
                    "priority": item.get("priority"),
                    "est_words": item.get("est_words"),
                    "suggested_url": item.get("suggested_url"),
                }
            )
        months.append({"month": m, "label": labels.get(m, f"Month {m}"), "weeks": month_weeks})
    return months


def build_internal_linking(
    core_topics: list[dict[str, Any]],
    priority_queue: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for core in core_topics:
        pillar = str(core.get("pillar") or core.get("primary_keyword") or "")
        children = [str(c.get("name") or c.get("primary_keyword")) for c in (core.get("clusters") or [])]
        children = [c for c in children if c]
        if pillar and children:
            links.append({"from": pillar, "to": children[:5], "note": "Pillar hubs to cluster pages"})
            for child in children[:5]:
                links.append({"from": child, "to": [pillar], "note": "Cluster links up to pillar"})
    # Cross-link quick wins to related big bets
    quick = [p for p in priority_queue if p.get("priority") == "Quick win"][:5]
    bets = [p for p in priority_queue if p.get("priority") == "Big bet"][:3]
    for q in quick:
        targets = [str(b.get("title") or b.get("keyword")) for b in bets if b.get("keyword") != q.get("keyword")]
        if targets:
            links.append(
                {
                    "from": str(q.get("title") or q.get("keyword")),
                    "to": targets[:2],
                    "note": "Quick win supports big-bet pillars",
                }
            )
    return links[:30]


def build_gaps(
    best: list[dict[str, Any]],
    evergreen: list[dict[str, Any]],
    website: dict[str, Any],
    competitor_domains: list[str],
) -> list[dict[str, Any]]:
    existing_titles: set[str] = set()
    for key in ("top_pages", "pages", "crawled_pages"):
        raw = website.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    for fk in ("title", "url", "path"):
                        if item.get(fk):
                            existing_titles.add(_norm(str(item[fk])))
                elif item:
                    existing_titles.add(_norm(str(item)))

    gaps: list[dict[str, Any]] = []
    for row in best + evergreen:
        if not isinstance(row, dict) or not row.get("keyword"):
            continue
        kw = str(row["keyword"])
        covered = any(_norm(kw) in t or t in _norm(kw) for t in existing_titles) if existing_titles else False
        if covered and not row.get("gap_flag"):
            continue
        if row.get("gap_flag") or row.get("bucket") in ("best", "evergreen") or not covered:
            gaps.append(
                {
                    "keyword": kw,
                    "competitors": row.get("competitor_domains") or competitor_domains[:3],
                    "volume": row.get("volume"),
                    "opportunity_score": row.get("opportunity_score"),
                    "action": "Create new page" if not covered else "Upgrade existing page",
                    "rationale": row.get("rationale")
                    or ("Competitor gap" if row.get("gap_flag") else "Missing topical coverage"),
                    "existing_coverage": covered,
                }
            )
        if len(gaps) >= 15:
            break
    return gaps


def build_success_metrics(
    priority_queue: list[dict[str, Any]],
    core_topics: list[dict[str, Any]],
) -> dict[str, Any]:
    track = [
        str(p.get("keyword"))
        for p in priority_queue
        if p.get("priority") in ("Quick win", "Big bet") and p.get("keyword")
    ][:12]
    return {
        "organic_traffic_target": "Grow non-brand organic sessions 20–40% over 12 weeks after publishing cadence starts",
        "keywords_to_track": track,
        "content_production_kpis": [
            "Publish at least 1 strategic article per week (12 in 12 weeks)",
            f"Ship {min(4, len(core_topics))} pillar pages in Month 1–2",
            "Internal links: every cluster page links to its pillar",
            "Track rankings for quick-win + big-bet keywords biweekly",
        ],
    }


def build_url_ia(
    *,
    domain: str,
    core_topics: list[dict[str, Any]],
    report_clusters: list[dict[str, Any]],
    priority_queue: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for i, core in enumerate(core_topics):
        pkw = str(core.get("primary_keyword") or core.get("pillar") or f"pillar-{i}")
        plan.append(
            {
                "type": "pillar",
                "title": str(core.get("pillar") or pkw),
                "suggested_url": f"https://{domain}/blog/{_slug(pkw)}/",
                "primary_keyword": pkw,
                "nav_group": "Resources",
                "priority": i + 1,
                "est_words": core.get("est_words"),
            }
        )
        for j, c in enumerate(core.get("clusters") or []):
            ckw = str(c.get("primary_keyword") or c.get("name") or "")
            path = c.get("recommended_url") or f"/blog/{_slug(ckw)}"
            if not str(path).startswith("http"):
                path = f"https://{domain}{path if str(path).startswith('/') else '/' + path}"
            plan.append(
                {
                    "type": "cluster",
                    "title": str(c.get("name") or ckw),
                    "suggested_url": path,
                    "primary_keyword": ckw,
                    "parent": core.get("pillar"),
                    "nav_group": "Resources",
                    "priority": 10 + i * 10 + j,
                    "est_words": c.get("est_words"),
                }
            )
    if not plan and report_clusters:
        for i, c in enumerate(report_clusters[:12]):
            path = c.get("recommended_url") or f"/blog/{_slug(str(c.get('primary_keyword') or i))}"
            if not str(path).startswith("http"):
                path = f"https://{domain}{path if str(path).startswith('/') else '/' + path}"
            plan.append(
                {
                    "type": "cluster_page",
                    "title": c.get("name"),
                    "suggested_url": path,
                    "primary_keyword": c.get("primary_keyword"),
                    "content_type": c.get("content_type"),
                    "intent": c.get("intent"),
                    "nav_group": "Resources",
                    "priority": i + 1,
                }
            )
    # Add remaining quick wins not already in plan
    existing = {_norm(str(u.get("primary_keyword") or "")) for u in plan}
    for item in priority_queue:
        if item.get("priority") == "Avoid":
            continue
        k = _norm(str(item.get("keyword") or ""))
        if k in existing:
            continue
        plan.append(
            {
                "type": "supporting",
                "title": item.get("title"),
                "suggested_url": item.get("suggested_url"),
                "primary_keyword": item.get("keyword"),
                "nav_group": "Resources",
                "priority": 100 + len(plan),
                "content_type": item.get("content_type"),
            }
        )
        if len(plan) >= 24:
            break
    return plan


async def run_content_strategy_plan(
    *,
    client_name: str,
    primary_url: str,
    industry: str | None,
    commercial: dict[str, Any],
    marketing: dict[str, Any],
    competitive: dict[str, Any],
    website: dict[str, Any],
    demand: dict[str, Any],
    domain: str,
    content_audit: dict[str, Any] | None = None,
    site_architecture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intake = dict(marketing.get("client_intake") or {})
    audience = (
        commercial.get("target_demographic")
        or intake.get("target_demographic")
        or marketing.get("target_audience")
        or "Target buyers in the primary market"
    )
    if isinstance(audience, list):
        audience = ", ".join(str(a) for a in audience[:4])

    topic_plan = dict(demand.get("topic_plan") or {})
    best = list(demand.get("best_opportunities") or [])
    evergreen = list(demand.get("strong_evergreen") or [])
    trends = list(demand.get("trend_plays") or [])
    avoid = list(demand.get("avoid") or [])
    clusters = list(demand.get("clusters") or [])
    cluster_report = dict(demand.get("cluster_report") or {})
    report_clusters = list(cluster_report.get("clusters") or clusters)
    if not report_clusters and isinstance(demand.get("cluster_report"), dict):
        report_clusters = list((demand.get("cluster_report") or {}).get("clusters") or [])
    from app.integrations.llm import extract_domain

    competitor_domains: list[str] = []
    competitors = list(demand.get("competitors") or competitive.get("competitors") or [])
    competitor_names = [str(n) for n in (demand.get("competitor_names") or []) if n]
    competitor_sites = list(demand.get("competitor_sites") or [])

    for d in demand.get("competitor_domains") or []:
        nd = extract_domain(str(d)) if d else ""
        if not nd and d and "://" not in str(d) and "/" not in str(d):
            nd = str(d).lower().removeprefix("www.")
        if nd and nd not in competitor_domains:
            competitor_domains.append(nd)

    if not competitor_domains:
        for row in competitors:
            if not isinstance(row, dict):
                continue
            nd = extract_domain(str(row.get("url") or row.get("domain") or ""))
            if nd and nd not in competitor_domains:
                competitor_domains.append(nd)
            name = str(row.get("name") or "").strip()
            if name and name not in competitor_names:
                competitor_names.append(name)

    geo_focus = (
        demand.get("geographic_focus")
        or commercial.get("geographic_focus")
        or intake.get("geographic_focus")
        or ""
    )
    location_name = demand.get("location_name") or geo_focus or "United States"

    # Build pillars list for authority map (from Phase 5 topics / Create Topic ideas)
    topics = list(demand.get("topics") or [])
    if not any(
        isinstance(t, dict) and (t.get("primary_keyword") or t.get("keyword"))
        for t in topics
    ) and topic_plan.get("topic_ideas"):
        from app.services.create_topic import topics_from_plan

        topics = topics_from_plan(topic_plan)
    pillars: list[dict[str, Any]] = []
    for t in topics:
        if not isinstance(t, dict):
            continue
        pkw = str(t.get("primary_keyword") or t.get("keyword") or "").strip()
        name = str(t.get("pillar") or t.get("title") or t.get("name") or pkw).strip()
        if not pkw and not name:
            continue
        if t.get("role") == "pillar" or len(pillars) < 4:
            pillars.append(
                {
                    "name": name,
                    "primary_keyword": pkw,
                    "supporting_keywords": t.get("supporting_keywords") or [],
                    "intent": t.get("intent"),
                    "opportunity_score": t.get("opportunity_score"),
                    "competitor_domains": t.get("competitor_domains") or [],
                }
            )
        if len(pillars) >= 6:
            break

    core_topics = build_authority_map(
        pillars=pillars,
        report_clusters=report_clusters,
        topic_plan=topic_plan,
        industry=industry,
        location=location_name if location_name not in ("United States", None) else geo_focus,
    )
    priority_queue = build_priority_queue(
        best=best,
        evergreen=evergreen,
        trends=trends,
        avoid=avoid,
        report_clusters=report_clusters,
        domain=domain,
        industry=industry,
        location=location_name if location_name not in ("United States", None) else geo_focus,
        topic_plan=topic_plan,
        topics=topics,
    )
    content_gaps = build_gaps(best, evergreen, website, competitor_domains)
    # Enrich gaps with competitor blog titles when Phase 5 crawled them
    if competitor_sites and content_gaps:
        blog_hints: list[str] = []
        for snap in competitor_sites:
            label = snap.get("name") or snap.get("domain")
            for b in (snap.get("blogs") or snap.get("pages") or [])[:3]:
                if b.get("title"):
                    blog_hints.append(f"{label}: {b.get('title')}")
        if blog_hints:
            for g in content_gaps[:8]:
                g["competitor_blog_examples"] = blog_hints[:6]

    calendar = build_calendar(priority_queue, weeks=12)
    linking = build_internal_linking(core_topics, priority_queue)
    metrics = build_success_metrics(priority_queue, core_topics)
    url_ia = build_url_ia(
        domain=domain,
        core_topics=core_topics,
        report_clusters=report_clusters,
        priority_queue=priority_queue,
    )

    # Legacy-compatible fields for existing UI / Phase memory
    priority_pages = [
        {
            "keyword": p.get("keyword"),
            "bucket": "best"
            if p.get("priority") == "Quick win"
            else "evergreen"
            if p.get("priority") == "Fill-in"
            else "trend"
            if p.get("priority") == "Big bet"
            else "avoid",
            "priority_label": p.get("priority"),
            "suggested_url": p.get("suggested_url"),
            "opportunity_score": p.get("opportunity_score"),
            "volume": p.get("volume"),
            "difficulty": p.get("difficulty"),
            "rationale": p.get("rationale"),
            "beat_competitors": p.get("beat_competitors") or [],
            "title": p.get("title"),
            "intent": p.get("intent"),
            "est_words": p.get("est_words"),
            "content_type": p.get("content_type"),
        }
        for p in priority_queue
        if p.get("priority") != "Avoid"
    ][:15]

    calendar_priorities = [
        {
            "wave": m["month"],
            "focus": m["label"],
            "pages": [w.get("keyword") for w in m.get("weeks") or [] if w.get("keyword")],
        }
        for m in calendar
    ]

    executive_summary = (
        f"Content strategy for {client_name}: {len(core_topics)} core topic pillars, "
        f"{len(priority_queue)} prioritized pieces, 12-week calendar. "
        f"Geo: {location_name}. "
        f"Gaps vs {', '.join(competitor_names[:3] or competitor_domains[:3]) or 'competitors'} "
        "drive quick wins first, then big-bet pillars for topical authority."
    )

    plan = {
        "business_name": client_name,
        "target_audience": str(audience),
        "industry": industry,
        "primary_url": primary_url,
        "geographic_focus": geo_focus,
        "location_name": location_name,
        "executive_summary": executive_summary,
        "core_topics": core_topics,
        "content_gaps": content_gaps,
        "priority_queue": priority_queue,
        "content_calendar": calendar,
        "internal_linking": linking,
        "success_metrics": metrics,
        "competitors": competitors or [
            {"name": n, "domain": d}
            for n, d in zip(competitor_names, competitor_domains)
        ],
        "competitor_names": competitor_names,
        "competitor_domains": competitor_domains,
        "competitor_sites": competitor_sites,
        # Compatibility with existing CDP / cards
        "pillars": [
            {
                "name": c.get("pillar"),
                "primary_keyword": c.get("primary_keyword"),
                "supporting_keywords": [
                    x.get("primary_keyword") for x in (c.get("clusters") or []) if x.get("primary_keyword")
                ],
                "intent": c.get("intent"),
                "opportunity_score": c.get("opportunity_score"),
                "est_words": c.get("est_words"),
            }
            for c in core_topics
        ],
        "url_ia_plan": url_ia,
        "priority_pages": priority_pages,
        "competitor_content_gaps": content_gaps,
        "calendar_priorities": calendar_priorities,
        "clusters_used": len(report_clusters),
        "website_context": {"has_website_summary": bool(website)},
        "source_phase5_providers": demand.get("providers_used") or [],
        "skills_used": ["content_strategy"],
        "funnel_balance": funnel_balance(priority_queue),
        "source": "content_strategy_rules",
        "sibling_skill": "content-audit",
        "handoffs": [
            {
                "concern": "Existing URL decay / cannibalisation / prune",
                "route_to": "content-audit",
            },
            {
                "concern": "URL tree & redirect map",
                "route_to": "site-architecture",
            },
            {
                "concern": "Brief + draft a queue item",
                "route_to": "content-brief → create-content",
            },
        ],
    }

    # Prefer refresh over new URL when Phase 8 audit already ran
    audit = dict(content_audit or {})
    audit_overrides: list[dict[str, Any]] = []
    if audit.get("inventory") or audit.get("refresh_queue"):
        refresh_paths: set[str] = set()
        consolidate_paths: set[str] = set()
        for row in list(audit.get("inventory") or []) + list(audit.get("refresh_queue") or []):
            if not isinstance(row, dict):
                continue
            path = str(row.get("path") or row.get("url") or "").lower()
            disp = str(row.get("disposition") or "").lower()
            if disp == "refresh":
                refresh_paths.add(path)
            elif disp == "consolidate":
                consolidate_paths.add(path)
        for item in priority_queue:
            if not isinstance(item, dict):
                continue
            sug = str(item.get("suggested_url") or item.get("url") or "").lower()
            kw = str(item.get("keyword") or "").lower()
            matched = None
            for p in refresh_paths:
                if p and (p in sug or sug in p or (kw and kw in p)):
                    matched = "refresh"
                    break
            if not matched:
                for p in consolidate_paths:
                    if p and (p in sug or sug in p or (kw and kw in p)):
                        matched = "consolidate"
                        break
            if matched:
                item["action"] = matched
                item["priority_note"] = (
                    f"content-audit {matched.upper()} — prefer over new URL"
                )
                audit_overrides.append(
                    {
                        "keyword": item.get("keyword"),
                        "suggested_url": item.get("suggested_url"),
                        "action": matched,
                    }
                )
        # Boost refresh items toward front of queue
        priority_queue.sort(
            key=lambda x: (
                0 if str(x.get("action") or "") == "refresh" else 1,
                0 if x.get("priority") == "Quick win" else 1,
                0 if x.get("priority") == "Big bet" else 2,
            )
        )
        plan["priority_queue"] = priority_queue
        plan["priority_pages"] = priority_pages  # keep compat list; queue is canonical
        plan["audit_overrides"] = audit_overrides
        plan["content_audit_applied"] = True
        if audit_overrides and isinstance(plan.get("executive_summary"), str):
            plan["executive_summary"] = (
                plan["executive_summary"]
                + f" Applied {len(audit_overrides)} content-audit override(s) "
                "(refresh/consolidate preferred over new URLs)."
            )
        from app.services.content_queue import build_combined_priority_queue

        combined = build_combined_priority_queue(
            content_audit=audit,
            content_strategy=plan,
            site_architecture=dict(site_architecture or {}),
        )
        plan.update(combined)
        if combined.get("combined_priority_queue"):
            # Surface combined queue as the engagement priority list
            plan["priority_queue_strategy_only"] = list(priority_queue)
    else:
        plan["content_audit_applied"] = False
        plan["audit_note"] = (
            "Gap analysis plans *new* topics only. On established sites run "
            "content-audit first (GSC two periods). Without it, decay and "
            "cannibalisation cannot be assessed — this strategy is built on an "
            "unverified picture of what already exists."
        )
        from app.services.content_queue import build_combined_priority_queue

        plan.update(
            build_combined_priority_queue(
                content_audit=None,
                content_strategy=plan,
                site_architecture=dict(site_architecture or {}),
            )
        )

    # Optional LLM enrichment of summary + calendar polish
    skill = skill_system_preamble("content_strategy")
    try:
        system = (
            (skill[:12000] if skill else "You are a content strategist.")
            + "\nReturn ONLY JSON with keys: executive_summary (string), "
            "calendar_notes (string), extra_internal_links (array of {from,to}). "
            "Do not invent volumes. Prefer refresh over new URL when audit says so."
        )
        user = (
            f"Business: {client_name} | {industry}\nAudience: {audience}\n"
            f"Geographic focus: {geo_focus or 'not set'} → {location_name}\n"
            f"Do NOT invent other cities.\n"
            f"Shared-memory competitors: {', '.join(competitor_names[:6] or competitor_domains[:5])}\n"
            f"Competitor site/blog snapshots: "
            f"{[{'name': s.get('name'), 'blogs': s.get('blog_count'), 'pages': s.get('page_count')} for s in competitor_sites[:4]]}\n"
            f"Core topics: {core_topics[:3]}\n"
            f"Top priority queue: {priority_queue[:8]}\n"
            f"Gaps: {content_gaps[:6]}\n"
            "Write a sharp executive_summary (4-6 sentences) and optional calendar_notes. "
            "Compare only against the listed competitors."
        )
        parsed = await synthesize_json(system, user)
        if isinstance(parsed, dict):
            if parsed.get("executive_summary") and len(str(parsed["executive_summary"])) > 40:
                plan["executive_summary"] = str(parsed["executive_summary"]).strip()
            if parsed.get("calendar_notes"):
                plan["calendar_notes"] = parsed["calendar_notes"]
            extra = parsed.get("extra_internal_links")
            if isinstance(extra, list):
                for link in extra[:10]:
                    if isinstance(link, dict) and link.get("from"):
                        plan["internal_linking"].append(link)
                plan["source"] = "content_strategy_skill"
    except Exception:  # noqa: BLE001
        try:
            polished = await synthesize_text(
                "You are an SEO content strategist. Write 4-6 sentences.",
                f"Summarize strategy for {client_name}: pillars={len(core_topics)}, "
                f"queue={len(priority_queue)}, gaps={len(content_gaps)}, "
                f"competitors={competitor_domains[:3]}",
            )
            if polished and len(polished.strip()) > 40:
                plan["executive_summary"] = polished.strip()
        except Exception:  # noqa: BLE001
            pass

    from app.services.bw_workbook import attach_workbook_to_content_strategy

    return attach_workbook_to_content_strategy(plan, site_architecture=site_architecture)
