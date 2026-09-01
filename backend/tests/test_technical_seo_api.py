"""Tests for Phase 7 technical SEO API routes."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Client, ClientDigitalProfile
from tests.conftest import make_user


async def _make_client(db_session) -> str:
    client = Client(
        legal_name="Tech SEO Co",
        display_name="Tech SEO Co",
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


async def test_technical_seo_issue_urls_pagination(api_client, db_session):
    client_id = await _make_client(db_session)
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one()
    profile.technical_seo_summary = {
        "issues": [
            {
                "rule_id": "broken_canonical",
                "title": "Broken canonical",
                "affected_urls": [f"https://example.com/p{i}" for i in range(5)],
            }
        ]
    }
    await db_session.commit()

    _, token = await make_user(db_session, "technical_seo_specialist", "techseo@example.com")
    resp = await api_client.get(
        f"/api/v1/clients/{client_id}/technical-seo/issues/broken_canonical/urls",
        params={"offset": 1, "limit": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["urls"]) == 2
    assert body["urls"][0] == "https://example.com/p1"
