"""Run-time CDD guards for the phase runner.

Two jobs, both at the single choke point every agent passes through
(``run_phase_with_validation``) rather than in each of the 13 agents:

1. Stamp the phase with the CDD version it is starting against.
2. Refuse to start a phase whose inputs are stale and unapproved.

Both degrade to a no-op on any error. A bookkeeping failure must never take the
pipeline down.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.services.agent_handoff import blocked_events, label
from app.services.cdd_impact import blocking_stale_upstream

log = get_logger("cdd_gate")


async def record_phase_start_safe(
    db: AsyncSession, *, client_id: UUID, agent_key: str
) -> None:
    """Stamp the starting CDD version, ignoring any bookkeeping failure."""
    try:
        from app.services.cdd_versions import record_phase_start

        await record_phase_start(db, client_id=client_id, agent_key=agent_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("cdd_phase_start_record_failed", agent_key=agent_key, error=str(exc)[:200])


async def stale_upstream_block(
    db: AsyncSession, *, client_id: UUID, agent_key: str
) -> list[dict] | None:
    """Block events when this phase's inputs are stale, else None.

    A phase is never blocked by its own staleness — re-running it is exactly how
    the operator clears it.
    """
    try:
        from app.services.cdd_impact import PhaseStaleness, StalenessState
        from app.services.cdd_versions import build_staleness_report

        raw = await build_staleness_report(db, client_id=client_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "cdd_staleness_check_failed",
            agent_key=agent_key,
            error=(str(exc) or type(exc).__name__)[:200],
            error_type=type(exc).__name__,
        )
        return None


    report = {
        phase: PhaseStaleness(
            agent_key=phase,
            state=StalenessState(entry.get("state", "fresh")),
            fields=list(entry.get("fields") or []),
            upstream=list(entry.get("upstream") or []),
        )
        for phase, entry in raw.items()
    }
    stale_deps = blocking_stale_upstream(agent_key, report)
    if not stale_deps:
        return None

    detail = "; ".join(
        f"{label(dep)} ({report[dep].reason()})" for dep in stale_deps
    )
    return blocked_events(
        agent_key,
        (
            f"Inputs are out of date after a CDD update: {detail}. "
            "Re-run the affected phase(s), or dismiss the staleness if the change "
            "does not matter to them."
        ),
        route_to=stale_deps[0],
    )
