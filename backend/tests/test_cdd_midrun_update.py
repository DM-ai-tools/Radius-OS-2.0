"""Mid-run CDD updates — the six scenarios, end to end."""

from __future__ import annotations

from sqlalchemy import select

from app.models import CddVersion, Client, ClientDigitalProfile
from app.services.cdd_gate import stale_upstream_block
from app.services.cdd_versions import (
    build_staleness_report,
    create_cdd_version,
    record_phase_start,
    validate_cdd_fields,
)


async def _client(db_session) -> Client:
    client = Client(
        legal_name="Midrun Co",
        display_name="Midrun Co",
        primary_url="https://midrun.example",
        industry="Dental",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()
    return client


async def _profile(db_session, client) -> ClientDigitalProfile:
    return (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()


async def _complete(db_session, client, *agents: str) -> None:
    profile = await _profile(db_session, client)
    attr = {
        "search_demand": "search_demand_status",
        "site_architecture": "site_architecture_status",
        "content_strategy": "seo_strategy_status",
        "competitor_market_agent": "competitor_status",
    }
    for agent in agents:
        await record_phase_start(db_session, client_id=client.id, agent_key=agent)
        setattr(profile, attr[agent], "complete")
    await db_session.flush()


# --- 1. update BEFORE the run starts ---------------------------------------


async def test_update_before_any_phase_runs_marks_nothing_stale(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert all(entry["state"] == "fresh" for entry in report.values())


async def test_pending_phase_uses_the_latest_cdd_with_no_approval(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    profile = await _profile(db_session, client)
    # The derived slice refreshed immediately, so a phase starting now reads new data.
    assert profile.commercial_scope["business_keywords"] == "seo, ppc"
    assert (await build_staleness_report(db_session, client_id=client.id))["search_demand"][
        "state"
    ] == "fresh"


# --- 2. update MID-RUN ------------------------------------------------------


async def test_in_progress_phase_is_not_interrupted_or_marked_stale(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    profile = await _profile(db_session, client)
    profile.search_demand_status = "in_progress"
    await db_session.flush()

    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "running"


async def test_a_phase_that_finishes_after_the_update_becomes_stale(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    profile = await _profile(db_session, client)
    profile.search_demand_status = "in_progress"
    await db_session.flush()

    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    # The run finishes normally — it was never interrupted.
    profile.search_demand_status = "complete"
    await db_session.flush()

    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "stale_direct"


# --- 3. update affecting COMPLETED steps ------------------------------------


async def test_completed_phase_is_marked_stale_but_never_auto_redone(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await _complete(db_session, client, "search_demand")
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "stale_direct"

    profile = await _profile(db_session, client)
    # Status untouched: nothing was re-run on the user's behalf.
    assert profile.search_demand_status == "complete"


async def test_staleness_propagates_to_downstream_completed_phases(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await _complete(db_session, client, "search_demand", "site_architecture")
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["site_architecture"]["state"] == "stale_upstream"
    assert "search_demand" in report["site_architecture"]["upstream"]


async def test_downstream_phase_is_blocked_from_starting_on_stale_inputs(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await _complete(db_session, client, "search_demand")
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    blocked = await stale_upstream_block(
        db_session, client_id=client.id, agent_key="site_architecture"
    )
    assert blocked is not None
    assert any("out of date" in str(e.get("content") or "") for e in blocked)


async def test_the_stale_phase_itself_is_not_blocked(db_session):
    """Re-running it is the fix, so it must be allowed to start."""
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await _complete(db_session, client, "search_demand")
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    assert (
        await stale_upstream_block(db_session, client_id=client.id, agent_key="search_demand")
        is None
    )


async def test_redoing_a_stale_phase_clears_it(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await _complete(db_session, client, "search_demand")
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    # Re-run: the phase re-stamps itself with the current version.
    await record_phase_start(db_session, client_id=client.id, agent_key="search_demand")
    profile = await _profile(db_session, client)
    profile.search_demand_status = "complete"
    await db_session.flush()

    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "fresh"
    assert report["site_architecture"]["state"] == "fresh"


# --- 4. update affecting ONLY pending steps ---------------------------------


async def test_change_touching_only_pending_phases_marks_nothing_stale(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"brand_guidelines": "calm"})
    # Only Search Demand has run; brand_guidelines feeds content_production /
    # on_page_seo, which have not started.
    await _complete(db_session, client, "search_demand")
    await create_cdd_version(
        db_session, client_id=client.id, fields={"brand_guidelines": "bold and direct"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "fresh"
    assert report["content_production"]["state"] == "fresh"


# --- 5. invalid / malformed CDD ---------------------------------------------


async def test_invalid_cdd_is_rejected_and_keeps_the_current_version(db_session):
    client = await _client(db_session)
    good = await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo"}
    )
    valid, error = validate_cdd_fields({"unknown_key": "x"})
    assert valid is False and error

    versions = (
        await db_session.execute(
            select(CddVersion).where(CddVersion.client_id == client.id)
        )
    ).scalars().all()
    assert [v.id for v in versions] == [good.id]


async def test_empty_extraction_is_rejected():
    assert validate_cdd_fields({})[0] is False
    assert validate_cdd_fields(None)[0] is False


# --- 6. rapid successive updates --------------------------------------------


async def test_rapid_updates_all_version_and_staleness_reflects_the_latest(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await _complete(db_session, client, "search_demand")

    for i in range(4):
        await create_cdd_version(
            db_session,
            client_id=client.id,
            fields={"business_keywords": f"seo, extra{i}"},
        )

    versions = (
        await db_session.execute(
            select(CddVersion).where(CddVersion.client_id == client.id)
        )
    ).scalars().all()
    assert len(versions) == 5

    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "stale_direct"


async def test_a_duplicate_upload_in_a_burst_does_not_add_a_version(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    versions = (
        await db_session.execute(
            select(CddVersion).where(CddVersion.client_id == client.id)
        )
    ).scalars().all()
    assert len(versions) == 1


async def test_update_after_the_run_finished_still_reports_staleness(db_session):
    client = await _client(db_session)
    await create_cdd_version(db_session, client_id=client.id, fields={"business_keywords": "seo"})
    await _complete(db_session, client, "search_demand", "site_architecture", "content_strategy")
    await create_cdd_version(
        db_session, client_id=client.id, fields={"business_keywords": "seo, ppc"}
    )
    report = await build_staleness_report(db_session, client_id=client.id)
    assert report["search_demand"]["state"] == "stale_direct"
    assert report["content_strategy"]["state"] == "stale_upstream"
