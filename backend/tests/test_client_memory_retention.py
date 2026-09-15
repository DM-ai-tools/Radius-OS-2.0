from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    AgentJob,
    ChatMessage,
    ChatSession,
    Client,
    ClientDigitalProfile,
    Role,
    User,
)
from app.security import hash_password
from app.services.client_memory_retention import (
    ARCHIVED_MESSAGE_CONTENT,
    archive_expired_client_memory,
)
from app.tasks.celery_app import celery_app


async def test_retention_archives_old_non_onboarding_memory_and_exempts_onboarding(
    db_session,
):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=45)
    recent = now - timedelta(days=2)
    role = (
        await db_session.execute(
            select(Role).where(Role.name == "head_of_department")
        )
    ).scalar_one()
    user = User(
        email="retention@example.com",
        full_name="Retention Tester",
        role_id=role.id,
        hashed_password=hash_password("testpass123"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    regular = Client(
        legal_name="Regular Client",
        display_name="Regular Client",
        primary_url="https://regular.example",
        industry="Retail",
        tier="B",
        status="active",
        is_onboarding=False,
    )
    onboarding = Client(
        legal_name="Onboarding Client",
        display_name="Onboarding Client",
        primary_url="https://onboarding.example",
        industry="Retail",
        tier="B",
        status="active",
        is_onboarding=False,
    )
    recent_client = Client(
        legal_name="Recent Client",
        display_name="Recent Client",
        primary_url="https://recent.example",
        industry="Retail",
        tier="B",
        status="active",
        is_onboarding=False,
    )
    db_session.add_all([regular, onboarding, recent_client])
    await db_session.flush()

    regular_profile = ClientDigitalProfile(
        client_id=regular.id,
        is_onboarding=False,
        commercial_scope={"sensitive": "regular"},
        content_planning_summary={"pages": [{"keyword": "private"}]},
        discovery_status="complete",
        updated_at=old,
    )
    onboarding_profile = ClientDigitalProfile(
        client_id=onboarding.id,
        is_onboarding=True,
        commercial_scope={"sensitive": "onboarding"},
        discovery_status="complete",
        updated_at=old,
    )
    recent_profile = ClientDigitalProfile(
        client_id=recent_client.id,
        is_onboarding=False,
        commercial_scope={"sensitive": "recent"},
        discovery_status="complete",
        updated_at=recent,
    )
    db_session.add_all([regular_profile, onboarding_profile, recent_profile])
    await db_session.flush()

    old_session = ChatSession(
        client_id=regular.id,
        user_id=user.id,
        active_agent_key="content_planning",
        is_onboarding=False,
        started_at=old,
    )
    mixed_session = ChatSession(
        client_id=regular.id,
        user_id=user.id,
        active_agent_key="content_production",
        is_onboarding=False,
        started_at=old,
    )
    onboarding_session = ChatSession(
        client_id=onboarding.id,
        user_id=user.id,
        active_agent_key="discovery_agent",
        is_onboarding=True,
        started_at=old,
    )
    db_session.add_all([old_session, mixed_session, onboarding_session])
    await db_session.flush()

    old_message = ChatMessage(
        session_id=old_session.id,
        role="user",
        content="old sensitive chat",
        structured_payload={"private": True},
        created_at=old,
    )
    mixed_old_message = ChatMessage(
        session_id=mixed_session.id,
        role="assistant",
        content="old part of active thread",
        structured_payload={"private": True},
        created_at=old,
    )
    mixed_recent_message = ChatMessage(
        session_id=mixed_session.id,
        role="user",
        content="recent chat",
        structured_payload={"keep": True},
        created_at=recent,
    )
    onboarding_message = ChatMessage(
        session_id=onboarding_session.id,
        role="user",
        content="onboarding history",
        structured_payload={"keep": True},
        created_at=old,
    )
    old_job = AgentJob(
        session_id=old_session.id,
        agent_key="content_planning",
        job_type="plan",
        status="succeeded",
        result_ref={"private": True},
        error_detail="sensitive error",
        created_at=old,
    )
    onboarding_job = AgentJob(
        session_id=onboarding_session.id,
        agent_key="discovery_agent",
        job_type="discovery",
        status="succeeded",
        result_ref={"keep": True},
        created_at=old,
    )
    db_session.add_all(
        [
            old_message,
            mixed_old_message,
            mixed_recent_message,
            onboarding_message,
            old_job,
            onboarding_job,
        ]
    )
    await db_session.flush()
    ids = {
        "old_message": old_message.id,
        "mixed_old_message": mixed_old_message.id,
        "mixed_recent_message": mixed_recent_message.id,
        "onboarding_message": onboarding_message.id,
        "old_session": old_session.id,
        "mixed_session": mixed_session.id,
        "onboarding_session": onboarding_session.id,
        "old_job": old_job.id,
        "onboarding_job": onboarding_job.id,
        "regular_profile": regular_profile.id,
        "onboarding_profile": onboarding_profile.id,
        "recent_profile": recent_profile.id,
    }
    await db_session.commit()

    result = await archive_expired_client_memory(
        db_session,
        retention_days=30,
        now=now,
    )
    await db_session.commit()
    db_session.expire_all()

    old_message = await db_session.get(ChatMessage, ids["old_message"])
    mixed_old_message = await db_session.get(ChatMessage, ids["mixed_old_message"])
    mixed_recent_message = await db_session.get(
        ChatMessage, ids["mixed_recent_message"]
    )
    onboarding_message = await db_session.get(ChatMessage, ids["onboarding_message"])
    old_session = await db_session.get(ChatSession, ids["old_session"])
    mixed_session = await db_session.get(ChatSession, ids["mixed_session"])
    onboarding_session = await db_session.get(ChatSession, ids["onboarding_session"])
    old_job = await db_session.get(AgentJob, ids["old_job"])
    onboarding_job = await db_session.get(AgentJob, ids["onboarding_job"])
    regular_profile = await db_session.get(
        ClientDigitalProfile, ids["regular_profile"]
    )
    onboarding_profile = await db_session.get(
        ClientDigitalProfile, ids["onboarding_profile"]
    )
    recent_profile = await db_session.get(
        ClientDigitalProfile, ids["recent_profile"]
    )

    assert result["archived"] == {
        "chat_messages": 2,
        "agent_jobs": 1,
        "chat_sessions": 1,
        "client_digital_profiles": 1,
    }
    assert old_message.content == ARCHIVED_MESSAGE_CONTENT
    assert old_message.structured_payload is None
    assert old_message.archived_at is not None
    assert mixed_old_message.content == ARCHIVED_MESSAGE_CONTENT
    assert mixed_recent_message.content == "recent chat"
    assert mixed_recent_message.archived_at is None
    assert old_session.archived_at is not None
    assert old_session.active_agent_key is None
    assert mixed_session.archived_at is None
    assert old_job.result_ref is None
    assert old_job.error_detail is None
    assert old_job.archived_at is not None

    assert regular_profile.commercial_scope == {}
    assert regular_profile.content_planning_summary == {}
    assert regular_profile.discovery_status == "archived"
    assert regular_profile.archived_at is not None
    assert recent_profile.commercial_scope == {"sensitive": "recent"}
    assert recent_profile.archived_at is None

    assert onboarding_message.content == "onboarding history"
    assert onboarding_message.archived_at is None
    assert onboarding_session.archived_at is None
    assert onboarding_job.result_ref == {"keep": True}
    assert onboarding_job.archived_at is None
    assert onboarding_profile.commercial_scope == {"sensitive": "onboarding"}
    assert onboarding_profile.discovery_status == "complete"
    assert onboarding_profile.archived_at is None


def test_client_memory_archive_is_scheduled_daily():
    schedule = celery_app.conf.beat_schedule["archive-expired-client-memory"]
    assert schedule["task"] == "app.tasks.jobs.archive_expired_client_memory"
    assert schedule["schedule"] == 86400.0
