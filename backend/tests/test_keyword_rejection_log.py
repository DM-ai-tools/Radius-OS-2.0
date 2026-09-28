"""Every rejected keyword is persisted, not sampled."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.models import Client, ClientDigitalProfile, KeywordRejection
from app.services.keyword_rejection_log import (
    classify_source_path,
    record_keyword_rejections,
)


async def _client(db_session) -> Client:
    client = Client(
        legal_name="Reject Co",
        display_name="Reject Co",
        primary_url="https://reject.example",
        industry="Dental",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()
    return client


def test_source_paths_map_filter_labels_to_coarse_buckets():
    assert classify_source_path("seeding") == "provider"
    assert classify_source_path("semrush_organic") == "provider"
    assert classify_source_path("dataforseo_gap") == "provider"
    assert classify_source_path("competitor_page") == "competitor"
    assert classify_source_path("chat_revision") == "chat"
    assert classify_source_path("opportunity") == "opportunity"
    assert classify_source_path("") == "unknown"


def test_provider_fallback_rows_are_their_own_source_path():
    assert classify_source_path("seeding", provider_fallback=True) == "fallback"


async def test_all_rejections_are_persisted_not_just_a_sample(db_session):
    client = await _client(db_session)
    rejections = [
        {
            "keyword": f"junk keyword {i}",
            "reason": "no_business_evidence",
            "seed": "dental implants",
            "source": "seeding",
            "match_class": "broad",
            "volume": i,
        }
        for i in range(50)
    ]
    written = await record_keyword_rejections(
        db_session,
        client_id=client.id,
        agent_key="search_demand",
        rejections=rejections,
    )
    assert written == 50

    rows = (
        await db_session.execute(
            select(KeywordRejection).where(KeywordRejection.client_id == client.id)
        )
    ).scalars().all()
    assert len(rows) == 50, "the 12-row sample cap must not apply here"


async def test_rejection_row_keeps_keyword_seed_reason_and_source(db_session):
    client = await _client(db_session)
    run_id = uuid4()
    await record_keyword_rejections(
        db_session,
        client_id=client.id,
        agent_key="search_demand",
        run_id=run_id,
        rejections=[
            {
                "keyword": "free dental jobs",
                "reason": "no_business_evidence",
                "seed": "dental implants",
                "source": "seeding",
                "match_class": "broad",
                "volume": 320,
            }
        ],
    )
    row = (
        await db_session.execute(
            select(KeywordRejection).where(KeywordRejection.client_id == client.id)
        )
    ).scalars().one()
    assert row.keyword == "free dental jobs"
    assert row.seed == "dental implants"
    assert row.reason == "no_business_evidence"
    assert row.source_path == "provider"
    assert row.source_label == "seeding"
    assert row.match_class == "broad"
    assert row.volume == 320
    assert row.run_id == run_id
    assert row.agent_key == "search_demand"


async def test_rows_without_a_keyword_are_skipped(db_session):
    client = await _client(db_session)
    written = await record_keyword_rejections(
        db_session,
        client_id=client.id,
        agent_key="search_demand",
        rejections=[{"keyword": "", "reason": "empty"}, {"reason": "empty"}, "junk"],
    )
    assert written == 0


async def test_non_integer_volume_does_not_break_persistence(db_session):
    client = await _client(db_session)
    written = await record_keyword_rejections(
        db_session,
        client_id=client.id,
        agent_key="search_demand",
        rejections=[{"keyword": "odd volume", "reason": "noisy", "volume": "n/a"}],
    )
    assert written == 1
    row = (
        await db_session.execute(
            select(KeywordRejection).where(KeywordRejection.client_id == client.id)
        )
    ).scalars().one()
    assert row.volume is None
