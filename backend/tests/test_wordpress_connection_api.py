"""Per-client WordPress connection endpoints.

WordPress is connected from the app per client (username + Application Password),
not from a deployment-wide .env — this is a multi-client tool and every client has
their own site. These tests pin: credentials are tested before being trusted, stored
encrypted, never echoed back, and one client's connection cannot leak into another's.
"""

from __future__ import annotations

from sqlalchemy import select

from app.integrations import wordpress
from app.models import ApiCredential, Client, ClientDigitalProfile
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


def _mock_verify_ok(monkeypatch, *, user="editor", can_publish=True):
    async def _verify(conn):
        return {"ok": True, "user": user, "capabilities_publish": can_publish, "site": conn.base_url}

    # api/integrations.py calls wordpress.verify_connection(...) module-qualified, so
    # patching the shared module here is enough — no separate patch needed per import site.
    monkeypatch.setattr(wordpress, "verify_connection", _verify)


def _mock_verify_fail(monkeypatch, error="unauthorized"):
    async def _verify(_conn):
        return {"ok": False, "error": error}

    monkeypatch.setattr(wordpress, "verify_connection", _verify)


async def test_connect_tests_credentials_before_storing(api_client, db_session, monkeypatch):
    """A bad Application Password must fail at connect time, not silently at publish time."""
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "seo_strategist", "strat@example.com")
    _mock_verify_fail(monkeypatch, error="unauthorized")

    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        json={
            "base_url": "https://client-site.example",
            "username": "editor",
            "app_password": "wrong-password",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "unauthorized" in resp.json()["detail"]

    stored = (
        await db_session.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id, ApiCredential.provider == "wordpress"
            )
        )
    ).scalars().all()
    assert stored == []


async def test_connect_stores_encrypted_and_status_never_echoes_password(
    api_client, db_session, monkeypatch
):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "seo_strategist", "strat2@example.com")
    _mock_verify_ok(monkeypatch, user="Editor Jane")

    connect = await api_client.post(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        json={
            "base_url": "https://client-site.example",
            "username": "editor",
            "app_password": "correct-app-password-1234",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect.status_code == 200
    body = connect.json()
    assert body["connected"] is True
    assert body["wp_user"] == "Editor Jane"
    assert "app_password" not in body
    assert "correct-app-password-1234" not in str(body)

    # Stored row holds only an encrypted blob, never the raw password.
    row = (
        await db_session.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id, ApiCredential.provider == "wordpress"
            )
        )
    ).scalar_one_or_none()
    assert row is not None
    assert b"correct-app-password-1234" not in row.encrypted_token

    status = await api_client.get(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status.status_code == 200
    sbody = status.json()
    assert sbody["connected"] is True
    assert sbody["username"] == "editor"
    assert "app_password" not in sbody
    assert "correct-app-password-1234" not in str(sbody)


async def test_reconnect_revokes_the_prior_credential(api_client, db_session, monkeypatch):
    """Re-running connect must not leave two active credentials for one client."""
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "seo_strategist", "strat3@example.com")
    _mock_verify_ok(monkeypatch)

    for _ in range(2):
        resp = await api_client.post(
            f"/api/v1/clients/{client_id}/integrations/wordpress",
            json={
                "base_url": "https://client-site.example",
                "username": "editor",
                "app_password": "pw",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    active = (
        await db_session.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == "wordpress",
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    assert len(active) == 1


async def test_disconnect_revokes_credential(api_client, db_session, monkeypatch):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "seo_strategist", "strat4@example.com")
    _mock_verify_ok(monkeypatch)

    await api_client.post(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        json={"base_url": "https://x.example", "username": "e", "app_password": "p"},
        headers={"Authorization": f"Bearer {token}"},
    )
    disconnect = await api_client.delete(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert disconnect.status_code == 200
    assert disconnect.json()["connected"] is False

    status = await api_client.get(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status.json()["connected"] is False


async def test_status_reports_not_connected_before_any_connect(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "seo_strategist", "strat5@example.com")

    status = await api_client.get(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status.status_code == 200
    assert status.json() == {"connected": False}


async def test_role_without_publish_trigger_cannot_connect(api_client, db_session, monkeypatch):
    """Connecting WordPress grants publish capability, so it needs publish-trigger rights."""
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "structured_data_specialist", "sds@example.com")
    _mock_verify_ok(monkeypatch)

    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/integrations/wordpress",
        json={"base_url": "https://x.example", "username": "e", "app_password": "p"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_two_clients_have_independent_connections(api_client, db_session, monkeypatch):
    client_a = await _make_client(db_session, "Client A")
    client_b = await _make_client(db_session, "Client B")
    _, token = await make_user(db_session, "seo_strategist", "strat6@example.com")
    _mock_verify_ok(monkeypatch)

    await api_client.post(
        f"/api/v1/clients/{client_a}/integrations/wordpress",
        json={"base_url": "https://a-site.example", "username": "a", "app_password": "pa"},
        headers={"Authorization": f"Bearer {token}"},
    )

    status_a = await api_client.get(
        f"/api/v1/clients/{client_a}/integrations/wordpress",
        headers={"Authorization": f"Bearer {token}"},
    )
    status_b = await api_client.get(
        f"/api/v1/clients/{client_b}/integrations/wordpress",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status_a.json()["connected"] is True
    assert status_b.json() == {"connected": False}
