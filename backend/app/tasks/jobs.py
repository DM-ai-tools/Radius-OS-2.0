"""Celery tasks. Agents also run the same work inline when Redis is down."""

import asyncio

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.logging_config import get_logger
from app.tasks.celery_app import celery_app

log = get_logger("tasks")

_LONG_TASK = {"acks_late": True, "reject_on_worker_lost": True}


async def _in_session(coro_factory):
    async with AsyncSessionLocal() as db:
        result = await coro_factory(db)
        await db.commit()
        return result


@celery_app.task(name="app.tasks.jobs.site_crawl", **_LONG_TASK)
def site_crawl(client_id: str, session_id: str, url: str) -> dict:
    from app.services.background_jobs import run_site_crawl

    log.info("site_crawl_task", client_id=client_id, url=url)
    result = asyncio.run(
        _in_session(lambda db: run_site_crawl(db, client_id=client_id, session_id=session_id, url=url))
    )
    log.info("site_crawl_task_finished", status=result.get("status"), url=url)
    return result


@celery_app.task(name="app.tasks.jobs.backlink_pull", **_LONG_TASK)
def backlink_pull(client_id: str, domain: str) -> dict:
    from app.services.background_jobs import run_backlink_pull

    log.info("backlink_pull_task", client_id=client_id, domain=domain)
    result = asyncio.run(
        _in_session(lambda db: run_backlink_pull(db, client_id=client_id, domain=domain))
    )
    log.info("backlink_pull_task_finished", status=result.get("status"), domain=domain)
    return result


@celery_app.task(name="app.tasks.jobs.competitor_scan", **_LONG_TASK)
def competitor_scan(client_id: str, session_id: str) -> dict:
    from app.services.background_jobs import run_competitor_scan

    log.info("competitor_scan_task", client_id=client_id, session_id=session_id)
    result = asyncio.run(
        _in_session(
            lambda db: run_competitor_scan(db, client_id=client_id, session_id=session_id)
        )
    )
    log.info("competitor_scan_task_finished", status=result.get("status"), client_id=client_id)
    return result


@celery_app.task(name="app.tasks.jobs.anomaly_detection", **_LONG_TASK)
def anomaly_detection(client_id: str) -> dict:
    from app.services.background_jobs import run_anomaly_detection

    log.info("anomaly_detection_task", client_id=client_id)
    result = asyncio.run(
        _in_session(lambda db: run_anomaly_detection(db, client_id=client_id))
    )
    log.info("anomaly_detection_task_finished", status=result.get("status"), client_id=client_id)
    return result


@celery_app.task(name="app.tasks.jobs.recheck_unverified_tracking", **_LONG_TASK)
def recheck_unverified_tracking() -> dict:
    """Hourly: re-check unverified tags and recover jobs a dead worker abandoned."""
    from app.services.background_jobs import (
        recheck_unverified_tracking as recheck_service,
        recover_stuck_agent_jobs,
    )

    async def _run() -> dict:
        async with AsyncSessionLocal() as db:
            tracking = await recheck_service(db)
            recovery = await recover_stuck_agent_jobs(db)
            await db.commit()
            return {"status": "ok", "tracking": tracking, "recovery": recovery}

    result = asyncio.run(_run())
    log.info(
        "scheduled_recheck_unverified_tracking",
        clients=result["tracking"].get("clients"),
        rechecked=result["tracking"].get("rechecked"),
        recovered=result["recovery"].get("recovered"),
        abandoned=result["recovery"].get("abandoned"),
    )
    return result


@celery_app.task(name="app.tasks.jobs.archive_expired_client_memory")
def archive_expired_client_memory() -> dict:
    """Daily soft-archive for expired non-onboarding client memory."""
    settings = get_settings()
    if not settings.client_memory_retention_enabled:
        return {"status": "disabled"}

    async def _run() -> dict:
        from app.services.client_memory_retention import (
            archive_expired_client_memory as archive_service,
        )

        async with AsyncSessionLocal() as db:
            result = await archive_service(
                db,
                retention_days=settings.client_memory_retention_days,
            )
            await db.commit()
            return result

    result = asyncio.run(_run())
    log.info("scheduled_client_memory_archive_completed", **result.get("archived", {}))
    return result
