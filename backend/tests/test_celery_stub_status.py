"""Celery tasks must run the work and recover jobs a dead worker left open."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    AgentJob,
    ChatSession,
    Client,
    ClientDigitalProfile,
    Role,
    TrackingAudit,
    User,
    WebsiteAudit,
)
from app.security import hash_password
from app.services.background_jobs import (
    recheck_unverified_tracking,
    recover_stuck_agent_jobs,
    run_anomaly_detection,
    run_backlink_pull,
    run_competitor_scan,
    run_site_crawl,
)
from app.tasks.celery_app import celery_app


async def _client(db_session, *, url: str = "https://acme.example") -> tuple[Client, ChatSession]:
    role = (
        await db_session.execute(select(Role).where(Role.name == "head_of_department"))
    ).scalar_one()
    user = User(
        email=f"jobs-{url}@example.com",
        full_name="Jobs Tester",
        role_id=role.id,
        hashed_password=hash_password("testpass123"),
        is_active=True,
    )
    client = Client(
        legal_name="Acme",
        display_name="Acme",
        primary_url=url,
        industry="Retail",
        tier="B",
        status="active",
    )
    db_session.add_all([user, client])
    await db_session.flush()
    db_session.add(
        ClientDigitalProfile(
            client_id=client.id,
            discovery_status="complete",
            tracking_status="not_started",
        )
    )
    session = ChatSession(client_id=client.id, user_id=user.id, active_agent_key="website_situation_agent")
    db_session.add(session)
    await db_session.flush()
    return client, session


async def test_site_crawl_persists_a_finished_job(db_session):
    client, session = await _client(db_session)
    result = await run_site_crawl(
        db_session,
        client_id=str(client.id),
        session_id=str(session.id),
        url=client.primary_url,
    )
    assert result["status"] == "succeeded"
    assert result["pages_found"] == 342
    job = (
        await db_session.execute(select(AgentJob).where(AgentJob.job_type == "site_crawl"))
    ).scalar_one()
    assert job.status == "succeeded"
    audit = (
        await db_session.execute(select(WebsiteAudit).where(WebsiteAudit.audit_type == "site_crawl"))
    ).scalar_one()
    assert audit.linked_job_id == job.id


async def test_backlink_pull_records_the_provider(db_session):
    client, _session = await _client(db_session)
    result = await run_backlink_pull(db_session, client_id=str(client.id), domain="acme.example")
    assert result["status"] == "succeeded"
    assert result["provider"]
    job = (
        await db_session.execute(select(AgentJob).where(AgentJob.job_type == "backlink_pull"))
    ).scalar_one()
    assert job.status == "succeeded"
    assert job.provider_used == result["provider"]


async def test_anomaly_detection_skips_until_tracking_is_complete(db_session):
    client, _session = await _client(db_session)
    skipped = await run_anomaly_detection(db_session, client_id=str(client.id))
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "tracking_baseline_not_complete"

    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    profile.tracking_status = "complete"
    await db_session.flush()
    done = await run_anomaly_detection(db_session, client_id=str(client.id))
    assert done["status"] == "succeeded"


async def test_competitor_scan_runs_the_agent(db_session, monkeypatch):
    client, session = await _client(db_session)

    async def _fake_run(db, *, client, session_id, user_id, message):
        return [{"type": "structured_card", "payload": {"empty": False}}]

    monkeypatch.setattr("app.agents.competitor.run_competitor", _fake_run)
    result = await run_competitor_scan(
        db_session,
        client_id=str(client.id),
        session_id=str(session.id),
    )
    assert result["status"] == "succeeded"
    assert result["events"] == 1


async def test_recheck_replaces_unverified_tracking_rows(db_session, monkeypatch):
    client, _session = await _client(db_session)
    db_session.add(
        TrackingAudit(
            client_id=client.id,
            element="ga4_base_tag",
            check_result="unverified",
            detail={"message": "fetch failed"},
            status="pending",
        )
    )
    await db_session.flush()

    async def _fake_validate(domain, has, only_elements=None):
        assert "ga4_base_tag" in only_elements
        return [
            {
                "element": "ga4_base_tag",
                "check_result": "pass",
                "detail": {"message": "GA4 tag detected"},
            }
        ]

    monkeypatch.setattr("app.services.background_jobs.validate_tracking", _fake_validate)
    result = await recheck_unverified_tracking(db_session)
    assert result["status"] == "ok"
    assert result["clients"] == 1
    assert result["resolved"] == 1
    assert result["still_unverified"] == 0
    latest = (
        await db_session.execute(
            select(TrackingAudit)
            .where(TrackingAudit.client_id == client.id)
            .order_by(TrackingAudit.detected_at.desc())
        )
    ).scalars().first()
    assert latest.check_result == "pass"


async def test_recheck_ignores_an_element_already_resolved(db_session, monkeypatch):
    client, _session = await _client(db_session, url="https://resolved.example")
    older = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.add_all(
        [
            TrackingAudit(
                client_id=client.id,
                element="ga4_base_tag",
                check_result="unverified",
                detail={},
                detected_at=older,
                status="pending",
            ),
            TrackingAudit(
                client_id=client.id,
                element="ga4_base_tag",
                check_result="pass",
                detail={"message": "already verified"},
                status="pending",
            ),
        ]
    )
    await db_session.flush()

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("resolved elements must not be rechecked")

    monkeypatch.setattr("app.services.background_jobs.validate_tracking", _should_not_run)
    result = await recheck_unverified_tracking(db_session)
    assert result["clients"] == 0
    assert result["rechecked"] == 0


async def test_stuck_running_job_is_recovered_then_abandoned(db_session):
    client, session = await _client(db_session, url="https://stuck.example")
    now = datetime.now(timezone.utc)
    stalled = AgentJob(
        session_id=session.id,
        agent_key="website_situation_agent",
        job_type="site_crawl",
        status="running",
        attempt_count=1,
        created_at=now - timedelta(hours=2),
    )
    fresh = AgentJob(
        session_id=session.id,
        agent_key="website_situation_agent",
        job_type="backlink_pull",
        status="running",
        attempt_count=1,
        created_at=now - timedelta(minutes=5),
    )
    exhausted = AgentJob(
        session_id=session.id,
        agent_key="search_demand",
        job_type="keyword_research",
        status="running",
        attempt_count=3,
        created_at=now - timedelta(hours=3),
    )
    db_session.add_all([stalled, fresh, exhausted])
    await db_session.flush()

    first = await recover_stuck_agent_jobs(db_session, now=now)
    assert first["recovered"] == 1
    assert first["abandoned"] == 1
    assert stalled.status == "retrying"
    assert stalled.attempt_count == 2
    assert stalled.error_detail == "recovered_after_stall"
    assert fresh.status == "running"
    assert exhausted.status == "failed"
    assert exhausted.error_detail == "abandoned_after_max_attempts"

    stalled.created_at = now - timedelta(hours=2)
    stalled.attempt_count = 3
    stalled.status = "running"
    stalled.completed_at = None
    second = await recover_stuck_agent_jobs(db_session, now=now)
    assert stalled.status == "failed"
    assert second["abandoned"] == 1


def test_long_running_tasks_are_redelivered_when_the_worker_dies():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_transport_options["visibility_timeout"] >= 3600
    schedule = celery_app.conf.beat_schedule["recheck-unverified-tracking"]
    assert schedule["task"] == "app.tasks.jobs.recheck_unverified_tracking"
    assert schedule["schedule"] == 3600.0
