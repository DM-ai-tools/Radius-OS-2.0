"""API cost tracking — platform and per-client spend.

Every total here is a database aggregate over the whole window. The breakdowns used to
be summed from the latest 500 (platform) or 200 (per client) calls only, so a busy
client's total was undercounted and the vendor rows did not add up to the headline
figure. Only the "recent calls" lists are capped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiUsageLog, Client
from app.services.agent_handoff import AGENT_LABELS

RECENT_CALLS = 75
UNLINKED_PHASE = "Not linked to a phase"


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


def phase_label(agent_key: str | None) -> str:
    """The phase name people see in the app ("Search Demand"), not the agent key."""
    if not agent_key or agent_key == "unattributed":
        return UNLINKED_PHASE
    return AGENT_LABELS.get(agent_key, agent_key.replace("_", " ").title())


def _aggregate_vendor_model_rows(
    rows: list[Any],
    *,
    success_only: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build cost_by_vendor and cost_by_model from usage rows.

    A row is one call, or one pre-aggregated group carrying its own ``calls`` count.
    """
    by_vendor: dict[str, dict[str, float | int]] = {}
    by_model: dict[str, dict[str, Any]] = {}

    for r in rows:
        if success_only and r.status != "success":
            continue
        cost = float(r.estimated_cost_usd or 0)
        calls = int(getattr(r, "calls", 1) or 1)
        vendor = vendor_label(r.provider, r.model)
        vb = by_vendor.setdefault(vendor, {"cost_usd": 0.0, "calls": 0, "unpriced_calls": 0})
        vb["cost_usd"] = float(vb["cost_usd"]) + cost
        vb["calls"] = int(vb["calls"]) + calls
        vb["unpriced_calls"] = int(vb["unpriced_calls"]) + int(getattr(r, "unpriced_calls", 0) or 0)

        mkey = f"{r.provider or 'unknown'}::{model_label(r.provider, r.model, r.operation)}"
        mb = by_model.setdefault(
            mkey,
            {
                "provider": r.provider or "unknown",
                "model": model_label(r.provider, r.model, r.operation),
                "vendor": vendor,
                "cost_usd": 0.0,
                "calls": 0,
                "unpriced_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
        )
        mb["cost_usd"] = float(mb["cost_usd"]) + cost
        mb["calls"] = int(mb["calls"]) + calls
        mb["unpriced_calls"] = int(mb["unpriced_calls"]) + int(getattr(r, "unpriced_calls", 0) or 0)
        if r.prompt_tokens:
            mb["prompt_tokens"] = int(mb["prompt_tokens"]) + int(r.prompt_tokens)
        if r.completion_tokens:
            mb["completion_tokens"] = int(mb["completion_tokens"]) + int(r.completion_tokens)

    cost_by_vendor = [
        {
            "vendor": k,
            "cost_usd": round(float(v["cost_usd"]), 6),
            "calls": int(v["calls"]),
            "unpriced_calls": int(v["unpriced_calls"]),
        }
        for k, v in sorted(by_vendor.items(), key=lambda x: -float(x[1]["cost_usd"]))
    ]
    cost_by_model = [
        {
            "provider": v["provider"],
            "model": v["model"],
            "vendor": v["vendor"],
            "cost_usd": round(float(v["cost_usd"]), 6),
            "calls": int(v["calls"]),
            "unpriced_calls": int(v["unpriced_calls"]),
            "prompt_tokens": int(v["prompt_tokens"]) or None,
            "completion_tokens": int(v["completion_tokens"]) or None,
        }
        for v in sorted(by_model.values(), key=lambda x: -float(x["cost_usd"]))
    ]
    return cost_by_vendor, cost_by_model


# ---------------------------------------------------------------- aggregates


def _window(days: int) -> tuple[datetime, datetime, datetime]:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    return now, since, since - timedelta(days=days)


async def _total(db: AsyncSession, start: datetime, end: datetime, *filters: Any) -> tuple[float, int]:
    cost, calls = (
        await db.execute(
            select(
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
            ).where(
                ApiUsageLog.created_at >= start,
                ApiUsageLog.created_at < end,
                ApiUsageLog.status == "success",
                *filters,
            )
        )
    ).one()
    return float(cost or 0), int(calls or 0)


async def _failed_calls(db: AsyncSession, since: datetime, *filters: Any) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).where(
                    ApiUsageLog.created_at >= since,
                    ApiUsageLog.status != "success",
                    *filters,
                )
            )
        ).scalar()
        or 0
    )


async def _model_groups(db: AsyncSession, since: datetime, *filters: Any) -> list[SimpleNamespace]:
    """One row per provider + model + operation, with the group's totals."""
    rows = (
        await db.execute(
            select(
                ApiUsageLog.provider,
                ApiUsageLog.model,
                ApiUsageLog.operation,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
                func.coalesce(func.sum(ApiUsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(ApiUsageLog.completion_tokens), 0),
                func.count().filter(ApiUsageLog.estimated_cost_usd.is_(None)),
            )
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success", *filters)
            .group_by(ApiUsageLog.provider, ApiUsageLog.model, ApiUsageLog.operation)
        )
    ).all()
    return [
        SimpleNamespace(
            provider=p,
            model=m,
            operation=op,
            status="success",
            estimated_cost_usd=float(c or 0),
            calls=int(n),
            prompt_tokens=int(pt or 0) or None,
            completion_tokens=int(ct or 0) or None,
            unpriced_calls=int(unpriced or 0),
        )
        for p, m, op, c, n, pt, ct, unpriced in rows
    ]


async def _by_column(db: AsyncSession, column: Any, since: datetime, *filters: Any) -> list[tuple[Any, float, int]]:
    rows = (
        await db.execute(
            select(
                column,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
            )
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success", *filters)
            .group_by(column)
        )
    ).all()
    return sorted(((k, float(c or 0), int(n)) for k, c, n in rows), key=lambda r: -r[1])


async def _daily(db: AsyncSession, since: datetime, days: int, *filters: Any) -> list[dict[str, Any]]:
    """Spend per UTC day across the window, zero-filled so the chart has every day."""
    day = func.date(ApiUsageLog.created_at)
    rows = (
        await db.execute(
            select(day, func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0), func.count())
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success", *filters)
            .group_by(day)
        )
    ).all()
    found = {str(d)[:10]: (float(c or 0), int(n)) for d, c, n in rows if d is not None}
    out: list[dict[str, Any]] = []
    day_cursor: date = since.date()
    today = datetime.now(UTC).date()
    while day_cursor <= today and len(out) <= days:
        cost, calls = found.get(day_cursor.isoformat(), (0.0, 0))
        out.append({"date": day_cursor.isoformat(), "cost_usd": round(cost, 6), "calls": calls})
        day_cursor += timedelta(days=1)
    return out


def _agent_rows(rows: list[tuple[Any, float, int]]) -> list[dict[str, Any]]:
    return [
        {
            "agent_key": k or "unattributed",
            "label": phase_label(k),
            "cost_usd": round(c, 6),
            "calls": n,
        }
        for k, c, n in rows
    ]


def _call_row(u: ApiUsageLog) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "provider": u.provider,
        "vendor": vendor_label(u.provider, u.model),
        "operation": u.operation,
        "model": u.model or model_label(u.provider, u.model, u.operation),
        "agent_key": u.agent_key,
        "phase": phase_label(u.agent_key),
        "client_id": str(u.client_id) if u.client_id else None,
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "estimated_cost_usd": float(u.estimated_cost_usd or 0),
        "latency_ms": u.latency_ms,
        "status": u.status,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


# ------------------------------------------------------------------ payloads


async def build_cost_tracker(db: AsyncSession, *, days: int = 7) -> dict[str, Any]:
    now, since, previous_since = _window(days)

    cost_total, call_count = await _total(db, since, now + timedelta(seconds=1))
    previous_cost, _previous_calls = await _total(db, previous_since, since)
    cost_by_vendor, cost_by_model = _aggregate_vendor_model_rows(
        await _model_groups(db, since), success_only=False
    )
    cost_by_provider = await _by_column(db, ApiUsageLog.provider, since)
    cost_by_agent = await _by_column(
        db, ApiUsageLog.agent_key, since, ApiUsageLog.agent_key.isnot(None)
    )

    cost_by_client_rows = (
        await db.execute(
            select(
                ApiUsageLog.client_id,
                Client.display_name,
                Client.primary_url,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
                func.max(ApiUsageLog.created_at),
            )
            .outerjoin(Client, Client.id == ApiUsageLog.client_id)
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success")
            .group_by(ApiUsageLog.client_id, Client.display_name, Client.primary_url)
        )
    ).all()
    cost_by_client_rows = sorted(cost_by_client_rows, key=lambda r: -float(r[3] or 0))

    recent = (
        await db.execute(
            select(ApiUsageLog)
            .where(ApiUsageLog.created_at >= since)
            .order_by(ApiUsageLog.created_at.desc())
            .limit(RECENT_CALLS)
        )
    ).scalars().all()

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "summary": {
            "api_calls": call_count,
            "estimated_cost_usd": round(cost_total, 6),
            "previous_cost_usd": round(previous_cost, 6),
            "failed_calls": await _failed_calls(db, since),
            "clients_with_usage": sum(1 for row in cost_by_client_rows if row[0]),
        },
        "daily": await _daily(db, since, days),
        "cost_by_vendor": cost_by_vendor,
        "cost_by_model": cost_by_model,
        "cost_by_provider": [
            {"provider": p, "cost_usd": round(c, 6), "calls": n} for p, c, n in cost_by_provider
        ],
        "cost_by_agent": _agent_rows(cost_by_agent),
        "cost_by_client": [
            {
                "client_id": str(cid) if cid else None,
                "client_name": name or ("Deleted client" if cid else "Not linked to a client"),
                # Two clients can share a name; the site tells them apart.
                "client_url": url or None,
                "cost_usd": round(float(c or 0), 6),
                "calls": int(n),
                "last_used_at": last.isoformat() if last else None,
            }
            for cid, name, url, c, n, last in cost_by_client_rows
        ],
        "recent_api_calls": [_call_row(u) for u in recent],
    }


async def build_client_cost_summary(
    db: AsyncSession, client_id: UUID, *, days: int = 30
) -> dict[str, Any]:
    now, since, previous_since = _window(days)
    mine = ApiUsageLog.client_id == client_id

    total, call_count = await _total(db, since, now + timedelta(seconds=1), mine)
    previous_cost, _ = await _total(db, previous_since, since, mine)
    cost_by_vendor, cost_by_model = _aggregate_vendor_model_rows(
        await _model_groups(db, since, mine), success_only=False
    )
    cost_by_provider = await _by_column(db, ApiUsageLog.provider, since, mine)
    cost_by_agent = await _by_column(db, ApiUsageLog.agent_key, since, mine)
    last_used = (
        await db.execute(select(func.max(ApiUsageLog.created_at)).where(mine))
    ).scalar()
    recent = (
        await db.execute(
            select(ApiUsageLog)
            .where(mine, ApiUsageLog.created_at >= since)
            .order_by(ApiUsageLog.created_at.desc())
            .limit(RECENT_CALLS)
        )
    ).scalars().all()

    return {
        "client_id": str(client_id),
        "window_days": days,
        "estimated_cost_usd": round(total, 6),
        "previous_cost_usd": round(previous_cost, 6),
        "call_count": call_count,
        "failed_calls": await _failed_calls(db, since, mine),
        "last_used_at": last_used.isoformat() if last_used else None,
        "daily": await _daily(db, since, days, mine),
        "cost_by_vendor": cost_by_vendor,
        "cost_by_model": cost_by_model,
        "cost_by_provider": [
            {"provider": p or "unknown", "cost_usd": round(c, 6), "calls": n}
            for p, c, n in cost_by_provider
        ],
        "cost_by_agent": _agent_rows(cost_by_agent),
        "calls": [
            {**_call_row(u), "cost_usd": float(u.estimated_cost_usd or 0)} for u in recent
        ],
    }
