"""The CDD upload endpoint: versioning, rejection, staleness in the response."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import CddVersion, Client, ClientDigitalProfile
from tests.conftest import make_user


async def _client(db_session) -> Client:
    client = Client(
        legal_name="Upload Co",
        display_name="Upload Co",
        primary_url="https://upload.example",
        industry="Dental",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()
    await db_session.commit()
    return client


def _upload(api_client, token, client_id, *, name="cdd.xlsx", content=b"spreadsheet bytes"):
    return api_client.post(
        f"/api/v1/clients/{client_id}/discovery/import-document",
        files={"file": (name, content, "application/vnd.ms-excel")},
        headers={"Authorization": f"Bearer {token}"},
    )


def _patches(fields):
    return (
        patch("app.services.intake_ai.extract_text_from_upload", return_value="some text"),
        patch(
            "app.services.intake_ai.extract_cdd_fields_from_text",
            AsyncMock(return_value=fields),
        ),
    )


async def test_document_stamp_does_not_rename_the_client(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-stamp@test.com")

    text_patch, fields_patch = _patches(
        {
            "business_name": "21/09/2026 (draft)",
            "business_keywords": "seo",
            "website_url": "https://clicktrends.com.au",
        }
    )
    with text_patch, fields_patch:
        resp = await _upload(api_client, token, client.id)
    assert resp.status_code == 200, resp.text

    await db_session.refresh(client)
    assert client.display_name == "Upload Co"
    assert client.legal_name == "Upload Co"
    assert "clicktrends.com.au" in client.primary_url


async def test_upload_creates_a_version_and_returns_the_diff(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up1@test.com")

    text_patch, fields_patch = _patches({"business_keywords": "seo, ppc"})
    with text_patch, fields_patch:
        resp = await _upload(api_client, token, client.id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cdd_version"]["version_no"] == 1
    assert "diff" in body and "staleness" in body

    rows = (
        await db_session.execute(select(CddVersion).where(CddVersion.client_id == client.id))
    ).scalars().all()
    assert len(rows) == 1


async def test_second_upload_increments_the_version(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up2@test.com")

    t1, f1 = _patches({"business_keywords": "seo"})
    with t1, f1:
        await _upload(api_client, token, client.id)
    t2, f2 = _patches({"business_keywords": "seo, ppc"})
    with t2, f2:
        resp = await _upload(api_client, token, client.id)
    assert resp.json()["cdd_version"]["version_no"] == 2
    assert "business_keywords" in resp.json()["diff"]["changed_keys"]


async def test_identical_reupload_does_not_create_a_second_version(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up3@test.com")

    for _ in range(2):
        t, f = _patches({"business_keywords": "seo"})
        with t, f:
            resp = await _upload(api_client, token, client.id)
    assert resp.json()["cdd_version"]["version_no"] == 1

    rows = (
        await db_session.execute(select(CddVersion).where(CddVersion.client_id == client.id))
    ).scalars().all()
    assert len(rows) == 1


async def test_unrecognized_document_is_rejected_and_keeps_the_active_version(
    db_session, api_client
):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up4@test.com")

    t1, f1 = _patches({"business_keywords": "seo"})
    with t1, f1:
        await _upload(api_client, token, client.id)

    bad_t, bad_f = _patches({"nothing_we_know": "x"})
    with bad_t, bad_f:
        resp = await _upload(api_client, token, client.id, name="junk.xlsx")
    assert resp.status_code == 400

    rows = (
        await db_session.execute(select(CddVersion).where(CddVersion.client_id == client.id))
    ).scalars().all()
    assert len(rows) == 1, "a rejected upload must not create a version"
    assert rows[0].fields["business_keywords"] == "seo"


async def test_empty_file_is_rejected(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up5@test.com")
    resp = await _upload(api_client, token, client.id, content=b"")
    assert resp.status_code == 400


async def test_version_history_endpoint_lists_versions(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up6@test.com")
    for kw in ("seo", "seo, ppc"):
        t, f = _patches({"business_keywords": kw})
        with t, f:
            await _upload(api_client, token, client.id)

    resp = await api_client.get(
        f"/api/v1/clients/{client.id}/cdd-versions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert [v["version_no"] for v in resp.json()["versions"]] == [1, 2]


async def test_staleness_endpoint_reports_estimates(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up7@test.com")
    from app.services.cdd_versions import create_cdd_version, record_phase_start

    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    profile.search_demand_status = "complete"
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    await db_session.commit()

    resp = await api_client.get(
        f"/api/v1/clients/{client.id}/cdd-staleness",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    entry = resp.json()["phases"]["search_demand"]
    assert entry["state"] == "stale_direct"
    assert entry["estimate"]["cost_usd"] >= 0
    assert entry["estimate"]["duration_seconds"] > 0


async def test_rollback_appends_a_new_version(db_session, api_client):
    client = await _client(db_session)
    _u, token = await make_user(db_session, "head_of_department", "cdd-up8@test.com")
    from app.services.cdd_versions import create_cdd_version

    v1 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "something else"}
    )
    await db_session.commit()

    resp = await api_client.post(
        f"/api/v1/clients/{client.id}/cdd-versions/{v1.id}/activate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cdd_version"]["version_no"] == 3
