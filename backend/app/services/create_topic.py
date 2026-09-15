"""Create Topic skill — generate SearchFit-style topic plans grounded in keyword data."""

from __future__ import annotations

import re
from typing import Any

from app.integrations.llm import synthesize_json
from app.services.keyword_opportunity import (
    current_year,
    is_stale_year_keyword,
    prefer_present_year,
)
from app.services.topic_naming import (
    is_generic_label,
    phrase_title,
    specific_page_title,
    specific_pillar_label,
)

_YEAR = current_year()

# Headline framework: Interest = Curiosity + a BIG PROMISE. Pain-point angles
# lead the cycle so topic plans express the audience's problem first, then
# cover solution / education / comparison / trust angles.
_ANGLE_CYCLE = (
    ("pain-point", "blog", "informational", "TOFU", "Struggling With {kw}?"),
    ("how-to", "guide", "informational", "TOFU", "How to {kw}"),
    ("mistakes", "blog", "informational", "TOFU", "{kw} Mistakes to Avoid"),
    ("what-why", "guide", "informational", "TOFU", "What Is {kw}? A Clear Guide"),
    ("why-failing", "blog", "informational", "TOFU", "Why Your {kw} Isn't Working"),
    ("listicle", "listicle", "commercial", "MOFU", "Best {kw} Strategies"),
    ("comparison", "comparison", "commercial", "MOFU", "{kw}: Options Compared"),
    ("never-again", "guide", "commercial", "MOFU", "Never Struggle With {kw} Again"),
    ("case-study", "blog", "commercial", "MOFU", "{kw} Case Study: Results & Lessons"),
    ("trends", "blog", "informational", "TOFU", "{kw} Trends for " + str(_YEAR)),
    ("beginner", "guide", "informational", "TOFU", "{kw} for Beginners"),
    ("advanced", "guide", "commercial", "BOFU", "Advanced {kw}: Deep Dive"),
    ("templates", "tool", "transactional", "BOFU", "{kw} Templates & Frameworks"),
)

# Maps a topic angle onto a headline-framework template so each idea gets a
# distinct, grammatical shape (instead of every title collapsing to "How to …").
_ANGLE_TEMPLATE = {
    "pain-point": "pain_struggle",
    "how-to": "howto",
    "mistakes": "list_mistakes",
    "what-why": "what_is",
    "why-failing": "why_not_working",
    "listicle": "best_strategies",
    "comparison": "comparison_options",
    "never-again": "never_again",
    "case-study": "case_study",
    "trends": "trends",
    "beginner": "beginner",
    "advanced": "advanced",
    "templates": "templates",
}


def _title_under_60(raw: str) -> str:
    # Past years → present year; keep present year in titles
    t = prefer_present_year(re.sub(r"\s+", " ", (raw or "").strip()))
    if len(t) <= 60:
        return t
    return t[:57].rstrip() + "…"


def _kd_to_difficulty(kd: Any) -> str:
    if kd is None:
        return "medium"
    try:
        n = float(kd)
    except (TypeError, ValueError):
        return "medium"
    if n < 30:
        return "low"
    if n < 60:
        return "medium"
    return "high"


def _parse_args_from_message(message: str) -> dict[str, Any]:
    """Extract optional count / audience / funnel from chat text."""
    lowered = (message or "").lower()
    count = 10
    m = re.search(r"\b(\d{1,2})\s+topics?\b", lowered)
    if m:
        count = max(3, min(20, int(m.group(1))))
    audience = None
    am = re.search(r"audience[:\s]+([^\n.;]+)", message or "", flags=re.IGNORECASE)
    if am:
        audience = am.group(1).strip()[:120]
    funnel = "all"
    if re.search(r"\btofu\b", lowered):
        funnel = "TOFU"
    elif re.search(r"\bmofu\b", lowered):
        funnel = "MOFU"
    elif re.search(r"\bbofu\b", lowered):
        funnel = "BOFU"
    return {"count": count, "audience": audience, "funnel": funnel}


def _norm_kw(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def format_audience_label(audience: Any) -> str | None:
    """Flatten CDD demographic blobs into a short readable audience label."""
    if audience is None:
        return None
    if isinstance(audience, str):
        text = audience.strip()
        return text or None
    if isinstance(audience, list):
        parts = [format_audience_label(x) for x in audience[:4]]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    if not isinstance(audience, dict):
        text = str(audience).strip()
        return text or None

    primary = audience.get("primary") if isinstance(audience.get("primary"), dict) else audience
    bits: list[str] = []
    titles = primary.get("job_titles") if isinstance(primary, dict) else None
    if isinstance(titles, list) and titles:
        bits.append(", ".join(str(t).strip() for t in titles[:3] if str(t).strip()))
    age = primary.get("age_range") if isinstance(primary, dict) else None
    if age:
        bits.append(f"ages {age}")
    gender = primary.get("gender") if isinstance(primary, dict) else None
    if gender and str(gender).strip().upper() not in {"M & F", "M&F", "ALL"}:
        bits.append(str(gender).strip())
    if not bits:
        for key in ("role", "label", "name", "description"):
            val = audience.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None
    return " · ".join(bits)


def _pool_by_keyword(pool: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in pool:
        kw = str(row.get("keyword") or "").strip()
        key = _norm_kw(kw)
        if kw and key and key not in out:
            out[key] = row
    return out


def _ground_primary_keyword(
    candidate: str,
    *,
    pool_by_kw: dict[str, dict[str, Any]],
    used: set[str],
) -> tuple[str, dict[str, Any] | None]:
    """Accept only an exact seeded keyword. No soft remap to unrelated rows."""
    key = _norm_kw(candidate)
    if key and key in pool_by_kw and key not in used:
        row = pool_by_kw[key]
        return str(row.get("keyword") or candidate).strip(), row
    return "", None


def _secondary_keywords_for(
    primary_keyword: str,
    *,
    pool: list[dict[str, Any]],
    limit: int = 8,
) -> list[str]:
    """Supporting keywords from the same seed/target family — never cross-service.

    Ranking: same seed+intent+class → same seed → same target → token overlap
    within the same seed/target only. Global same-intent matching is forbidden
    (that is what mixed 'google ads' secondaries into Meta / Instagram topics).
    """
    primary = _norm_kw(primary_keyword)
    if not primary:
        return []
    head = next((row for row in pool if _norm_kw(row.get("keyword")) == primary), None)
    if not head:
        return []
    parent = _norm_kw(head.get("parent_topic"))
    seed = _norm_kw(head.get("seed"))
    target = _norm_kw(head.get("target"))
    intent = _norm_kw(head.get("intent"))
    match_class = _norm_kw(head.get("match_class"))
    primary_tokens = {t for t in primary.split() if len(t) > 2}
    scored: list[tuple[int, str]] = []
    seen = {primary}
    for row in pool:
        kw = str(row.get("keyword") or "").strip()
        nkw = _norm_kw(kw)
        if not kw or nkw in seen:
            continue
        row_seed = _norm_kw(row.get("seed"))
        row_target = _norm_kw(row.get("target"))
        same_seed = bool(seed and row_seed == seed)
        same_target = bool(target and row_target == target)
        same_parent = bool(parent and _norm_kw(row.get("parent_topic")) == parent)
        if not (same_seed or same_target or same_parent):
            continue
        score = 0
        if same_seed:
            score += 40
        if same_target:
            score += 30
        if same_parent:
            score += 20
        if intent and _norm_kw(row.get("intent")) == intent:
            score += 15
        if match_class and _norm_kw(row.get("match_class")) == match_class:
            score += 10
        overlap = len(primary_tokens & {t for t in nkw.split() if len(t) > 2})
        score += min(12, overlap * 4)
        # Prefer specific phrases over ultra-broad heads as secondaries
        if len(nkw.split()) <= 2:
            score -= 8
        scored.append((score, kw))
        seen.add(nkw)
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [kw for _, kw in scored[:limit]]


def _shape_from_keyword(
    row: dict[str, Any],
    *,
    index: int = 0,
    pain_point: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """Shape a topic from the Comprehensive Headline Framework.

    Pipeline (create-topic/references/headline-framework-topic-strategy.md):
      keyword + intent + match_class
        → traffic temperature (cold/warm/hot)
        → funnel + content format + template
        → curiosity + benefit title (honest — no invented proof)
    """
    from app.services.headline_framework import (
        pick_primary_headline,
        traffic_temperature,
    )

    kw = str(row.get("keyword") or "").strip()
    intent = _norm_kw(row.get("intent") or "informational")
    match_class = _norm_kw(row.get("match_class") or "related")
    nkw = _norm_kw(kw)
    traffic = traffic_temperature(intent)
    service = str(row.get("service") or row.get("target") or row.get("seed") or "").strip()

    if intent == "transactional":
        funnel = "BOFU"
        angle_options = ("templates", "advanced", "never-again")
        content_type = "tool"
    elif intent == "commercial":
        funnel = "MOFU"
        angle_options = ("listicle", "comparison", "case-study", "never-again")
        content_type = "listicle"
    else:
        funnel = "TOFU"
        # Cold traffic: problem identification first, then how-to / mistakes
        angle_options = ("pain-point", "mistakes", "how-to", "what-why", "why-failing")
        content_type = "guide"

    if match_class == "exact" and intent == "informational":
        angle_cat = "what-why" if index % 2 == 0 else "pain-point"
    elif match_class == "exact" and intent == "commercial":
        angle_cat = "listicle"
    elif match_class == "phrase":
        angle_cat = "how-to" if intent == "informational" else "comparison"
    elif match_class == "related":
        angle_cat = "mistakes" if intent == "informational" else "case-study"
    elif match_class == "broad" and intent == "commercial":
        angle_cat = "comparison"
    else:
        angle_cat = angle_options[index % len(angle_options)]

    # Keywords that already encode the query shape keep that shape in the title
    if nkw.startswith(("how to", "what is", "best ", "top ")) or " vs " in nkw:
        title = _title_under_60(phrase_title(kw))
        if nkw.startswith("best ") or nkw.startswith("top ") or " vs " in nkw:
            funnel = "MOFU" if funnel == "TOFU" else funnel
            intent = intent if intent != "informational" else "commercial"
            traffic = traffic_temperature(intent)
            content_type = "listicle" if " vs " not in nkw else "comparison"
            angle_cat = "comparison" if " vs " in nkw else "listicle"
    else:
        cycle = next((a for a in _ANGLE_CYCLE if a[0] == angle_cat), _ANGLE_CYCLE[0])
        content_type = cycle[1]
        title = _title_under_60(
            pick_primary_headline(
                kw,
                page_type=content_type,
                content_type=content_type,
                intent=intent,
                audience=audience,
                prefer_template=_ANGLE_TEMPLATE.get(angle_cat),
                pain_point=pain_point,
            )
        )

    benefit = phrase_title(kw)
    if pain_point:
        benefit = f"relief from {pain_point.strip()[:80]}"

    return {
        "intent": intent,
        "funnel": funnel,
        "type": content_type,
        "angle_category": angle_cat,
        "title": title,
        "match_class": match_class,
        "traffic": traffic,
        "content_format": content_type,
        "core_topic": service or kw,
        "benefit": benefit,
        "angle": (
            f"Framework {traffic}-traffic / {match_class} / {intent}: "
            f"{angle_cat} template for '{kw}'"
            + (f" under {service}" if service else "")
            + "."
        ),
        "why": (
            f"Blog required inputs: keyword='{kw}', intent={intent}, "
            f"type={match_class}, traffic={traffic}, format={content_type}"
            + (f", audience={audience}" if audience else "")
            + (f", pain={pain_point[:60]}" if pain_point else "")
            + ". Curiosity + benefit title; no invented proof claims."
        ),
    }


def _fallback_topic_plan(
    *,
    seed: str,
    count: int,
    audience: str | None,
    funnel: str,
    best: list[dict[str, Any]],
    evergreen: list[dict[str, Any]],
    competitor_domains: list[str],
    products: list[str] | None = None,
    pain_points: list[str] | None = None,
) -> dict[str, Any]:
    # Prefer gap + specific rows; skip stale years
    pool = [
        r
        for r in (list(best) + [r for r in evergreen if r not in best])
        if not is_stale_year_keyword(str(r.get("keyword") or ""))
    ]
    pool.sort(
        key=lambda r: (
            1 if r.get("gap_flag") else 0,
            float(r.get("gap_score") or 0),
            float(r.get("specificity") or 0),
            float(r.get("opportunity_score") or 0),
        ),
        reverse=True,
    )
    if not pool:
        pool = [{"keyword": seed, "volume": None, "difficulty": None, "opportunity_score": None}]

    pains = [p.strip() for p in (pain_points or []) if p and p.strip()]

    ideas: list[dict[str, Any]] = []
    for i in range(count):
        row = pool[i % len(pool)]
        kw = prefer_present_year(str(row.get("keyword") or seed).strip())
        pain = pains[i % len(pains)] if pains else None
        angle_cat, ctype, intent, stage, _title_tmpl = _ANGLE_CYCLE[i % len(_ANGLE_CYCLE)]
        if funnel != "all" and stage != funnel:
            stage = funnel
        # Prefer commercial/transactional angles for service keywords
        if products and any(p.lower() in kw.lower() for p in products if p):
            if angle_cat in ("trends", "beginner") and i % 2 == 0:
                angle_cat, ctype, intent, stage, _title_tmpl = _ANGLE_CYCLE[2]  # listicle
        # Preserve keywords that are already well-shaped queries; otherwise build a
        # grounded, grammatical title via the headline framework, letting the angle
        # pick the template so repeated keywords still yield varied titles.
        if kw.lower().startswith(("how to", "what is", "best ", "top ")) or " vs " in kw.lower():
            title = _title_under_60(phrase_title(kw))
        else:
            title = _title_under_60(
                specific_page_title(
                    kw,
                    content_type=ctype,
                    intent=intent,
                    template_hint=_ANGLE_TEMPLATE.get(angle_cat),
                    pain_point=pain,
                )
            )
        comps = row.get("competitor_domains") or competitor_domains[:3]
        why = (
            f"Supports {audience or 'the target audience'} researching {kw} "
            f"with a {angle_cat.replace('-', ' ')} angle."
        )
        if pain:
            why = (
                f"Addresses the pain point '{pain}' for "
                f"{audience or 'the target audience'} researching {kw} "
                f"({angle_cat.replace('-', ' ')} angle)."
            )
        if row.get("gap_flag") or comps:
            why += (
                f" Competitors ({', '.join(str(c) for c in comps[:2]) or 'peers'}) "
                "already rank here — client coverage is thin."
            )
        secondary_keywords = _secondary_keywords_for(kw, pool=pool)
        ideas.append(
            {
                "title": title,
                "keyword": kw,
                "primary_keyword": kw,
                "secondary_keywords": secondary_keywords,
                "supporting_keywords": secondary_keywords,
                "intent": row.get("intent") or intent,
                "funnel": stage,
                "type": ctype,
                "difficulty": _kd_to_difficulty(row.get("difficulty")),
                "why": why,
                "angle": (
                    f"Differentiate vs {', '.join(str(c) for c in comps[:2]) or 'competitors'} "
                    f"with practical, service-specific {angle_cat} content."
                ),
                "key_sections": [
                    f"What {kw} means in practice",
                    "Step-by-step approach",
                    "Common pitfalls",
                    "Tools and next actions",
                ],
                "angle_category": angle_cat,
                "volume": row.get("volume"),
                "opportunity_score": row.get("opportunity_score"),
                "competitor_domains": list(comps)[:5],
                "gap_flag": bool(row.get("gap_flag")),
            }
        )

    pillar = ideas[0]["title"] if ideas else seed
    supporting = [
        {"title": t["title"], "publish_first": i == 0}
        for i, t in enumerate(ideas[1:6])
    ]
    return {
        "seed": seed,
        "audience": audience,
        "funnel_filter": funnel,
        "topic_ideas": ideas,
        "funnel_balance": funnel_balance(ideas),
        "cluster_map": {"pillar": pillar, "supporting": supporting},
        "publishing_order": [t["title"] for t in ideas],
        "internal_linking": [],
        "source": "fallback_rules",
    }


def funnel_balance(ideas: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"TOFU": 0, "MOFU": 0, "BOFU": 0}
    for idea in ideas:
        stage = str(idea.get("funnel") or "TOFU").upper()
        if stage in counts:
            counts[stage] += 1
    return counts


def funnel_from_intent(intent: str | None, keyword: str = "") -> str:
    """Map intent → TOFU/MOFU/BOFU (Architecture v1.9 funnel mapping).

    Single source of truth for downstream phases (Content Strategy, Content
    Brief) that need a funnel tag but weren't handed one by their input pack.
    """
    i = (intent or "").lower()
    kw = (keyword or "").lower()
    if i == "transactional" or any(x in kw for x in ("buy", "pricing", "cost", "near me", "hire")):
        return "BOFU"
    if i == "commercial" or any(x in kw for x in ("vs", "versus", "alternative", "compare", "best")):
        return "MOFU"
    return "TOFU"


def funnel_balance_warnings(counts: dict[str, int]) -> list[str]:
    """Flag funnel stages missing from a generated topic/brief set.

    MoFu (comparison, alternatives, decision-support) is the layer most often
    dropped — call it out by name so operators notice before publishing a
    TOFU/BOFU-only plan.
    """
    total = sum(counts.values())
    if total == 0:
        return []
    warnings: list[str] = []
    for stage in ("TOFU", "MOFU", "BOFU"):
        if counts.get(stage, 0) == 0:
            label = "MoFu (comparison/alternatives/decision-support)" if stage == "MOFU" else stage
            warnings.append(f"No {label} topics in this plan — funnel is unbalanced.")
    return warnings


def _normalize_plan(raw: dict[str, Any], *, seed: str, count: int) -> dict[str, Any]:
    ideas_in = raw.get("topic_ideas") or raw.get("topics") or []
    ideas: list[dict[str, Any]] = []
    if isinstance(ideas_in, list):
        for item in ideas_in[:count]:
            if not isinstance(item, dict):
                continue
            primary_kw = prefer_present_year(
                str(item.get("primary_keyword") or item.get("keyword") or seed).strip()
            )
            title_raw = prefer_present_year(
                str(item.get("title") or item.get("primary_keyword") or item.get("keyword") or seed)
            )
            if is_stale_year_keyword(primary_kw):
                continue
            if is_generic_label(title_raw) and primary_kw:
                title_raw = specific_page_title(
                    primary_kw,
                    content_type=str(item.get("type") or item.get("content_type") or "blog"),
                    intent=str(item.get("intent") or "") or None,
                )
            title = _title_under_60(title_raw)
            secondary = [
                str(s).strip()
                for s in (item.get("secondary_keywords") or item.get("supporting_keywords") or [])
                if str(s).strip() and _norm_kw(s) != _norm_kw(primary_kw)
            ][:8]
            ideas.append(
                {
                    "title": title,
                    "keyword": primary_kw,
                    "primary_keyword": primary_kw,
                    "secondary_keywords": secondary,
                    "supporting_keywords": secondary,
                    "intent": str(item.get("intent") or "informational").lower(),
                    "funnel": str(item.get("funnel") or "TOFU").upper(),
                    "type": str(item.get("type") or "guide").lower(),
                    "difficulty": str(item.get("difficulty") or "medium").lower(),
                    "why": str(item.get("why") or ""),
                    "angle": str(item.get("angle") or ""),
                    "key_sections": list(item.get("key_sections") or [])[:5],
                    "angle_category": item.get("angle_category"),
                    "volume": item.get("volume"),
                    "opportunity_score": item.get("opportunity_score"),
                    "competitor_domains": list(item.get("competitor_domains") or [])[:5],
                    "gap_flag": bool(item.get("gap_flag")),
                }
            )
    cluster = raw.get("cluster_map") or {}
    if not isinstance(cluster, dict):
        cluster = {}
    pillar = str(cluster.get("pillar") or (ideas[0]["title"] if ideas else seed))
    supporting = cluster.get("supporting") or []
    if not isinstance(supporting, list):
        supporting = []
    return {
        "seed": str(raw.get("seed") or seed),
        "audience": raw.get("audience"),
        "funnel_filter": raw.get("funnel_filter") or "all",
        "topic_ideas": ideas,
        "funnel_balance": funnel_balance(ideas),
        "cluster_map": {"pillar": pillar, "supporting": supporting},
        "publishing_order": raw.get("publishing_order") or [t["title"] for t in ideas],
        "internal_linking": raw.get("internal_linking") or [],
        "source": "create_topic_skill",
    }


def topics_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idea in plan.get("topic_ideas") or []:
        if not isinstance(idea, dict):
            continue
        out.append(
            {
                "role": "pillar" if not out else "supporting",
                "pillar": specific_pillar_label(
                    str(idea.get("title") or ""),
                    str(idea.get("primary_keyword") or idea.get("keyword") or ""),
                    intent=idea.get("intent"),
                ),
                "primary_keyword": idea.get("primary_keyword") or idea.get("keyword"),
                "title": idea.get("title"),
                "supporting_keywords": list(
                    idea.get("supporting_keywords") or idea.get("secondary_keywords") or []
                )[:8],
                "intent": idea.get("intent"),
                "opportunity_score": idea.get("opportunity_score"),
                "competitor_domains": idea.get("competitor_domains") or [],
                "volume": idea.get("volume"),
                "gap_flag": idea.get("gap_flag"),
            }
        )
    return out


async def run_create_topic(
    *,
    seed: str,
    message: str = "",
    audience: str | None = None,
    count: int | None = None,
    funnel: str | None = None,
    best_opportunities: list[dict[str, Any]] | None = None,
    strong_evergreen: list[dict[str, Any]] | None = None,
    keyword_pool: list[dict[str, Any]] | None = None,
    competitor_domains: list[str] | None = None,
    competitor_names: list[str] | None = None,
    geographic_focus: str | None = None,
    location_name: str | None = None,
    competitor_context: str | None = None,
    industry: str | None = None,
    products: list[str] | None = None,
    cdd_keywords: list[str] | None = None,
    clusters: list[dict[str, Any]] | None = None,
    pain_points: list[str] | None = None,
) -> dict[str, Any]:
    args = _parse_args_from_message(message)
    n = count or args["count"]
    aud = format_audience_label(audience) or args["audience"]
    fun = funnel or args["funnel"]
    best = [
        r
        for r in (best_opportunities or [])
        if not is_stale_year_keyword(str(r.get("keyword") or ""))
    ]
    evergreen = [
        r
        for r in (strong_evergreen or [])
        if not is_stale_year_keyword(str(r.get("keyword") or ""))
    ]
    # Authoritative pool = multi-mode seeded keywords (or the prompt rows).
    # Topic primary/secondary keywords must snap back to this set.
    source_pool = [
        r
        for r in (keyword_pool or best or evergreen)
        if isinstance(r, dict)
        and str(r.get("keyword") or "").strip()
        and not is_stale_year_keyword(str(r.get("keyword") or ""))
    ]
    if not source_pool:
        source_pool = list(best) + [r for r in evergreen if r not in best]
    pool_by_kw = _pool_by_keyword(source_pool)
    comps = competitor_domains or []
    cnames = competitor_names or []
    services = products or []
    cdd_kws = cdd_keywords or []
    topic_pool = list(source_pool)

    from app.agents.prompts import load_skill_file

    skill = load_skill_file("create-topic")
    system = (
        (skill[:12000] if skill else "You are a topic researcher.")
        + "\n\nReturn ONLY valid JSON matching the Structured JSON schema in the skill.\n"
        "FRESHNESS RULE: Drop past years (e.g. 2024/2025). "
        f"USE the present year ({_YEAR}) in trend / roundup / annual titles when relevant. "
        f"Prefer '{_YEAR}' framing over evergreen-only when the keyword is timely.\n"
        "GEO RULE: Use only the provided geographic_focus / location_name for local angles. "
        "Never invent other cities.\n"
        "COMPETITOR GAP RULE: Prefer keywords where listed competitors rank and the client does not. "
        "Say so in why/angle.\n"
        "SPECIFICITY RULE: Prefer specific service keywords over ultra-generic heads. "
        "Ground topics in client services and CDD keywords.\n"
        "TITLE RULE: Never name a topic with a single generic word (SEO, Marketing, Business, Content). "
        "Use the exact PRIMARY multi-word keyword phrase as the title core. "
        "Do NOT invent filler like 'The Complete Guide to …' unless the keyword is already that query.\n"
        "INTENT RULE: Every topic must include accurate search intent and funnel.\n"
        "KEYWORD RULE: `primary_keyword` MUST be copied EXACTLY from the provided "
        "seeded keyword list (character-for-character). Never invent, paraphrase, "
        "or brand-swap keywords. `secondary_keywords` must also be exact phrases "
        "from that same list AND from the same seed/target as the primary.\n"
        "INTENT + TYPE RULE: Respect each row's `intent` (informational/commercial/"
        "transactional) and `match_class` (exact/phrase/related/broad). "
        "Informational → TOFU guides; commercial → MOFU comparisons/listicles; "
        "transactional → BOFU tools/templates. Exact = pillar definition; "
        "phrase = how-to; related = mistakes/supporting; broad = only as comparison "
        "when commercial.\n"
        "HEADLINE RULE (Interest = Curiosity + Big Promise): every title must trigger "
        "curiosity AND promise a specific, strongly desired benefit. Lead the plan with "
        "pain-point angles — express the audience's problem in the title or angle "
        "(struggling-with, why-it-isn't-working, mistakes, never-again, warning-signs shapes). "
        "'How to X' is allowed ONLY when X is an action (verb phrase); never 'How To Content Marketing'.\n"
        "HONESTY RULE: Never invent statistics, authority names, social proof counts, "
        "guarantees, or outcomes. Specificity must come only from the provided keyword "
        "data, services, audience, pain points, and geography."
    )
    # Fixed primaries from multi-mode seeding — Topic Plan cannot invent outside this list.
    assigned_rows = (best or topic_pool)[:n]
    assigned_keywords = [
        str(r.get("keyword") or "").strip()
        for r in assigned_rows
        if str(r.get("keyword") or "").strip()
    ]
    kw_lines = []
    for row in assigned_rows:
        kw_lines.append(
            {
                "keyword": row.get("keyword"),
                "volume": row.get("volume"),
                "difficulty": row.get("difficulty"),
                "opportunity_score": row.get("opportunity_score"),
                "bucket": row.get("bucket"),
                "intent": row.get("intent"),
                "gap_flag": row.get("gap_flag"),
                "gap_score": row.get("gap_score"),
                "specificity": row.get("specificity"),
                "business_fit": row.get("business_fit"),
                "competitor_domains": row.get("competitor_domains"),
                "competitor_positions": row.get("competitor_positions"),
                "rationale": row.get("rationale"),
                "seed": row.get("seed"),
                "match_class": row.get("match_class"),
            }
        )
    cluster_bits = []
    for c in (clusters or [])[:8]:
        if isinstance(c, dict):
            cluster_bits.append(
                {
                    "name": c.get("name"),
                    "intent": c.get("intent"),
                    "funnel": c.get("funnel"),
                    "primary_keyword": c.get("primary_keyword"),
                }
            )
    user = (
        f"ARGUMENTS.seed = {seed}\n"
        f"ARGUMENTS.count = {len(assigned_keywords) or n}\n"
        f"ARGUMENTS.audience = {aud or ''}\n"
        f"ARGUMENTS.funnel = {fun}\n"
        f"Industry: {industry or 'unknown'}\n"
        f"Client services / products (from CDD): {', '.join(services) or 'none'}\n"
        f"CDD business keywords: {', '.join(cdd_kws) or 'none'}\n"
        f"Audience pain points: {', '.join(p for p in (pain_points or []) if p) or 'derive from keywords — do not invent facts'}\n"
        f"Geographic focus: {geographic_focus or 'not set'}\n"
        f"Resolved location: {location_name or 'United States (national)'}\n"
        f"Competitor names: {', '.join(cnames) or 'none'}\n"
        f"Competitor domains: {', '.join(comps)}\n"
        f"{competitor_context or ''}\n"
        f"Existing intent clusters: {cluster_bits}\n"
        f"ASSIGNED multi-mode seeded keywords (one topic per row; "
        f"primary_keyword MUST equal keyword exactly):\n{kw_lines}\n"
        f"Write exactly {len(assigned_keywords) or n} topic ideas — one for each "
        "assigned keyword above. Copy primary_keyword character-for-character from "
        f"the assigned list. Use present year {_YEAR} only when a year is needed. "
        "Do NOT invent other keywords. Titles must include the assigned keyword phrase. "
        "secondary_keywords must also be exact phrases from the assigned list when used. "
        "Include cluster_map, publishing_order, internal_linking. "
        "Each topic needs intent + funnel aligned to the keyword."
    )

    def _idea_from_row(
        row: dict[str, Any],
        *,
        llm_idea: dict[str, Any] | None = None,
        index: int = 0,
    ) -> dict[str, Any]:
        primary_kw = str(row.get("keyword") or "").strip()
        llm_idea = llm_idea or {}
        pain = (pain_points or [None])[index % max(1, len(pain_points or [None]))]
        shape = _shape_from_keyword(
            row,
            index=index,
            pain_point=pain if isinstance(pain, str) else None,
            audience=aud,
        )
        # LLM may polish the title, but only if it still contains the primary keyword
        # and does not fight the intent-derived shape for commercial/transactional rows.
        title_raw = str(llm_idea.get("title") or "").strip()
        if (
            title_raw
            and _norm_kw(primary_kw) in _norm_kw(title_raw)
            and shape["intent"] == "informational"
        ):
            title = _title_under_60(prefer_present_year(title_raw))
        else:
            title = shape["title"]

        # Secondaries: only keep LLM picks that share seed/target with primary
        primary_seed = _norm_kw(row.get("seed"))
        primary_target = _norm_kw(row.get("target"))
        secondary: list[str] = []
        for s in llm_idea.get("secondary_keywords") or llm_idea.get("supporting_keywords") or []:
            text = str(s).strip()
            key = _norm_kw(text)
            if not text or key == _norm_kw(primary_kw) or key not in pool_by_kw:
                continue
            other = pool_by_kw[key]
            same_family = (
                (primary_seed and _norm_kw(other.get("seed")) == primary_seed)
                or (primary_target and _norm_kw(other.get("target")) == primary_target)
            )
            if same_family:
                secondary.append(text)
        if len(secondary) < 3:
            secondary = _secondary_keywords_for(primary_kw, pool=topic_pool)
        return {
            "title": title,
            "keyword": primary_kw,
            "primary_keyword": primary_kw,
            "secondary_keywords": secondary[:8],
            "supporting_keywords": secondary[:8],
            "intent": shape["intent"],
            "funnel": shape["funnel"],
            "type": shape["type"],
            "difficulty": row.get("difficulty")
            if row.get("difficulty") is not None
            else _kd_to_difficulty(row.get("difficulty")),
            "why": str(llm_idea.get("why") or "") or shape["why"],
            "angle": str(llm_idea.get("angle") or "") or shape["angle"],
            "key_sections": list(llm_idea.get("key_sections") or [])[:5],
            "angle_category": shape["angle_category"],
            "traffic": shape.get("traffic"),
            "content_format": shape.get("content_format"),
            "core_topic": shape.get("core_topic"),
            "benefit": shape.get("benefit"),
            "volume": row.get("volume"),
            "opportunity_score": row.get("opportunity_score"),
            "competitor_domains": list(
                row.get("competitor_domains") or comps[:3] or []
            )[:5],
            "gap_flag": bool(row.get("gap_flag")),
            "match_class": row.get("match_class") or shape["match_class"],
            "seed": row.get("seed"),
            "target": row.get("target"),
            "service": row.get("service"),
            "ungrounded": False,
            "framework": "headline_interest_curiosity_promise",
        }

    parsed = await synthesize_json(system, user)
    llm_by_kw: dict[str, dict[str, Any]] = {}
    if isinstance(parsed, dict) and (parsed.get("topic_ideas") or parsed.get("topics")):
        plan = _normalize_plan(parsed, seed=seed, count=max(n, len(assigned_keywords)))
        for idea in plan.get("topic_ideas") or []:
            key = _norm_kw(idea.get("primary_keyword") or idea.get("keyword"))
            if key and key not in llm_by_kw:
                llm_by_kw[key] = idea

    if assigned_rows:
        ideas = [
            _idea_from_row(
                row,
                llm_idea=llm_by_kw.get(_norm_kw(row.get("keyword"))),
                index=i,
            )
            for i, row in enumerate(assigned_rows)
        ]
        plan_out = {
            "seed": seed,
            "audience": aud,
            "funnel_filter": fun,
            "topic_ideas": ideas,
            "funnel_balance": funnel_balance(ideas),
            "cluster_map": (
                (parsed.get("cluster_map") if isinstance(parsed, dict) else None)
                or {
                    "pillar": ideas[0]["title"] if ideas else seed,
                    "supporting": [
                        {"title": t["title"], "publish_first": i == 0}
                        for i, t in enumerate(ideas[1:6])
                    ],
                }
            ),
            "publishing_order": (
                (parsed.get("publishing_order") if isinstance(parsed, dict) else None)
                or [t["title"] for t in ideas]
            ),
            "internal_linking": (
                (parsed.get("internal_linking") if isinstance(parsed, dict) else None)
                or []
            ),
            "source": "multi_mode_seeding",
            "keyword_source": "multi_mode_seeding",
            "assigned_keywords": assigned_keywords,
        }
        return plan_out

    fallback = _fallback_topic_plan(
        seed=seed,
        count=n,
        audience=aud,
        funnel=fun,
        best=best or topic_pool,
        evergreen=[],
        competitor_domains=comps,
        products=services,
        pain_points=pain_points,
    )
    fallback["keyword_source"] = "multi_mode_seeding"
    return fallback


def ground_topic_plan_ideas(
    topic_plan: dict[str, Any],
    *,
    keyword_dataset: list[dict[str, Any]] | None = None,
    ranked_all: list[dict[str, Any]] | None = None,
    clusters: list[dict[str, Any]] | None = None,
    serp_by_kw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach live metrics / secondaries / cluster labels to topic ideas."""
    metrics_by_kw: dict[str, dict[str, Any]] = {}
    for r in keyword_dataset or []:
        if isinstance(r, dict) and r.get("keyword"):
            metrics_by_kw[str(r.get("keyword") or "").strip().lower()] = r
    for r in ranked_all or []:
        if not isinstance(r, dict):
            continue
        key = str(r.get("keyword") or "").strip().lower()
        if key and key not in metrics_by_kw:
            metrics_by_kw[key] = r
    seed_kw_keys = set(metrics_by_kw)
    serp_by_kw = serp_by_kw or {}
    clusters = clusters or []
    grounded: list[dict[str, Any]] = []
    for idea in topic_plan.get("topic_ideas") or []:
        if not isinstance(idea, dict):
            continue
        mk = str(idea.get("primary_keyword") or idea.get("keyword") or "").strip().lower()
        if mk and seed_kw_keys and mk not in seed_kw_keys:
            continue
        met = metrics_by_kw.get(mk) or {}
        if idea.get("volume") is None and met.get("volume") is not None:
            idea["volume"] = met.get("volume")
        if idea.get("difficulty") in (None, "", "—") and met.get("difficulty") is not None:
            idea["difficulty"] = met.get("difficulty")
        if not idea.get("intent") and met.get("intent"):
            idea["intent"] = met.get("intent")
        if met.get("cpc") is not None:
            idea["cpc"] = met.get("cpc")
        if met.get("competitor_domains"):
            idea["competitor_domains"] = met.get("competitor_domains")
        if met.get("serp_titles"):
            idea["serp_titles"] = met.get("serp_titles")
        elif mk in serp_by_kw:
            idea["serp_titles"] = serp_by_kw[mk]
        primary_seed = _norm_kw(met.get("seed") or idea.get("seed"))
        primary_target = _norm_kw(met.get("target") or idea.get("target"))
        secondary: list[str] = []
        for s in idea.get("secondary_keywords") or idea.get("supporting_keywords") or []:
            text = str(s).strip()
            key = text.lower()
            if not text or key == mk or (seed_kw_keys and key not in seed_kw_keys):
                continue
            other = metrics_by_kw.get(key) or {}
            same_family = (
                (primary_seed and _norm_kw(other.get("seed")) == primary_seed)
                or (primary_target and _norm_kw(other.get("target")) == primary_target)
            )
            if same_family or not seed_kw_keys:
                secondary.append(text)
        if len(secondary) < 3 and keyword_dataset:
            secondary = _secondary_keywords_for(
                str(idea.get("primary_keyword") or idea.get("keyword") or ""),
                pool=keyword_dataset,
            )
        idea["secondary_keywords"] = secondary[:8]
        idea["supporting_keywords"] = secondary[:8]
        if met.get("match_class") and not idea.get("match_class"):
            idea["match_class"] = met.get("match_class")
        if met.get("seed") and not idea.get("seed"):
            idea["seed"] = met.get("seed")
        if met.get("target") and not idea.get("target"):
            idea["target"] = met.get("target")
        for c in clusters:
            if not isinstance(c, dict):
                continue
            if mk == str(c.get("primary_keyword") or "").strip().lower():
                idea["cluster"] = c.get("name")
                break
        grounded.append(idea)
    topic_plan["topic_ideas"] = grounded
    topic_plan["keyword_source"] = topic_plan.get("keyword_source") or "multi_mode_seeding"
    return topic_plan


def attach_url_map_to_topic_plan(
    topic_plan: dict[str, Any],
    url_map_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """Stamp OPTIMIZE / CREATE / final URL onto each topic idea from the URL map."""
    rows = list((url_map_report or {}).get("final_url_map") or [])
    by_kw: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in (
            row.get("primary_keyword"),
            row.get("keyword"),
            row.get("cluster_primary"),
        ):
            k = str(key or "").strip().lower()
            if k and k not in by_kw:
                by_kw[k] = row
    for idea in topic_plan.get("topic_ideas") or []:
        if not isinstance(idea, dict):
            continue
        mk = str(idea.get("primary_keyword") or idea.get("keyword") or "").strip().lower()
        row = by_kw.get(mk)
        if not row:
            continue
        idea["url_map_action"] = row.get("action") or row.get("status")
        idea["selected_url"] = row.get("final_url") or row.get("selected_url") or row.get("url")
        idea["url_score"] = row.get("url_score") or row.get("score")
        idea["existing_page"] = row.get("existing_page") or row.get("matched_url")
    topic_plan["url_map_summary"] = dict((url_map_report or {}).get("summary") or {})
    topic_plan["deferred"] = False
    topic_plan["created_after_url_map"] = True
    return topic_plan


async def run_create_topic_for_new_clusters(
    *,
    demand_or_ctx: dict[str, Any] | None = None,
    message: str = "",
    industry: str | None = None,
    audience: str | None = None,
    topic_seed: str | None = None,
    topic_kw_pool: list[dict[str, Any]] | None = None,
    keyword_dataset: list[dict[str, Any]] | None = None,
    clusters: list[dict[str, Any]] | None = None,
    competitor_domains: list[str] | None = None,
    competitor_names: list[str] | None = None,
    geographic_focus: str | None = None,
    location_name: str | None = None,
    competitor_context: str | None = None,
    products: list[str] | None = None,
    cdd_keywords: list[str] | None = None,
    pain_points: list[str] | None = None,
    serp_by_kw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Draft topics only for clusters classified ``new_topic`` vs the sitemap.

    Existing / review clusters are listed on the plan but do not get new drafts.
    URL mapping runs afterward and uses the same dispositions.
    """
    ctx = dict(demand_or_ctx or {})
    all_clusters = list(
        clusters
        or ctx.get("clusters")
        or (ctx.get("cluster_report") or {}).get("clusters")
        or []
    )
    new_clusters = [
        c
        for c in all_clusters
        if isinstance(c, dict) and str(c.get("topic_disposition") or "") == "new_topic"
    ]
    existing_clusters = [
        c
        for c in all_clusters
        if isinstance(c, dict)
        and str(c.get("topic_disposition") or "") in ("existing_topic", "existing_review")
    ]

    dataset = list(keyword_dataset or ctx.get("keyword_dataset") or [])
    pool_by_kw = {
        str(r.get("keyword") or "").strip().lower(): r
        for r in dataset
        if isinstance(r, dict) and r.get("keyword")
    }
    # Prefer service-selected rows that belong to new clusters; else cluster primaries.
    selected_pool: list[dict[str, Any]] = []
    new_primaries: set[str] = set()
    for c in new_clusters:
        from app.services.content_pipeline import identify_primary_keyword

        pk = str(identify_primary_keyword(c) or c.get("primary_keyword") or "").strip()
        if pk:
            new_primaries.add(pk.lower())
            row = pool_by_kw.get(pk.lower())
            if row:
                selected_pool.append({**row, "cluster": c.get("name"), "intent": c.get("intent") or row.get("intent"), "funnel": c.get("funnel") or row.get("funnel")})
            else:
                selected_pool.append(
                    {
                        "keyword": pk,
                        "intent": c.get("intent"),
                        "funnel": c.get("funnel"),
                        "cluster": c.get("name"),
                        "volume": c.get("volume"),
                        "difficulty": c.get("difficulty"),
                    }
                )
    if topic_kw_pool:
        for r in topic_kw_pool:
            if not isinstance(r, dict):
                continue
            kw = str(r.get("keyword") or "").strip().lower()
            if kw and kw in new_primaries and not any(
                str(x.get("keyword") or "").strip().lower() == kw for x in selected_pool
            ):
                selected_pool.append(r)
    if not selected_pool and topic_kw_pool:
        # No dispositions yet — fall back to provided pool
        selected_pool = list(topic_kw_pool)

    seed = (topic_seed or ctx.get("topic_seed") or "").strip()
    if not seed:
        seeds = ctx.get("seed_keywords") or []
        seed = str(seeds[0] if seeds else "topics").strip() or "topics"

    if not selected_pool:
        return {
            "deferred": False,
            "created_after_sitemap_classification": True,
            "selection_mode": "new_clusters_vs_sitemap",
            "selection_version": "phase5_sitemap_then_topics_v1",
            "topic_ideas": [],
            "existing_on_site": [
                {
                    "cluster": c.get("name"),
                    "primary_keyword": c.get("primary_keyword"),
                    "matched_url": (c.get("existing_page_match") or {}).get("matched_url"),
                    "disposition": c.get("topic_disposition"),
                    "intent": c.get("intent"),
                    "funnel": c.get("funnel"),
                }
                for c in existing_clusters
            ],
            "note": "No new-topic clusters after sitemap classification — nothing to draft.",
        }

    topic_plan = await run_create_topic(
        seed=seed,
        message=message,
        audience=audience or ctx.get("audience"),
        best_opportunities=selected_pool,
        strong_evergreen=[],
        keyword_pool=dataset or selected_pool,
        competitor_domains=competitor_domains or list(ctx.get("competitor_domains") or []),
        competitor_names=competitor_names or list(ctx.get("competitor_names") or []),
        geographic_focus=geographic_focus or str(ctx.get("geographic_focus") or ""),
        location_name=location_name or str(ctx.get("location_name") or ""),
        competitor_context=competitor_context or str(ctx.get("competitor_context") or ""),
        industry=industry or ctx.get("industry"),
        products=products or list(ctx.get("products") or ctx.get("services") or []),
        cdd_keywords=cdd_keywords or list(ctx.get("cdd_keywords") or []),
        clusters=new_clusters,
        pain_points=pain_points or list(ctx.get("pain_points") or []),
    )
    topic_plan["selection_mode"] = "new_clusters_vs_sitemap"
    topic_plan["selection_version"] = "phase5_sitemap_then_topics_v1"
    topic_plan["created_after_sitemap_classification"] = True
    topic_plan["created_after_url_map"] = False
    topic_plan["deferred"] = False
    topic_plan["assigned_services"] = [
        str(r.get("service") or r.get("target") or r.get("seed") or r.get("cluster") or "")
        for r in selected_pool
    ]
    topic_plan["existing_on_site"] = [
        {
            "cluster": c.get("name"),
            "primary_keyword": c.get("primary_keyword"),
            "matched_url": (c.get("existing_page_match") or {}).get("matched_url"),
            "disposition": c.get("topic_disposition"),
            "intent": c.get("intent"),
            "funnel": c.get("funnel"),
        }
        for c in existing_clusters
    ]
    topic_plan = ground_topic_plan_ideas(
        topic_plan,
        keyword_dataset=dataset or selected_pool,
        ranked_all=selected_pool,
        clusters=new_clusters,
        serp_by_kw=serp_by_kw or {},
    )
    for idea in topic_plan.get("topic_ideas") or []:
        if isinstance(idea, dict):
            idea["topic_disposition"] = "new_topic"
    return topic_plan


# Back-compat alias used by earlier Phase 6 wiring — now drafts from demand
# classification and optionally stamps URL map actions afterward.
async def run_create_topic_after_url_map(
    *,
    demand: dict[str, Any],
    url_map_report: dict[str, Any] | None = None,
    message: str = "",
    industry: str | None = None,
) -> dict[str, Any]:
    topic_plan = await run_create_topic_for_new_clusters(
        demand_or_ctx=demand,
        message=message,
        industry=industry,
    )
    if url_map_report:
        topic_plan = attach_url_map_to_topic_plan(topic_plan, url_map_report)
    return topic_plan
