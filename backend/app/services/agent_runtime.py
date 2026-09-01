"""Shared runtime helpers every phase agent repeats.

These two operations appeared verbatim in each of the 13 agents — the profile lookup
15 times, the supersede-prior-findings block 10 times, identical except for the
agent_key string. Extracted so the ledger contract lives in one place: an agent that
re-runs must retire its previous pending findings, or the review queue accumulates
stale duplicates that all point at the same profile row.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClientDigitalProfile, FindingsLedger


async def get_profile(db: AsyncSession, client_id: UUID) -> ClientDigitalProfile:
    """The client's shared-memory row. Raises if absent — every phase requires it."""
    return (
        await db.execute(
            select(ClientDigitalProfile).where(
                ClientDigitalProfile.client_id == client_id
            )
        )
    ).scalar_one()


async def supersede_pending_findings(
    db: AsyncSession, *, client_id: UUID, agent_key: str
) -> int:
    """Retire this agent's still-pending findings before it publishes new ones.

    Returns how many were superseded. Without this a re-run leaves the older pending
    rows in the queue, and approving the phase would resolve findings for output the
    reviewer never saw.
    """
    prior = (
        await db.execute(
            select(FindingsLedger).where(
                FindingsLedger.client_id == client_id,
                FindingsLedger.agent_key == agent_key,
                FindingsLedger.status == "pending",
            )
        )
    ).scalars().all()
    for row in prior:
        row.status = "superseded"
    return len(prior)
