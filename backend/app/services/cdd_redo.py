"""What it costs to redo a phase.

Estimates come from this client's own ``api_usage_log`` history for that agent
wherever possible — real measured spend beats a guessed table, and it
self-corrects as the pipeline changes. The static fallback only applies when a
phase has never run for this client.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiUsageLog

# Fallback order-of-magnitude figures, used only with no history. Phase 5 is the
# one measured figure (≈$0.06 of LLM spend plus provider calls, ~6 minutes);
# the rest are deliberately coarse and labelled as estimates.
_FALLBACK: dict[str, tuple[float, int]] = {
    "discovery_agent": (0.02, 120),
    "tracking_access_agent": (0.01, 90),
    "website_situation_agent": (0.03, 300),
    "competitor_market_agent": (0.15, 480),
    "search_demand": (0.06, 360),
    "site_architecture": (0.02, 120),
    "content_strategy": (0.05, 240),
    "technical_seo": (0.04, 600),
    "content_audit": (0.03, 240),
    "content_planning": (0.02, 120),
    "content_production": (0.20, 300),
    "on_page_seo": (0.03, 180),
    "publishing": (0.01, 120),
}


async def estimate_redo(
    db: AsyncSession,
    *,
    client_id: UUID,
    agent_key: str,
) -> dict[str, Any]:
    """Estimated cost and duration to re-run one phase for this client."""
    row = (
        await db.execute(
            select(
                func.sum(ApiUsageLog.estimated_cost_usd),
                func.sum(ApiUsageLog.latency_ms),
                func.count(ApiUsageLog.id),
            ).where(
                ApiUsageLog.client_id == client_id,
                ApiUsageLog.agent_key == agent_key,
            )
        )
    ).first()

    total_cost, total_latency_ms, call_count = (row or (None, None, 0))
    if call_count:
        return {
            "source": "history",
            "cost_usd": round(float(total_cost or 0), 4),
            "duration_seconds": int((total_latency_ms or 0) / 1000),
            "provider_calls": int(call_count),
            "note": f"Based on {int(call_count)} recorded provider/LLM calls for this client.",
        }

    cost, seconds = _FALLBACK.get(agent_key, (0.05, 240))
    return {
        "source": "estimate",
        "cost_usd": cost,
        "duration_seconds": seconds,
        "provider_calls": None,
        "note": "No recorded history for this client — typical figure.",
    }
