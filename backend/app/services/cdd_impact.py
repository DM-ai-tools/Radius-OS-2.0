"""Which phases a CDD change invalidates.

Two inputs decide this:

1. ``CDD_FIELD_IMPACT`` — which phases actually read each CDD field. Authored
   from a trace of the pipeline, not guessed: a field only lists a phase if that
   phase's code reads it (directly, or through the commercial_scope copy).
2. ``CONSUMES`` in ``agent_handoff`` — the existing phase dependency graph,
   inverted here. If Search Demand is stale, everything built on top of it is
   stale too.

Staleness is DERIVED, never stored as a mutable flag: each phase records the CDD
version it ran against, and this module compares that to the latest version via
the diffs in between. A phase that was running when a new version landed simply
finishes, records the older version, and reads as stale afterwards — which is
the required "let it finish, then mark it" behaviour, with no mid-run mutation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.services.agent_handoff import CONSUMES, PHASE_ORDER
from app.services.cdd_diff import CddDiff
from app.services.discovery_fields import ACCESS_FIELDS

# CDD field -> phases that read it. An empty tuple means "no phase consumes
# this", and a change to it marks nothing stale.
CDD_FIELD_IMPACT: dict[str, tuple[str, ...]] = {
    # --- identity / targeting -------------------------------------------
    "inferred_industry": ("competitor_market_agent", "search_demand"),
    "business_model": ("content_strategy",),
    "positioning": ("content_strategy", "content_production"),
    # --- the keyword engine ---------------------------------------------
    "business_keywords": ("search_demand",),
    "products": ("search_demand", "site_architecture", "content_strategy"),
    "products_for_promotion": ("search_demand", "content_strategy"),
    "geographic_focus": ("search_demand", "competitor_market_agent"),
    "industry_targeting": ("search_demand",),
    # --- audience / voice ------------------------------------------------
    "target_demographic": ("search_demand", "content_strategy", "content_production"),
    "b2b_b2c": ("content_strategy", "content_production"),
    "brand_guidelines": ("content_production", "on_page_seo"),
    "content_creation_notes": ("content_production",),
    "blogs_notes": ("content_strategy", "content_production"),
    # --- goals ------------------------------------------------------------
    "objectives": ("content_strategy", "content_planning"),
    "business_goal": ("content_strategy", "content_planning"),
    "sales_promises": ("content_production",),
    "strategy_approach": ("content_strategy",),
    # --- SWOT --------------------------------------------------------------
    "strengths": ("content_strategy",),
    "weaknesses": ("content_strategy",),
    "opportunities": ("content_strategy",),
    "threats": ("content_strategy",),
    # --- commercial metrics: recorded, not consumed by any generator -------
    "average_ticket_size": (),
    "lifetime_value": (),
    "revenue_split": (),
    "sales_cycle": (),
    "seasonality": ("content_strategy",),
    "lead_modes": ("content_strategy",),
    "seo_traffic_current": (),
    "seo_traffic_target": (),
    "sem_leads_current": (),
    "sem_leads_target": (),
    "other_marketing_spend": (),
    # --- research-only, cosmetic -------------------------------------------
    "public_reviews_summary": (),
    "social_presence": (),
    "google_business_signals": (),
    # Competitor discovery belongs exclusively to Phase 4; CDD-listed
    # competitors are deliberately ignored by the pipeline, so a change to
    # them invalidates nothing.
    "competitors": (),
    "known_competitors": (),
}

# Access / credential fields all gate the same two phases.
for _access_field in ACCESS_FIELDS:
    CDD_FIELD_IMPACT.setdefault(_access_field, ("tracking_access_agent", "publishing"))


def _build_dependents() -> dict[str, set[str]]:
    """Invert CONSUMES: phase -> phases that depend on it."""
    dependents: dict[str, set[str]] = {phase: set() for phase in PHASE_ORDER}
    for agent, deps in CONSUMES.items():
        for dep in deps:
            dependents.setdefault(dep, set()).add(agent)
    return dependents


DEPENDENTS: dict[str, set[str]] = _build_dependents()


class StalenessState(str, Enum):
    FRESH = "fresh"
    RUNNING = "running"
    STALE_DIRECT = "stale_direct"
    STALE_UPSTREAM = "stale_upstream"
    DISMISSED = "dismissed"


@dataclass
class PhaseStaleness:
    agent_key: str
    state: StalenessState = StalenessState.FRESH
    fields: list[str] = field(default_factory=list)
    upstream: list[str] = field(default_factory=list)
    recorded_version_id: str | None = None

    @property
    def is_stale(self) -> bool:
        return self.state in (StalenessState.STALE_DIRECT, StalenessState.STALE_UPSTREAM)

    def reason(self) -> str:
        if self.state is StalenessState.STALE_DIRECT:
            return "CDD fields changed: " + ", ".join(self.fields)
        if self.state is StalenessState.STALE_UPSTREAM:
            return "Upstream phase is stale: " + ", ".join(self.upstream)
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_key": self.agent_key,
            "state": self.state.value,
            "fields": self.fields,
            "upstream": self.upstream,
            "recorded_version_id": self.recorded_version_id,
            "reason": self.reason(),
        }


def directly_impacted_phases(diff: CddDiff) -> set[str]:
    """Phases that read at least one field this diff touched."""
    impacted: set[str] = set()
    for key in diff.changed_keys():
        impacted.update(CDD_FIELD_IMPACT.get(key, ()))
    return impacted


def impacted_fields_by_phase(diff: CddDiff) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in diff.changed_keys():
        for phase in CDD_FIELD_IMPACT.get(key, ()):
            out.setdefault(phase, []).append(key)
    return {phase: sorted(keys) for phase, keys in out.items()}


def expand_downstream(seeds: set[str]) -> set[str]:
    """Seed phases plus everything transitively built on them."""
    seen = set(seeds)
    queue = deque(seeds)
    while queue:
        current = queue.popleft()
        for dependent in DEPENDENTS.get(current, set()):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return seen


# Statuses that mean "this phase produced an output that could now be stale".
_SETTLED_STATUSES = {"complete", "pending_signoff"}


def evaluate_staleness(
    *,
    phase_versions: dict[str, str | None],
    phase_statuses: dict[str, str],
    latest_version_id: str | None,
    diff_by_version: dict[str, CddDiff],
    dismissed_through: dict[str, str | None] | None = None,
    version_order: list[str] | None = None,
) -> dict[str, PhaseStaleness]:
    """Derive each phase's staleness from the version it ran against.

    ``diff_by_version`` maps a version id to the diff that PRODUCED it (i.e.
    the change from its predecessor), so a phase several versions behind is
    judged against every change since.
    """
    dismissed_through = dismissed_through or {}
    order = version_order or sorted(diff_by_version)
    report = {
        phase: PhaseStaleness(agent_key=phase, recorded_version_id=phase_versions.get(phase))
        for phase in PHASE_ORDER
    }

    def _diffs_after(version_id: str | None) -> list[CddDiff]:
        if version_id is None:
            return []
        if version_id not in order:
            return [diff_by_version[v] for v in order]
        start = order.index(version_id) + 1
        return [diff_by_version[v] for v in order[start:]]

    # Pass 1 — direct impact.
    directly_stale: set[str] = set()
    for phase in PHASE_ORDER:
        entry = report[phase]
        status = str(phase_statuses.get(phase) or "not_started")

        if status == "in_progress":
            entry.state = StalenessState.RUNNING
            continue
        if status not in _SETTLED_STATUSES:
            # Not started (or blocked): it will simply run against the latest
            # CDD. Nothing to approve.
            continue

        recorded = phase_versions.get(phase)
        if recorded == latest_version_id:
            continue

        changed_fields: set[str] = set()
        for diff in _diffs_after(recorded):
            changed_fields.update(impacted_fields_by_phase(diff).get(phase, []))
        if not changed_fields:
            continue

        dismissed_at = dismissed_through.get(phase)
        if dismissed_at is not None and dismissed_at == latest_version_id:
            entry.state = StalenessState.DISMISSED
            entry.fields = sorted(changed_fields)
            continue

        entry.state = StalenessState.STALE_DIRECT
        entry.fields = sorted(changed_fields)
        directly_stale.add(phase)

    # Pass 2 — downstream propagation.
    for phase in expand_downstream(directly_stale) - directly_stale:
        entry = report[phase]
        if entry.state in (StalenessState.RUNNING, StalenessState.DISMISSED):
            continue
        status = str(phase_statuses.get(phase) or "not_started")
        if status not in _SETTLED_STATUSES:
            continue
        upstream = sorted(
            dep for dep in CONSUMES.get(phase, []) if report[dep].is_stale
        )
        if not upstream:
            continue
        if dismissed_through.get(phase) == latest_version_id:
            entry.state = StalenessState.DISMISSED
            continue
        entry.state = StalenessState.STALE_UPSTREAM
        entry.upstream = upstream

    return report


def blocking_stale_upstream(
    agent_key: str,
    report: dict[str, PhaseStaleness],
) -> list[str]:
    """Stale dependencies that should stop ``agent_key`` from starting.

    A phase is never blocked by its own staleness — re-running it is the fix.
    """
    return sorted(
        dep
        for dep in CONSUMES.get(agent_key, [])
        if dep in report and report[dep].is_stale
    )
