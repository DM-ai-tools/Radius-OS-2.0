"""Celery task stubs — agents also run jobs inline for local/dev without Redis."""

import asyncio

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.logging_config import get_logger
from app.tasks.celery_app import celery_app

log = get_logger("tasks")


@celery_app.task(name="app.tasks.jobs.site_crawl")
def site_crawl(client_id: str, session_id: str, url: str) -> dict:
    log.info("site_crawl_task", client_id=client_id, url=url)
    return {"status": "not_dispatched_execution_is_inline", "url": url}


@celery_app.task(name="app.tasks.jobs.backlink_pull")
def backlink_pull(client_id: str, domain: str) -> dict:
    log.info("backlink_pull_task", client_id=client_id, domain=domain)
    return {"status": "not_dispatched_execution_is_inline", "domain": domain}


@celery_app.task(name="app.tasks.jobs.competitor_scan")
def competitor_scan(client_id: str, session_id: str) -> dict:
    log.info("competitor_scan_task", client_id=client_id, session_id=session_id)
    return {"status": "not_dispatched_execution_is_inline", "client_id": client_id}


@celery_app.task(name="app.tasks.jobs.anomaly_detection")
def anomaly_detection(client_id: str) -> dict:
    log.info("anomaly_detection_task", client_id=client_id)
    return {"status": "not_dispatched_execution_is_inline", "client_id": client_id}


@celery_app.task(name="app.tasks.jobs.recheck_unverified_tracking")
def recheck_unverified_tracking() -> dict:
    log.info("scheduled_recheck_unverified_tracking_noop")
    return {"status": "not_implemented"}


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
