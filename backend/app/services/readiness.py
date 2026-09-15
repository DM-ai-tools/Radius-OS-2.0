"""Weighted readiness model across Phases 1–4."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    ClientDigitalProfile,
    CompetitorProfile,
    DiscoveryResponse,
    ReadinessScore,
    TrackingAudit,
    WebsiteAudit,
)
from app.services.discovery_fields import DISCOVERY_FIELDS

# Phase weights sum to 1.0
PHASE_WEIGHTS = {
    "discovery": Decimal("0.30"),
    "tracking": Decimal("0.25"),
    "website": Decimal("0.25"),
    "competitor": Decimal("0.20"),
}

TRACKING_ELEMENTS = [
    "ga4_base_tag",
    "conversion_event",
    "gtm_container",
    "search_console_access",
    "cross_domain_tracking",
]


async def compute_discovery_score(db: AsyncSession, client_id: UUID) -> tuple[Decimal, list[str]]:
    result = await db.execute(
        select(DiscoveryResponse).where(DiscoveryResponse.client_id == client_id)
    )
    rows = list(result.scalars().all())
    by_key: dict[str, DiscoveryResponse] = {}
    for r in rows:
        # Prefer client_questionnaire, then approved
        existing = by_key.get(r.field_key)
        if existing is None or r.source == "client_questionnaire":
            by_key[r.field_key] = r

    missing: list[str] = []
    scored = 0
    for key in DISCOVERY_FIELDS:
        row = by_key.get(key)
        raw_val = None
        if row and isinstance(row.field_value, dict):
            raw_val = row.field_value.get("value")
        elif row:
            raw_val = row.field_value
        empty = raw_val in (None, "", [], {}) or (
            isinstance(raw_val, str) and not raw_val.strip()
        )
        if row is None or empty:
            missing.append(key)
            continue
        if row.confidence is not None and float(row.confidence) < 0.5:
            missing.append(f"{key} (low confidence)")
            scored += 0.5
        else:
            scored += 1
    score = Decimal(str(round(100 * scored / len(DISCOVERY_FIELDS), 2)))
    return score, missing


async def compute_tracking_score(db: AsyncSession, client_id: UUID) -> tuple[Decimal, list[str]]:
    result = await db.execute(
        select(TrackingAudit).where(TrackingAudit.client_id == client_id)
    )
    rows = list(result.scalars().all())
    latest: dict[str, TrackingAudit] = {}
    for r in rows:
        latest[r.element] = r

    missing: list[str] = []
    points = 0.0
    for el in TRACKING_ELEMENTS:
        row = latest.get(el)
        if row is None:
            missing.append(el)
            continue
        if row.check_result == "pass":
            points += 1
        elif row.check_result == "warning":
            points += 0.7
            missing.append(f"{el} (warning)")
        elif row.check_result == "unverified":
            points += 0.2
            missing.append(f"{el} (unverified)")
        else:
            missing.append(f"{el} (fail)")
    score = Decimal(str(round(100 * points / len(TRACKING_ELEMENTS), 2)))
    return score, missing


async def compute_website_score(db: AsyncSession, client_id: UUID) -> tuple[Decimal, list[str]]:
    result = await db.execute(select(WebsiteAudit).where(WebsiteAudit.client_id == client_id))
    rows = list(result.scalars().all())
    types = {r.audit_type for r in rows}
    required = {"crawl_technical", "backlink_summary", "traffic_anomaly"}
    missing = sorted(required - types)
    approved = [r for r in rows if r.status in ("approved", "edited")]
    if not rows:
        return Decimal(0), list(required)
    base = 100 * len(types & required) / len(required)
    if approved:
        base = min(100, base + 10)
    critical = [r for r in rows if r.severity == "critical" and r.status == "pending"]
    if critical:
        missing.append("critical findings pending review")
        base = max(0, base - 15)
    return Decimal(str(round(base, 2))), missing


async def compute_competitor_score(db: AsyncSession, client_id: UUID) -> tuple[Decimal, list[str]]:
    result = await db.execute(
        select(CompetitorProfile).where(CompetitorProfile.client_id == client_id)
    )
    comps = list(result.scalars().all())
    missing: list[str] = []
    if not comps:
        return Decimal(0), ["competitor set"]
    confirmed = [c for c in comps if c.confirmed]
    clustered = [c for c in comps if c.positioning_cluster]
    points = 40
    if confirmed:
        points += 30
    else:
        missing.append("competitor set not confirmed")
    if clustered:
        points += 30
    else:
        missing.append("positioning clusters")
    return Decimal(str(min(100, points))), missing


async def recompute_readiness(db: AsyncSession, client_id: UUID) -> ClientDigitalProfile:
    scores = {
        "discovery": await compute_discovery_score(db, client_id),
        "tracking": await compute_tracking_score(db, client_id),
        "website": await compute_website_score(db, client_id),
        "competitor": await compute_competitor_score(db, client_id),
    }

    overall = Decimal(0)
    all_missing: list[str] = []
    for phase, (score, missing) in scores.items():
        db.add(
            ReadinessScore(
                client_id=client_id,
                phase=phase,
                score=score,
                missing_fields={"items": missing},
            )
        )
        overall += score * PHASE_WEIGHTS[phase]
        all_missing.extend([f"{phase}: {m}" for m in missing])

    result = await db.execute(
        select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
    )
    profile = result.scalar_one()
    profile.overall_readiness_score = Decimal(str(round(float(overall), 2)))
    # Score is informational; Phase 5+ still require predecessor approve gates.
    profile.ready_for_phase5 = True
    await db.flush()
    return profile


def readiness_payload(profile: ClientDigitalProfile, phase_scores: dict) -> dict:
    settings = get_settings()
    missing: list[str] = []
    for phase, data in phase_scores.items():
        missing.extend([f"{phase}: {m}" for m in data.get("missing", [])])
    return {
        "overall": float(profile.overall_readiness_score or 0),
        "threshold": settings.readiness_threshold,
        "ready_for_phase5": True,
        "phases": phase_scores,
        "missing": missing,
        "statuses": {
            "discovery": profile.discovery_status,
            "tracking": profile.tracking_status,
            "website": profile.website_status,
            "competitor": profile.competitor_status,
            "search_demand": profile.search_demand_status,
            "seo_strategy": profile.seo_strategy_status,
            "site_architecture": profile.site_architecture_status,
            "technical_seo": profile.technical_seo_status,
            "content_audit": profile.content_audit_status,
            "content_planning": profile.content_planning_status,
            "content_production": profile.content_production_status,
            "on_page_seo": profile.on_page_seo_status,
            "publishing": profile.publishing_status,
        },
        "can_gate": False,
    }
