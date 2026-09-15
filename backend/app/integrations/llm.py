"""Claude (Anthropic) + OpenRouter (Gemini Pro) with deterministic mock fallback."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"


@lru_cache(maxsize=4)
def _anthropic_client(api_key: str):
    """One client per key, reused across calls.

    Each AsyncAnthropic owns an httpx connection pool; building one per request threw
    that pool away every time and forced a fresh TLS handshake. Keyed on the api_key so
    rotating the key (or clear_settings_cache in tests) yields a new client rather than
    silently reusing the old credential. Import stays local — anthropic is optional at
    runtime when only OpenRouter or mock mode is configured.
    """
    import anthropic

    return anthropic.AsyncAnthropic(api_key=api_key)


def _skill_provider_ready() -> bool:
    settings = get_settings()
    if settings.use_mock_llm:
        return False
    if settings.llm_provider == "openrouter":
        return bool(settings.openrouter_api_key)
    return bool(settings.anthropic_api_key)


def _gemini_reasoning_budget(max_tokens: int) -> int:
    """Reserve answer tokens so Gemini thinking cannot starve visible content."""
    answer_reserve = min(2048, max(512, max_tokens // 2))
    return max(128, max_tokens - answer_reserve)


def _extract_openrouter_text(message: dict[str, Any]) -> str:
    """Flatten OpenRouter/Gemini message content (string, parts, or reasoning)."""
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("output_text") or ""
                if text:
                    parts.append(str(text))
        content = "".join(parts)
    if isinstance(content, str) and content.strip():
        return content.strip()
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    return ""


async def _openrouter_chat(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 2500,
) -> str | None:
    from app.services.api_meter import _usage_tokens, record_api_call

    settings = get_settings()
    if not settings.openrouter_api_key:
        return None
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://searchfit.local",
        "X-Title": "Radius OS Phase1-4",
    }
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if "gemini" in model.lower():
        body["reasoning"] = {"max_tokens": _gemini_reasoning_budget(max_tokens)}
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=body)
            if resp.status_code >= 400:
                raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError(f"OpenRouter empty choices: {str(data)[:200]}")
            choice = choices[0] if isinstance(choices[0], dict) else {}
            message = choice.get("message") or {}
            content = _extract_openrouter_text(message)
            if not content:
                usage = data.get("usage") or {}
                finish = choice.get("finish_reason") or choice.get("native_finish_reason")
                raise RuntimeError(
                    "OpenRouter empty content "
                    f"(model={model} finish={finish} "
                    f"completion_tokens={usage.get('completion_tokens')})"
                )
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            pt, ct = _usage_tokens(usage)
            await record_api_call(
                provider="openrouter",
                operation="chat.completions",
                model=model,
                prompt_tokens=pt,
                completion_tokens=ct,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                status="success",
            )
            return content
    except Exception as exc:
        await record_api_call(
            provider="openrouter",
            operation="chat.completions",
            model=model,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            status="error",
            error_detail=str(exc)[:500],
        )
        raise


async def _anthropic_chat(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 2500,
) -> str | None:
    from app.services.api_meter import record_api_call

    settings = get_settings()
    if not settings.anthropic_api_key:
        return None

    client = _anthropic_client(settings.anthropic_api_key)
    t0 = time.perf_counter()
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "input_tokens", None) if usage else None
        ct = getattr(usage, "output_tokens", None) if usage else None
        await record_api_call(
            provider="anthropic",
            operation="messages.create",
            model=model,
            prompt_tokens=int(pt) if pt is not None else None,
            completion_tokens=int(ct) if ct is not None else None,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            status="success",
        )
        return resp.content[0].text
    except Exception as exc:
        await record_api_call(
            provider="anthropic",
            operation="messages.create",
            model=model,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            status="error",
            error_detail=str(exc)[:500],
        )
        raise


async def _skill_chat(
    *,
    system: str,
    user: str,
    max_tokens: int = 2500,
    model: str | None = None,
) -> str | None:
    """Skill-model completion via OpenRouter or Anthropic.

    Architecture v1.9: cheap Haiku for routing (elsewhere), Sonnet for reasoning,
    Gemini 2.5 Pro forced for Competitor Research via ``model=``.
    """
    settings = get_settings()
    chosen = model or settings.skill_model
    if settings.llm_provider == "openrouter":
        return await _openrouter_chat(
            system=system,
            user=user,
            model=chosen,
            max_tokens=max_tokens,
        )
    return await _anthropic_chat(
        system=system,
        user=user,
        model=chosen,
        max_tokens=max_tokens,
    )


async def route_agent(message: str, profile_statuses: dict[str, str]) -> str:
    """Pick discovery / tracking / website / competitor / phase 5–12 agent."""
    settings = get_settings()
    lowered = message.lower()

    # Heuristic first (fast, works offline)
    if any(k in lowered for k in ("discover", "questionnaire", "business model", "onboard")):
        return "discovery_agent"
    if any(k in lowered for k in ("track", "ga4", "gtm", "oauth", "analytics", "tag")):
        return "tracking_access_agent"

    # Phases 7–12 before the broad website catch-all
    if any(
        k in lowered
        for k in (
            "publish",
            "publishing",
            "indexnow",
            "recrawl",
            "indexation checklist",
            "phase 12",
        )
    ):
        return "publishing"
    if any(
        k in lowered
        for k in (
            "on-page",
            "on page",
            "onpage",
            "schema markup",
            "json-ld",
            "internal linking",
            "meta tags",
            "optimize this page",
            "optimize meta",
            "phase 11",
        )
    ):
        return "on_page_seo"
    if any(
        k in lowered
        for k in (
            "content brief",
            "content production",
            "create content",
            "draft markdown",
            "write draft",
            "write the full draft",
            "write the content for",
            "write content for",
            "draft this topic",
            "phase 10",
        )
    ):
        return "content_production"
    if any(
        k in lowered
        for k in (
            "content planning",
            "page planning",
            "content roadmap",
            "new page plan",
            "phase 9",
        )
    ):
        return "content_planning"
    if any(
        k in lowered
        for k in (
            "content audit",
            "existing content",
            "page inventory",
            "cannibalization",
            "phase 8",
        )
    ):
        return "content_audit"
    if any(
        k in lowered
        for k in (
            "technical seo",
            "tech seo",
            "core web vitals",
            "site speed",
            "page speed",
            "crawlability",
            "robots.txt",
            "render blocking",
            "broken link",
            "dead link",
            "dead url",
            "404",
            "link checker",
            "link audit",
            "phase 7",
        )
    ):
        return "technical_seo"

    if any(
        k in lowered
        for k in (
            "crawl",
            "website",
            "backlink",
            "anomaly",
            "site audit",
            "technical",
            "improve seo",
            "improve rankings",
            "indexation",
            "sitemap",
            "mobile-friendly",
            "seo audit",
            "audit seo",
            "seo health",
            "seo issues",
        )
    ):
        return "website_situation_agent"
    if any(
        k in lowered
        for k in (
            "site architecture",
            "information architecture",
            "url hierarchy",
            "url structure",
            "folder structure",
            "silo",
            "siloing",
            "click depth",
            "crawl depth",
            "breadcrumb",
            "mega menu",
            "faceted navigation",
            "redirect map",
            "site restructure",
            "orphan pages",
            "phase 6 architecture",
        )
    ):
        return "site_architecture"
    if any(
        k in lowered
        for k in (
            "content audit",
            "audit my content",
            "audit my blog",
            "content pruning",
            "prune content",
            "content inventory",
            "content decay",
            "traffic decay",
            "traffic dropped",
            "old posts",
            "which pages should i delete",
            "cannibalisation",
            "cannibalization",
            "thin content",
            "striking distance",
            "should i update or rewrite",
            "existing content audit",
            "phase 8",
        )
    ):
        return "content_audit"
    if any(
        k in lowered
        for k in (
            "content strategy",
            "url plan",
            "url ia",
            "nav plan",
            "pillar page",
            "phase 6",
            "seo strategy",
            "editorial plan",
            "content calendar",
            "what should i write",
            "content roadmap",
            "content gap",
            "topic research",
            "plan content",
        )
    ):
        return "content_strategy"
    if any(
        k in lowered
        for k in (
            "keyword research",
            "search demand",
            "create topic",
            "topic map",
            "keyword cluster",
            "clustering",
            "phase 5 keyword",
            "best opportunities",
            "evergreen keyword",
        )
    ):
        return "search_demand"
    if any(k in lowered for k in ("competitor", "market", "keyword gap", "ranking", "landscape")):
        return "competitor_market_agent"
    if any(k in lowered for k in ("readiness", "gate")) and "phase 5" not in lowered:
        return "readiness_gate"
    if "phase 5" in lowered and any(k in lowered for k in ("ready", "gate", "readiness")):
        return "readiness_gate"
    if "phase 5" in lowered:
        return "search_demand"

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

    # After 1–4 complete: search demand → strategy → IA → technical → audit → P9–12
    if profile_statuses.get("search_demand_status", "not_started") != "complete":
        if any(k in lowered for k in ("readiness", "score", "gate")):
            return "readiness_gate"
        return "search_demand"
    if profile_statuses.get("seo_strategy_status", "not_started") != "complete":
        return "content_strategy"
    if profile_statuses.get("site_architecture_status", "not_started") != "complete":
        return "site_architecture"
    if profile_statuses.get("technical_seo_status", "not_started") != "complete":
        return "technical_seo"
    if profile_statuses.get("content_audit_status", "not_started") != "complete":
        return "content_audit"
    if profile_statuses.get("content_planning_status", "not_started") != "complete":
        return "content_planning"
    if profile_statuses.get("content_production_status", "not_started") != "complete":
        return "content_production"
    if profile_statuses.get("on_page_seo_status", "not_started") != "complete":
        return "on_page_seo"
    if profile_statuses.get("publishing_status", "not_started") != "complete":
        return "publishing"

    if not settings.use_mock_llm and settings.anthropic_api_key:
        return await _claude_route(message, profile_statuses)
    return "publishing"


async def _claude_route(message: str, profile_statuses: dict[str, str]) -> str:
    settings = get_settings()
    try:
        client = _anthropic_client(settings.anthropic_api_key)
        prompt = (
            "Pick exactly one agent_key from: discovery_agent, tracking_access_agent, "
            "website_situation_agent, competitor_market_agent, readiness_gate, "
            "search_demand, content_strategy, site_architecture, technical_seo, "
            "content_audit, content_planning, content_production, on_page_seo, publishing.\n"
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
            "search_demand",
            "content_strategy",
            "site_architecture",
            "technical_seo",
            "content_audit",
            "content_planning",
            "content_production",
            "on_page_seo",
            "publishing",
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


async def synthesize_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    raise_on_error: bool = False,
) -> dict[str, Any] | None:
    """Ask skill model for JSON only; returns parsed dict or None.

    Pass ``model`` to override (e.g. competitor_model / Gemini for P4).
    """
    if not _skill_provider_ready():
        if raise_on_error:
            raise RuntimeError("LLM provider not ready (mock mode or missing API key)")
        return None
    try:
        text = await _skill_chat(
            system=system + "\nRespond with valid JSON only. No markdown fences.",
            user=user,
            max_tokens=max_tokens,
            model=model,
        )
        if not text:
            if raise_on_error:
                raise RuntimeError("LLM returned empty content")
            return None
        return _parse_json_content(text)
    except Exception as exc:
        log.warning("synthesize_json_failed", error=str(exc), provider=get_settings().llm_provider)
        if raise_on_error:
            raise
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
        "that vertical: products, positioning, reviews, and local signals.\n"
        "Use website content plus any visible signals about Google Business / local listings, "
        "social presence, and reviews in the SAME industry. Competitor discovery is owned "
        "exclusively by Phase 4 — do not search for, infer, or return competitors here.\n"
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
        "strengths, weaknesses, opportunities, threats, brand_guidelines,\n"
        "public_reviews_summary, social_presence, google_business_signals, discrepancies.\n"
        "Also include null/low-confidence stubs for client-only CDD keys:\n"
        "business_goal, average_ticket_size, lifetime_value, lead_modes, strategy_approach,\n"
        "sales_promises, content_creation_notes, blogs_notes, revenue_split, sales_cycle,\n"
        "seasonality, objectives, other_marketing_spend,\n"
        "seo_traffic_current, seo_traffic_target, sem_leads_current, sem_leads_target.\n"
        'Each content key (except discrepancies) = {"value": ..., "confidence": 0.0-1.0}.\n'
        "inferred_industry value = short vertical label.\n"
        "products value = string array of top offerings.\n"
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
    out["discrepancies"] = _normalize_discrepancies(discs)
    return out


def _normalize_discrepancies(raw: Any) -> list[dict[str, str]]:
    """Coerce LLM discrepancy notes into `{field_key, explanation}` objects.

    Models often return a list of strings instead of objects; indexing those as
    dicts raises ``TypeError: string indices must be integers, not 'str'``.
    """
    if not isinstance(raw, list):
        if isinstance(raw, str) and raw.strip() or isinstance(raw, dict):
            raw = [raw]
        else:
            return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            key = str(item.get("field_key") or item.get("field") or "").strip() or "business_model"
            expl = str(
                item.get("explanation") or item.get("note") or item.get("message") or ""
            ).strip()
        elif isinstance(item, str) and item.strip():
            key = "business_model"
            expl = item.strip()
        else:
            continue
        marker = f"{key}:{expl}"
        if marker in seen:
            continue
        seen.add(marker)
        out.append({"field_key": key, "explanation": expl or "Review with client."})
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


def draft_images_dir() -> Path:
    root = Path(__file__).resolve().parents[2] / "data" / "draft_images"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _openrouter_headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://searchfit.local",
        "X-Title": "Radius OS Phase1-4",
    }


def _decode_data_url(value: str) -> tuple[bytes, str] | None:
    m = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", value.strip(), re.DOTALL)
    if not m:
        return None
    mime = m.group(1).lower()
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except Exception:  # noqa: BLE001
        return None
    return raw, mime


def _persist_image_bytes(raw: bytes, *, stem: str, mime: str = "image/png") -> str:
    ext = "jpg" if "jpeg" in mime or mime.endswith("jpg") else "webp" if "webp" in mime else "png"
    path = draft_images_dir() / f"{stem}.{ext}"
    path.write_bytes(raw)
    return f"/media/drafts/{path.name}"


def _extract_image_payload(data: Any) -> tuple[str | None, str | None]:
    """Return (url_or_data, b64) from OpenRouter images or chat-modalities responses."""
    if not isinstance(data, dict):
        return None, None
    rows = data.get("data")
    if isinstance(rows, list) and rows:
        first = rows[0] if isinstance(rows[0], dict) else {}
        url = first.get("url") or first.get("image_url")
        if isinstance(url, dict):
            url = url.get("url")
        b64 = first.get("b64_json") or first.get("b64")
        if url or b64:
            return (str(url) if url else None), (str(b64) if b64 else None)
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        images = message.get("images") or message.get("image") or []
        if isinstance(images, dict):
            images = [images]
        for img in images:
            if not isinstance(img, dict):
                continue
            url = img.get("image_url") or img.get("url")
            if isinstance(url, dict):
                url = url.get("url")
            if url:
                return str(url), None
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                url = part.get("image_url") or part.get("url")
                if isinstance(url, dict):
                    url = url.get("url")
                if url and (str(url).startswith("data:image") or str(url).startswith("http")):
                    return str(url), None
    return None, None


async def generate_openrouter_image(
    prompt: str,
    *,
    aspect_ratio: str = "16:9",
) -> dict[str, Any]:
    """Nano Banana (Gemini Flash Image) via OpenRouter. Saves to /media/drafts/."""
    settings = get_settings()
    text = (prompt or "").strip()
    if not text:
        return {"ok": False, "error": "empty_prompt"}
    if settings.use_mock_llm or not settings.openrouter_api_key:
        return {"ok": False, "error": "image_gen_unavailable"}
    model = settings.image_model or "google/gemini-2.5-flash-image"
    stem = hashlib.sha1(f"{aspect_ratio}:{text}".encode()).hexdigest()[:20]
    headers = _openrouter_headers()
    url: str | None = None
    b64: str | None = None
    last_error = ""
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            resp = await client.post(
                OPENROUTER_IMAGES_URL,
                headers=headers,
                json={
                    "model": model,
                    "prompt": text,
                    "n": 1,
                    "aspect_ratio": aspect_ratio,
                    "image_config": {"aspect_ratio": aspect_ratio},
                },
            )
            if resp.status_code < 400:
                url, b64 = _extract_image_payload(resp.json())
            else:
                last_error = f"images HTTP {resp.status_code}: {resp.text[:220]}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:220]
        if not url and not b64:
            try:
                resp = await client.post(
                    OPENROUTER_URL,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": text}],
                        "modalities": ["image", "text"],
                        "image_config": {"aspect_ratio": aspect_ratio},
                        # OpenRouter otherwise reserves a huge default completion budget
                        # (~29k tokens) and 402s even when a few dollars remain.
                        "max_tokens": 2048,
                    },
                )
                if resp.status_code >= 400:
                    last_error = f"chat HTTP {resp.status_code}: {resp.text[:220]}"
                else:
                    url, b64 = _extract_image_payload(resp.json())
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)[:220]
        if b64 and not url:
            try:
                raw = base64.b64decode(b64, validate=False)
                src = _persist_image_bytes(raw, stem=stem)
                return {"ok": True, "src": src, "mime": "image/png"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"b64_persist: {exc}"[:220]}
        if url and url.startswith("data:image"):
            decoded = _decode_data_url(url)
            if not decoded:
                return {"ok": False, "error": "invalid_data_url"}
            raw, mime = decoded
            src = _persist_image_bytes(raw, stem=stem, mime=mime)
            return {"ok": True, "src": src, "mime": mime}
        if url and url.startswith(("http://", "https://")):
            try:
                img = await client.get(url, follow_redirects=True, timeout=60)
                img.raise_for_status()
                mime = (img.headers.get("content-type") or "image/png").split(";")[0].strip()
                if not mime.startswith("image/"):
                    mime = "image/png"
                src = _persist_image_bytes(img.content, stem=stem, mime=mime)
                return {"ok": True, "src": src, "mime": mime}
            except Exception as exc:  # noqa: BLE001
                host = urlparse(url).netloc
                log.warning("openrouter_image_download_failed", host=host, error=str(exc)[:160])
                return {"ok": True, "src": url, "mime": "image/png", "remote": True}
    log.warning("openrouter_image_failed", error=last_error, model=model)
    return {"ok": False, "error": last_error or "no_image_in_response"}
