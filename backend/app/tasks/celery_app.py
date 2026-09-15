"""Celery app for long-running jobs. Falls back to inline execution when Redis is down."""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "searchfit",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "recheck-unverified-tracking": {
            "task": "app.tasks.jobs.recheck_unverified_tracking",
            "schedule": 3600.0,
        },
        "archive-expired-client-memory": {
            "task": "app.tasks.jobs.archive_expired_client_memory",
            "schedule": 86400.0,
        },
    },
)

celery_app.autodiscover_tasks(["app.tasks"])
