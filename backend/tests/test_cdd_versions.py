"""CDD version store: immutable history, atomic numbering, derived staleness."""

from __future__ import annotations

from sqlalchemy import select

from app.models import CddVersion, Client, ClientDigitalProfile, PhaseState
from app.services.cdd_versions import (
    active_version,
    build_staleness_report,
    create_cdd_version,
    dismiss_staleness,
    record_phase_start,
    rollback_to_version,
    validate_cdd_fields,
)


async def _client(db_session, **profile_kwargs) -> Client:
    client = Client(
        legal_name="Version Co",
        display_name="Version Co",
        primary_url="https://version.example",
        industry="Dental",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id, **profile_kwargs))
    await db_session.flush()
    return client


# --- validation -------------------------------------------------------------


def test_validation_rejects_an_empty_field_map():
    ok, error = validate_cdd_fields({})
    assert ok is False and error


def test_validation_rejects_a_map_with_no_recognized_fields():
    ok, error = validate_cdd_fields({"totally_unknown": "x", "another": "y"})
    assert ok is False
    assert "recogniz" in error.lower()


def test_validation_accepts_one_recognized_field():
    ok, error = validate_cdd_fields({"business_keywords": "seo"})
    assert ok is True and error is None


def test_validation_rejects_all_blank_values():
    ok, _error = validate_cdd_fields({"business_keywords": "  ", "products": ""})
    assert ok is False


# --- versioning -------------------------------------------------------------


async def test_first_version_is_numbered_one(db_session):
    client = await _client(db_session)
    version = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    assert version.version_no == 1
    assert (await active_version(db_session, client.id)).id == version.id


async def test_versions_increment_and_never_overwrite(db_session):
    client = await _client(db_session)
    v1 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    v2 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    assert (v1.version_no, v2.version_no) == (1, 2)
    rows = (
        await db_session.execute(
            select(CddVersion).where(CddVersion.client_id == client.id)
        )
    ).scalars().all()
    assert len(rows) == 2, "history must be append-only"


async def test_identical_content_does_not_create_a_new_version(db_session):
    client = await _client(db_session)
    fields = {"business_keywords": "seo"}
    v1 = await create_cdd_version(db_session, client_id=client.id, fields=fields)
    v2 = await create_cdd_version(db_session, client_id=client.id, fields=dict(fields))
    assert v2.id == v1.id, "a double-clicked upload must not churn versions"


async def test_reordered_list_content_is_treated_as_identical(db_session):
    client = await _client(db_session)
    v1 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    v2 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "ppc, seo"}
    )
    assert v2.id == v1.id


async def test_version_stores_the_diff_against_its_predecessor(db_session):
    client = await _client(db_session)
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    v2 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    assert "business_keywords" in (v2.diff or {}).get("changed_keys", [])


async def test_rapid_successive_versions_all_persist(db_session):
    client = await _client(db_session)
    for i in range(5):
        await create_cdd_version(
            db_session,
            client_id=client.id,
            fields={"business_keywords": f"seo, kw{i}"},
        )
    rows = (
        await db_session.execute(
            select(CddVersion).where(CddVersion.client_id == client.id)
        )
    ).scalars().all()
    assert sorted(r.version_no for r in rows) == [1, 2, 3, 4, 5]


# --- commercial_scope refresh ----------------------------------------------


async def test_new_version_refreshes_cdd_derived_keys_on_the_profile(db_session):
    client = await _client(
        db_session,
        commercial_scope={"business_keywords": "old", "service_prioritization": {"confirmed_at": "x"}},
    )
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    assert profile.commercial_scope["business_keywords"] == "seo, ppc"


async def test_refresh_preserves_non_cdd_state_in_commercial_scope(db_session):
    """service_prioritization and harvested intel are not CDD fields."""
    client = await _client(
        db_session,
        commercial_scope={
            "service_prioritization": {"confirmed_at": "x", "selected_service_ids": ["a"]},
            "competitor_keyword_intel": {"keywords": [{"keyword": "k"}]},
            "keyword_pool_target": 8000,
        },
    )
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    assert profile.commercial_scope["service_prioritization"]["confirmed_at"] == "x"
    assert profile.commercial_scope["competitor_keyword_intel"]["keywords"]
    assert profile.commercial_scope["keyword_pool_target"] == 8000


# --- phase state + staleness ------------------------------------------------


async def test_record_phase_start_captures_the_active_version(db_session):
    client = await _client(db_session)
    v1 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    state = await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    assert state.cdd_version_id == v1.id
    assert state.run_id is not None


async def test_record_phase_start_is_idempotent_per_phase(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    rows = (
        await db_session.execute(
            select(PhaseState).where(PhaseState.client_id == client.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_completed_phase_on_an_old_version_reports_stale(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    profile.search_demand_status = "complete"
    await db_session.flush()

    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "stale_direct"
    assert "business_keywords" in report["search_demand"]["fields"]


async def test_downstream_phase_reports_stale_upstream(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    for agent in ("search_demand", "site_architecture"):
        await record_phase_start(db_session, client_id=client.id, agent_key=agent)
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    profile.search_demand_status = "complete"
    profile.site_architecture_status = "complete"
    await db_session.flush()

    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["site_architecture"]["state"] == "stale_upstream"


async def test_dismissing_staleness_clears_it(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    profile.search_demand_status = "complete"
    await db_session.flush()
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )

    await dismiss_staleness(db_session, client_id=client.id, agent_key="search_demand")
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "dismissed"


async def test_rollback_creates_a_new_version_rather_than_mutating_history(db_session):
    client = await _client(db_session)
    v1 = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "totally different"}
    )
    v3 = await rollback_to_version(db_session, client_id=client.id, version_id=v1.id)
    assert v3.version_no == 3
    assert v3.rolled_back_from == v1.id
    assert v3.fields == v1.fields
    assert (await active_version(db_session, client.id)).id == v3.id


# --- cache invalidation on CDD change ---------------------------------------


def test_competitor_cache_key_changes_with_the_cdd_fingerprint():
    """A 14-day competitor cache must not survive an industry change."""
    from app.services.cache import competitor_cache_key

    dental = competitor_cache_key("client-1", fingerprint="dental")
    bakery = competitor_cache_key("client-1", fingerprint="bakery")
    assert dental != bakery


def test_competitor_cache_key_is_stable_for_the_same_fingerprint():
    from app.services.cache import competitor_cache_key

    assert competitor_cache_key("client-1", fingerprint="dental") == competitor_cache_key(
        "client-1", fingerprint="dental"
    )


def test_competitor_cache_key_without_a_fingerprint_still_works():
    """Backward compatible with callers that pass no fingerprint."""
    from app.services.cache import competitor_cache_key

    assert "client-1" in competitor_cache_key("client-1")


def test_cdd_fingerprint_tracks_industry_and_services():
    from app.services.cdd_versions import cdd_fingerprint

    a = cdd_fingerprint(industry="Dental", services=["Implants"])
    b = cdd_fingerprint(industry="Bakery", services=["Implants"])
    c = cdd_fingerprint(industry="Dental", services=["Implants", "Whitening"])
    assert len({a, b, c}) == 3
    assert cdd_fingerprint(industry="dental ", services=["implants"]) == a


# --- A4: a confirmed service selection must not outlive a services change ----


async def test_services_change_reopens_the_confirmed_service_selection(db_session):
    client = await _client(
        db_session,
        commercial_scope={
            "products": "SEO, PPC",
            "service_prioritization": {
                "confirmed_at": "2026-09-01T00:00:00+00:00",
                "selected_service_ids": ["seo", "ppc"],
            },
        },
    )
    await create_cdd_version(db_session, client_id=client.id, fields={"products": "SEO, PPC"})
    await create_cdd_version(
        db_session, client_id=client.id, fields={"products": "SEO, PPC, Web Design"}
    )
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    prio = profile.commercial_scope["service_prioritization"]
    assert prio.get("confirmed_at") is None, "selection must be re-opened"
    assert prio.get("reopened_reason") == "cdd_services_changed"
    # The prior selection is preserved so the user sees what they picked before.
    assert prio["selected_service_ids"] == ["seo", "ppc"]


async def test_a_change_that_does_not_touch_services_keeps_the_selection(db_session):
    client = await _client(
        db_session,
        commercial_scope={
            "products": "SEO, PPC",
            "service_prioritization": {
                "confirmed_at": "2026-09-01T00:00:00+00:00",
                "selected_service_ids": ["seo"],
            },
        },
    )
    await create_cdd_version(
        db_session, client_id=client.id, fields={"products": "SEO, PPC", "positioning": "a"}
    )
    await create_cdd_version(
        db_session, client_id=client.id, fields={"products": "SEO, PPC", "positioning": "b"}
    )
    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    assert profile.commercial_scope["service_prioritization"]["confirmed_at"]
