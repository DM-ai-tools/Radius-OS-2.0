"""Client CRUD authorization and deletion safety (AUDIT-005, AUDIT-006).

Before this, every endpoint in clients.py accepted any authenticated role —
including destructive DELETE — with no audit trail, and deleting a client
that had ever incurred a metered API call would raise a foreign-key
violation on Postgres (masked locally by SQLite's default FK enforcement,
which is why it went unnoticed). These pin: identity-mutating endpoints
require CSM/HoD, deletions are logged, and ApiUsageLog rows are cleaned up
before the Client row goes away.
"""

from __future__ import annotations

from sqlalchemy import select

from app.api import clients as clients_module
from app.models import ApiUsageLog, Client, ClientDigitalProfile
from tests.conftest import make_user


async def _make_client(db_session, name: str = "Test Co") -> str:
    client = Client(
        legal_name=name,
        display_name=name,
        primary_url="https://example.com",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.commit()
    return str(client.id)


async def test_create_client_requires_csm_or_hod_role(api_client, db_session):
    _, token = await make_user(db_session, "content_seo_specialist", "content@example.com")

    resp = await api_client.post(
        "/api/v1/clients",
        json={"name": "New Co", "primary_url": "https://new.example", "industry": "Retail"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_create_client_allowed_for_csm(api_client, db_session):
    _, token = await make_user(db_session, "client_success_manager", "csm@example.com")

    resp = await api_client.post(
        "/api/v1/clients",
        json={"name": "New Co", "primary_url": "https://new.example", "industry": "Retail"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


async def test_update_client_requires_csm_or_hod_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "on_page_seo_specialist", "onpage@example.com")

    resp = await api_client.patch(
        f"/api/v1/clients/{client_id}",
        json={"industry": "Finance"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_delete_client_requires_csm_or_hod_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "seo_qa_lead", "qa@example.com")

    resp = await api_client.delete(
        f"/api/v1/clients/{client_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    still_there = (
        await db_session.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    assert still_there is not None


async def test_delete_client_allowed_for_head_of_department(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "head_of_department", "hod@example.com")

    resp = await api_client.delete(
        f"/api/v1/clients/{client_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


async def test_delete_client_logs_the_deletion(api_client, db_session, monkeypatch):
    client_id = await _make_client(db_session, "Audit Me Co")
    _, token = await make_user(db_session, "head_of_department", "hod-log@example.com")

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        clients_module.log, "warning", lambda event, **kw: events.append((event, kw))
    )

    resp = await api_client.delete(
        f"/api/v1/clients/{client_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    assert any(
        event == "client_deleted" and kw.get("client_id") == client_id
        for event, kw in events
    )


async def test_delete_client_cleans_up_api_usage_log_rows(api_client, db_session):
    """On Postgres this used to raise a foreign-key violation instead of a
    clean 204 — SQLite doesn't enforce the FK by default, so the meaningful
    assertion here is that the cleanup actually ran, not that no exception
    was raised."""
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "head_of_department", "hod-usage@example.com")

    db_session.add(
        ApiUsageLog(client_id=client_id, provider="anthropic", operation="chat_turn")
    )
    await db_session.commit()

    resp = await api_client.delete(
        f"/api/v1/clients/{client_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204

    remaining_logs = (
        await db_session.execute(
            select(ApiUsageLog).where(ApiUsageLog.client_id == client_id)
        )
    ).scalars().all()
    assert remaining_logs == []

    remaining_client = (
        await db_session.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    assert remaining_client is None
