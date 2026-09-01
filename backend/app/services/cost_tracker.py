"""API cost tracking — platform and per-client spend."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiUsageLog, Client


def vendor_label(provider: str | None, model: str | None = None) -> str:
    """Human-friendly vendor bucket for cost segregation (Claude, Ahrefs, etc.)."""
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    if p == "ahrefs":
        return "Ahrefs"
    if p == "dataforseo":
        return "DataForSEO"
    if p == "pagespeed":
        return "PageSpeed Insights"
    if p in ("web_fetch", "crawl"):
        return "Web fetch"
    if p == "perplexity":
        return "Perplexity"
    if p in ("openrouter", "anthropic", "llm"):
        if p == "anthropic" or "claude" in m:
            return "Claude"
        if "gemini" in m:
            return "Gemini"
        if "gpt" in m or "openai/" in m:
            return "GPT"
        if "perplexity" in m or "sonar" in m:
            return "Perplexity"
        if m:
            return "LLM (other)"
        return "Claude" if p == "anthropic" else "OpenRouter"
    if p:
        return p.replace("_", " ").title()
    return "Unknown"


def model_label(provider: str | None, model: str | None, operation: str | None = None) -> str:
    """Display label for model/operation row."""
    if model and str(model).strip():
        return str(model).strip()
    if operation and str(operation).strip():
        return str(operation).strip()
    return vendor_label(provider, model)


def _aggregate_vendor_model_rows(
    rows: list[Any],
    *,
    success_only: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build cost_by_vendor and cost_by_model from ApiUsageLog rows."""
    by_vendor: dict[str, dict[str, float | int]] = {}
    by_model: dict[str, dict[str, Any]] = {}

    for r in rows:
        if success_only and r.status != "success":
            continue
        cost = float(r.estimated_cost_usd or 0)
        vendor = vendor_label(r.provider, r.model)
        vb = by_vendor.setdefault(vendor, {"cost_usd": 0.0, "calls": 0})
        vb["cost_usd"] = float(vb["cost_usd"]) + cost
        vb["calls"] = int(vb["calls"]) + 1

        mkey = f"{r.provider or 'unknown'}::{model_label(r.provider, r.model, r.operation)}"
        mb = by_model.setdefault(
            mkey,
            {
                "provider": r.provider or "unknown",
                "model": model_label(r.provider, r.model, r.operation),
                "vendor": vendor,
                "cost_usd": 0.0,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
        )
        mb["cost_usd"] = float(mb["cost_usd"]) + cost
        mb["calls"] = int(mb["calls"]) + 1
        if r.prompt_tokens:
            mb["prompt_tokens"] = int(mb["prompt_tokens"]) + int(r.prompt_tokens)
        if r.completion_tokens:
            mb["completion_tokens"] = int(mb["completion_tokens"]) + int(r.completion_tokens)

    cost_by_vendor = [
        {"vendor": k, "cost_usd": round(float(v["cost_usd"]), 6), "calls": int(v["calls"])}
        for k, v in sorted(by_vendor.items(), key=lambda x: -float(x[1]["cost_usd"]))
    ]
    cost_by_model = [
        {
            "provider": v["provider"],
            "model": v["model"],
            "vendor": v["vendor"],
            "cost_usd": round(float(v["cost_usd"]), 6),
            "calls": int(v["calls"]),
            "prompt_tokens": int(v["prompt_tokens"]) or None,
            "completion_tokens": int(v["completion_tokens"]) or None,
        }
        for v in sorted(by_model.values(), key=lambda x: -float(x["cost_usd"]))
    ]
    return cost_by_vendor, cost_by_model


async def build_cost_tracker(db: AsyncSession, *, days: int = 7) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)

    usage_rows = (
        await db.execute(
            select(ApiUsageLog)
            .where(ApiUsageLog.created_at >= since)
            .order_by(ApiUsageLog.created_at.desc())
            .limit(500)
        )
    ).scalars().all()

    success_rows = [u for u in usage_rows if u.status == "success"]
    cost_by_vendor, cost_by_model = _aggregate_vendor_model_rows(success_rows, success_only=False)

    cost_total = (
        await db.execute(
            select(func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0)).where(
                ApiUsageLog.created_at >= since,
                ApiUsageLog.status == "success",
            )
        )
    ).scalar()

    call_count = (
        await db.execute(
            select(func.count()).where(
                ApiUsageLog.created_at >= since,
                ApiUsageLog.status == "success",
            )
        )
    ).scalar()

    cost_by_provider = (
        await db.execute(
            select(
                ApiUsageLog.provider,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
            )
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success")
            .group_by(ApiUsageLog.provider)
            .order_by(func.sum(ApiUsageLog.estimated_cost_usd).desc())
        )
    ).all()

    cost_by_agent = (
        await db.execute(
            select(
                ApiUsageLog.agent_key,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
            )
            .where(
                ApiUsageLog.created_at >= since,
                ApiUsageLog.status == "success",
                ApiUsageLog.agent_key.isnot(None),
            )
            .group_by(ApiUsageLog.agent_key)
            .order_by(func.sum(ApiUsageLog.estimated_cost_usd).desc())
        )
    ).all()

    cost_by_client_rows = (
        await db.execute(
            select(
                ApiUsageLog.client_id,
                Client.display_name,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
            )
            .outerjoin(Client, Client.id == ApiUsageLog.client_id)
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success")
            .group_by(ApiUsageLog.client_id, Client.display_name)
            .order_by(func.sum(ApiUsageLog.estimated_cost_usd).desc())
        )
    ).all()

    recent_calls = [
        {
            "id": str(u.id),
            "provider": u.provider,
            "vendor": vendor_label(u.provider, u.model),
            "operation": u.operation,
            "model": u.model or model_label(u.provider, u.model, u.operation),
            "agent_key": u.agent_key,
            "client_id": str(u.client_id) if u.client_id else None,
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "estimated_cost_usd": float(u.estimated_cost_usd or 0),
            "latency_ms": u.latency_ms,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in usage_rows[:75]
    ]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_days": days,
        "summary": {
            "api_calls": int(call_count or 0),
            "estimated_cost_usd": float(cost_total or 0),
            "clients_with_usage": sum(1 for cid, _, _, _ in cost_by_client_rows if cid),
        },
        "cost_by_vendor": cost_by_vendor,
        "cost_by_model": cost_by_model,
        "cost_by_provider": [
            {"provider": p, "cost_usd": float(c or 0), "calls": int(n)}
            for p, c, n in cost_by_provider
        ],
        "cost_by_agent": [
            {"agent_key": k or "unattributed", "cost_usd": float(c or 0), "calls": int(n)}
            for k, c, n in cost_by_agent
        ],
        "cost_by_client": [
            {
                "client_id": str(cid) if cid else None,
                "client_name": name or "Unattributed",
                "cost_usd": float(c or 0),
                "calls": int(n),
            }
            for cid, name, c, n in cost_by_client_rows
        ],
        "recent_api_calls": recent_calls,
    }


async def build_client_cost_summary(
    db: AsyncSession, client_id: UUID, *, days: int = 30
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(ApiUsageLog)
            .where(ApiUsageLog.client_id == client_id, ApiUsageLog.created_at >= since)
            .order_by(ApiUsageLog.created_at.desc())
            .limit(200)
        )
    ).scalars().all()

    cost_by_provider: dict[str, dict[str, float | int]] = {}
    cost_by_agent: dict[str, dict[str, float | int]] = {}
    for r in rows:
        if r.status != "success":
            continue
        cost = float(r.estimated_cost_usd or 0)
        prov = r.provider or "unknown"
        agent = r.agent_key or "unattributed"
        bucket = cost_by_provider.setdefault(prov, {"cost_usd": 0.0, "calls": 0})
        bucket["cost_usd"] = float(bucket["cost_usd"]) + cost
        bucket["calls"] = int(bucket["calls"]) + 1
        abucket = cost_by_agent.setdefault(agent, {"cost_usd": 0.0, "calls": 0})
        abucket["cost_usd"] = float(abucket["cost_usd"]) + cost
        abucket["calls"] = int(abucket["calls"]) + 1

    success_rows = [r for r in rows if r.status == "success"]
    cost_by_vendor, cost_by_model = _aggregate_vendor_model_rows(success_rows, success_only=False)

    total = sum(float(r.estimated_cost_usd or 0) for r in rows if r.status == "success")
    return {
        "client_id": str(client_id),
        "window_days": days,
        "estimated_cost_usd": round(total, 6),
        "call_count": len([r for r in rows if r.status == "success"]),
        "cost_by_vendor": cost_by_vendor,
        "cost_by_model": cost_by_model,
        "cost_by_provider": [
            {"provider": k, "cost_usd": v["cost_usd"], "calls": v["calls"]}
            for k, v in sorted(cost_by_provider.items(), key=lambda x: -float(x[1]["cost_usd"]))
        ],
        "cost_by_agent": [
            {"agent_key": k, "cost_usd": v["cost_usd"], "calls": v["calls"]}
            for k, v in sorted(cost_by_agent.items(), key=lambda x: -float(x[1]["cost_usd"]))
        ],
        "calls": [
            {
                "id": str(r.id),
                "provider": r.provider,
                "vendor": vendor_label(r.provider, r.model),
                "operation": r.operation,
                "model": r.model or model_label(r.provider, r.model, r.operation),
                "agent_key": r.agent_key,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cost_usd": float(r.estimated_cost_usd or 0),
                "latency_ms": r.latency_ms,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows[:75]
        ],
    }
