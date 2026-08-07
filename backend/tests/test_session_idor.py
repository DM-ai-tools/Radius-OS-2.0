from app.models import Client, ClientDigitalProfile
from tests.conftest import make_user


async def _make_client(db_session) -> str:
    client = Client(
        legal_name="Test Co",
        display_name="Test Co",
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


async def test_user_cannot_read_another_users_session_messages(api_client, db_session):
    client_id = await _make_client(db_session)
    _, owner_token = await make_user(db_session, "client_success_manager", "owner@example.com")
    _, other_token = await make_user(db_session, "client_success_manager", "other@example.com")

    create_resp = await api_client.post(
        "/api/v1/sessions",
        json={"client_id": client_id},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["id"]

    other_resp = await api_client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert other_resp.status_code == 404

    owner_resp = await api_client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert owner_resp.status_code == 200


async def test_user_cannot_post_message_into_another_users_session(api_client, db_session):
    client_id = await _make_client(db_session)
    _, owner_token = await make_user(db_session, "client_success_manager", "owner2@example.com")
    _, other_token = await make_user(db_session, "client_success_manager", "other2@example.com")

    create_resp = await api_client.post(
        "/api/v1/sessions",
        json={"client_id": client_id},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    session_id = create_resp.json()["id"]

    resp = await api_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "hello"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404
