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


async def test_confirm_persists_service_scope_and_derives_flat_ids(db_session, api_client):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "head_of_department", "scope-hod@test.com")

    resp = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={
            "selected_service_ids": ["social media marketing"],
            "selected_subservice_ids": ["meta ads", "tiktok-ads"],
            "service_catalog": [
                {
                    "id": "social media marketing",
                    "name": "Social Media Marketing",
                    "subservices": [
                        {"id": "meta ads", "name": "Meta Ads"},
                        {"id": "tiktok-ads", "name": "TikTok Ads"},
                    ],
                }
            ],
            "service_scope": {
                "social media marketing": {
                    "name": "Social Media Marketing",
                    "offered": [{"id": "meta ads", "name": "Meta Ads"}],
                    "excluded": [{"id": "tiktok-ads", "name": "TikTok Ads"}],
                }
            },
            "confirmed": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["service_prioritization"]
    assert saved["selected_subservice_ids"] == ["meta ads"]
    entry = saved["service_scope"]["social media marketing"]
    assert entry["decided_at"]
    assert [e["name"] for e in entry["excluded"]] == ["TikTok Ads"]

    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    await db_session.refresh(profile)
    stored = profile.commercial_scope["service_prioritization"]
    assert stored["service_scope"]["social media marketing"]["offered"][0]["name"] == "Meta Ads"


async def test_confirm_without_scope_still_works_and_stays_unscoped(db_session, api_client):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "head_of_department", "scope-hod2@test.com")
    resp = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={"selected_service_ids": ["seo"], "confirmed": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["service_prioritization"]["service_scope"] == {}


async def test_get_pack_includes_the_industry_services_the_model_suggests(db_session, api_client):
    from unittest.mock import AsyncMock, patch

    client = await _client(db_session)  # industry: Plumbing
    _user, token = await make_user(db_session, "head_of_department", "industry-hod@test.com")
    reply = {
        "services": [
            {"name": "Drain Cleaning", "subservices": ["Hydro Jetting", "CCTV Drain Inspection"]},
            {"name": "Hot Water Systems", "subservices": ["Heat Pump Installation", "Tankless Installation"]},
        ]
    }
    llm = AsyncMock(return_value=reply)
    with (
        patch("app.services.industry_services.synthesize_json", new=llm),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        resp = await api_client.get(
            f"/api/v1/clients/{client.id}/service-prioritization",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    pack = resp.json()
    by_name = {s["name"]: s for s in pack["service_catalog"]}
    assert {"Drain Cleaning", "Hot Water Systems"} <= set(by_name)
    drain = by_name["Drain Cleaning"]
    assert drain["source"] == "industry" and drain["candidate_new_service"] is True
    assert [s["name"] for s in drain["subservices"]] == ["Hydro Jetting", "CCTV Drain Inspection"]
    assert pack["industry_service_count"] == 2
    assert pack["industry_taxonomy_status"] == "ok"
    assert "drain cleaning" not in pack["defaults"]["selected_service_ids"]
    # the model was told the client's industry
    assert "Plumbing" in llm.await_args.args[1]


async def test_get_pack_still_works_when_the_model_is_unavailable(db_session, api_client):
    from unittest.mock import AsyncMock, patch

    client = await _client(db_session)
    _user, token = await make_user(db_session, "head_of_department", "industry-hod2@test.com")
    with (
        patch("app.services.industry_services.synthesize_json", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        resp = await api_client.get(
            f"/api/v1/clients/{client.id}/service-prioritization",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["industry_service_count"] == 0
    assert resp.json()["industry_taxonomy_status"] == "failed"
