"""Company and prior-phase context for validation — reuse CDP, do not duplicate stores."""

from __future__ import annotations

from typing import Any

from app.models import Client, ClientDigitalProfile
from app.services.phase_validation.criteria import get_criteria


def _trim(value: Any, *, max_chars: int = 4000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[: max_chars - 3] + "..."
    if isinstance(value, list):
        return [_trim(v, max_chars=max_chars // 2) for v in value[:40]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 40:
                out["…"] = f"{len(value) - 40} more keys omitted"
                break
            out[str(k)] = _trim(v, max_chars=max_chars // 2)
        return out
    return value


def build_company_context(
    client: Client,
    profile: ClientDigitalProfile,
) -> dict[str, Any]:
    """Dynamic company context for the current client — never hard-coded."""
    commercial = dict(profile.commercial_scope or {})
    marketing = dict(profile.marketing_context or {})
    intake = marketing.get("client_intake") if isinstance(marketing.get("client_intake"), dict) else {}

    products = (
        commercial.get("products")
        or commercial.get("products_for_promotion")
        or intake.get("products")
        or intake.get("services")
    )
    geo = (
        commercial.get("geographic_focus")
        or marketing.get("geographic_focus")
        or intake.get("geographic_focus")
    )
    audience = (
        commercial.get("target_demographic")
        or marketing.get("target_audience")
        or intake.get("target_audience")
    )
    business_model = commercial.get("business_model") or intake.get("business_model")
    positioning = commercial.get("positioning") or marketing.get("positioning")
    brand = marketing.get("brand_guidelines") or intake.get("brand_guidelines")
    standards = marketing.get("compliance_constraints") or intake.get("compliance_constraints")
    keywords = commercial.get("business_keywords") or intake.get("business_keywords")

    ctx = {
        "display_name": client.display_name,
        "legal_name": client.legal_name,
        "primary_url": client.primary_url,
        "industry": client.industry,
        "tier": client.tier,
        "business_model": business_model,
        "products_services": products,
        "geographic_focus": geo,
        "target_audience": audience,
        "positioning": positioning,
        "brand_guidelines": brand,
        "compliance_standards": standards,
        "business_keywords": keywords,
        "objectives": marketing.get("objectives") or marketing.get("business_goal"),
        "known_competitors": marketing.get("competitors") or commercial.get("competitors"),
    }
    # Drop empty — keep prompt focused
    return {k: v for k, v in ctx.items() if v not in (None, "", [], {})}


def _slim_website_prior(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep crawl inventory, drop per-page audit blobs that blow the validator budget."""
    keep = {
        k: raw.get(k)
        for k in (
            "pages_found",
            "indexable",
            "sample_urls",
            "note",
            "error",
            "seo_overall_score",
            "seo_pages_analyzed",
            "seo_score_band",
        )
        if raw.get(k) not in (None, "", [])
    }
    crawl = raw.get("crawl_technical")
    if isinstance(crawl, dict):
        summary = crawl.get("summary") if isinstance(crawl.get("summary"), dict) else crawl
        for key in ("pages_found", "sample_urls", "note", "error", "status_samples"):
            if keep.get(key) in (None, "", []) and summary.get(key) not in (None, "", []):
                val = summary.get(key)
                if key == "status_samples" and isinstance(val, list):
                    keep["sample_urls"] = [
                        (row.get("url") if isinstance(row, dict) else row)
                        for row in val[:12]
                    ]
                else:
                    keep[key] = val
    return keep or {"note": "website situation present (details omitted for validator budget)"}


def build_prior_phase_context(
    profile: ClientDigitalProfile,
    agent_key: str,
) -> dict[str, Any]:
    criteria = get_criteria(agent_key)
    if not criteria:
        return {}
    prior: dict[str, Any] = {}
    for attr in criteria.prior_phase_attrs:
        raw = getattr(profile, attr, None)
        if not raw:
            continue
        if attr == "website_situation_summary" and isinstance(raw, dict):
            prior[attr] = _slim_website_prior(raw)
        else:
            prior[attr] = _trim(raw, max_chars=1800)
    return prior


_WEBSITE_INVENTORY_KEYS = (
    "sample_urls",
    "pages_found",
    "indexable",
    "discovered_urls",
    "status_samples",
    "pages",
    "note",
    "error",
    "crawl_error",
    "page_hierarchy",
)


def _merge_website_inventory(card: dict[str, Any], summary: dict[str, Any] | None) -> dict[str, Any]:
    """UI website_audit cards keep inventory under tabs; CDP summary has the crawl URLs."""
    merged = dict(card)
    if not summary:
        return merged
    for key in _WEBSITE_INVENTORY_KEYS:
        if merged.get(key) in (None, "", []) and summary.get(key) not in (None, "", []):
            merged[key] = summary[key]
    return merged


def _slim_scorecard(row: dict[str, Any]) -> dict[str, Any]:
    derived = row.get("derived") if isinstance(row.get("derived"), dict) else {}
    return {
        k: v
        for k, v in {
            "name": row.get("name"),
            "url": row.get("url"),
            "tier": row.get("tier"),
            "tier_name": row.get("tier_name"),
            "source": row.get("source"),
            "composite": row.get("composite"),
            "maturity": derived.get("maturity") or row.get("maturity"),
            "future_threat": derived.get("future_threat") or row.get("future_threat"),
            "similarity": derived.get("similarity"),
        }.items()
        if v not in (None, "", [])
    }


def compact_phase_output(agent_key: str, output: dict[str, Any]) -> dict[str, Any]:
    """Drop UI/heatmap blobs so the validator always sees the deliverable, not a truncated prompt."""
    if not output:
        return output
    if agent_key == "competitor_market_agent":
        baseline = output.get("client_baseline")
        baseline_s = None
        if isinstance(baseline, dict):
            baseline_s = {
                "name": baseline.get("name"),
                "url": baseline.get("url"),
                "maturity_score": baseline.get("maturity_score"),
            }
        cards = output.get("scorecards") or []
        slim_cards = [_slim_scorecard(s) for s in cards[:12] if isinstance(s, dict)]
        compact = {
            "card_type": output.get("card_type"),
            "industry": output.get("industry"),
            "analysis_mode": output.get("analysis_mode"),
            "competitors_scored": output.get("competitors_scored"),
            "competitors_in_report": output.get("competitors_in_report"),
            "executive_summary": output.get("executive_summary"),
            "client_baseline": baseline_s or output.get("client_baseline_maturity"),
            "competitors": output.get("competitors") or [],
            "scorecards": slim_cards or cards[:12],
            "tier_overview": output.get("tier_overview") or [],
            "excluded_tier5": output.get("excluded_tier5") or [],
            "recommendations": output.get("recommendations"),
            "discovery_sources": output.get("discovery_sources"),
            "empty": output.get("empty"),
        }
        return {k: v for k, v in compact.items() if v not in (None, "", [])}
    if agent_key == "website_situation_agent":
        compact = {
            "card_type": output.get("card_type"),
            "pages_found": output.get("pages_found"),
            "indexable": output.get("indexable"),
            "sample_urls": output.get("sample_urls") or output.get("discovered_urls"),
            "status_samples": output.get("status_samples"),
            "pages": output.get("pages"),
            "note": output.get("note"),
            "error": output.get("error") or output.get("crawl_error"),
            "tabs_run": output.get("tabs_run"),
            "severities": output.get("severities"),
        }
        tabs = output.get("tabs")
        if isinstance(tabs, dict):
            compact["tabs"] = {
                name: {
                    k: tab.get(k)
                    for k in (
                        "pages_found",
                        "indexable",
                        "note",
                        "error",
                        "status",
                        "discovered_urls",
                        "status_samples",
                    )
                    if isinstance(tab, dict) and tab.get(k) not in (None, "", [])
                }
                for name, tab in tabs.items()
            }
        return {k: v for k, v in compact.items() if v not in (None, "", [], {})}
    return output


def extract_phase_output(
    *,
    profile: ClientDigitalProfile,
    agent_key: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prefer the structured card payload; fall back to CDP working summary."""
    found: dict[str, Any] | None = None
    for ev in reversed(events):
        if ev.get("type") != "structured_card":
            continue
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("card_type") == "phase_validation_report":
            continue
        if payload.get("agent_key") and payload.get("agent_key") != agent_key:
            continue
        # Strip UI-only keys that dilute validation
        cleaned = {
            k: v
            for k, v in payload.items()
            if k not in ("actions", "required_role", "event_type")
        }
        if cleaned:
            if agent_key == "website_situation_agent":
                summary = getattr(profile, "website_situation_summary", None)
                cleaned = _merge_website_inventory(
                    cleaned, summary if isinstance(summary, dict) else None
                )
            found = cleaned
            break

    if found is None:
        criteria = get_criteria(agent_key)
        if criteria and criteria.summary_attr:
            summary = getattr(profile, criteria.summary_attr, None)
            if isinstance(summary, dict) and summary:
                found = dict(summary)

    if found is None and agent_key == "discovery_agent":
        found = {
            "commercial_scope": dict(profile.commercial_scope or {}),
            "marketing_context": dict(profile.marketing_context or {}),
            "discovery_status": profile.discovery_status,
        }

    if not found:
        return {}
    return compact_phase_output(agent_key, found)


def context_keys_used(company_ctx: dict[str, Any], prior: dict[str, Any]) -> list[str]:
    keys = [f"company.{k}" for k in company_ctx]
    keys.extend(f"prior.{k}" for k in prior)
    return keys
