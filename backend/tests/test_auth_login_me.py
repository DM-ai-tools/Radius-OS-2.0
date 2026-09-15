"""AUDIT-017: /auth/login and /auth/me had zero test coverage — no success
case, no wrong-password case, no inactive-user case — despite being the
highest-risk surface in the app (this is exactly the endpoint AUDIT-001's
access-control fix lives behind)."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Role, User
from app.security import hash_password


async def _make_active_user(db_session, *, email: str, password: str, role_name: str) -> User:
    role = (await db_session.execute(select(Role).where(Role.name == role_name))).scalar_one()
    user = User(
        email=email,
        full_name="Test User",
        role_id=role.id,
        hashed_password=hash_password(password),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    return user


async def test_login_succeeds_with_correct_credentials(api_client, db_session):
    await _make_active_user(
        db_session, email="login-ok@example.com", password="correct-horse-1", role_name="seo_qa_lead"
    )
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "login-ok@example.com", "password": "correct-horse-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


async def test_login_fails_with_wrong_password(api_client, db_session):
    await _make_active_user(
        db_session, email="login-bad@example.com", password="correct-horse-1", role_name="seo_qa_lead"
    )
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "login-bad@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_login_fails_for_nonexistent_email(api_client):
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever123"},
    )
    assert resp.status_code == 401


async def test_login_fails_for_inactive_user(api_client, db_session):
    role = (
        await db_session.execute(select(Role).where(Role.name == "seo_qa_lead"))
    ).scalar_one()
    user = User(
        email="inactive@example.com",
        full_name="Inactive User",
        role_id=role.id,
        hashed_password=hash_password("correct-horse-1"),
        is_active=False,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "correct-horse-1"},
    )
    # login() itself only checks password, not is_active — get_current_user's
    # is_active gate is what actually keeps a deactivated account out. Either
    # a 401 here or a token that /me then rejects is an acceptable contract;
    # what would NOT be acceptable is issuing a token /me also accepts.
    if resp.status_code == 200:
        token = resp.json()["access_token"]
        me = await api_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 401
    else:
        assert resp.status_code == 401


async def test_me_returns_current_user_profile(api_client, db_session):
    await _make_active_user(
        db_session, email="me-ok@example.com", password="correct-horse-1", role_name="client_success_manager"
    )
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "me-ok@example.com", "password": "correct-horse-1"},
    )
    token = login.json()["access_token"]

    me = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "me-ok@example.com"
    assert body["role_name"] == "client_success_manager"
    assert "hashed_password" not in body


async def test_me_requires_authentication(api_client):
    resp = await api_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_rejects_garbage_token(api_client):
    resp = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401
