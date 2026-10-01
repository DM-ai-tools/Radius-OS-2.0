"""Work the Celery workers actually run, including stalled-job recovery.

Chat still executes these inline when Redis is down. The task bodies call
the same functions so a worker does the crawl, backlink pull, competitor
scan, anomaly check, or tracking recheck instead of returning a stub.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.providers import crawl_site, pull_backlinks, validate_tracking
from app.ml.scoring import detect_traffic_anomalies
from app.models import (
    AgentJob,
    ApiCredential,
    ChatSession,
    Client,
    FindingsLedger,
    TrackingAudit,
    WebsiteAudit,
)

STUCK_AFTER = timedelta(minutes=30)
MAX_JOB_ATTEMPTS = 3
_OPEN_STATUSES = ("queued", "running", "retrying")


def _as_uuid(value: str | UUID) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def recover_stuck_agent_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    stuck_after: timedelta = STUCK_AFTER,
    max_attempts: int = MAX_JOB_ATTEMPTS,
) -> dict:
    """Requeue jobs a dead worker left in queued/running, or abandon them.

    A job younger than ``stuck_after`` is left alone. After ``max_attempts``
    it is marked failed so the chat stops showing a crawl that will never finish.
    """
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - stuck_after
    rows = (
        await db.execute(
            select(AgentJob).where(
                AgentJob.status.in_(_OPEN_STATUSES),
                AgentJob.completed_at.is_(None),
                AgentJob.archived_at.is_(None),
            )
        )
    ).scalars().all()
    recovered = 0
    abandoned = 0
    for job in rows:
        created = _aware(job.created_at)
        if created is None or created >= cutoff:
            continue
        attempts = int(job.attempt_count or 0)
        if attempts >= max_attempts:
            job.status = "failed"
            job.completed_at = moment
            job.error_detail = "abandoned_after_max_attempts"
            abandoned += 1
            continue
        job.status = "retrying"
        job.attempt_count = attempts + 1
        job.error_detail = "recovered_after_stall"
        recovered += 1
    return {
        "status": "ok",
        "recovered": recovered,
        "abandoned": abandoned,
    }


async def _claim_job(
    db: AsyncSession,
    *,
    session_id: UUID | None,
    agent_key: str,
    job_type: str,
) -> AgentJob | None:
    if session_id is None:
        return None
    existing = (
        await db.execute(
            select(AgentJob)
            .where(
                AgentJob.session_id == session_id,
                AgentJob.job_type == job_type,
                AgentJob.status.in_(_OPEN_STATUSES),
                AgentJob.archived_at.is_(None),
            )
            .order_by(AgentJob.created_at.desc())
        )
    ).scalars().first()
    if existing is not None:
        existing.status = "running"
        existing.attempt_count = max(int(existing.attempt_count or 0), 1)
        return existing
    job = AgentJob(
        session_id=session_id,
        agent_key=agent_key,
        job_type=job_type,
        status="running",
        attempt_count=1,
    )
    db.add(job)
    await db.flush()
    return job


def _finish(job: AgentJob | None, *, status: str, error: str | None = None, result: dict | None = None) -> None:
    if job is None:
        return
    job.status = status
    job.completed_at = datetime.now(timezone.utc)
    if error:
        job.error_detail = error[:280]
    if result is not None:
        job.result_ref = result


async def run_site_crawl(
    db: AsyncSession,
    *,
    client_id: str,
    session_id: str,
    url: str,
) -> dict:
    sid = _as_uuid(session_id)
    cid = _as_uuid(client_id)
    job = await _claim_job(
        db,
        session_id=sid,
        agent_key="website_situation_agent",
        job_type="site_crawl",
    )
    try:
        crawl = await crawl_site(url)
    except Exception as exc:  # noqa: BLE001
        _finish(job, status="failed", error=str(exc))
        return {"status": "failed", "url": url, "error": str(exc)[:280]}
    summary = {
        "pages_found": crawl.get("pages_found"),
        "indexable": crawl.get("indexable"),
        "severity": crawl.get("severity"),
    }
    _finish(job, status="succeeded", result=summary)
    if cid is not None:
        db.add(
            WebsiteAudit(
                client_id=cid,
                audit_type="site_crawl",
                summary=crawl,
                severity=str(crawl.get("severity") or "info"),
                linked_job_id=job.id if job else None,
                status="pending",
            )
        )
        await db.flush()
    return {"status": "succeeded", "url": url, **summary}


async def run_backlink_pull(db: AsyncSession, *, client_id: str, domain: str) -> dict:
    cid = _as_uuid(client_id)
    session = await _latest_session(db, cid)
    job = await _claim_job(
        db,
        session_id=session.id if session else None,
        agent_key="website_situation_agent",
        job_type="backlink_pull",
    )
    try:
        payload, provider = await pull_backlinks(domain)
    except Exception as exc:  # noqa: BLE001
        _finish(job, status="failed", error=str(exc))
        return {"status": "failed", "domain": domain, "error": str(exc)[:280]}
    summary = {"provider": provider, "domain": domain}
    _finish(job, status="succeeded", result=summary)
    if job is not None:
        job.provider_used = provider
    if cid is not None:
        db.add(
            WebsiteAudit(
                client_id=cid,
                audit_type="backlink_summary",
                summary=payload,
                severity="info",
                linked_job_id=job.id if job else None,
                status="pending",
            )
        )
        await db.flush()
    return {"status": "succeeded", "domain": domain, "provider": provider}


async def run_anomaly_detection(db: AsyncSession, *, client_id: str) -> dict:
    cid = _as_uuid(client_id)
    if cid is None:
        return {"status": "failed", "client_id": client_id, "error": "invalid_client_id"}
    from app.services.agent_runtime import get_profile

    try:
        profile = await get_profile(db, cid)
    except Exception:  # noqa: BLE001
        return {
            "status": "skipped",
            "client_id": client_id,
            "reason": "tracking_baseline_not_complete",
        }
    if profile.tracking_status != "complete":
        return {
            "status": "skipped",
            "client_id": client_id,
            "reason": "tracking_baseline_not_complete",
        }
    session = await _latest_session(db, cid)
    job = await _claim_job(
        db,
        session_id=session.id if session else None,
        agent_key="website_situation_agent",
        job_type="anomaly_detection",
    )
    anomaly = detect_traffic_anomalies()
    _finish(
        job,
        status="succeeded",
        result={"severity": anomaly.get("severity"), "drop_percent": anomaly.get("drop_percent")},
    )
    db.add(
        WebsiteAudit(
            client_id=cid,
            audit_type="traffic_anomaly",
            summary=anomaly,
            severity=str(anomaly.get("severity") or "info"),
            linked_job_id=job.id if job else None,
            status="pending",
        )
    )
    await db.flush()
    return {
        "status": "succeeded",
        "client_id": client_id,
        "severity": anomaly.get("severity"),
    }


async def run_competitor_scan(db: AsyncSession, *, client_id: str, session_id: str) -> dict:
    cid = _as_uuid(client_id)
    sid = _as_uuid(session_id)
    client = await db.get(Client, cid) if cid else None
    session = await db.get(ChatSession, sid) if sid else None
    if client is None or session is None:
        return {"status": "failed", "client_id": client_id, "error": "client_or_session_missing"}
    from app.agents.competitor import run_competitor

    try:
        events = await run_competitor(
            db,
            client=client,
            session_id=session.id,
            user_id=session.user_id,
            message="refresh competitor scan",
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "client_id": client_id, "error": str(exc)[:280]}
    failed = any(isinstance(event, dict) and event.get("type") == "error" for event in events)
    return {
        "status": "failed" if failed else "succeeded",
        "client_id": client_id,
        "events": len(events),
    }


async def _latest_session(db: AsyncSession, client_id: UUID | None) -> ChatSession | None:
    if client_id is None:
        return None
    return (
        await db.execute(
            select(ChatSession)
            .where(ChatSession.client_id == client_id, ChatSession.archived_at.is_(None))
            .order_by(ChatSession.started_at.desc())
        )
    ).scalars().first()


async def recheck_unverified_tracking(db: AsyncSession) -> dict:
    """Re-run live tag checks for the latest unverified tracking rows."""
    rows = (
        await db.execute(select(TrackingAudit).order_by(TrackingAudit.detected_at.desc()))
    ).scalars().all()
    latest: dict[tuple[UUID, str], TrackingAudit] = {}
    for row in rows:
        key = (row.client_id, row.element)
        if key not in latest:
            latest[key] = row
    by_client: dict[UUID, set[str]] = {}
    for (client_id, element), row in latest.items():
        if row.check_result == "unverified":
            by_client.setdefault(client_id, set()).add(element)

    rechecked = 0
    still_unverified = 0
    resolved = 0
    for client_id, elements in by_client.items():
        client = await db.get(Client, client_id)
        if client is None or not elements:
            continue
        creds = (
            await db.execute(
                select(ApiCredential).where(
                    ApiCredential.client_id == client.id,
                    ApiCredential.revoked_at.is_(None),
                )
            )
        ).scalars().all()
        has = {c.provider: True for c in creds}
        checks = await validate_tracking(
            client.primary_url,
            has,
            only_elements=elements,
        )
        for check in checks:
            if check.get("element") not in elements:
                continue
            audit = TrackingAudit(
                client_id=client.id,
                element=str(check["element"]),
                check_result=str(check["check_result"]),
                detail=dict(check.get("detail") or {}),
                detected_at=datetime.now(timezone.utc),
                status="pending",
            )
            db.add(audit)
            await db.flush()
            db.add(
                FindingsLedger(
                    client_id=client.id,
                    agent_key="tracking_access_agent",
                    source_table="tracking_audits",
                    source_id=audit.id,
                    confidence="high" if audit.check_result == "pass" else "medium",
                    status="pending",
                )
            )
            rechecked += 1
            if audit.check_result == "unverified":
                still_unverified += 1
            else:
                resolved += 1
    return {
        "status": "ok",
        "clients": len(by_client),
        "rechecked": rechecked,
        "still_unverified": still_unverified,
        "resolved": resolved,
    }
