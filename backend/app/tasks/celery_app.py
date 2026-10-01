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
    # Ack only after the task finishes. If the worker dies mid-crawl, the
    # broker redelivers it instead of dropping a job the chat still shows as running.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 7200},
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
