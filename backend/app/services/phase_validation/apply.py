"""Apply validation decisions to CDP, findings, jobs, and chat events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_config import get_logger
from app.models import AgentJob, Client, ClientDigitalProfile
from app.services.agent_runtime import supersede_pending_findings
from app.services.audit import log_event
from app.services.phase_validation.criteria import (
    AGENT_STATUS_ATTR,
    AGENT_SUMMARY_ATTR,
    get_criteria,
)
from app.services.phase_validation.revision import (
    escalate_after_max_attempts,
    revision_message_for,
    should_retry,
    validation_events,
)
from app.services.phase_validation.schema import ValidationDecision, ValidationResult
from app.services.phase_validation.service import validate_phase_output

log = get_logger("phase_validation.apply")

AgentRunner = Callable[..., Awaitable[list[dict]]]


def _without_phase_cards(events: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for ev in events:
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if ev.get("type") == "structured_card" and payload.get("card_type") != "phase_validation_report":
            continue
        kept.append(ev)
    return kept


def _reject_narrower_output(
    *,
    profile: ClientDigitalProfile,
    agent_key: str,
    events: list[dict],
    held_summary: dict | None,
    held_events: list[dict] | None,
    held_breadth: int,
) -> tuple[list[dict], dict | None, list[dict] | None, int, bool]:
    """Keep the saved deliverable when this run contains less work than the last.

    Returns events, held summary, held events, held breadth, and whether the new
    run was discarded.
    """
    from app.services.phase_validation.breadth import deliverable_breadth
    from app.services.phase_validation.context import extract_phase_output

    output = extract_phase_output(profile=profile, agent_key=agent_key, events=events)
    new_breadth = deliverable_breadth(output)
    attr = AGENT_SUMMARY_ATTR.get(agent_key)
    if held_breadth and new_breadth < held_breadth:
        if attr and held_summary is not None:
            setattr(profile, attr, deepcopy(held_summary))
        restored = list(held_events) if held_events is not None else _without_phase_cards(events)
        restored.append(
            {
                "type": "system_notice",
                "content": (
                    "This run was smaller than the saved deliverable "
                    f"({new_breadth} items vs {held_breadth}). "
                    "The wider deliverable was kept."
                ),
            }
        )
        log.info(
            "phase_output_narrowing_rejected",
            agent_key=agent_key,
            previous=held_breadth,
            current=new_breadth,
        )
        return restored, held_summary, held_events, held_breadth, True

    if new_breadth >= held_breadth and new_breadth > 0:
        summary = getattr(profile, attr, None) if attr else None
        return (
            events,
            deepcopy(summary) if isinstance(summary, dict) else held_summary,
            list(events),
            new_breadth,
            False,
        )
    return events, held_summary, held_events, held_breadth, False


def _attach_validation_to_summary(
    profile: ClientDigitalProfile,
    agent_key: str,
    result: ValidationResult,
) -> None:
    attr = AGENT_SUMMARY_ATTR.get(agent_key)
    if not attr:
        return
    summary = getattr(profile, attr, None)
    if not isinstance(summary, dict):
        return
    history = list(summary.get("_validation_history") or [])
    history.append(result.to_dict())
    # Keep last 5 attempts
    summary = {**summary, "_validation": result.to_dict(), "_validation_history": history[-5:]}
    setattr(profile, attr, summary)


def _attach_validation_to_cards(
    events: list[dict],
    result: ValidationResult,
    *,
    keep_approve: bool = False,
) -> list[dict]:
    """Return phase cards plus a validation report.

    On acceptance the phase card keeps Approve. On an unrecoverable failure we strip
    Approve so a blocked output cannot be signed off, then still surface the QC card.
    A semantic reject that the deterministic re-check would still allow
    (``keep_approve``) leaves Approve in place for the human sign-off.
    Intermediate revision attempts stay silent until the loop finishes.
    """
    out: list[dict] = []
    for ev in events:
        payload = dict(ev.get("payload") or {}) if isinstance(ev.get("payload"), dict) else {}
        if (
            result.blocks_pipeline
            and not keep_approve
            and ev.get("type") in ("structured_card", "checkpoint")
            and payload.get("card_type") != "phase_validation_report"
        ):
            payload["actions"] = [a for a in (payload.get("actions") or []) if a != "approve"]
            payload["validation_blocked_approve"] = True
            out.append({**ev, "payload": payload})
        else:
            out.append(ev)
    out.extend(validation_events(result))
    return out


async def _persist_validation_row(
    db: AsyncSession,
    *,
    client_id: UUID,
    session_id: UUID | None,
    result: ValidationResult,
) -> None:
    from app.models.governance import PhaseValidation

    row = PhaseValidation(
        client_id=client_id,
        session_id=session_id,
        agent_key=result.agent_key,
        iteration=result.iteration,
        decision=result.decision,
        result=result.to_dict(),
        output_fingerprint=result.output_fingerprint,
    )
    db.add(row)
    await log_event(
        db,
        client_id=client_id,
        actor_type="agent",
        event_type="phase_validation",
        event_detail={
            "agent_key": result.agent_key,
            "decision": result.decision,
            "iteration": result.iteration,
            "failed": [c.check_id for c in result.failed_checks()],
            "company_context_keys": result.company_context_keys,
        },
    )
    await db.flush()


async def run_phase_with_validation(
    db: AsyncSession,
    *,
    runner: AgentRunner,
    client: Client,
    profile: ClientDigitalProfile,
    session_id: UUID,
    user_id: UUID,
    agent_key: str,
    message: str,
) -> list[dict]:
    """Execute agent → validate → optionally revise → accept or block.

    Validator outages do not wipe phase output (pass_with_warnings / unavailable path).
    Rejected outputs cannot remain pending_signoff with approve enabled.
    """
    # CDD guards run before every path below, including the validation-disabled
    # short-circuits: a phase must never start on stale inputs, and every run
    # records the CDD version it began against.
    from app.services.cdd_gate import record_phase_start_safe, stale_upstream_block

    blocked = await stale_upstream_block(db, client_id=client.id, agent_key=agent_key)
    if blocked:
        return blocked
    await record_phase_start_safe(db, client_id=client.id, agent_key=agent_key)

    settings = get_settings()
    if not getattr(settings, "feature_phase_validation", True):
        return await runner(
            db,
            client=client,
            session_id=session_id,
            user_id=user_id,
            message=message,
        )

    # Skip validation for readiness (not in AGENT_RUNNERS path anyway)
    if get_criteria(agent_key) is None:
        return await runner(
            db,
            client=client,
            session_id=session_id,
            user_id=user_id,
            message=message,
        )

    max_attempts = 1 if agent_key == "technical_seo" else max(
        1, int(getattr(settings, "validation_max_attempts", 2))
    )
    from app.services.agent_memory import record_qa_result, with_memory

    remembered = with_memory(message, client_id=str(client.id), agent_key=agent_key)
    current_message = remembered
    events: list[dict] = []
    result: ValidationResult | None = None
    attr = AGENT_SUMMARY_ATTR.get(agent_key)
    existing = getattr(profile, attr, None) if attr else None
    from app.services.phase_validation.breadth import deliverable_breadth

    held_summary = deepcopy(existing) if isinstance(existing, dict) and existing else None
    held_events: list[dict] | None = None
    held_breadth = deliverable_breadth(existing) if isinstance(existing, dict) else 0

    for attempt in range(1, max_attempts + 1):
        try:
            events = await runner(
                db,
                client=client,
                session_id=session_id,
                user_id=user_id,
                message=current_message,
            )
            await db.flush()
        except Exception:
            await db.rollback()
            raise
        await db.refresh(profile)
        events, held_summary, held_events, held_breadth, discarded_narrowing = _reject_narrower_output(
            profile=profile,
            agent_key=agent_key,
            events=events,
            held_summary=held_summary,
            held_events=held_events,
            held_breadth=held_breadth,
        )

        result = await validate_phase_output(
            client=client,
            profile=profile,
            agent_key=agent_key,
            events=events,
            iteration=attempt,
        )
        _attach_validation_to_summary(profile, agent_key, result)
        record_qa_result(client_id=str(client.id), result=result)

        # Accepted — return the phase output plus the validation report.
        if result.passed or result.decision == ValidationDecision.UNAVAILABLE.value:
            await _persist_validation_row(
                db, client_id=client.id, session_id=session_id, result=result
            )
            return _attach_validation_to_cards(events, result)

        # Needs revision and attempts remain — silently loop: feed corrections back
        # to the generating agent and re-run. Nothing is surfaced to the user.
        if (
            not discarded_narrowing
            and should_retry(result, attempt=attempt, max_attempts=max_attempts)
        ):
            await _persist_validation_row(
                db, client_id=client.id, session_id=session_id, result=result
            )
            log.info(
                "phase_validation_retry",
                agent_key=agent_key,
                attempt=attempt,
                decision=result.decision,
                failed=[c.check_id for c in result.failed_checks()],
            )
            await supersede_pending_findings(db, client_id=client.id, agent_key=agent_key)
            status_attr = AGENT_STATUS_ATTR.get(agent_key)
            if status_attr:
                setattr(profile, status_attr, "in_progress")
            current_message = revision_message_for(result, remembered)
            await db.flush()
            continue

        # Exhausted / reject — keep pending findings so a usable deliverable can still
        # be approved if the validator was a false positive (truncated prompt, etc.).
        result = escalate_after_max_attempts(result)
        await _persist_validation_row(
            db, client_id=client.id, session_id=session_id, result=result
        )
        _attach_validation_to_summary(profile, agent_key, result)
        status_attr = AGENT_STATUS_ATTR.get(agent_key)
        # Same gate the review endpoint uses. A semantic reject on a real
        # deliverable must not hide Approve; an empty or broken output still does.
        human_can_approve = (
            await latest_validation_blocks_approve(
                db, client_id=client.id, agent_key=agent_key
            )
        ) is None
        if human_can_approve:
            if status_attr and getattr(profile, status_attr, None) != "pending_signoff":
                setattr(profile, status_attr, "pending_signoff")
            events = _attach_validation_to_cards(events, result, keep_approve=True)
            out = list(events)
            if status_attr:
                out.append(
                    {
                        "type": "phase_status",
                        "payload": {status_attr: "pending_signoff"},
                    }
                )
            await db.flush()
            return out

        if status_attr:
            setattr(profile, status_attr, "in_progress")
        events = _attach_validation_to_cards(events, result)
        out = list(events)
        out.append(
            {
                "type": "phase_status",
                "payload": {
                    status_attr or f"{agent_key}_status": "in_progress",
                },
            }
        )
        db.add(
            AgentJob(
                session_id=session_id,
                agent_key=agent_key,
                job_type="phase_validation",
                status="failed",
                attempt_count=result.iteration,
                result_ref=result.to_dict(),
                error_detail=(result.summary or result.decision)[:500],
                completed_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()
        return out

    # Unreachable, but keep mypy happy
    assert result is not None
    return _attach_validation_to_cards(events, result)


async def latest_validation_blocks_approve(
    db: AsyncSession,
    *,
    client_id: UUID,
    agent_key: str,
) -> ValidationResult | None:
    """If the newest validation for this phase still blocks approve, return it.

    Re-checks the current CDP/cache with deterministic rules so a stale semantic
    reject (truncated prompt, missing payload) cannot trap a real deliverable.
    """
    from sqlalchemy import select

    from app.models import Client, ClientDigitalProfile
    from app.models.governance import PhaseValidation
    from app.services.phase_validation.context import (
        build_company_context,
        build_prior_phase_context,
        compact_phase_output,
        extract_phase_output,
    )
    from app.services.phase_validation.deterministic import run_deterministic_checks
    from app.services.phase_validation.schema import decide_from_checks

    row = (
        await db.execute(
            select(PhaseValidation)
            .where(
                PhaseValidation.client_id == client_id,
                PhaseValidation.agent_key == agent_key,
            )
            .order_by(
                PhaseValidation.created_at.desc(),
                PhaseValidation.iteration.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return None
    data = row.result if isinstance(row.result, dict) else {}
    decision = data.get("decision") or row.decision
    if decision not in (
        ValidationDecision.REJECT.value,
        ValidationDecision.NEEDS_REVISION.value,
    ):
        return None

    def _blocked(result_decision: str, summary: str, iteration: int) -> ValidationResult:
        return ValidationResult(
            agent_key=agent_key,
            phase_label=str(data.get("phase_label") or agent_key),
            iteration=iteration,
            decision=result_decision,
            summary=summary,
            checks=[],
        )

    client = await db.get(Client, client_id)
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one_or_none()
    if client is None or profile is None:
        return _blocked(decision, str(data.get("summary") or ""), int(data.get("iteration") or row.iteration or 1))

    output = extract_phase_output(profile=profile, agent_key=agent_key, events=[])
    if agent_key == "competitor_market_agent" and not (
        output.get("competitors") or output.get("scorecards")
    ):
        from app.services.cache import cache_get, competitor_cache_key

        cached = await cache_get(competitor_cache_key(str(client_id)))
        if isinstance(cached, dict):
            output = compact_phase_output(agent_key, cached)

    live_decision = decide_from_checks(
        run_deterministic_checks(
            agent_key=agent_key,
            output=output,
            company_ctx=build_company_context(client, profile),
            prior=build_prior_phase_context(profile, agent_key),
        )
    )
    if live_decision not in (
        ValidationDecision.REJECT.value,
        ValidationDecision.NEEDS_REVISION.value,
    ):
        return None
    return _blocked(
        decision,
        str(data.get("summary") or ""),
        int(data.get("iteration") or row.iteration or 1),
    )
