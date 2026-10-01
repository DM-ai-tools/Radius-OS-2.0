"""compute_discovery_score source preference — a blank answer must not win."""

from decimal import Decimal

from app.models import Client, ClientDigitalProfile, DiscoveryResponse
from app.services.readiness import compute_discovery_score


async def _client(db_session) -> Client:
    client = Client(
        legal_name="Score Co",
        display_name="Score Co",
        primary_url="https://score.example",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()
    return client


async def test_blank_questionnaire_answer_does_not_mask_ai_fill(db_session):
    client = await _client(db_session)
    db_session.add(
        DiscoveryResponse(
            client_id=client.id,
            source="client_questionnaire",
            field_key="business_model",
            field_value={"value": ""},
            confidence=None,
            discrepancy_flag=False,
            status="pending",
        )
    )
    db_session.add(
        DiscoveryResponse(
            client_id=client.id,
            source="ai_gap_fill",
            field_key="business_model",
            field_value={"value": "Professional services"},
            confidence=Decimal("0.4"),
            discrepancy_flag=False,
            status="pending",
        )
    )
    await db_session.flush()

    _score, missing = await compute_discovery_score(db_session, client.id)
    # Half credit with an explicit low-confidence marker — not "missing entirely".
    assert "business_model (low confidence)" in missing
    assert "business_model" not in missing


async def test_real_questionnaire_answer_still_beats_ai_fill(db_session):
    client = await _client(db_session)
    db_session.add(
        DiscoveryResponse(
            client_id=client.id,
            source="ai_gap_fill",
            field_key="business_model",
            field_value={"value": "Guessed model"},
            confidence=Decimal("0.4"),
            discrepancy_flag=False,
            status="pending",
        )
    )
    db_session.add(
        DiscoveryResponse(
            client_id=client.id,
            source="client_questionnaire",
            field_key="business_model",
            field_value={"value": "Trade services"},
            confidence=None,
            discrepancy_flag=False,
            status="pending",
        )
    )
    await db_session.flush()

    _score, missing = await compute_discovery_score(db_session, client.id)
    # A confirmed answer scores full credit, so it is absent from missing.
    assert "business_model" not in missing
    assert "business_model (low confidence)" not in missing
