"""Validation result schema — explicit decisions, not opaque scores."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ValidationDecision(str, Enum):
    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    NEEDS_REVISION = "needs_revision"
    REJECT = "reject"
    UNAVAILABLE = "unavailable"


class CheckSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    WARNING = "warning"
    INFO = "info"


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"


# Categories used for filtering / reporting
CHECK_CATEGORIES = (
    "company_alignment",
    "domain_alignment",
    "industry_alignment",
    "service_product_alignment",
    "business_model_alignment",
    "target_audience_alignment",
    "geographic_market_alignment",
    "company_standard_compliance",
    "phase_objective_compliance",
    "input_output_consistency",
    "factual_consistency",
    "completeness",
    "relevance",
    "quality",
    "prior_phase_consistency",
    "schema_structure",
    "honesty_anti_hallucination",
)


@dataclass
class CheckResult:
    check_id: str
    parameter: str
    category: str
    status: str
    severity: str
    message: str
    evidence: str | None = None
    recommended_correction: str | None = None
    source: str = "deterministic"  # deterministic | semantic

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    agent_key: str
    phase_label: str
    iteration: int
    decision: str
    checks: list[CheckResult] = field(default_factory=list)
    applicable_parameters: list[str] = field(default_factory=list)
    company_context_keys: list[str] = field(default_factory=list)
    summary: str = ""
    revision_guidance: str | None = None
    validator_error: str | None = None
    output_fingerprint: str | None = None

    @property
    def passed(self) -> bool:
        return self.decision in (
            ValidationDecision.PASS.value,
            ValidationDecision.PASS_WITH_WARNINGS.value,
        )

    @property
    def blocks_pipeline(self) -> bool:
        return self.decision in (
            ValidationDecision.NEEDS_REVISION.value,
            ValidationDecision.REJECT.value,
        )

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.FAILED.value]

    def warning_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.WARNING.value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_key": self.agent_key,
            "phase_label": self.phase_label,
            "iteration": self.iteration,
            "decision": self.decision,
            "checks": [c.to_dict() for c in self.checks],
            "applicable_parameters": list(self.applicable_parameters),
            "company_context_keys": list(self.company_context_keys),
            "summary": self.summary,
            "revision_guidance": self.revision_guidance,
            "validator_error": self.validator_error,
            "output_fingerprint": self.output_fingerprint,
            "passed": self.passed,
            "blocks_pipeline": self.blocks_pipeline,
        }


def decide_from_checks(
    checks: list[CheckResult],
    *,
    validator_error: str | None = None,
) -> str:
    """Map check outcomes to a pipeline decision.

    Primary signal is severity of failures — not a numeric score.
    """
    if validator_error and not any(c.status == CheckStatus.FAILED.value for c in checks):
        return ValidationDecision.UNAVAILABLE.value

    failed = [c for c in checks if c.status == CheckStatus.FAILED.value]
    warnings = [c for c in checks if c.status == CheckStatus.WARNING.value]

    if any(c.severity == CheckSeverity.CRITICAL.value for c in failed):
        return ValidationDecision.REJECT.value
    if any(c.severity == CheckSeverity.MAJOR.value for c in failed):
        return ValidationDecision.NEEDS_REVISION.value
    if failed:
        # minor failures still need revision when explicitly failed
        return ValidationDecision.NEEDS_REVISION.value
    if warnings:
        return ValidationDecision.PASS_WITH_WARNINGS.value
    if validator_error:
        return ValidationDecision.UNAVAILABLE.value
    return ValidationDecision.PASS.value
