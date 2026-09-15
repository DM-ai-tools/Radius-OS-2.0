"""AUDIT-013: CDD document upload/parse must not block the event loop.

extract_text_from_upload is a synchronous, CPU-bound parser (XLSX/DOCX/PDF up
to 12MB); it's now offloaded via asyncio.to_thread instead of running inline
in the async request handler. This is a behavior-neutral change (same
function, same arguments — just run on a worker thread), so the meaningful
regression check is that the endpoint still works end to end."""

from __future__ import annotations

from app.models import Client, ClientDigitalProfile
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


async def test_import_discovery_document_parses_text_file(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-upload@example.com")

    content = b"Business name: Acme Retail\nWebsite: https://acme.example\n"
    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/discovery/import-document",
        files={"file": ("cdd.txt", content, "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["filename"] == "cdd.txt"


async def test_import_discovery_document_rejects_empty_file(api_client, db_session):
    client_id = await _make_client(db_session)
    _, token = await make_user(db_session, "client_success_manager", "csm-upload2@example.com")

    resp = await api_client.post(
        f"/api/v1/clients/{client_id}/discovery/import-document",
        files={"file": ("cdd.txt", b"", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
