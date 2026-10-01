"""CDD version store and derived staleness.

A new CDD can arrive at any point in a run. Rather than restarting, the system:

* keeps every accepted version immutably, numbered per client;
* refreshes the CDD-derived slice of ``commercial_scope`` / ``marketing_context``
  straight away, so phases that have NOT started simply use the latest CDD;
* records, per phase, the CDD version that phase started against;
* derives staleness by comparing that recorded version to the latest one.

Nothing is ever auto-redone. Completed phases are marked stale and the operator
decides, which is why staleness is derived rather than stored as a mutable flag:
a phase that was mid-run when the upload landed finishes normally, keeps the
older version id, and reads as stale immediately afterwards.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import CddVersion, ClientDigitalProfile, PhaseState
from app.models.governance import DEFAULT_RUN_ID
from app.services.agent_handoff import PHASE_ORDER
from app.services.cdd_diff import CddDiff, diff_cdd, normalize_field_value
from app.services.cdd_impact import evaluate_staleness
from app.services.discovery_fields import (
    ACCESS_FIELDS,
    CLIENT_ONLY_FIELDS,
    RESEARCH_FIELDS,
)

log = get_logger("cdd_versions")

# The CDD-derived slice of the profile. Kept here rather than inline in
# review.py so the upload path and the Discovery-approve path cannot drift into
# publishing different key sets.
COMMERCIAL_CDD_KEYS: frozenset[str] = frozenset(
    {
        "business_model",
        "business_keywords",
        "products",
        "products_for_promotion",
        "positioning",
        "revenue_split",
        "average_order_value",
        "average_ticket_size",
        "lifetime_value",
        "lead_modes",
        "sales_cycle",
        "seasonality",
        "geographic_focus",
        "target_demographic",
        "b2b_b2c",
        "industry_targeting",
        "inferred_industry",
        "seo_traffic_current",
        "seo_traffic_target",
        "sem_leads_current",
        "sem_leads_target",
    }
)

MARKETING_CDD_KEYS: frozenset[str] = frozenset(
    {
        "objectives",
        "business_goal",
        "sales_promises",
        "compliance_constraints",
        "brand_guidelines",
        "other_marketing_spend",
        "competitors",
        "public_reviews_summary",
        "social_presence",
        "google_business_signals",
        "strengths",
        "weaknesses",
        "opportunities",
        "threats",
        "strategy_approach",
        "content_creation_notes",
        "blogs_notes",
    }
)

KNOWN_CDD_FIELDS: frozenset[str] = frozenset(
    {*RESEARCH_FIELDS, *CLIENT_ONLY_FIELDS, *ACCESS_FIELDS, "competitors", "business_name", "website_url"}
)

_STATUS_ATTR = {agent: f"{agent}_status" for agent in PHASE_ORDER}
# Profile status attributes do not all match the agent key.
_STATUS_ATTR.update(
    {
        "discovery_agent": "discovery_status",
        "tracking_access_agent": "tracking_status",
        "website_situation_agent": "website_status",
        "competitor_market_agent": "competitor_status",
        "content_strategy": "seo_strategy_status",
    }
)


def validate_cdd_fields(fields: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Reject a CDD that carries no usable content.

    Validation happens BEFORE a version row exists, so a malformed upload leaves
    the active version untouched.
    """
    if not isinstance(fields, dict) or not fields:
        return False, "No fields could be extracted from the document."
    recognized = {
        key
        for key, value in fields.items()
        if key in KNOWN_CDD_FIELDS and str(value or "").strip()
    }
    if not recognized:
        return (
            False,
            "No recognized CDD fields with values were found in the document.",
        )
    return True, None


def content_hash(fields: dict[str, Any] | None) -> str:
    """Stable hash of the NORMALIZED field map.

    Normalized so a re-export that merely reorders a comma-separated list hashes
    identically and does not create a redundant version.
    """
    normalized: dict[str, Any] = {}
    for key, value in sorted((fields or {}).items()):
        norm = normalize_field_value(key, value)
        normalized[key] = sorted(norm) if isinstance(norm, set) else norm
    return hashlib.sha256(
        json.dumps(normalized, sort_keys=True, default=str).encode()
    ).hexdigest()


def cdd_fingerprint(
    *, industry: str | None, services: list[str] | None = None
) -> str:
    """Short hash of the CDD inputs a cached artefact depends on.

    Used to key caches (competitor scans, keyword verdicts) so a CDD change
    invalidates them structurally instead of needing an explicit sweep.
    """
    blob = json.dumps(
        {
            "industry": str(industry or "").strip().lower(),
            "services": sorted(str(s or "").strip().lower() for s in (services or [])),
        },
        sort_keys=True,
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


async def active_version(db: AsyncSession, client_id: UUID) -> CddVersion | None:
    return (
        await db.execute(
            select(CddVersion)
            .where(CddVersion.client_id == client_id)
            .order_by(CddVersion.version_no.desc())
            .limit(1)
        )
    ).scalars().first()


async def list_versions(db: AsyncSession, client_id: UUID) -> list[CddVersion]:
    return list(
        (
            await db.execute(
                select(CddVersion)
                .where(CddVersion.client_id == client_id)
                .order_by(CddVersion.version_no)
            )
        ).scalars().all()
    )


def _apply_cdd_slice(profile: ClientDigitalProfile, fields: dict[str, Any]) -> None:
    """Refresh only the CDD-derived keys, preserving everything else.

    ``commercial_scope`` also holds pipeline state that is NOT from the CDD —
    the confirmed service prioritization, harvested competitor keyword intel,
    the keyword pool target. Those must survive a CDD update untouched.
    """
    commercial = dict(profile.commercial_scope or {})
    marketing = dict(profile.marketing_context or {})
    for key, value in (fields or {}).items():
        if value in (None, "", [], {}):
            continue
        if key in COMMERCIAL_CDD_KEYS:
            commercial[key] = value
        if key in MARKETING_CDD_KEYS:
            marketing[key] = value
    profile.commercial_scope = commercial
    profile.marketing_context = marketing


# Fields whose change invalidates a previously confirmed service selection.
_SERVICE_DEFINING_FIELDS = ("products", "products_for_promotion")


def _reopen_service_selection_if_services_changed(
    profile: ClientDigitalProfile, diff: CddDiff, *, had_previous_version: bool
) -> None:
    """Re-open the 80/20 service selection when the service list itself changed.

    A selection confirmed against the old service list is not a decision about
    the new one — silently keeping ``confirmed_at`` would make the Phase 5 redo
    skip the selection card and quietly target the wrong set of services. The
    prior picks are preserved so the operator can see what they chose before.
    """
    # The first version diffs against nothing, so every field reads as
    # "added" — there is no prior selection to invalidate in that case.
    if not had_previous_version:
        return
    changed = set(diff.changed_keys())
    if not changed.intersection(_SERVICE_DEFINING_FIELDS):
        return
    commercial = dict(profile.commercial_scope or {})
    prioritization = dict(commercial.get("service_prioritization") or {})
    if not prioritization.get("confirmed_at"):
        return
    prioritization["confirmed_at"] = None
    prioritization["reopened_reason"] = "cdd_services_changed"
    commercial["service_prioritization"] = prioritization
    profile.commercial_scope = commercial


async def create_cdd_version(
    db: AsyncSession,
    *,
    client_id: UUID,
    fields: dict[str, Any],
    source: str = "upload",
    filename: str | None = None,
    file_path: str | None = None,
    created_by: UUID | None = None,
    rolled_back_from: UUID | None = None,
) -> CddVersion:
    """Append a CDD version, or return the active one when nothing changed."""
    current = await active_version(db, client_id)
    new_hash = content_hash(fields)
    if current is not None and current.content_hash == new_hash:
        # Identical content (double-clicked upload, re-export). Do not churn a
        # version — that would mark phases stale for no real change.
        return current

    diff = diff_cdd(current.fields if current else None, fields)
    version = CddVersion(
        client_id=client_id,
        version_no=(current.version_no + 1) if current else 1,
        source=source,
        content_hash=new_hash,
        filename=filename,
        file_path=file_path,
        fields=dict(fields or {}),
        diff=diff.as_dict(),
        rolled_back_from=rolled_back_from,
        created_by=created_by,
    )
    db.add(version)
    await db.flush()

    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalars().first()
    if profile is not None:
        # Phases that have not started must pick up the latest CDD with no
        # approval step, so the derived slice refreshes immediately.
        _apply_cdd_slice(profile, fields)
        _reopen_service_selection_if_services_changed(
            profile, diff, had_previous_version=current is not None
        )

    log.info(
        "cdd_version_created",
        client_id=str(client_id),
        version_no=version.version_no,
        changed=len(diff.changed_keys()),
        source=source,
    )
    return version


async def rollback_to_version(
    db: AsyncSession,
    *,
    client_id: UUID,
    version_id: UUID,
    created_by: UUID | None = None,
) -> CddVersion:
    """Restore an older version by appending a copy of it. History is never mutated."""
    target = (
        await db.execute(
            select(CddVersion).where(
                CddVersion.id == version_id, CddVersion.client_id == client_id
            )
        )
    ).scalars().first()
    if target is None:
        raise ValueError("CDD version not found for this client")
    return await create_cdd_version(
        db,
        client_id=client_id,
        fields=dict(target.fields or {}),
        source="rollback",
        filename=target.filename,
        file_path=target.file_path,
        created_by=created_by,
        rolled_back_from=target.id,
    )


async def get_phase_state(
    db: AsyncSession,
    *,
    client_id: UUID,
    agent_key: str,
    run_id: UUID = DEFAULT_RUN_ID,
) -> PhaseState | None:
    return (
        await db.execute(
            select(PhaseState).where(
                PhaseState.client_id == client_id,
                PhaseState.run_id == run_id,
                PhaseState.agent_key == agent_key,
            )
        )
    ).scalars().first()


async def record_phase_start(
    db: AsyncSession,
    *,
    client_id: UUID,
    agent_key: str,
    run_id: UUID = DEFAULT_RUN_ID,
) -> PhaseState:
    """Stamp the phase with the CDD version it is about to run against.

    Captured at START, so a version landing mid-run does not retroactively make
    this run look current.
    """
    from datetime import datetime, timezone

    version = await active_version(db, client_id)
    state = await get_phase_state(db, client_id=client_id, agent_key=agent_key, run_id=run_id)
    if state is None:
        state = PhaseState(client_id=client_id, run_id=run_id, agent_key=agent_key)
        db.add(state)
    state.cdd_version_id = version.id if version else None
    state.started_at = datetime.now(timezone.utc)
    state.completed_at = None
    await db.flush()
    return state


async def dismiss_staleness(
    db: AsyncSession,
    *,
    client_id: UUID,
    agent_key: str,
    run_id: UUID = DEFAULT_RUN_ID,
) -> PhaseState:
    """Accept this phase as-is against the current CDD version.

    A later version re-raises staleness — a dismissal covers what the operator
    actually saw, not everything that might come next.
    """
    version = await active_version(db, client_id)
    state = await get_phase_state(db, client_id=client_id, agent_key=agent_key, run_id=run_id)
    if state is None:
        state = PhaseState(client_id=client_id, run_id=run_id, agent_key=agent_key)
        db.add(state)
    state.dismissed_through_version_id = version.id if version else None
    await db.flush()
    log.info("cdd_staleness_dismissed", client_id=str(client_id), agent_key=agent_key)
    return state


async def build_staleness_report(
    db: AsyncSession,
    *,
    client_id: UUID,
    run_id: UUID = DEFAULT_RUN_ID,
) -> dict[str, dict[str, Any]]:
    """Current staleness for every phase, derived from recorded versions."""
    versions = await list_versions(db, client_id)
    if not versions:
        return {phase: {"agent_key": phase, "state": "fresh", "fields": [], "upstream": []} for phase in PHASE_ORDER}

    latest = versions[-1]
    order = [str(v.id) for v in versions]
    diff_by_version = {
        str(v.id): CddDiff(
            added=dict((v.diff or {}).get("added") or {}),
            removed=dict((v.diff or {}).get("removed") or {}),
            modified={
                k: (entry.get("old"), entry.get("new"))
                for k, entry in ((v.diff or {}).get("modified") or {}).items()
            },
        )
        for v in versions
    }

    states = list(
        (
            await db.execute(
                select(PhaseState).where(
                    PhaseState.client_id == client_id, PhaseState.run_id == run_id
                )
            )
        ).scalars().all()
    )
    by_agent = {s.agent_key: s for s in states}
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalars().first()

    phase_versions = {
        phase: (str(by_agent[phase].cdd_version_id) if phase in by_agent and by_agent[phase].cdd_version_id else None)
        for phase in PHASE_ORDER
    }
    phase_statuses = {
        phase: str(getattr(profile, _STATUS_ATTR.get(phase, f"{phase}_status"), "not_started") or "not_started")
        if profile is not None
        else "not_started"
        for phase in PHASE_ORDER
    }
    dismissed_through = {
        phase: (
            str(by_agent[phase].dismissed_through_version_id)
            if phase in by_agent and by_agent[phase].dismissed_through_version_id
            else None
        )
        for phase in PHASE_ORDER
    }

    report = evaluate_staleness(
        phase_versions=phase_versions,
        phase_statuses=phase_statuses,
        latest_version_id=str(latest.id),
        diff_by_version=diff_by_version,
        dismissed_through=dismissed_through,
        version_order=order,
    )
    return {phase: entry.as_dict() for phase, entry in report.items()}
