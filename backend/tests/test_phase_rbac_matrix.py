from __future__ import annotations

import uuid

from sqlalchemy import delete

from app.models import (
    ChatSession,
    Client,
    ClientDigitalProfile,
    FindingsLedger,
    Role,
    RolePermission,
    User,
)
from app.security import create_access_token, hash_password
from app.services.role_skills import PHASE_AGENTS
from tests.conftest import make_user

AGENT_PHASES = tuple(key for key in PHASE_AGENTS if key != "readiness_gate")


async def _client(db_session) -> Client:
    client = Client(
        legal_name="RBAC Matrix Co",
        display_name="RBAC Matrix Co",
        primary_url="https://rbac.example",
        industry="Testing",
        tier="B",
        status="active",
        is_onboarding=False,
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(
        ClientDigitalProfile(client_id=client.id, is_onboarding=False)
    )
    await db_session.commit()
    return client


async def _custom_user(
    db_session,
    *,
    role_name: str,
    email: str,
    permissions: list[tuple[str, bool, bool]],
) -> tuple[User, str]:
    role = Role(name=role_name, description="RBAC matrix role")
    db_session.add(role)
    await db_session.flush()
    for agent_key, can_trigger, can_approve in permissions:
        db_session.add(
            RolePermission(
                role_id=role.id,
                agent_key=agent_key,
                can_trigger=can_trigger,
                can_approve=can_approve,
            )
        )
    user = User(
        email=email,
        full_name=role_name,
        role_id=role.id,
        hashed_password=hash_password("testpass123"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user, create_access_token(str(user.id), extra={"role": role_name})


async def _matrix_users(db_session):
    hod, hod_token = await make_user(
        db_session,
        "head_of_department",
        "rbac-hod@example.com",
    )
    # Prove the intentional HoD bypass does not depend on seeded permission rows.
    await db_session.execute(
        delete(RolePermission).where(RolePermission.role_id == hod.role_id)
    )
    trigger_user, trigger_token = await _custom_user(
        db_session,
        role_name="rbac_trigger_only",
        email="rbac-trigger@example.com",
        permissions=[(key, True, False) for key in PHASE_AGENTS],
    )
    blocked_user, blocked_token = await _custom_user(
        db_session,
        role_name="rbac_no_permissions",
        email="rbac-blocked@example.com",
        permissions=[],
    )
    await db_session.commit()
    return (
        (hod, hod_token),
        (trigger_user, trigger_token),
        (blocked_user, blocked_token),
    )


async def _session(db_session, client: Client, user: User) -> ChatSession:
    session = ChatSession(
        client_id=client.id,
        user_id=user.id,
        is_onboarding=False,
    )
    db_session.add(session)
    await db_session.commit()
    return session


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_every_phase_chat_trigger_enforces_same_permission_matrix(
    api_client,
    db_session,
    monkeypatch,
):
    from app.orchestration import pipeline as pipeline_module
    from app.services import chat_qa, chat_revisions, phase_validation

    client = await _client(db_session)
    (hod, hod_token), (trigger_user, trigger_token), (blocked_user, blocked_token) = (
        await _matrix_users(db_session)
    )
    hod_session = await _session(db_session, client, hod)
    trigger_session = await _session(db_session, client, trigger_user)
    blocked_session = await _session(db_session, client, blocked_user)
    selected = {"agent_key": AGENT_PHASES[0]}

    async def fake_route(_content, _statuses):
        return selected["agent_key"]

    async def no_revision(*_args, **_kwargs):
        return None

    async def no_qa(*_args, **_kwargs):
        return None

    async def fake_validated_run(*_args, **kwargs):
        return [
            {
                "type": "agent_message",
                "agent_key": kwargs["agent_key"],
                "content": "phase started",
            }
        ]

    monkeypatch.setattr(pipeline_module, "route_agent", fake_route)
    monkeypatch.setattr(chat_revisions, "maybe_revise_from_chat", no_revision)
    monkeypatch.setattr(chat_qa, "maybe_answer_from_memory", no_qa)
    monkeypatch.setattr(phase_validation, "run_phase_with_validation", fake_validated_run)

    for agent_key in AGENT_PHASES:
        selected["agent_key"] = agent_key
        for session, token, expected in (
            (hod_session, hod_token, 200),
            (trigger_session, trigger_token, 200),
            (blocked_session, blocked_token, 403),
        ):
            response = await api_client.post(
                f"/api/v1/sessions/{session.id}/messages",
                json={"content": f"run {agent_key}"},
                headers=_auth(token),
            )
            assert response.status_code == expected, (
                agent_key,
                response.status_code,
                response.text,
            )


async def test_every_phase_rest_and_chat_approval_enforce_same_matrix(
    api_client,
    db_session,
):
    client = await _client(db_session)
    (hod, hod_token), (trigger_user, trigger_token), (blocked_user, blocked_token) = (
        await _matrix_users(db_session)
    )
    hod_session = await _session(db_session, client, hod)
    trigger_session = await _session(db_session, client, trigger_user)
    blocked_session = await _session(db_session, client, blocked_user)

    confirmed = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={"confirmed": True},
        headers=_auth(trigger_token),
    )
    assert confirmed.status_code == 403
    draft = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={"confirmed": False},
        headers=_auth(trigger_token),
    )
    assert draft.status_code == 200

    for agent_key in AGENT_PHASES:
        # Dedicated REST phase-review path.
        for token in (trigger_token, blocked_token):
            response = await api_client.post(
                f"/api/v1/clients/{client.id}/phases/{agent_key}/review",
                json={"action": "approve"},
                headers=_auth(token),
            )
            assert response.status_code == 403, (agent_key, response.text)

        rest_finding = FindingsLedger(
            client_id=client.id,
            agent_key=agent_key,
            source_table="rbac_test",
            source_id=uuid.uuid4(),
            confidence="high",
            status="pending",
        )
        db_session.add(rest_finding)
        await db_session.commit()
        response = await api_client.post(
            f"/api/v1/clients/{client.id}/phases/{agent_key}/review",
            json={"action": "approve"},
            headers=_auth(hod_token),
        )
        assert response.status_code == 200, (agent_key, response.text)

        # The same approval expressed through the conversational endpoint.
        for session, token in (
            (trigger_session, trigger_token),
            (blocked_session, blocked_token),
        ):
            session.active_agent_key = agent_key
            await db_session.commit()
            response = await api_client.post(
                f"/api/v1/sessions/{session.id}/messages",
                json={"content": "approve"},
                headers=_auth(token),
            )
            assert response.status_code == 403, (agent_key, response.text)

        chat_finding = FindingsLedger(
            client_id=client.id,
            agent_key=agent_key,
            source_table="rbac_test",
            source_id=uuid.uuid4(),
            confidence="high",
            status="pending",
        )
        db_session.add(chat_finding)
        hod_session.active_agent_key = agent_key
        await db_session.commit()
        response = await api_client.post(
            f"/api/v1/sessions/{hod_session.id}/messages",
            json={"content": "approve"},
            headers=_auth(hod_token),
        )
        assert response.status_code == 200, (agent_key, response.text)


async def test_permissionless_user_can_still_ask_chat_questions(
    api_client,
    db_session,
    monkeypatch,
):
    from app.services import chat_qa

    client = await _client(db_session)
    user, token = await _custom_user(
        db_session,
        role_name="rbac_questions_only",
        email="rbac-questions@example.com",
        permissions=[],
    )
    session = await _session(db_session, client, user)

    async def answer_question(**_kwargs):
        return [{"type": "agent_message", "content": "Read-only answer"}]

    monkeypatch.setattr(chat_qa, "maybe_answer_from_memory", answer_question)
    response = await api_client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"content": "What is our current SEO status?"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.json()["events"][0]["content"] == "Read-only answer"


async def test_all_other_phase_mutation_endpoints_reject_permissionless_user(
    api_client,
    db_session,
):
    client = await _client(db_session)
    _user, token = await _custom_user(
        db_session,
        role_name="rbac_endpoint_blocked",
        email="rbac-endpoints@example.com",
        permissions=[],
    )
    headers = _auth(token)
    cases = (
        (
            "post",
            f"/api/v1/clients/{client.id}/content-production/site-preview",
            {"json": {"title": "Draft", "markdown": "Private draft"}},
        ),
        (
            "post",
            f"/api/v1/clients/{client.id}/discovery/import-document",
            {"files": {"file": ("cdd.txt", b"Business: Test", "text/plain")}},
        ),
        (
            "get",
            f"/api/v1/clients/{client.id}/service-prioritization",
            {},
        ),
        (
            "put",
            f"/api/v1/clients/{client.id}/service-prioritization",
            {"json": {}},
        ),
        (
            "post",
            "/api/v1/oauth/authorize-url",
            {"json": {"client_id": str(client.id), "provider": "ga4"}},
        ),
        (
            "post",
            "/api/v1/oauth/mock-grant",
            {
                "json": {
                    "client_id": str(client.id),
                    "provider": "ga4",
                    "scope": "read",
                }
            },
        ),
        ("get", f"/api/v1/clients/{client.id}/readiness", {}),
        (
            "post",
            f"/api/v1/clients/{client.id}/readiness/gate",
            {"json": {"approve": True}},
        ),
    )
    for method, path, kwargs in cases:
        response = await api_client.request(
            method,
            path,
            headers=headers,
            **kwargs,
        )
        assert response.status_code == 403, (method, path, response.text)
