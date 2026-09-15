"""System and user prompts for the independent AI Validation Agent."""

from __future__ import annotations

import json
from typing import Any

from app.services.phase_validation.context import _trim
from app.services.phase_validation.criteria import (
    criteria_prompt_block,
    list_applicable_parameters,
)

_PROMPT_CHAR_BUDGET = 48000

VALIDATOR_SYSTEM_PROMPT = """You are an independent Quality-Control Validation Agent for a multi-phase SEO/operations pipeline.

You do NOT generate phase deliverables. You evaluate an existing phase output.

Core stance:
- Do NOT assume the generated output is correct.
- Actively look for contradictions, irrelevant content, unsupported claims, missing requirements,
  company misalignment, domain/industry/service mismatch, and phase-specific defects.
- Be conservative about factual truth: if context is insufficient to verify a claim, mark the
  check as inconclusive — do not invent evidence.
- Distinguish clearly between: genuinely incorrect claims, unsupported assumptions, reasonable
  inferences, and information that cannot be determined from available context.
- Avoid false positives: do not fail an output merely because a detail is absent from company
  context when that detail is a reasonable, on-domain inference for the phase.
- Only evaluate the validation parameters listed for THIS phase. Skip irrelevant dimensions.
- If `phase_output` is present in the user JSON, you MUST evaluate that object. Never fail
  completeness/schema as "missing deliverable" because company_context or prior_phase_outputs
  are large, or because heatmap/tools tables were omitted.
- If a field is absent from a compacted payload but the core deliverable (peers, URLs, topics)
  is present, mark that field inconclusive — not critical.

Severity guide:
- critical: fundamentally wrong company/domain, empty/unusable deliverable, dangerous false claim
- major: meaningful misalignment or missing required substance that should be revised before continue
- minor: real issue but limited blast radius
- warning: non-blocking quality note

Decision guide (overall decision is computed by the system from your checks; still propose one):
- pass: no material issues
- pass_with_warnings: usable with non-critical notes
- needs_revision: meaningful problems that should be corrected
- reject: fundamentally invalid, unrelated, contradictory, or unusable

Respond with valid JSON only matching this schema:
{
  "checks": [
    {
      "check_id": "string",
      "parameter": "one of the applicable parameters",
      "category": "same as parameter unless more specific",
      "status": "passed|failed|warning|inconclusive|skipped",
      "severity": "critical|major|minor|warning|info",
      "message": "what you found",
      "evidence": "short quote or field reference from output/context, or null",
      "recommended_correction": "actionable fix or null"
    }
  ],
  "summary": "2-4 sentences",
  "proposed_decision": "pass|pass_with_warnings|needs_revision|reject",
  "revision_guidance": "concrete instructions for the next generation attempt, or null"
}
"""


def build_semantic_user_prompt(
    *,
    agent_key: str,
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
    output: dict[str, Any],
    iteration: int,
) -> str:
    applicable = list_applicable_parameters(agent_key)
    # phase_output FIRST so a char cap cannot drop the deliverable behind huge priors.
    payload = {
        "phase_output": _trim(output, max_chars=16000),
        "company_context": _trim(company_ctx, max_chars=3500),
        "prior_phase_outputs": _trim(prior, max_chars=4000),
        "iteration": iteration,
        "phase_criteria": criteria_prompt_block(agent_key),
        "applicable_parameters": applicable,
        "instructions": (
            "Evaluate phase_output against company_context and prior_phase_outputs "
            "using ONLY applicable_parameters. Return one check object per parameter you "
            "actually evaluated. Use status=skipped only when truly not applicable. "
            "If phase_output is present, do not fail for a missing deliverable."
        ),
    }
    raw = json.dumps(payload, default=str, ensure_ascii=False)
    if len(raw) <= _PROMPT_CHAR_BUDGET:
        return raw
    # Keep phase_output; shrink prior/criteria from the tail only as a last resort.
    payload["prior_phase_outputs"] = _trim(prior, max_chars=1200)
    payload["phase_criteria"] = str(payload["phase_criteria"])[:2000]
    return json.dumps(payload, default=str, ensure_ascii=False)[:_PROMPT_CHAR_BUDGET]


def build_revision_message(
    *,
    original_message: str,
    validation: dict[str, Any],
) -> str:
    """Feedback for the next phase run — phase objective unchanged."""
    failed = [
        c
        for c in (validation.get("checks") or [])
        if isinstance(c, dict) and c.get("status") == "failed"
    ]
    lines = [
        original_message.strip(),
        "",
        "--- VALIDATION FEEDBACK (revision attempt) ---",
        "Your previous output failed independent validation. Keep the same phase objective.",
        "Correct the issues below. Do not invent a new scope.",
        f"Decision: {validation.get('decision')}",
        f"Summary: {validation.get('summary') or ''}",
    ]
    guidance = validation.get("revision_guidance")
    if guidance:
        lines.append(f"Revision guidance: {guidance}")
    for c in failed[:12]:
        lines.append(
            f"- [{c.get('severity')}] {c.get('parameter')}: {c.get('message')}"
            + (f" Evidence: {c.get('evidence')}" if c.get("evidence") else "")
            + (
                f" Fix: {c.get('recommended_correction')}"
                if c.get("recommended_correction")
                else ""
            )
        )
    lines.append("--- END VALIDATION FEEDBACK ---")
    return "\n".join(lines)
