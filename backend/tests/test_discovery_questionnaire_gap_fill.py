"""Questionnaire submit → AI fills the blanks it is allowed to fill."""

from sqlalchemy import select

from app.models import Client, ClientDigitalProfile, DiscoveryResponse, FindingsLedger
from app.services import discovery_gap_fill
from tests.conftest import make_user


async def _client(db_session) -> Client:
    client = Client(
        legal_name="Gap Co",
        display_name="Gap Co",
        primary_url="https://gap.example",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()
    await db_session.commit()
    return client


async def _submit(api_client, token, client_id, fields):
    return await api_client.post(
        f"/api/v1/clients/{client_id}/questionnaire",
        json={"fields": fields},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_blank_fields_are_filled_by_ai_and_stored_low_confidence(
    db_session, api_client, monkeypatch
):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "client_success_manager", "gap-csm@test.com")

    async def _fake_fill(**kwargs):
        assert "business_model" in kwargs["missing"]
        return {
            "business_model": {
                "value": "Professional services",
                "confidence": 0.4,
                "rationale": "Site sells consulting engagements.",
            }
        }

    monkeypatch.setattr(
        discovery_gap_fill, "fill_missing_discovery_fields", _fake_fill
    )

    resp = await _submit(
        api_client,
        token,
        client.id,
        {"business_model": "", "target_demographic": "Homeowners 30-55"},
    )
    assert resp.status_code == 200

    rows = (
        await db_session.execute(
            select(DiscoveryResponse).where(
                DiscoveryResponse.client_id == client.id,
                DiscoveryResponse.source == "ai_gap_fill",
            )
        )
    ).scalars().all()
    assert [r.field_key for r in rows] == ["business_model"]
    assert rows[0].field_value["value"] == "Professional services"
    assert float(rows[0].confidence) < 0.5
    assert rows[0].status == "pending"


async def test_ai_filled_rows_get_findings_ledger_entries(
    db_session, api_client, monkeypatch
):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "client_success_manager", "gap-csm2@test.com")

    async def _fake_fill(**kwargs):
        return {"business_model": {"value": "Ecommerce", "confidence": 0.4}}

    monkeypatch.setattr(
        discovery_gap_fill, "fill_missing_discovery_fields", _fake_fill
    )
    await _submit(api_client, token, client.id, {"business_model": ""})

    row = (
        await db_session.execute(
            select(DiscoveryResponse).where(
                DiscoveryResponse.client_id == client.id,
                DiscoveryResponse.source == "ai_gap_fill",
            )
        )
    ).scalars().one()
    ledgers = (
        await db_session.execute(
            select(FindingsLedger).where(FindingsLedger.source_id == row.id)
        )
    ).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].agent_key == "discovery_agent"
    assert ledgers[0].status == "pending"
    assert ledgers[0].confidence == "low"


async def test_answered_fields_are_never_sent_for_fill(
    db_session, api_client, monkeypatch
):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "client_success_manager", "gap-csm3@test.com")
    seen: dict = {}

    async def _fake_fill(**kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(
        discovery_gap_fill, "fill_missing_discovery_fields", _fake_fill
    )
    await _submit(
        api_client,
        token,
        client.id,
        {"business_model": "Trade services", "positioning": ""},
    )
    assert "business_model" not in seen["missing"]
    assert "positioning" in seen["missing"]
    assert seen["known"]["business_model"] == "Trade services"


async def test_blank_answers_are_not_persisted_as_rows(
    db_session, api_client, monkeypatch
):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "client_success_manager", "gap-csm4@test.com")

    async def _fake_fill(**kwargs):
        return {}

    monkeypatch.setattr(
        discovery_gap_fill, "fill_missing_discovery_fields", _fake_fill
    )
    await _submit(api_client, token, client.id, {"business_model": "   "})

    rows = (
        await db_session.execute(
            select(DiscoveryResponse).where(
                DiscoveryResponse.client_id == client.id,
                DiscoveryResponse.source == "client_questionnaire",
            )
        )
    ).scalars().all()
    assert rows == []


async def test_gate_card_reports_ai_filled_fields(db_session, api_client, monkeypatch):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "client_success_manager", "gap-csm5@test.com")

    async def _fake_fill(**kwargs):
        return {
            "business_model": {
                "value": "Professional services",
                "confidence": 0.4,
                "rationale": "No cart, consulting copy.",
            }
        }

    monkeypatch.setattr(
        discovery_gap_fill, "fill_missing_discovery_fields", _fake_fill
    )
    resp = await _submit(api_client, token, client.id, {"business_model": ""})
    cards = [
        e["payload"]
        for e in resp.json()["events"]
        if e.get("type") == "structured_card"
    ]
    gate = next(c for c in cards if c["card_type"] == "discovery_confirmation")
    assert gate["ai_filled"]["business_model"]["value"] == "Professional services"
    assert gate["ai_filled"]["business_model"]["confidence"] < 0.5


async def test_submit_still_succeeds_when_gap_fill_raises(
    db_session, api_client, monkeypatch
):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "client_success_manager", "gap-csm6@test.com")

    async def _boom(**kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(discovery_gap_fill, "fill_missing_discovery_fields", _boom)
    resp = await _submit(api_client, token, client.id, {"business_model": ""})
    assert resp.status_code == 200
    rows = (
        await db_session.execute(
            select(DiscoveryResponse).where(
                DiscoveryResponse.client_id == client.id,
                DiscoveryResponse.source == "ai_gap_fill",
            )
        )
    ).scalars().all()
    assert rows == []
