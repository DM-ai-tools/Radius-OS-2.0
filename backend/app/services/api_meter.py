"""Record cost and latency for every outbound API call.

Context (client_id, session_id, agent_key) is set once per chat turn in the
orchestrator so integration modules can log without threading a DB session through
every helper.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.db import AsyncSessionLocal
from app.logging_config import get_logger
from app.models.operations import ApiUsageLog

log = get_logger("api_meter")

# USD per 1M tokens — (input, output). Rough OpenRouter/Anthropic list prices.
_LLM_PRICE_PER_M: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.8, 4.0),
    "google/gemini-2.5-pro": (1.25, 5.0),
    "google/gemini-2.5-flash": (0.15, 0.6),
    "openai/gpt-4o": (2.5, 10.0),
    "openai/gpt-4o-mini": (0.15, 0.6),
}

# Flat per-call estimates when providers don't return token usage.
_PROVIDER_FLAT_USD: dict[str, float] = {
    "dataforseo": 0.0025,
    "ahrefs": 0.01,
    "perplexity": 0.005,
    "pagespeed": 0.0,
    "web_fetch": 0.0,
}


class _MeterContext:
    __slots__ = ("agent_key", "client_id", "session_id", "phase_budget_usd", "phase_spent", "phase_reserved")

    def __init__(
        self,
        *,
        client_id: UUID | None = None,
        session_id: UUID | None = None,
        agent_key: str | None = None,
        phase_budget_usd: float | None = None,
    ) -> None:
        self.client_id = client_id
        self.session_id = session_id
        self.agent_key = agent_key
        self.phase_budget_usd = phase_budget_usd
        self.phase_spent = 0.0
        self.phase_reserved = 0.0


_meter_ctx: ContextVar[_MeterContext] = ContextVar(
    "api_meter_ctx", default=_MeterContext()
)


@asynccontextmanager
async def api_meter_context(
    *,
    client_id: UUID | None = None,
    session_id: UUID | None = None,
    agent_key: str | None = None,
    phase_budget_usd: float | None = None,
):
    token: Token = _meter_ctx.set(
        _MeterContext(
            client_id=client_id,
            session_id=session_id,
            agent_key=agent_key,
            phase_budget_usd=phase_budget_usd,
        )
    )
    try:
        yield
    finally:
        _meter_ctx.reset(token)


def begin_paid_call(reserve: float) -> bool:
    """Hold ``reserve`` dollars against the phase cap. True when the call may start.

    No cap means every call may start. The reserve is released by ``end_paid_call``
    once the real cost is known, so concurrent calls cannot all slip under the cap.
    """
    ctx = _meter_ctx.get()
    cap = ctx.phase_budget_usd
    if not cap or cap <= 0:
        return True
    if ctx.phase_spent + ctx.phase_reserved + reserve > cap:
        log.warning(
            "phase_budget_blocked",
            agent_key=ctx.agent_key,
            spent_usd=round(ctx.phase_spent, 4),
            reserved_usd=round(ctx.phase_reserved, 4),
            cap_usd=cap,
        )
        return False
    ctx.phase_reserved += reserve
    return True


def end_paid_call(reserve: float) -> None:
    """Release the hold taken by ``begin_paid_call``. The logged cost stays in ``phase_spent``."""
    ctx = _meter_ctx.get()
    if not ctx.phase_budget_usd:
        return
    ctx.phase_reserved = max(0.0, ctx.phase_reserved - reserve)


def estimate_llm_cost_usd(
    model: str | None,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> Decimal | None:
    if prompt_tokens is None and completion_tokens is None:
        return None
    pt = int(prompt_tokens or 0)
    ct = int(completion_tokens or 0)
    key = (model or "").strip().lower()
    rates = _LLM_PRICE_PER_M.get(key)
    if not rates:
        for partial, price in _LLM_PRICE_PER_M.items():
            if partial in key or key in partial:
                rates = price
                break
    if not rates:
        rates = (2.0, 8.0)
    inp, out = rates
    cost = (pt / 1_000_000) * inp + (ct / 1_000_000) * out
    return Decimal(str(round(cost, 6)))


def estimate_provider_cost_usd(provider: str, *, units: int = 1) -> Decimal:
    unit = _PROVIDER_FLAT_USD.get(provider.lower(), 0.0)
    return Decimal(str(round(unit * max(1, units), 6)))


def anthropic_call_cost(
    model: str | None,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_write_tokens: int | None = None,
    cache_read_tokens: int | None = None,
) -> Decimal | None:
    """Token price plus Anthropic cache write (1.25×) and cache read (0.1×)."""
    base = estimate_llm_cost_usd(
        model, prompt_tokens=input_tokens, completion_tokens=output_tokens
    )
    if base is None and not cache_write_tokens and not cache_read_tokens:
        return None
    key = (model or "").strip().lower()
    rates = _LLM_PRICE_PER_M.get(key)
    if not rates:
        for partial, price in _LLM_PRICE_PER_M.items():
            if partial in key or key in partial:
                rates = price
                break
    input_rate = rates[0] if rates else 2.0
    extra = (int(cache_write_tokens or 0) / 1_000_000) * input_rate * 1.25
    extra += (int(cache_read_tokens or 0) / 1_000_000) * input_rate * 0.1
    return Decimal(str(round(float(base or 0) + extra, 6)))


def reported_usage_cost(usage: dict[str, Any] | None) -> float | None:
    """Dollar amount the provider put on the response. None when it didn't."""
    if not isinstance(usage, dict):
        return None
    raw = usage.get("cost")
    if raw is None and isinstance(usage.get("cost_details"), dict):
        raw = usage["cost_details"].get("upstream_inference_cost")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def resolve_recorded_cost(
    *,
    provider: str,
    status: str,
    estimated_cost_usd: Decimal | float | None,
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    apply_flat_estimate: bool = True,
) -> Decimal | None:
    """Use a reported dollar amount, then token rates, then a known flat price.

    ``apply_flat_estimate`` stays off when a flat price would invent a charge
    the provider did not report (Ahrefs bills in units, not $0.01 a call).
    """
    if estimated_cost_usd is not None:
        return Decimal(str(estimated_cost_usd))
    if provider.lower() in ("openrouter", "anthropic", "llm"):
        cost = estimate_llm_cost_usd(
            model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )
        if cost is not None:
            return cost
    if status == "success" and apply_flat_estimate and provider.lower() in _PROVIDER_FLAT_USD:
        return estimate_provider_cost_usd(provider)
    return None


async def provider_spend_today_usd(provider: str) -> float | None:
    """Sum of recorded cost for this provider since UTC midnight.

    None means the meter could not be read. Callers must not treat that as zero
    and must not invent a spend figure.
    """
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        async with AsyncSessionLocal() as db:
            total = await db.scalar(
                select(func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0)).where(
                    ApiUsageLog.provider == provider,
                    ApiUsageLog.created_at >= start,
                )
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("api_meter_spend_read_failed", provider=provider, error=str(exc)[:200])
        return None
    try:
        return float(total or 0)
    except (TypeError, ValueError):
        return None


async def record_api_call(
    *,
    provider: str,
    operation: str,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    estimated_cost_usd: Decimal | float | None = None,
    latency_ms: int | None = None,
    status: str = "success",
    error_detail: str | None = None,
    request_meta: dict[str, Any] | None = None,
    client_id: UUID | None = None,
    session_id: UUID | None = None,
    agent_key: str | None = None,
    apply_flat_estimate: bool = True,
) -> None:
    ctx = _meter_ctx.get()
    cid = client_id if client_id is not None else ctx.client_id
    sid = session_id if session_id is not None else ctx.session_id
    akey = agent_key if agent_key is not None else ctx.agent_key

    pt = prompt_tokens
    ct = completion_tokens
    total = None
    if pt is not None or ct is not None:
        total = int(pt or 0) + int(ct or 0)

    # A reported amount or token price wins. A flat price is only applied for
    # providers whose call has a known dollar rate. Manus credits, Ahrefs units,
    # and other unpriced calls stay null rather than being stored as $0.
    cost = resolve_recorded_cost(
        provider=provider,
        status=status,
        estimated_cost_usd=estimated_cost_usd,
        model=model,
        prompt_tokens=pt,
        completion_tokens=ct,
        apply_flat_estimate=apply_flat_estimate,
    )

    row = ApiUsageLog(
        client_id=cid,
        session_id=sid,
        agent_key=akey,
        provider=provider,
        operation=operation,
        model=model,
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=total,
        estimated_cost_usd=Decimal(str(cost)) if cost is not None else None,
        latency_ms=latency_ms,
        status=status,
        error_detail=(error_detail or "")[:500] or None,
        request_meta=request_meta,
    )
    if ctx.phase_budget_usd:
        ctx.phase_spent += float(cost or 0)
    try:
        async with AsyncSessionLocal() as db:
            db.add(row)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("api_meter_persist_failed", provider=provider, error=str(exc)[:200])


def _usage_tokens(usage: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not usage:
        return None, None
    pt = usage.get("prompt_tokens") or usage.get("input_tokens")
    ct = usage.get("completion_tokens") or usage.get("output_tokens")
    return (
        int(pt) if pt is not None else None,
        int(ct) if ct is not None else None,
    )
