"""Soft-archive and anonymize expired non-onboarding client memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentJob, ChatMessage, ChatSession, ClientDigitalProfile

ARCHIVED_MESSAGE_CONTENT = "[Archived and anonymized by client-memory retention policy]"

_CDP_SUMMARY_FIELDS = (
    "commercial_scope",
    "marketing_context",
    "tracking_baseline",
    "website_situation_summary",
    "competitive_landscape_summary",
    "search_demand_summary",
    "seo_strategy_summary",
    "site_architecture_summary",
    "technical_seo_summary",
    "content_audit_summary",
    "content_planning_summary",
    "content_production_summary",
    "on_page_seo_summary",
    "publishing_summary",
)

_CDP_STATUS_FIELDS = (
    "discovery_status",
    "tracking_status",
    "website_status",
    "competitor_status",
    "search_demand_status",
    "seo_strategy_status",
    "site_architecture_status",
    "technical_seo_status",
    "content_audit_status",
    "content_planning_status",
    "content_production_status",
    "on_page_seo_status",
    "publishing_status",
)


def _rowcount(result: Any) -> int:
    count = getattr(result, "rowcount", 0)
    return max(0, int(count or 0))


async def archive_expired_client_memory(
    db: AsyncSession,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Archive expired chat/job/CDP data while preserving onboarding clients.

    Rows remain for referential integrity and retention reporting, but sensitive
    free text and JSON payloads are removed.  The operation is idempotent.
    """
    days = max(1, int(retention_days))
    archived_at = now or datetime.now(timezone.utc)
    cutoff = archived_at - timedelta(days=days)

    eligible_session_ids = (
        select(ChatSession.id)
        .where(ChatSession.is_onboarding.is_(False))
    )

    messages = await db.execute(
        update(ChatMessage)
        .where(
            ChatMessage.session_id.in_(eligible_session_ids),
            ChatMessage.created_at < cutoff,
            ChatMessage.archived_at.is_(None),
        )
        .values(
            content=ARCHIVED_MESSAGE_CONTENT,
            structured_payload=None,
            archived_at=archived_at,
        )
        .execution_options(synchronize_session=False)
    )

    jobs = await db.execute(
        update(AgentJob)
        .where(
            AgentJob.session_id.in_(eligible_session_ids),
            AgentJob.created_at < cutoff,
            AgentJob.archived_at.is_(None),
        )
        .values(
            result_ref=None,
            error_detail=None,
            archived_at=archived_at,
        )
        .execution_options(synchronize_session=False)
    )

    has_recent_message = exists(
        select(ChatMessage.id).where(
            ChatMessage.session_id == ChatSession.id,
            ChatMessage.created_at >= cutoff,
            ChatMessage.archived_at.is_(None),
        )
    )
    has_recent_job = exists(
        select(AgentJob.id).where(
            AgentJob.session_id == ChatSession.id,
            AgentJob.created_at >= cutoff,
            AgentJob.archived_at.is_(None),
        )
    )
    sessions = await db.execute(
        update(ChatSession)
        .where(
            ChatSession.id.in_(eligible_session_ids),
            ChatSession.started_at < cutoff,
            ChatSession.archived_at.is_(None),
            ~has_recent_message,
            ~has_recent_job,
        )
        .values(active_agent_key=None, archived_at=archived_at)
        .execution_options(synchronize_session=False)
    )

    cdp_values: dict[str, Any] = {
        field: {} for field in _CDP_SUMMARY_FIELDS
    }
    cdp_values.update({field: "archived" for field in _CDP_STATUS_FIELDS})
    cdp_values.update(
        {
            "overall_readiness_score": None,
            "ready_for_phase5": False,
            "archived_at": archived_at,
            "updated_at": archived_at,
        }
    )
    profiles = await db.execute(
        update(ClientDigitalProfile)
        .where(
            ClientDigitalProfile.is_onboarding.is_(False),
            ClientDigitalProfile.updated_at < cutoff,
        )
        .values(**cdp_values)
        .execution_options(synchronize_session=False)
    )

    return {
        "status": "completed",
        "retention_days": days,
        "cutoff": cutoff.isoformat(),
        "archived_at": archived_at.isoformat(),
        "archived": {
            "chat_messages": _rowcount(messages),
            "agent_jobs": _rowcount(jobs),
            "chat_sessions": _rowcount(sessions),
            "client_digital_profiles": _rowcount(profiles),
        },
    }
