import asyncio

from sqlalchemy import select

import app.orchestration.pipeline as pipeline_module
from app.models import ChatSession, Client, ClientDigitalProfile, Role, User
from app.security import hash_password


async def test_pipeline_times_out_instead_of_hanging(db_session, monkeypatch):
    client = Client(
        legal_name="Slow Co",
        display_name="Slow Co",
        primary_url="https://example.com",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))

    role = (
        await db_session.execute(select(Role).where(Role.name == "client_success_manager"))
    ).scalar_one()
    user = User(
        email="slow-test@example.com",
        full_name="Slow Tester",
        role_id=role.id,
        hashed_password=hash_password("testpass123"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    session = ChatSession(
        client_id=client.id, user_id=user.id, active_agent_key="discovery_agent"
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    async def _never_finishes(db, *, client, session_id, user_id, message):
        await asyncio.sleep(9999)
        return []

    monkeypatch.setitem(pipeline_module.AGENT_RUNNERS, "discovery_agent", _never_finishes)
    monkeypatch.setattr(pipeline_module, "AGENT_TIMEOUT_SECONDS", 0.2)

    async def _fake_route(content, statuses):
        return "discovery_agent"

    monkeypatch.setattr(pipeline_module, "route_agent", _fake_route)

    events = await pipeline_module.process_chat_turn(
        db_session, session=session, user=user, content="run discovery"
    )
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "discovery_agent" in events[0]["content"]
