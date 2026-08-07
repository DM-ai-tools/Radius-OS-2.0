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


async def test_questionnaire_forbidden_for_view_only_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "content_seo_specialist", "viewer@example.com")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/questionnaire",
        json={"fields": {"business_model": "b2b"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_questionnaire_allowed_for_csm(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm2@example.com")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/questionnaire",
        json={"fields": {"business_model": "b2b"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


async def test_known_changes_forbidden_for_view_only_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "content_seo_specialist", "viewer2@example.com")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/tracking/known-changes",
        json={"fields": {"note": "moved to GA4"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_manual_competitor_forbidden_for_view_only_role(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "content_seo_specialist", "viewer3@example.com")
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/competitors/manual",
        json={"name": "Rival Co", "url": "https://rival.example"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
