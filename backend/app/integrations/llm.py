"""Claude (Anthropic) + OpenRouter (Gemini Pro) with deterministic mock fallback."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _skill_provider_ready() -> bool:
    settings = get_settings()
    if settings.use_mock_llm:
        return False
    if settings.llm_provider == "openrouter":
        return bool(settings.openrouter_api_key)
    return bool(settings.anthropic_api_key)


async def _openrouter_chat(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 2500,
) -> str | None:
    settings = get_settings()
    if not settings.openrouter_api_key:
        return None
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://searchfit.local",
        "X-Title": "Radius OS Phase1-4",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenRouter empty choices: {str(data)[:200]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            # Some models return content parts
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text") or "")
                elif isinstance(part, str):
                    parts.append(part)
            content = "".join(parts)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenRouter empty content")
        return content.strip()


async def _anthropic_chat(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 2500,
) -> str | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


async def _skill_chat(*, system: str, user: str, max_tokens: int = 2500) -> str | None:
    """Skill-model completion via OpenRouter (Gemini) or Anthropic."""
    settings = get_settings()
    if settings.llm_provider == "openrouter":
        return await _openrouter_chat(
            system=system,
            user=user,
            model=settings.skill_model,
            max_tokens=max_tokens,
        )
    return await _anthropic_chat(
        system=system,
        user=user,
        model=settings.skill_model,
        max_tokens=max_tokens,
    )


async def route_agent(message: str, profile_statuses: dict[str, str]) -> str:
    """Pick discovery / tracking / website / competitor agent."""
    settings = get_settings()
    lowered = message.lower()

    # Heuristic first (fast, works offline)
    if any(k in lowered for k in ("discover", "questionnaire", "business model", "onboard")):
        return "discovery_agent"
    if any(k in lowered for k in ("track", "ga4", "gtm", "oauth", "analytics", "tag")):
        return "tracking_access_agent"
    if any(
        k in lowered
        for k in (
            "crawl",
            "website",
            "backlink",
            "anomaly",
            "site audit",
            "technical",
            "broken link",
            "dead link",
            "dead url",
            "404",
            "link checker",
            "link audit",
            "on-page",
            "on page",
            "optimize this page",
            "optimize meta",
            "meta tags",
            "improve seo",
            "improve rankings",
            "technical seo",
            "tech seo",
            "core web vitals",
            "site speed",
            "page speed",
            "crawlability",
            "indexation",
            "robots.txt",
            "sitemap",
            "mobile-friendly",
            "render blocking",
            "seo audit",
            "audit seo",
            "seo health",
            "seo issues",
            "site audit",
        )
    ):
        return "website_situation_agent"
    if any(k in lowered for k in ("competitor", "market", "keyword gap", "ranking", "landscape")):
        return "competitor_market_agent"
    if any(k in lowered for k in ("readiness", "phase 5", "gate")):
        return "readiness_gate"

    # Default progression based on profile status
    order = [
        ("discovery_status", "discovery_agent"),
        ("tracking_status", "tracking_access_agent"),
        ("website_status", "website_situation_agent"),
        ("competitor_status", "competitor_market_agent"),
    ]
    for status_key, agent in order:
        if profile_statuses.get(status_key, "not_started") != "complete":
            return agent

    if not settings.use_mock_llm and settings.anthropic_api_key:
        return await _claude_route(message, profile_statuses)
    return "readiness_gate"


async def _claude_route(message: str, profile_statuses: dict[str, str]) -> str:
    settings = get_settings()
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = (
            "Pick exactly one agent_key from: discovery_agent, tracking_access_agent, "
            "website_situation_agent, competitor_market_agent, readiness_gate.\n"
            f"Profile statuses: {json.dumps(profile_statuses)}\n"
            f"User message: {message}\n"
            "Reply with only the agent_key."
        )
        resp = await client.messages.create(
            model=settings.router_model,
            max_tokens=40,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        for key in (
            "discovery_agent",
            "tracking_access_agent",
            "website_situation_agent",
            "competitor_market_agent",
            "readiness_gate",
        ):
            if key in text:
                return key
    except Exception as exc:  # noqa: BLE001
        log.warning("claude_route_failed", error=str(exc))
    return "discovery_agent"


async def synthesize_text(system: str, user: str) -> str:
    if not _skill_provider_ready():
        return f"{user[:200]}"
    try:
        text = await _skill_chat(system=system, user=user, max_tokens=1200)
        return text or user
    except Exception as exc:  # noqa: BLE001
        log.warning("synthesize_text_failed", error=str(exc), provider=get_settings().llm_provider)
        return user


def extract_domain(url: str) -> str:
    url = url.strip()
    url = re.sub(r"^https?://", "", url)
    return url.split("/")[0].lower()


def _parse_json_content(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Prefer first JSON object if model added prose
    if not text.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
    return json.loads(text)


async def synthesize_json(system: str, user: str) -> dict[str, Any] | None:
    """Ask skill model for JSON only; returns parsed dict or None."""
    if not _skill_provider_ready():
        return None
    try:
        text = await _skill_chat(
            system=system + "\nRespond with valid JSON only. No markdown fences.",
            user=user,
            max_tokens=4096,
        )
        if not text:
            return None
        return _parse_json_content(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("synthesize_json_failed", error=str(exc), provider=get_settings().llm_provider)
        return None


async def live_pre_research(
    display_name: str,
    primary_url: str,
    industry: str | None = None,
) -> dict[str, Any]:
    """D1 — research public footprint before asking the client anything."""
    from app.integrations.web_fetch import (
        absolute_links,
        fetch_url,
        page_text_excerpt,
        parse_html,
    )

    if not _skill_provider_ready():
        return mock_pre_research(display_name, primary_url, industry=industry)

    fetched = await fetch_url(primary_url)
    if fetched.get("error") or not fetched.get("text"):
        base = mock_pre_research(display_name, primary_url, industry=industry)
        for k, v in list(base.items()):
            if isinstance(v, dict) and "confidence" in v:
                v["confidence"] = min(float(v["confidence"]), 0.25)
                if k in ("business_model", "products", "positioning"):
                    v["value"] = (
                        f"Live fetch failed ({fetched.get('error') or 'empty'}). "
                        "No research available from the site — confirm with client."
                    )
                    v["confidence"] = 0.1
        base["discrepancies"] = [
            {
                "field_key": "business_model",
                "explanation": f"Could not fetch {primary_url}: {fetched.get('error')}",
            }
        ]
        return base

    parser = parse_html(fetched["text"])
    excerpt = page_text_excerpt(parser, 4500)
    resolved = str(fetched.get("url") or primary_url)
    host = extract_domain(resolved)

    # Pull a couple of same-site pages for richer footprint (About / Services)
    secondary_bits: list[str] = []
    social_hrefs: list[str] = []
    social_hosts = (
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "tiktok.com",
    )
    for href in absolute_links(resolved, parser.hrefs)[:40]:
        low = href.lower()
        if any(s in low for s in social_hosts):
            social_hrefs.append(href)
        path = low.split(host, 1)[-1] if host in low else ""
        if any(p in path for p in ("/about", "/our-story", "/services", "/what-we-do", "/company")):
            if len(secondary_bits) < 2:
                extra = await fetch_url(href)
                if extra.get("text") and not extra.get("error"):
                    ep = parse_html(extra["text"])
                    secondary_bits.append(
                        f"--- {href} ---\nTitle: {ep.title}\n{page_text_excerpt(ep, 1200)}"
                    )

    industry_hint = (industry or "").strip()
    system = (
        "You are the Radius OS Discovery Agent running D1 — Automated pre-research.\n"
        "BEFORE any client questionnaire, research the public footprint and draft a first pass.\n"
        "Dynamically adapt to ANY industry or vertical — do not assume the client is an "
        "agency, ecommerce brand, SaaS company, or any other default. Infer the real "
        "industry from the site (and intake hint if provided) and keep ALL analysis inside "
        "that vertical: products, positioning, competitors, reviews, and local signals.\n"
        "Use website content plus any visible signals about Google Business / local listings, "
        "social presence, reviews, and visible competitors in the SAME industry.\n"
        "Never invent revenue numbers, AOV, or private sales-cycle facts — leave those null "
        "with low confidence (the short client questionnaire covers them in D2).\n"
        "If evidence is thin, say so and keep confidence low. Do not fabricate reviews."
    )
    user = (
        f"Company: {display_name}\nPrimary URL: {resolved}\nDomain: {host}\n"
        f"Industry from intake (may be blank): {industry_hint or '(not provided — infer from site)'}\n"
        f"Title: {parser.title}\nMeta: {parser.meta.get('description', '')}\n"
        f"H1s: {parser.h1s[:6]}\nH2s: {parser.h2s[:12]}\n"
        f"Social links found on site: {social_hrefs[:12] or 'none detected'}\n"
        f"Home excerpt:\n{excerpt}\n\n"
        f"Extra pages:\n{chr(10).join(secondary_bits) or '(none fetched)'}\n\n"
        "Return JSON with keys covering the APSA Client Discovery Document research block:\n"
        "inferred_industry, business_model, business_keywords, products, products_for_promotion,\n"
        "positioning, geographic_focus, target_demographic, b2b_b2c, industry_targeting,\n"
        "competitors, strengths, weaknesses, opportunities, threats, brand_guidelines,\n"
        "public_reviews_summary, social_presence, google_business_signals, discrepancies.\n"
        "Also include null/low-confidence stubs for client-only CDD keys:\n"
        "business_goal, average_ticket_size, lifetime_value, lead_modes, strategy_approach,\n"
        "sales_promises, content_creation_notes, blogs_notes, revenue_split, sales_cycle,\n"
        "seasonality, objectives, other_marketing_spend,\n"
        "seo_traffic_current, seo_traffic_target, sem_leads_current, sem_leads_target.\n"
        'Each content key (except discrepancies) = {"value": ..., "confidence": 0.0-1.0}.\n'
        "inferred_industry value = short vertical label.\n"
        "products value = string array of top offerings.\n"
        'competitors value = [{"name":"...","url":"https://..."}] (3–6 if possible).\n'
        "Never invent ticket size, LTV, or private sales facts — leave those null/low confidence.\n"
        "If unsupported by evidence, value null/empty and confidence <= 0.2."
    )
    parsed = await synthesize_json(system, user)
    if not parsed:
        return mock_pre_research(display_name, primary_url, industry=industry)

    from app.services.discovery_fields import CLIENT_ONLY_FIELDS, RESEARCH_FIELDS

    keys = tuple(RESEARCH_FIELDS) + tuple(CLIENT_ONLY_FIELDS)
    out: dict[str, Any] = {}
    for key in keys:
        raw = parsed.get(key)
        if isinstance(raw, dict) and "value" in raw:
            out[key] = {
                "value": raw.get("value"),
                "confidence": float(raw.get("confidence") or 0.3),
            }
        else:
            out[key] = {"value": raw, "confidence": 0.3}

    # Prefer intake industry when model left inferred blank
    inferred = out.get("inferred_industry", {}).get("value")
    if industry_hint and (not inferred or str(inferred).strip().lower() in ("", "null", "n/a")):
        out["inferred_industry"] = {"value": industry_hint, "confidence": 0.85}
    elif industry_hint and inferred and str(inferred).strip().lower() != industry_hint.lower():
        # Keep model inference but note intake for D2 review via discrepancy
        pass

    # Enrich social from crawl if model left it empty
    social_val = out.get("social_presence", {}).get("value")
    if (not social_val) and social_hrefs:
        out["social_presence"] = {
            "value": "Social profiles linked from site: " + "; ".join(social_hrefs[:8]),
            "confidence": 0.55,
        }

    discs = parsed.get("discrepancies") or []
    out["discrepancies"] = discs if isinstance(discs, list) else []
    return out


def mock_pre_research(
    display_name: str,
    primary_url: str,
    industry: str | None = None,
) -> dict[str, Any]:
    domain = extract_domain(primary_url)
    vertical = (industry or "General / multi-category").strip()
    return {
        "inferred_industry": {
            "value": vertical,
            "confidence": 0.8 if industry else 0.4,
        },
        "business_model": {
            "value": (
                f"{display_name} appears to operate in {vertical} via {domain}"
            ),
            "confidence": 0.72,
        },
        "products": {
            "value": [
                f"Core {vertical} offering",
                "Complementary services",
                "Support / aftercare",
            ],
            "confidence": 0.65,
        },
        "positioning": {
            "value": f"Mid-market {vertical} brand with quality-focused messaging",
            "confidence": 0.60,
        },
        "competitors": {
            "value": [
                {"name": f"Rival of {display_name}", "url": f"https://competitor-a-{domain}"},
                {"name": f"{vertical} Category Leader", "url": "https://category-leader.example"},
                {"name": f"{vertical} Value Player", "url": "https://value-player.example"},
            ],
            "confidence": 0.58,
        },
        "public_reviews_summary": {
            "value": "Public reviews lean mixed: service praised; price sensitivity mentioned.",
            "confidence": 0.45,
        },
        "social_presence": {
            "value": "LinkedIn and Instagram likely; confirm handles with client.",
            "confidence": 0.35,
        },
        "google_business_signals": {
            "value": "Local / regional presence suggested by site copy — confirm listing.",
            "confidence": 0.35,
        },
        "revenue_split": {"value": None, "confidence": 0.1},
        "average_order_value": {"value": None, "confidence": 0.1},
        "sales_cycle": {"value": None, "confidence": 0.1},
        "seasonality": {"value": None, "confidence": 0.1},
        "objectives": {"value": None, "confidence": 0.1},
        "compliance_constraints": {"value": None, "confidence": 0.1},
        "other_marketing_spend": {"value": None, "confidence": 0.1},
        "discrepancies": [
            {
                "field_key": "positioning",
                "explanation": (
                    "Public reviews suggest price-sensitive buyers while site copy "
                    "emphasizes premium positioning."
                ),
            }
        ],
    }
