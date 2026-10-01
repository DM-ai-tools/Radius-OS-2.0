"""The selection gate persists the #1 service and the research depth."""

from sqlalchemy import select

from app.models import Client, ClientDigitalProfile
from tests.conftest import make_user


async def _client(db_session) -> Client:
    client = Client(
        legal_name="Depth Co",
        display_name="Depth Co",
        primary_url="https://depth.example",
        industry="Plumbing",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()
    await db_session.commit()
    return client


async def test_confirm_persists_top_priority_service_and_pool_target(
    db_session, api_client
):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "head_of_department", "depth-hod@test.com")

    resp = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={
            "selected_service_ids": ["drain-cleaning", "hydro-jetting"],
            "selected_subservice_ids": [],
            "primary_service_ids": [],
            "top_priority_service_id": "drain-cleaning",
            "keyword_pool_target": 8000,
            "confirmed": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["service_prioritization"]["top_priority_service_id"] == (
        "drain-cleaning"
    )

    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(
                ClientDigitalProfile.client_id == client.id
            )
        )
    ).scalar_one()
    await db_session.refresh(profile)
    commercial = profile.commercial_scope or {}
    assert commercial["keyword_pool_target"] == 8000
    assert commercial["service_prioritization"]["top_priority_service_id"] == (
        "drain-cleaning"
    )


async def test_unselected_top_priority_is_rejected_by_the_endpoint(
    db_session, api_client
):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "head_of_department", "depth-hod2@test.com")

    resp = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={
            "selected_service_ids": ["hydro-jetting"],
            "selected_subservice_ids": [],
            "primary_service_ids": [],
            "top_priority_service_id": "drain-cleaning",
            "confirmed": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["service_prioritization"]["top_priority_service_id"] is None
