"""LLM semantic validation for nuanced company/domain/quality checks."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Awaitable

from app.integrations.llm import synthesize_json
from app.logging_config import get_logger
from app.services.phase_validation.criteria import list_applicable_parameters
from app.services.phase_validation.prompts import VALIDATOR_SYSTEM_PROMPT, build_semantic_user_prompt
from app.services.phase_validation.schema import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
)

log = get_logger("phase_validation.semantic")

# Optional injectable for tests
SemanticCaller = Callable[[str, str], Awaitable[dict[str, Any] | None]]


def _fingerprint(output: dict[str, Any]) -> str:
    raw = json.dumps(output, sort_keys=True, default=str)[:20000]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_check(raw: dict[str, Any], *, applicable: set[str]) -> CheckResult | None:
    if not isinstance(raw, dict):
        return None
    parameter = str(raw.get("parameter") or raw.get("category") or "").strip()
    if not parameter:
        return None
    # Drop hallucinated parameters outside the phase rubric
    if applicable and parameter not in applicable and raw.get("status") != CheckStatus.SKIPPED.value:
        # Allow honesty_anti_hallucination aliasing
        if parameter not in applicable:
            return None
    status = str(raw.get("status") or CheckStatus.INCONCLUSIVE.value).lower()
    if status not in {s.value for s in CheckStatus}:
        status = CheckStatus.INCONCLUSIVE.value
    severity = str(raw.get("severity") or CheckSeverity.WARNING.value).lower()
    if severity not in {s.value for s in CheckSeverity}:
        severity = CheckSeverity.WARNING.value
    return CheckResult(
        check_id=str(raw.get("check_id") or f"semantic:{parameter}")[:120],
        parameter=parameter,
        category=str(raw.get("category") or parameter),
        status=status,
        severity=severity,
        message=str(raw.get("message") or "")[:1000],
        evidence=(str(raw["evidence"])[:800] if raw.get("evidence") is not None else None),
        recommended_correction=(
            str(raw["recommended_correction"])[:800]
            if raw.get("recommended_correction") is not None
            else None
        ),
        source="semantic",
    )


async def run_semantic_validation(
    *,
    agent_key: str,
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
    output: dict[str, Any],
    iteration: int = 1,
    caller: SemanticCaller | None = None,
) -> tuple[list[CheckResult], str | None, str | None]:
    """Returns (checks, revision_guidance, error_message)."""
    applicable = set(list_applicable_parameters(agent_key))
    user = build_semantic_user_prompt(
        agent_key=agent_key,
        company_ctx=company_ctx,
        prior=prior,
        output=output,
        iteration=iteration,
    )

    async def _default_call(system: str, user_msg: str) -> dict[str, Any] | None:
        return await synthesize_json(system, user_msg, max_tokens=3500)

    call = caller or _default_call
    try:
        parsed = await call(VALIDATOR_SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic_validation_failed", agent_key=agent_key, error=str(exc))
        return [], None, f"validator_exception:{exc}"

    if not isinstance(parsed, dict):
        # Mock LLM / missing provider — treat as unavailable, not a failure of the phase
        return [], None, "validator_unavailable:no_json_response"

    checks: list[CheckResult] = []
    for raw in parsed.get("checks") or []:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_check(raw, applicable=applicable)
        if normalized:
            checks.append(normalized)

    guidance = parsed.get("revision_guidance")
    guidance_s = str(guidance) if guidance else None
    if not checks and not parsed.get("summary"):
        return [], guidance_s, "validator_malformed:empty_checks"
    return checks, guidance_s, None


def output_fingerprint(output: dict[str, Any]) -> str:
    return _fingerprint(output)
