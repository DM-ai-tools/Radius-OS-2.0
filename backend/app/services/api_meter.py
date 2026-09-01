"""Record cost and latency for every outbound API call.

Context (client_id, session_id, agent_key) is set once per chat turn in the
orchestrator so integration modules can log without threading a DB session through
every helper.
"""

from __future__ import annotations

import time
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
    __slots__ = ("client_id", "session_id", "agent_key")

    def __init__(
        self,
        *,
        client_id: UUID | None = None,
        session_id: UUID | None = None,
        agent_key: str | None = None,
    ) -> None:
        self.client_id = client_id
        self.session_id = session_id
        self.agent_key = agent_key


_meter_ctx: ContextVar[_MeterContext] = ContextVar(
    "api_meter_ctx", default=_MeterContext()
)


@asynccontextmanager
async def api_meter_context(
    *,
    client_id: UUID | None = None,
    session_id: UUID | None = None,
    agent_key: str | None = None,
):
    token: Token = _meter_ctx.set(
        _MeterContext(
            client_id=client_id,
            session_id=session_id,
            agent_key=agent_key,
        )
    )
    try:
        yield
    finally:
        _meter_ctx.reset(token)


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

    cost = estimated_cost_usd
    if cost is None and provider.lower() in ("openrouter", "anthropic", "llm"):
        cost = estimate_llm_cost_usd(model, prompt_tokens=pt, completion_tokens=ct)
    if cost is None and status == "success":
        cost = estimate_provider_cost_usd(provider)

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
