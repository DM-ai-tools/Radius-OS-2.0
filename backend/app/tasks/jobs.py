"""Celery task stubs — agents also run jobs inline for local/dev without Redis."""

from app.logging_config import get_logger
from app.tasks.celery_app import celery_app

log = get_logger("tasks")


@celery_app.task(name="app.tasks.jobs.site_crawl")
def site_crawl(client_id: str, session_id: str, url: str) -> dict:
    log.info("site_crawl_task", client_id=client_id, url=url)
    return {"status": "not_dispatched_execution_is_inline", "url": url}


@celery_app.task(name="app.tasks.jobs.backlink_pull")
def backlink_pull(client_id: str, domain: str) -> dict:
    return {"status": "ok", "domain": domain}


@celery_app.task(name="app.tasks.jobs.competitor_scan")
def competitor_scan(client_id: str, session_id: str) -> dict:
    return {"status": "ok", "client_id": client_id}


@celery_app.task(name="app.tasks.jobs.anomaly_detection")
def anomaly_detection(client_id: str) -> dict:
    return {"status": "ok", "client_id": client_id}


@celery_app.task(name="app.tasks.jobs.recheck_unverified_tracking")
def recheck_unverified_tracking() -> dict:
    log.info("scheduled_recheck_unverified_tracking_noop")
    return {"status": "not_implemented"}
