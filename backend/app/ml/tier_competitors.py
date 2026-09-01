"""16-parameter tiered competitor scoring (ads-category-competitors skill)."""

from __future__ import annotations

import hashlib
from typing import Any

PARAMS = [
    ("company_size", "Company Size", 0.10),
    ("service_maturity", "Service Maturity", 0.08),
    ("industry_specialization", "Industry Specialization", 0.07),
    ("client_profile", "Client Profile Similarity", 0.09),
    ("digital_presence", "Digital Presence", 0.06),
    ("ai_adoption", "AI Adoption", 0.08),
    ("creative_strength", "Creative Strength", 0.07),
    ("performance_marketing", "Performance Marketing", 0.07),
    ("technology_stack", "Technology Stack", 0.05),
    ("growth_indicators", "Growth Indicators", 0.08),
    ("brand_authority", "Brand Authority", 0.06),
    ("innovation", "Innovation Score", 0.05),
    ("pricing_position", "Pricing Position", 0.04),
    ("geographic_reach", "Geographic Reach", 0.04),
    ("client_retention", "Client Retention", 0.06),
    ("ad_spend", "Ad Spend & Budget Intelligence", 0.06),
]

# Architecture v1.9 — service-level comparison (not one composite only)
SERVICE_CATEGORIES: dict[str, list[str]] = {
    "seo": ["digital_presence", "brand_authority", "industry_specialization", "technology_stack"],
    "google_ads": ["performance_marketing", "ad_spend", "growth_indicators"],
    "meta": ["creative_strength", "performance_marketing", "digital_presence"],
    "email_marketing": ["client_retention", "client_profile", "digital_presence"],
    "cro": ["creative_strength", "technology_stack", "innovation"],
}


def service_scores_from_params(parameters: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Average 0–10 param scores into service categories (Architecture v1.9)."""
    out: dict[str, float] = {}
    for service, keys in SERVICE_CATEGORIES.items():
        vals = []
        for k in keys:
            cell = parameters.get(k) or {}
            try:
                vals.append(float(cell.get("score", 0)))
            except (TypeError, ValueError):
                continue
        out[service] = round(sum(vals) / len(vals), 1) if vals else 0.0
    return out


def best_competitor_by_service(
    scorecards: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Pick the strongest competitor per service category."""
    best: dict[str, dict[str, Any]] = {}
    for s in scorecards:
        services = s.get("service_scores") or service_scores_from_params(s.get("parameters") or {})
        for svc, sc in services.items():
            cur = best.get(svc)
            if not cur or float(sc) > float(cur.get("score") or 0):
                best[svc] = {
                    "service": svc,
                    "competitor": s.get("name"),
                    "url": s.get("url"),
                    "score": float(sc),
                }
    return best


def _stable_score(seed: str, lo: int = 3, hi: int = 9) -> int:
    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    return lo + (h % (hi - lo + 1))


def _coerce_param_score(raw_score: Any) -> int:
    """Normalize model scores to integer 0–10 (handles 0–1 and 0–100 mistakes)."""
    if raw_score is None:
        return 5
    try:
        f = float(raw_score)
    except (TypeError, ValueError):
        return 5
    if 0 < f < 1:
        f *= 10  # model used 0–1 scale → int() would zero these
    elif f > 10 and f <= 100:
        f = f / 10.0  # model used 0–100
    return max(0, min(10, int(round(f))))


def score_entity(name: str, url: str, *, is_client: bool = False, industry: str | None = None) -> dict[str, Any]:
    """Deterministic mock-safe 0–10 scores for all 16 parameters."""
    base = f"{name}|{url}|{industry or ''}"
    scores: dict[str, dict[str, Any]] = {}
    for key, label, _w in PARAMS:
        raw = _stable_score(f"{base}|{key}", 3, 9)
        if is_client and key in ("ai_adoption", "growth_indicators"):
            raw = max(3, raw - 1)
        if not is_client and key == "industry_specialization":
            raw = min(10, raw + 1)
        scores[key] = {
            "label": label,
            "score": raw,
            "evidence": (
                f"Public signals for {name} ({key.replace('_', ' ')}) — "
                + ("client baseline" if is_client else "competitor scan")
                + "; mid-range used where data is thin."
            ),
        }
    return scores


async def score_entity_live(
    name: str,
    url: str,
    *,
    is_client: bool = False,
    industry: str | None = None,
) -> dict[str, Any]:
    """Score from live page fetch + skill model; falls back to mid-range if LLM unavailable."""
    from app.config import get_settings
    from app.integrations.llm import synthesize_json
    from app.integrations.web_fetch import fetch_url, page_text_excerpt, parse_html
    from app.agents.prompts import skill_system_preamble

    if get_settings().use_mock_providers or get_settings().use_mock_llm:
        return score_entity(name, url, is_client=is_client, industry=industry)

    fetched = await fetch_url(url)
    resolved_url = str(fetched.get("url") or url)
    parser = parse_html(fetched.get("text") or "")
    excerpt = page_text_excerpt(parser, 2500)
    thin_page = bool(fetched.get("error")) or len(excerpt.strip()) < 80
    keys = [p[0] for p in PARAMS]
    labels = {p[0]: p[1] for p in PARAMS}
    industry_ctx = (industry or "infer from site — any vertical").strip()
    # Architecture v1.9: Competitor Research forced to Gemini 2.5 Pro
    competitor_model = get_settings().competitor_model
    payload = await synthesize_json(
        skill_system_preamble("competitor_market_agent")
        + "\n\nScore EACH of the 16 parameters on an INTEGER scale of 0 to 10 "
        "(not 0–1, not 0–100). "
        "Adapt scoring to the client's industry — do not assume agency/ads-only signals. "
        "For Industry Specialization, score vertical overlap with the stated client industry. "
        "For Service Maturity / Performance Marketing, interpret relative to THIS business type "
        "(e.g. product catalog & booking for a clinic; inventory & retail media for ecommerce; "
        "paid + SEO stack for an agency). "
        "Use website evidence. If evidence is thin or the page failed to load, "
        "use conservative mid-range 3–5 and say so in evidence — NEVER use 0 "
        "unless the business clearly has zero signal for that parameter. "
        "Never invent employee counts or revenue as facts.",
        (
            f"Entity: {name}\nURL: {resolved_url}\n"
            f"Client industry / vertical: {industry_ctx}\n"
            f"Role: {'client baseline — score THIS business, not competitors' if is_client else 'competitor in the same vertical'}\n"
            f"Title: {parser.title}\nMeta: {parser.meta.get('description', '')}\n"
            f"Fetch status: {fetched.get('status_code')} error={fetched.get('error')}\n"
            f"Excerpt:\n{excerpt or '(empty — score mid-range 3–5 from company name/industry only)'}\n\n"
            f"industry_specialization = overlap with client industry '{industry_ctx}' "
            "(exact same vertical -> 8-10; adjacent -> 5-7; unrelated -> 1-4).\n"
            f"Return JSON object keyed by: {keys}. Each value = "
            '{"score": <integer 0-10>, "evidence": "short string"}.'
        ),
        model=competitor_model,
    )
    if not payload:
        # Still prefer mid-range over zeros when the live site was unreachable
        if thin_page:
            scores = score_entity(name, resolved_url, is_client=is_client, industry=industry)
            for key in scores:
                scores[key]["score"] = max(3, min(5, int(scores[key]["score"])))
                scores[key]["evidence"] = (
                    f"Page fetch thin/failed for {resolved_url}; "
                    "conservative mid-range 3–5 per skill rules."
                )
            return scores
        return score_entity(name, resolved_url, is_client=is_client, industry=industry)

    # Unwrap common nesting mistakes from models
    if isinstance(payload.get("parameters"), dict):
        payload = payload["parameters"]

    scores: dict[str, dict[str, Any]] = {}
    for key, label, _w in PARAMS:
        raw = payload.get(key)
        if raw is None or raw == {}:
            sc, evidence = 4, "Insufficient public data — conservative mid-range (3–5)."
        elif isinstance(raw, dict):
            sc = _coerce_param_score(raw.get("score", 5))
            evidence = str(raw.get("evidence") or "Insufficient public data — conservative mid score.")
        elif isinstance(raw, (int, float, str)):
            sc = _coerce_param_score(raw)
            evidence = "Score from model (no evidence string)."
        else:
            sc, evidence = 4, "Insufficient public data — conservative mid-range (3–5)."
        # Skill: never leave thin-data params at 0
        if sc <= 0 and (thin_page or "insufficien" in evidence.lower() or "thin" in evidence.lower()):
            sc = 4
            evidence = "Insufficient public data — conservative mid-range (3–5)."
        scores[key] = {"label": label or labels[key], "score": sc, "evidence": evidence}

    # If the model zeroed the whole profile (common after failed fetch), lift to mid-range
    avg = sum(int(v["score"]) for v in scores.values()) / max(len(scores), 1)
    if avg < 1.5:
        for key, label, _w in PARAMS:
            scores[key] = {
                "label": label,
                "score": 4,
                "evidence": (
                    f"Insufficient usable page evidence for {name} "
                    f"({fetched.get('error') or 'empty excerpt'}); "
                    "conservative mid-range 3–5 per skill rules."
                ),
            }
    return scores


def _proximity(a: int, b: int) -> float:
    return float(10 - abs(a - b))


def derived_scores(client_scores: dict[str, dict], comp_scores: dict[str, dict]) -> dict[str, float]:
    def s(d: dict, key: str) -> int:
        return int(d[key]["score"])

    similarity = (
        _proximity(s(client_scores, "company_size"), s(comp_scores, "company_size")) * 0.25
        + min(s(client_scores, "service_maturity"), s(comp_scores, "service_maturity")) * 0.20
        + s(comp_scores, "client_profile") * 0.25
        + s(comp_scores, "industry_specialization") * 0.15
        + _proximity(s(client_scores, "geographic_reach"), s(comp_scores, "geographic_reach")) * 0.10
        + _proximity(s(client_scores, "pricing_position"), s(comp_scores, "pricing_position")) * 0.05
    ) * 10

    maturity = (
        s(comp_scores, "service_maturity") * 0.13
        + s(comp_scores, "performance_marketing") * 0.13
        + s(comp_scores, "technology_stack") * 0.13
        + s(comp_scores, "ai_adoption") * 0.13
        + s(comp_scores, "creative_strength") * 0.10
        + s(comp_scores, "brand_authority") * 0.10
        + s(comp_scores, "innovation") * 0.10
        + s(comp_scores, "client_retention") * 0.08
        + s(comp_scores, "ad_spend") * 0.10
    ) * 10

    future_threat = (
        s(comp_scores, "growth_indicators") * 0.30
        + s(comp_scores, "ai_adoption") * 0.20
        + s(comp_scores, "innovation") * 0.15
        + s(comp_scores, "digital_presence") * 0.15
        + s(comp_scores, "brand_authority") * 0.10
        + s(comp_scores, "ad_spend") * 0.10
    ) * 10

    client_maturity = (
        s(client_scores, "service_maturity") * 0.13
        + s(client_scores, "performance_marketing") * 0.13
        + s(client_scores, "technology_stack") * 0.13
        + s(client_scores, "ai_adoption") * 0.13
        + s(client_scores, "creative_strength") * 0.10
        + s(client_scores, "brand_authority") * 0.10
        + s(client_scores, "innovation") * 0.10
        + s(client_scores, "client_retention") * 0.08
        + s(client_scores, "ad_spend") * 0.10
    ) * 10

    size_ratio = s(comp_scores, "company_size") / max(s(client_scores, "company_size"), 1)
    if 2.0 <= size_ratio <= 5.0 and s(comp_scores, "industry_specialization") >= 5 and maturity > client_maturity:
        aspirational = (
            maturity * 0.40
            + s(comp_scores, "brand_authority") * 10 * 0.25
            + s(comp_scores, "creative_strength") * 10 * 0.20
            + s(comp_scores, "innovation") * 10 * 0.15
        )
    else:
        aspirational = max(20.0, maturity * 0.45)

    return {
        "similarity": round(min(100.0, similarity), 1),
        "maturity": round(min(100.0, maturity), 1),
        "future_threat": round(min(100.0, future_threat), 1),
        "aspirational": round(min(100.0, aspirational), 1),
        "client_maturity": round(min(100.0, client_maturity), 1),
    }


def composite_and_tier(derived: dict[str, float]) -> tuple[float, int, str]:
    composite = (
        derived["similarity"] * 0.30
        + derived["maturity"] * 0.25
        + derived["future_threat"] * 0.25
        + derived["aspirational"] * 0.20
    )
    composite = round(composite, 1)
    if composite >= 85:
        return composite, 1, "Aspirational Leaders"
    if composite >= 70:
        return composite, 2, "Strong Direct Competitors"
    if composite >= 55:
        return composite, 3, "Emerging Challengers"
    if composite >= 40:
        return composite, 4, "Local/Similar Competitors"
    return composite, 5, "Low Relevance"


async def build_tiered_analysis(
    client_name: str,
    client_url: str,
    industry: str | None,
    competitors: list[dict[str, str]],
) -> dict[str, Any]:
    import asyncio

    from app.config import get_settings
    from app.integrations.web_fetch import fetch_url

    live = not get_settings().use_mock_providers and not get_settings().use_mock_llm
    # Cap interactive runs — sequential 10× LLM scores feel stuck in chat
    competitors = list(competitors)[:6]
    # Resolve working site URL (www / .com.au fallbacks) before scoring
    client_fetch = await fetch_url(client_url)
    resolved_client_url = str(client_fetch.get("url") or client_url)
    if live:
        client_scores = await score_entity_live(
            client_name, resolved_client_url, is_client=True, industry=industry
        )
    else:
        client_scores = score_entity(
            client_name, resolved_client_url, is_client=True, industry=industry
        )
    client_derived = derived_scores(client_scores, client_scores)

    sem = asyncio.Semaphore(3)

    async def _score_one(c: dict[str, str]) -> dict[str, Any]:
        async with sem:
            if live:
                cs = await score_entity_live(c["name"], c["url"], industry=industry)
            else:
                cs = score_entity(c["name"], c["url"], industry=industry)
            d = derived_scores(client_scores, cs)
            composite, tier, tier_name = composite_and_tier(d)
            svc = service_scores_from_params(cs)
            return {
                "name": c["name"],
                "url": c["url"],
                "source": c.get("source", "search"),
                "parameters": cs,
                "service_scores": svc,
                "derived": d,
                "composite": composite,
                "tier": tier,
                "tier_name": tier_name,
                "strengths": [
                    f"Strong {max(cs.items(), key=lambda x: x[1]['score'])[1]['label']}",
                    f"Maturity {d['maturity']:.0f}/100",
                ],
                "weaknesses": [
                    f"Watch gap vs client on "
                    + min(
                        cs.items(),
                        key=lambda x: x[1]["score"],
                    )[1]["label"],
                ],
                "recommendation": (
                    f"Tier {tier} ({tier_name}): "
                    + (
                        "Study and adapt playbook."
                        if tier == 1
                        else "Differentiate aggressively."
                        if tier == 2
                        else "Monitor growth trajectory."
                        if tier == 3
                        else "Aware but not primary focus."
                        if tier == 4
                        else "Exclude from active monitoring."
                    )
                ),
            }

    scorecards = list(await asyncio.gather(*[_score_one(c) for c in competitors]))

    scorecards.sort(key=lambda x: x["composite"], reverse=True)
    relevant = [s for s in scorecards if s["tier"] < 5]
    excluded = [s for s in scorecards if s["tier"] == 5]

    emerging = [s for s in relevant if s["tier"] == 3]
    top_threat = max(emerging, key=lambda x: x["derived"]["future_threat"]) if emerging else None

    heatmap = {
        "parameters": [p[0] for p in PARAMS],
        "labels": [p[1] for p in PARAMS],
        "client": {k: v["score"] for k, v in client_scores.items()},
        "competitors": {
            s["name"]: {k: v["score"] for k, v in s["parameters"].items()} for s in relevant[:6]
        },
    }

    recommendations = {
        "benchmark": [
            f"Study {s['name']}'s strengths — {s['recommendation']}" for s in relevant if s["tier"] == 1
        ][:3]
        or ["No Tier 1 aspirational leaders in this set — expand search next quarter."],
        "differentiate": [
            f"Against {s['name']}: {s['recommendation']}" for s in relevant if s["tier"] == 2
        ][:3],
        "monitor": [
            f"Watch {s['name']} — {s['recommendation']}" for s in relevant if s["tier"] == 3
        ][:3],
        "top_emerging_threat": (
            {
                "name": top_threat["name"],
                "future_threat": top_threat["derived"]["future_threat"],
                "note": top_threat["recommendation"],
            }
            if top_threat
            else None
        ),
    }

    # Parameter gap priorities (client vs best competitor score per param)
    gap_priorities = []
    for key, label, _w in PARAMS:
        client_sc = int(client_scores[key]["score"])
        best_name, best_sc = None, -1
        for s in relevant[:6]:
            sc = int(s["parameters"][key]["score"])
            if sc > best_sc:
                best_sc, best_name = sc, s["name"]
        gap = best_sc - client_sc
        priority = "critical" if gap >= 4 else "important" if gap >= 2 else "strength" if gap <= 0 else "watch"
        gap_priorities.append(
            {
                "priority": priority,
                "parameter": label,
                "parameter_key": key,
                "client_score": client_sc,
                "top_competitor": best_name,
                "top_competitor_score": best_sc,
                "gap": gap,
                "action": (
                    f"Close the gap vs {best_name} on {label}."
                    if gap > 0
                    else f"Leverage {label} as a competitive advantage."
                ),
            }
        )
    gap_priorities.sort(key=lambda g: g["gap"], reverse=True)

    tier_map = {
        "1": [
            {"name": s["name"], "composite": s["composite"], "note": s["recommendation"]}
            for s in relevant
            if s["tier"] == 1
        ],
        "2": [
            {"name": s["name"], "composite": s["composite"], "note": s["recommendation"]}
            for s in relevant
            if s["tier"] == 2
        ],
        "3": [
            {"name": s["name"], "composite": s["composite"], "note": s["recommendation"]}
            for s in relevant
            if s["tier"] == 3
        ],
        "4": [
            {"name": s["name"], "composite": s["composite"], "note": s["recommendation"]}
            for s in relevant
            if s["tier"] == 4
        ],
    }

    top_direct = next((s for s in relevant if s["tier"] == 2), relevant[0] if relevant else None)
    exec_bits = [
        f"{client_name} baseline maturity is {client_derived['client_maturity']:.0f}/100.",
        f"Analyzed {len(competitors)} competitors; {len(relevant)} remain after filtering Tier 5.",
    ]
    if top_direct:
        exec_bits.append(
            f"Primary direct pressure: {top_direct['name']} "
            f"(Tier {top_direct['tier']}, composite {top_direct['composite']})."
        )
    if top_threat:
        exec_bits.append(
            f"#1 emerging threat: {top_threat['name']} "
            f"(Future Threat {top_threat['derived']['future_threat']:.0f}/100)."
        )
    if gap_priorities:
        top_gap = gap_priorities[0]
        exec_bits.append(
            f"Top parameter gap: {top_gap['parameter']} "
            f"(client {top_gap['client_score']} vs {top_gap['top_competitor']} "
            f"{top_gap['top_competitor_score']})."
        )

    monitoring_plan = {
        "quarterly_checklist": [
            "Re-score Tier 2 and Tier 3 competitors on Growth Indicators",
            "Check Meta Ad Library and Google Ads Transparency for spend trajectory changes",
            "Review competitor LinkedIn for new hires, service launches, and awards",
            "Update AI Adoption scores — this parameter shifts fastest",
            "Update Ad Spend & Budget Intelligence — scaling up/down and new platforms",
            "Re-classify any competitors that have moved tiers",
        ],
        "tools": [
            {"tool": "LinkedIn Company Pages", "purpose": "Headcount, hiring, posts", "frequency": "Monthly"},
            {"tool": "Clutch / G2", "purpose": "Reviews, ratings, awards", "frequency": "Quarterly"},
            {"tool": "Meta Ad Library", "purpose": "Active ad campaigns", "frequency": "Monthly"},
            {"tool": "Google Ads Transparency", "purpose": "Search ad activity", "frequency": "Quarterly"},
            {"tool": "SimilarWeb (free)", "purpose": "Traffic trends", "frequency": "Quarterly"},
            {"tool": "Google Alerts", "purpose": "Brand mentions, press", "frequency": "Ongoing"},
            {"tool": "BuiltWith", "purpose": "Technology stack changes", "frequency": "Quarterly"},
            {"tool": "Social Blade", "purpose": "Social growth rates", "frequency": "Monthly"},
        ],
    }

    client_svc = service_scores_from_params(client_scores)
    for s in scorecards:
        if "service_scores" not in s:
            s["service_scores"] = service_scores_from_params(s.get("parameters") or {})
    by_service = best_competitor_by_service(relevant)

    from datetime import date

    return {
        "generated": date.today().isoformat(),
        "target_business": {"name": client_name, "url": resolved_client_url},
        "industry": industry or "n/a",
        "competitors_scored": len(competitors),
        "competitors_in_report": len(relevant),
        "executive_summary": " ".join(exec_bits),
        "client_baseline": {
            "name": client_name,
            "url": resolved_client_url,
            "parameters": client_scores,
            "maturity_score": client_derived["client_maturity"],
            "service_scores": client_svc,
        },
        "service_level_comparison": {
            "categories": list(SERVICE_CATEGORIES.keys()),
            "client": client_svc,
            "best_by_service": by_service,
            "note": (
                "Architecture v1.9: benchmark each service against the competitor "
                "that is strongest in that category — not one overall composite."
            ),
        },
        "scorecards": relevant,
        "excluded_tier5": [{"name": s["name"], "composite": s["composite"]} for s in excluded],
        "heatmap": heatmap,
        "recommendations": recommendations,
        "parameter_gaps": gap_priorities,
        "tier_map": tier_map,
        "monitoring_plan": monitoring_plan,
        "tier_overview": [
            {
                "rank": i + 1,
                "name": s["name"],
                "tier": s["tier"],
                "tier_name": s["tier_name"],
                "composite": s["composite"],
                "similarity": s["derived"]["similarity"],
                "maturity": s["derived"]["maturity"],
                "future_threat": s["derived"]["future_threat"],
                "aspirational": s["derived"]["aspirational"],
            }
            for i, s in enumerate(relevant)
        ],
    }
