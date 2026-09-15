"""AUDIT-023: list_clients / list_findings / list_sessions had no pagination
bound and would return every matching row. These pin that a limit is now
respected — sessions.list_messages already did this correctly; these three
endpoints now match that established pattern."""

from __future__ import annotations

from app.models import Client, ClientDigitalProfile, FindingsLedger
from tests.conftest import make_user


async def _make_client(db_session, name: str) -> str:
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


async def test_list_clients_respects_limit(api_client, db_session):
    for i in range(5):
        await _make_client(db_session, f"Client {i}")
    _, token = await make_user(db_session, "client_success_manager", "csm-page1@example.com")

    resp = await api_client.get(
        "/api/v1/clients?limit=2", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_list_clients_rejects_limit_above_cap(api_client, db_session):
    _, token = await make_user(db_session, "client_success_manager", "csm-page2@example.com")
    resp = await api_client.get(
        "/api/v1/clients?limit=100000", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422


async def test_list_findings_respects_limit(api_client, db_session):
    client_id = await _make_client(db_session, "Findings Co")
    for i in range(5):
        db_session.add(
            FindingsLedger(
                client_id=client_id,
                agent_key="discovery_agent",
                source_table="discovery_responses",
                source_id=client_id,
                confidence="high",
                status="pending",
            )
        )
    await db_session.commit()
    _, token = await make_user(db_session, "client_success_manager", "csm-page3@example.com")

    resp = await api_client.get(
        f"/api/v1/clients/{client_id}/findings?limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_list_sessions_respects_limit(api_client, db_session):
    client_id = await _make_client(db_session, "Sessions Co")
    _, token = await make_user(db_session, "client_success_manager", "csm-page4@example.com")

    for _ in range(5):
        r = await api_client.post(
            "/api/v1/sessions",
            json={"client_id": client_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    resp = await api_client.get(
        "/api/v1/sessions?limit=2", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2
