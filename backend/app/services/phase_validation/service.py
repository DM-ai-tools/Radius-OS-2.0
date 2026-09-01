"""Orchestrate deterministic + semantic validation into a ValidationResult."""

from __future__ import annotations

from typing import Any

from app.models import Client, ClientDigitalProfile
from app.services.phase_validation.context import (
    build_company_context,
    build_prior_phase_context,
    context_keys_used,
    extract_phase_output,
)
from app.services.phase_validation.criteria import get_criteria, list_applicable_parameters
from app.services.phase_validation.deterministic import run_deterministic_checks
from app.services.phase_validation.schema import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    ValidationDecision,
    ValidationResult,
    decide_from_checks,
)
from app.services.phase_validation.semantic import (
    SemanticCaller,
    output_fingerprint,
    run_semantic_validation,
)

_MISSING_PAYLOAD_MARKERS = (
    "only contains company_context",
    "company_context and prior",
    "prior_phase",
    "missing the analysis",
    "analysis phase output",
    "phase_output is missing",
    "phase output is missing",
    "not present in the payload",
    "payload only contains",
)


def _output_has_deliverable(output: dict[str, Any]) -> bool:
    for key in (
        "scorecards",
        "competitors",
        "sample_urls",
        "pages",
        "pages_found",
        "topic_plan",
        "priority_queue",
        "discovered_urls",
        "status_samples",
    ):
        if output.get(key) not in (None, "", [], {}):
            return True
    return False


def _soften_missing_payload_failures(
    checks: list[CheckResult],
    output: dict[str, Any],
) -> list[CheckResult]:
    """Don't reject a real deliverable because the validator prompt was truncated."""
    if not _output_has_deliverable(output):
        return checks
    softened: list[CheckResult] = []
    for check in checks:
        msg = (check.message or "").lower()
        if (
            check.source == "semantic"
            and check.status == CheckStatus.FAILED.value
            and any(marker in msg for marker in _MISSING_PAYLOAD_MARKERS)
        ):
            softened.append(
                CheckResult(
                    check_id=check.check_id,
                    parameter=check.parameter,
                    category=check.category,
                    status=CheckStatus.WARNING.value,
                    severity=CheckSeverity.WARNING.value,
                    message=(
                        "Semantic validator could not see the full phase_output "
                        "(prompt truncated). Deterministic checks stand."
                    ),
                    evidence=check.message,
                    source="semantic",
                )
            )
            continue
        softened.append(check)
    return softened


def _should_skip_validation(output: dict[str, Any], events: list[dict[str, Any]]) -> str | None:
    """Return skip reason when validation should not run (blocked / error-only turns)."""
    if output.get("blocked"):
        return "phase_blocked"
    types = {ev.get("type") for ev in events}
    if "error" in types and "structured_card" not in types:
        return "phase_error"
    # Bounce/blocked notices without a deliverable card
    if "structured_card" not in types and not output:
        return "no_deliverable"
    return None


async def validate_phase_output(
    *,
    client: Client,
    profile: ClientDigitalProfile,
    agent_key: str,
    events: list[dict[str, Any]],
    iteration: int = 1,
    semantic_caller: SemanticCaller | None = None,
    skip_semantic: bool = False,
) -> ValidationResult:
    criteria = get_criteria(agent_key)
    phase_label = criteria.phase_label if criteria else agent_key
    company_ctx = build_company_context(client, profile)
    prior = build_prior_phase_context(profile, agent_key)
    output = extract_phase_output(profile=profile, agent_key=agent_key, events=events)

    skip = _should_skip_validation(output, events)
    if skip:
        return ValidationResult(
            agent_key=agent_key,
            phase_label=phase_label,
            iteration=iteration,
            decision=ValidationDecision.PASS.value,
            checks=[
                CheckResult(
                    check_id="skip_validation",
                    parameter="phase_objective_compliance",
                    category="phase_objective_compliance",
                    status=CheckStatus.SKIPPED.value,
                    severity=CheckSeverity.INFO.value,
                    message=f"Validation skipped ({skip}) — no deliverable to QC.",
                    source="deterministic",
                )
            ],
            applicable_parameters=list_applicable_parameters(agent_key),
            company_context_keys=context_keys_used(company_ctx, prior),
            summary=f"Validation skipped: {skip}.",
            output_fingerprint=output_fingerprint(output) if output else None,
        )

    checks = run_deterministic_checks(
        agent_key=agent_key,
        output=output,
        company_ctx=company_ctx,
        prior=prior,
    )

    revision_guidance: str | None = None
    validator_error: str | None = None
    if criteria and not criteria.semantic_parameters:
        skip_semantic = True
    if not skip_semantic and semantic_caller is None:
        from app.config import get_settings

        # Mock LLM cannot produce real QC JSON — keep deterministic checks only.
        if get_settings().use_mock_llm:
            skip_semantic = True
    if not skip_semantic:
        sem_checks, guidance, err = await run_semantic_validation(
            agent_key=agent_key,
            company_ctx=company_ctx,
            prior=prior,
            output=output,
            iteration=iteration,
            caller=semantic_caller,
        )
        checks.extend(sem_checks)
        revision_guidance = guidance
        validator_error = err

    checks = _soften_missing_payload_failures(checks, output)
    decision = decide_from_checks(checks, validator_error=validator_error)

    # Unavailable semantic with only deterministic passes → treat as pass_with_warnings
    if decision == ValidationDecision.UNAVAILABLE.value:
        if any(c.status == CheckStatus.FAILED.value for c in checks):
            decision = decide_from_checks(checks, validator_error=None)
        else:
            checks.append(
                CheckResult(
                    check_id="semantic_unavailable",
                    parameter="quality",
                    category="quality",
                    status=CheckStatus.WARNING.value,
                    severity=CheckSeverity.WARNING.value,
                    message=(
                        "Semantic validator unavailable or malformed; "
                        "deterministic checks only. Phase left for human review."
                    ),
                    evidence=validator_error,
                    source="semantic",
                )
            )
            decision = ValidationDecision.PASS_WITH_WARNINGS.value

    failed = [c for c in checks if c.status == CheckStatus.FAILED.value]
    summary_bits = []
    if decision == ValidationDecision.PASS.value:
        summary_bits.append("All applicable checks passed.")
    elif decision == ValidationDecision.PASS_WITH_WARNINGS.value:
        summary_bits.append("Usable with warnings.")
    elif decision == ValidationDecision.NEEDS_REVISION.value:
        summary_bits.append("Meaningful issues require revision before continuing.")
    elif decision == ValidationDecision.REJECT.value:
        summary_bits.append("Output rejected — fundamentally invalid or critically misaligned.")
    if failed:
        summary_bits.append(
            "Failures: "
            + "; ".join(f"{c.parameter}: {c.message}" for c in failed[:5])
        )

    if not revision_guidance and failed:
        revision_guidance = "\n".join(
            f"- Fix {c.parameter}: {c.recommended_correction or c.message}" for c in failed[:8]
        )

    return ValidationResult(
        agent_key=agent_key,
        phase_label=phase_label,
        iteration=iteration,
        decision=decision,
        checks=checks,
        applicable_parameters=list_applicable_parameters(agent_key),
        company_context_keys=context_keys_used(company_ctx, prior),
        summary=" ".join(summary_bits),
        revision_guidance=revision_guidance,
        validator_error=validator_error,
        output_fingerprint=output_fingerprint(output),
    )
