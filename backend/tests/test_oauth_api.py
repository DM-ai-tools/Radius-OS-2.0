"""AUDIT-017: the entire OAuth authorize/callback/token-storage flow had zero
test coverage. Also pins AUDIT-039: the state token minted for the Google
consent round trip must be short-lived, not inherit the full session-token
lifetime, since it's a single-purpose CSRF token, not a credential."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.api import oauth as oauth_module
from app.models import ApiCredential, Client, ClientDigitalProfile
from app.security import decode_access_token
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


async def test_oauth_config_reports_not_configured(api_client, db_session, monkeypatch):
    monkeypatch.setattr(oauth_module, "oauth_configured", lambda: False)
    _, token = await make_user(db_session, "client_success_manager", "csm-cfg@example.com")
    resp = await api_client.get(
        "/api/v1/oauth/config", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["configured"] is False


async def test_authorize_url_returns_mock_mode_when_not_configured(
    api_client, db_session, monkeypatch
):
    monkeypatch.setattr(oauth_module, "oauth_configured", lambda: False)
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-auth1@example.com")

    resp = await api_client.post(
        "/api/v1/oauth/authorize-url",
        json={"client_id": client_id, "provider": "ga4"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "mock"
    assert body["url"] is None


async def test_authorize_url_rejects_unknown_provider(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-auth2@example.com")

    resp = await api_client.post(
        "/api/v1/oauth/authorize-url",
        json={"client_id": client_id, "provider": "not-a-real-provider"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_authorize_url_state_token_is_short_lived_not_session_length(
    api_client, db_session, monkeypatch
):
    """AUDIT-039: this used to reuse create_access_token's full session
    expiry (settings.jwt_expire_minutes, default 480 min) for a token whose
    only job is surviving one Google consent redirect."""
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-auth3@example.com")

    monkeypatch.setattr(oauth_module, "oauth_configured", lambda: True)
    monkeypatch.setattr(
        oauth_module, "build_authorize_url", lambda *, state, provider: f"https://google.example?state={state}"
    )

    resp = await api_client.post(
        "/api/v1/oauth/authorize-url",
        json={"client_id": client_id, "provider": "ga4"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "google"
    state = body["url"].split("state=", 1)[1]

    payload = decode_access_token(state)
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    minutes_left = (exp - datetime.now(timezone.utc)).total_seconds() / 60
    assert 0 < minutes_left <= 11, "OAuth state token must not inherit the full session lifetime"


async def test_mock_grant_stores_credential(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-grant@example.com")

    resp = await api_client.post(
        "/api/v1/oauth/mock-grant",
        json={"client_id": client_id, "provider": "search_console"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["mode"] == "mock"

    stored = (
        await db_session.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == "search_console",
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    assert stored is not None


async def test_mock_grant_rejects_unknown_provider(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-grant2@example.com")

    resp = await api_client.post(
        "/api/v1/oauth/mock-grant",
        json={"client_id": client_id, "provider": "not-a-real-provider"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_mock_grant_revokes_prior_grant_for_same_provider(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-grant3@example.com")

    for _ in range(2):
        resp = await api_client.post(
            "/api/v1/oauth/mock-grant",
            json={"client_id": client_id, "provider": "gtm"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    active = (
        await db_session.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == "gtm",
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    assert len(active) == 1


async def test_list_credentials_excludes_revoked(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-list@example.com")

    await api_client.post(
        "/api/v1/oauth/mock-grant",
        json={"client_id": client_id, "provider": "ga4"},
        headers={"Authorization": f"Bearer {token}"},
    )
    listed = await api_client.get(
        f"/api/v1/oauth/clients/{client_id}/credentials",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["provider"] == "ga4"
    assert "encrypted_token" not in rows[0]


async def test_oauth_callback_rejects_missing_code_or_state(api_client):
    resp = await api_client.get("/api/v1/oauth/callback", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "oauth=error" in resp.headers["location"]


async def test_oauth_callback_rejects_invalid_state(api_client):
    resp = await api_client.get(
        "/api/v1/oauth/callback?code=abc&state=garbage-not-a-jwt",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth=error" in resp.headers["location"]


async def test_oauth_callback_success_path_stores_credential(api_client, db_session, monkeypatch):
    client_id = await _make_client(db_session)
    user, _ = await make_user(db_session, "client_success_manager", "csm-cb@example.com")

    from app.security import create_access_token

    state = create_access_token(
        subject=str(user.id),
        extra={
            "oauth": True,
            "client_id": client_id,
            "provider": "ga4",
            "uid": str(user.id),
        },
        expires_minutes=10,
    )

    async def _fake_exchange(code):
        return {"access_token": "fake-google-token", "scope": "analytics.readonly"}

    from app.api import oauth as oauth_module

    monkeypatch.setattr(oauth_module, "exchange_code", _fake_exchange)

    resp = await api_client.get(
        f"/api/v1/oauth/callback?code=real-code&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth=ok" in resp.headers["location"]

    stored = (
        await db_session.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == "ga4",
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    assert stored is not None
