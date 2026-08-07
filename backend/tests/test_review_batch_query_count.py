from sqlalchemy import event

from app.db import engine
from app.models import Client, ClientDigitalProfile, DiscoveryResponse, FindingsLedger
from app.services.review import approve_phase_batch
from tests.conftest import make_user


async def _make_client_with_pending_findings(db_session, n: int) -> str:
    client = Client(
        legal_name="Batch Co",
        display_name="Batch Co",
        primary_url="https://example.com",
        industry="Retail",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    for i in range(n):
        dr = DiscoveryResponse(
            client_id=client.id,
            source="client_questionnaire",
            field_key=f"field_{i}",
            field_value={"value": f"val_{i}"},
            confidence=None,
            discrepancy_flag=False,
            status="pending",
        )
        db_session.add(dr)
        await db_session.flush()
        db_session.add(
            FindingsLedger(
                client_id=client.id,
                agent_key="discovery_agent",
                source_table="discovery_responses",
                source_id=dr.id,
                confidence="high",
                status="pending",
            )
        )
    await db_session.commit()
    return str(client.id)


async def test_batch_approve_resolves_all_and_uses_bounded_query_count(db_session):
    client_id = await _make_client_with_pending_findings(db_session, 5)
    hod_user, _ = await make_user(db_session, "head_of_department", "hod-batch@example.com")

    statement_count = 0

    def _count(*args, **kwargs):
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        result = await approve_phase_batch(
            db_session,
            user=hod_user,
            client_id=client_id,
            agent_key="discovery_agent",
            action="approve",
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert result["resolved"] == 5
    # Measured directly: old N+1 code (per-row User/FindingsLedger re-fetch +
    # re-run require_permission) issued 53 statements for 5 rows; the fixed
    # code (shared _apply_review, permission checked once) issues 33. Bound
    # set well below the old figure but with headroom above the current one,
    # so this is a regression guard against the N+1 pattern reappearing, not
    # an exact-count assertion.
    assert statement_count < 40, f"expected a bounded query count, got {statement_count}"
