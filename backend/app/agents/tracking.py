"""Phase 2 — Access, Tracking & Data Collection (T1–T6)."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.providers import validate_tracking
from app.integrations.web_fetch import detect_tracking_snippets, fetch_url, parse_html
from app.ml.scoring import tracking_anomaly_flags
from app.models import ApiCredential, Client, FindingsLedger, TrackingAudit
from app.services.agent_runtime import get_profile
from app.services.audit import log_event
from app.services.readiness import compute_tracking_score, recompute_readiness
from app.services.role_skills import required_role_for
from app.services.tracking_workflow import (
    KNOWN_CHANGE_FIELDS,
    build_t1_platforms,
    build_t2_audit,
    build_t3_conversions,
    build_t4_baseline,
    compute_tracking_blockers,
)
from app.agents.prompts import load_skill

PROVIDER_ELEMENTS = {
    "ga4": ("ga4_base_tag", "conversion_event", "cross_domain_tracking"),
    "gtm": ("gtm_container",),
    "search_console": ("search_console_access",),
}


def _scoped_providers(message: str) -> list[str] | None:
    lowered = message.lower()
    if not any(k in lowered for k in ("re-check", "recheck", "re-validate", "revalidate", "granted")):
        return None
    found: list[str] = []
    if re.search(r"\bga4\b", lowered) or "google analytics" in lowered:
        found.append("ga4")
    if re.search(r"\bgtm\b", lowered) or "tag manager" in lowered:
        found.append("gtm")
    if "search console" in lowered or re.search(r"\bgsc\b", lowered):
        found.append("search_console")
    if (
        "google business" in lowered
        or re.search(r"\bgbp\b", lowered)
        or "business profile" in lowered
    ):
        found.append("google_business")
    return found or None


async def run_tracking(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    """Run automated T1–T4, then emit T5 known-changes form (T6 after submit)."""
    _ = load_skill("tracking_access_agent")
    _ = session_id, user_id
    events: list[dict] = []
    profile = await get_profile(db, client.id)

    from app.services.agent_handoff import blocked_events, consume_events

    events.extend(
        consume_events(
            "tracking_access_agent",
            pack_notes=[f"discovery={profile.discovery_status}"],
        )
    )
    if profile.discovery_status != "complete":
        events.extend(
            blocked_events(
                "tracking_access_agent",
                "Approve Phase 1 Discovery first so tracking uses locked commercial scope.",
                route_to="discovery_agent",
            )
        )
        return events

    profile.tracking_status = "in_progress"

    scope = _scoped_providers(message)
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Phase 2 — Access, Tracking & Data Collection (T1–T6). "
                + (
                    "Scoped re-check for " + ", ".join(scope) + "…"
                    if scope
                    else "Running automated T1–T4, then known-changes (T5)…"
                )
            ),
        }
    )

    creds = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client.id,
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    has = {c.provider: True for c in creds}
    oauth_providers = ("ga4", "search_console", "gtm", "google_business")
    provider_status = {
        p: ("Granted" if has.get(p) else "Not granted") for p in oauth_providers
    }

    # ── T1 — Access collection & connection verification ──
    cdd_access = {}
    if profile:
        cdd_access = dict((profile.tracking_baseline or {}).get("cdd_access") or {})
        if not cdd_access:
            cdd_access = dict(
                ((profile.marketing_context or {}).get("access_credentials") or {})
            )
    t1_platforms = build_t1_platforms(has, cdd_access=cdd_access)
    missing_oauth = [p for p in oauth_providers if not has.get(p)]
    if scope:
        missing_oauth = [p for p in missing_oauth if p in scope]

    events.append(
        {
            "type": "agent_message",
            "agent_key": "tracking_access_agent",
            "content": (
                "T1 — Access collection & connection verification (automated). "
                "Checking CMS, hosting, GSC, GA4, GTM, Google Business, CRM, and call-tracking."
            ),
        }
    )
    events.append(
        {
            "type": "structured_card",
            "payload": {
                "card_type": "tracking_t1_access",
                "title": "T1 — Access collection & connection verification",
                "subtitle": "Connection test per platform",
                "mode": "AUTOMATED",
                "step": "T1",
                "platforms": t1_platforms,
                "provider_status": provider_status,
                "agent_key": "tracking_access_agent",
            },
        }
    )

    if missing_oauth:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    "Google APIs not connected ("
                    + ", ".join(missing_oauth)
                    + "). Continuing without GA4 / GSC / GTM / Business Profile — "
                    "tag checks use live-site HTML only. Historical metrics stay empty."
                ),
            }
        )

    only_elements: set[str] | None = None
    if scope:
        only_elements = set()
        for p in scope:
            only_elements.update(PROVIDER_ELEMENTS.get(p, ()))

    checks = await validate_tracking(
        client.primary_url, has, only_elements=only_elements
    )
    flags = tracking_anomaly_flags()

    snippets: dict = {}
    try:
        url = client.primary_url if client.primary_url.startswith("http") else f"https://{client.primary_url}"
        fetched = await fetch_url(url)
        parser = parse_html(fetched.get("text") or "")
        snippets = detect_tracking_snippets(fetched.get("text") or "", parser.scripts)
    except Exception:  # noqa: BLE001
        snippets = {}

    # Persist tracking_audits (same as before)
    rows_out: list[dict] = []
    prior_by_element: dict[str, TrackingAudit] = {}
    if only_elements is not None:
        prior = (
            await db.execute(
                select(TrackingAudit)
                .where(TrackingAudit.client_id == client.id)
                .order_by(TrackingAudit.detected_at.desc())
            )
        ).scalars().all()
        for row in prior:
            if row.element not in prior_by_element:
                prior_by_element[row.element] = row

    checked_elements: set[str] = set()
    for check in checks:
        detail = dict(check["detail"])
        if flags and check["element"] in ("ga4_base_tag", "conversion_event"):
            detail["anomaly_flags"] = flags
        row = TrackingAudit(
            client_id=client.id,
            element=check["element"],
            check_result=check["check_result"],
            detail=detail,
            status="pending",
        )
        db.add(row)
        await db.flush()
        db.add(
            FindingsLedger(
                client_id=client.id,
                agent_key="tracking_access_agent",
                source_table="tracking_audits",
                source_id=row.id,
                confidence="high" if check["check_result"] == "pass" else "medium",
                status="pending",
            )
        )
        rows_out.append(
            {
                "id": str(row.id),
                "element": row.element,
                "check_result": row.check_result,
                "detail": detail,
            }
        )
        checked_elements.add(row.element)

    if only_elements is not None:
        for el, prow in prior_by_element.items():
            if el in checked_elements:
                continue
            rows_out.append(
                {
                    "id": str(prow.id),
                    "element": prow.element,
                    "check_result": prow.check_result,
                    "detail": prow.detail or {},
                    "carried_forward": True,
                }
            )

    # ── T2 — Automated tracking audit ──
    t2 = build_t2_audit(checks, snippets)
    events.append(
        {
            "type": "agent_message",
            "agent_key": "tracking_access_agent",
            "content": (
                "T2 — Automated tracking audit: tag presence, duplicates, cross-domain, "
                "referral exclusions, and cookie-consent notes (analytics layer, not page crawl)."
            ),
        }
    )
    events.append(
        {
            "type": "structured_card",
            "payload": {
                "card_type": "tracking_t2_audit",
                "step": "T2",
                "agent_key": "tracking_access_agent",
                **t2,
            },
        }
    )

    # ── T3 — Conversion validation ──
    t3 = build_t3_conversions(checks)
    events.append(
        {
            "type": "agent_message",
            "agent_key": "tracking_access_agent",
            "content": (
                "T3 — Conversion tracking validation: primary/secondary conversions must be "
                "verified as firing — configured-but-unverified counts as not working."
            ),
        }
    )
    events.append(
        {
            "type": "structured_card",
            "payload": {
                "card_type": "tracking_t3_conversions",
                "step": "T3",
                "agent_key": "tracking_access_agent",
                **t3,
            },
        }
    )

    # ── T4 — Historical baseline ──
    t4 = build_t4_baseline(
        has_ga4=bool(has.get("ga4")),
        has_gsc=bool(has.get("search_console")),
        display_name=client.display_name,
    )
    # Stash on profile draft area until T6 approve (under tracking_baseline draft)
    baseline = dict(profile.tracking_baseline or {})
    baseline["historical_draft"] = t4
    baseline["t1_platforms"] = t1_platforms
    profile.tracking_baseline = baseline

    events.append(
        {
            "type": "agent_message",
            "agent_key": "tracking_access_agent",
            "content": (
                "T4 — Historical baseline extraction: 6–12 months of organic users, sessions, "
                "conversions, revenue, impressions, clicks, CTR, position, queries, landings, "
                "index coverage, branded vs non-branded (via GSC + GA4 when connected)."
            ),
        }
    )
    events.append(
        {
            "type": "structured_card",
            "payload": {
                "card_type": "tracking_t4_baseline",
                "step": "T4",
                "agent_key": "tracking_access_agent",
                **t4,
            },
        }
    )

    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="tool_call",
        event_detail={
            "tool": "tracking_t1_t4",
            "agent": "tracking_access_agent",
            "scope": scope,
        },
    )
    await recompute_readiness(db, client.id)

    # ── T5 — Known-changes log (human input) ──
    events.append(
        {
            "type": "agent_message",
            "agent_key": "tracking_access_agent",
            "content": (
                "T5 — Known-changes log needs a person. APIs cannot see redesigns, domain moves, "
                "past SEO work, manual actions, security incidents, or algorithm timing — "
                "Client Success + client capture that institutional memory."
            ),
        }
    )
    events.append(
        {
            "type": "structured_card",
            "payload": {
                "card_type": "tracking_t5_known_changes",
                "title": "T5 — Known-changes log",
                "subtitle": "Client Success Manager + client",
                "mode": "HUMAN INPUT",
                "step": "T5",
                "fields": KNOWN_CHANGE_FIELDS,
                "agent_key": "tracking_access_agent",
                "actions": ["submit_known_changes"],
            },
        }
    )
    events.append(
        {
            "type": "phase_status",
            "payload": {
                "tracking_status": profile.tracking_status,
                "overall_readiness_score": float(profile.overall_readiness_score or 0),
            },
        }
    )
    events.append(
        {
            "type": "system_notice",
            "content": "Next: submit T5 known-changes, then T6 Technical SEO sign-off.",
        }
    )
    return events


async def build_tracking_signoff_events(
    db: AsyncSession,
    *,
    client_id: UUID,
    known_changes: dict | None = None,
) -> list[dict]:
    """T6 — Readiness scoring & sign-off after known-changes submitted."""
    events: list[dict] = []
    profile = await get_profile(db, client_id)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one()

    if known_changes is not None:
        baseline = dict(profile.tracking_baseline or {})
        baseline["known_changes"] = known_changes
        profile.tracking_baseline = baseline

    rows = (
        await db.execute(
            select(TrackingAudit)
            .where(TrackingAudit.client_id == client_id)
            .order_by(TrackingAudit.detected_at.desc())
        )
    ).scalars().all()
    latest: dict[str, TrackingAudit] = {}
    for r in rows:
        if r.element not in latest:
            latest[r.element] = r
    rows_out = [
        {
            "id": str(r.id),
            "element": r.element,
            "check_result": r.check_result,
            "detail": r.detail or {},
        }
        for r in latest.values()
    ]

    score, missing = await compute_tracking_score(db, client_id)
    blockers = compute_tracking_blockers(rows_out)
    await recompute_readiness(db, client_id)
    profile.tracking_status = "pending_signoff"
    await db.flush()

    automation_level = 0 if blockers else 1
    card = {
        "card_type": "tracking_t6_signoff",
        "title": "T6 — Readiness scoring & sign-off",
        "subtitle": "Technical SEO Specialist",
        "mode": "HUMAN GATE",
        "step": "T6",
        "client_name": client.display_name,
        "tracking_score": float(score),
        "missing": missing,
        "blockers": blockers,
        "automation_level": automation_level,
        "automation_note": (
            "Tracking blockers found — client held at automation Level 0 until fixed."
            if blockers
            else "No hard tracking blockers in the current checks."
        ),
        "known_changes": (profile.tracking_baseline or {}).get("known_changes") or {},
        "historical_draft": (profile.tracking_baseline or {}).get("historical_draft") or {},
        "rows": rows_out,
        "agent_key": "tracking_access_agent",
        "actions": ["approve", "flag_for_client"],
        "required_role": required_role_for("tracking_access_agent"),
    }

    events.append(
        {
            "type": "agent_message",
            "agent_key": "tracking_access_agent",
            "content": (
                f"T6 — weighted tracking readiness is {float(score):.0f}%. "
                + (
                    f"{len(blockers)} blocker(s) keep automation at Level 0. "
                    if blockers
                    else "No Level-0 blockers flagged. "
                )
                + "Technical SEO Specialist must approve before this writes System Access "
                "and Historical Baseline into the Client Digital Profile."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {
                "tracking_status": profile.tracking_status,
                "overall_readiness_score": float(profile.overall_readiness_score or 0),
            },
        }
    )
    events.append(
        {
            "type": "system_notice",
            "content": "Next: Tech SEO Approve baseline (or flag), then run website situation analysis.",
        }
    )
    return events
