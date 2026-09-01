"""Revision attempt tracking and feedback construction."""

from __future__ import annotations

from typing import Any

from app.services.phase_validation.prompts import build_revision_message
from app.services.phase_validation.schema import ValidationDecision, ValidationResult


def should_retry(result: ValidationResult, *, attempt: int, max_attempts: int) -> bool:
    if attempt >= max_attempts:
        return False
    return result.decision == ValidationDecision.NEEDS_REVISION.value


def revision_message_for(result: ValidationResult, original_message: str) -> str:
    return build_revision_message(
        original_message=original_message,
        validation=result.to_dict(),
    )


def escalate_after_max_attempts(result: ValidationResult) -> ValidationResult:
    """If still needs_revision after max attempts, escalate to reject so pipeline cannot proceed silently."""
    if result.decision != ValidationDecision.NEEDS_REVISION.value:
        return result
    result.decision = ValidationDecision.REJECT.value
    result.summary = (
        (result.summary or "")
        + " Escalated to reject after exhausting validation revision attempts."
    ).strip()
    return result


def validation_events(result: ValidationResult) -> list[dict[str, Any]]:
    """Chat/UI events describing validation outcome."""
    decision = result.decision
    icon = {
        ValidationDecision.PASS.value: "passed",
        ValidationDecision.PASS_WITH_WARNINGS.value: "passed with warnings",
        ValidationDecision.NEEDS_REVISION.value: "needs revision",
        ValidationDecision.REJECT.value: "rejected",
        ValidationDecision.UNAVAILABLE.value: "unavailable (phase kept for human review)",
    }.get(decision, decision)

    failed = result.failed_checks()
    lines = [
        f"Validation ({result.phase_label}, attempt {result.iteration}): {icon}.",
        result.summary or "",
    ]
    for c in failed[:6]:
        lines.append(f"• [{c.severity}] {c.parameter}: {c.message}")

    events: list[dict[str, Any]] = [
        {
            "type": "system_notice",
            "content": " ".join(x for x in lines if x).strip(),
            "payload": {
                "event_type": "phase_validation",
                "validation": result.to_dict(),
            },
        },
        {
            "type": "structured_card",
            "payload": {
                **result.to_dict(),
                "card_type": "phase_validation_report",
                "title": f"Validation: {result.phase_label}",
                "agent_key": result.agent_key,
                "validated_agent_key": result.agent_key,
                "actions": [],
            },
        },
    ]
    return events
