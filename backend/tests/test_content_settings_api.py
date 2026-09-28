"""Operator settings for content generation.

Two resources, both following the shape `service-prioritization` already
established for "operator configures something a later phase consumes":
GET returns the saved pack plus the catalogs the UI needs, PUT saves it into a
JSON column on the profile. No migration.

Deliberately NOT here: a generation trigger. Content production runs through
chat → ``run_phase_with_validation`` (CDD staleness gate, validation/revision
loop, findings ledger). A REST "generate now" route would bypass all of it, so
these endpoints only persist settings — the agent reads them at run time, the
same way Phase 5 reads ``commercial_scope.service_prioritization``.
"""

from __future__ import annotations

from app.models import Client, ClientDigitalProfile
from tests.conftest import make_user

_EMAIL = iter(f"signals-hod-{n}@test.com" for n in range(200))


async def _setup(db_session) -> tuple[str, dict]:
    """A client with a profile, plus auth headers for a head_of_department."""
    client = Client(
        legal_name="Signals Co",
        display_name="Signals Co",
        primary_url="https://signals.example.com",
        industry="Digital Marketing",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(ClientDigitalProfile(client_id=client.id))
    await db_session.flush()
    _user, token = await make_user(db_session, "head_of_department", next(_EMAIL))
    await db_session.commit()
    return str(client.id), {"Authorization": f"Bearer {token}"}


# --- content settings ------------------------------------------------------


async def test_get_content_settings_returns_defaults_and_catalogs(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.get(f"/api/v1/clients/{cid}/content-settings", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["preset"] == "data_driven"
    assert body["weights"]["skill"] == "high"
    assert body["weights"]["creative"] == "off"
    # Catalogs so the UI never hardcodes a list.
    assert "data_driven" in body["available_presets"]
    assert "service-page" in body["available_skills"]
    assert "pas" in body["available_frameworks"]
    assert "auto" in body["available_frameworks"]
    assert set(body["signal_names"]) >= {"offers", "funnel", "creative"}


async def test_put_content_settings_persists_and_reads_back(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/content-settings",
        json={
            "preset": "competitor_beating",
            "weight_overrides": {"offers": "off", "past_performance": "high"},
            "framework_choice": "pas",
            "skill_override": "service-page",
            "selected_competitors_only": True,
            "creative_mode": False,
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    again = (await api_client.get(f"/api/v1/clients/{cid}/content-settings", headers=h)).json()
    assert again["preset"] == "competitor_beating"
    assert again["weights"]["offers"] == "off"
    assert again["weights"]["past_performance"] == "high"
    assert again["framework_choice"] == "pas"
    assert again["selected_competitors_only"] is True


async def test_an_unknown_preset_is_rejected(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/content-settings", json={"preset": "banana"},
        headers=h,
    )
    assert resp.status_code == 400
    assert "preset" in resp.text.lower()


async def test_an_unknown_weight_label_is_rejected(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/content-settings",
        json={"weight_overrides": {"offers": "very high"}},
        headers=h,
    )
    assert resp.status_code == 400
    assert "weight" in resp.text.lower()


async def test_an_unknown_signal_name_is_rejected(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/content-settings",
        json={"weight_overrides": {"telepathy": "high"}},
        headers=h,
    )
    assert resp.status_code == 400


async def test_an_unknown_skill_is_rejected_rather_than_silently_ignored(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/content-settings", json={"skill_override": "nope"},
        headers=h,
    )
    assert resp.status_code == 400
    assert "skill" in resp.text.lower()


async def test_settings_for_a_missing_client_are_404(db_session, api_client):
    _cid, h = await _setup(db_session)
    resp = await api_client.get(
        "/api/v1/clients/00000000-0000-0000-0000-000000000000/content-settings",
        headers=h,
    )
    assert resp.status_code == 404


# --- business profile + offers --------------------------------------------


OFFER = {
    "name": "Free 30-min SEO audit",
    "type": "lead_magnet",
    "description": "A free audit of your site",
    "url": "/offers/free-audit",
    "cta": "Claim your free audit",
    "starts_at": "2026-09-01T00:00:00+10:00",
    "ends_at": "2026-12-01T00:00:00+10:00",
    "active": True,
    "applies_to": ["SEO"],
    "locations": [],
    "funnel_stages": ["TOFU", "MOFU"],
    "priority": 1,
    "terms": "New clients only.",
}


async def test_put_business_profile_saves_type_size_reach_and_offers(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/business-profile",
        json={
            "business_type": "agency",
            "business_size": "small",
            "location_model": "city_wide",
            "locations_served": ["Melbourne", "Geelong"],
            "offers": [OFFER],
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text

    body = (await api_client.get(f"/api/v1/clients/{cid}/business-profile", headers=h)).json()
    assert body["business_type"] == "agency"
    assert body["business_size"] == "small"
    assert body["location_model"] == "city_wide"
    assert body["locations_served"] == ["Melbourne", "Geelong"]
    assert len(body["offers"]) == 1
    assert body["offers"][0]["name"] == OFFER["name"]


async def test_an_offer_without_an_id_gets_one_assigned(db_session, api_client):
    cid, h = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/business-profile", json={"offers": [OFFER]},
        headers=h,
    )
    body = (await api_client.get(f"/api/v1/clients/{cid}/business-profile", headers=h)).json()
    assert body["offers"][0]["id"], "server must assign a stable offer id"


async def test_get_reports_whether_each_offer_is_currently_active(db_session, api_client):
    """The operator needs to see at a glance which offers content may mention."""
    cid, h = await _setup(db_session)
    expired = {**OFFER, "id": "old", "name": "Expired promo",
               "ends_at": "2026-01-01T00:00:00+10:00"}
    await api_client.put(
        f"/api/v1/clients/{cid}/business-profile", json={"offers": [OFFER, expired]},
        headers=h,
    )
    offers = {o["name"]: o for o in
              (await api_client.get(f"/api/v1/clients/{cid}/business-profile", headers=h)).json()["offers"]}
    assert offers["Free 30-min SEO audit"]["currently_active"] is True
    assert offers["Expired promo"]["currently_active"] is False
    assert "expired" in offers["Expired promo"]["status_reason"].lower()


async def test_an_unparseable_offer_date_is_rejected_at_the_boundary(db_session, api_client):
    """Better a 400 than an offer that silently never activates."""
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/business-profile",
        json={"offers": [{**OFFER, "ends_at": "whenever"}]},
        headers=h,
    )
    assert resp.status_code == 400
    assert "ends_at" in resp.text


async def test_an_offer_needs_a_name(db_session, api_client):
    """A missing name is rejected by the schema (422); a blank one by the handler (400).
    Either way it never reaches storage as a nameless offer."""
    cid, h = await _setup(db_session)
    missing = await api_client.put(
        f"/api/v1/clients/{cid}/business-profile",
        json={"offers": [{"type": "promotion"}]},
        headers=h,
    )
    assert missing.status_code == 422

    blank = await api_client.put(
        f"/api/v1/clients/{cid}/business-profile",
        json={"offers": [{"name": "   ", "type": "promotion"}]},
        headers=h,
    )
    assert blank.status_code == 400
    assert "name" in blank.text.lower()

    body = (await api_client.get(f"/api/v1/clients/{cid}/business-profile", headers=h)).json()
    assert body["offers"] == []


async def test_an_invalid_funnel_stage_is_rejected(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/business-profile",
        json={"offers": [{**OFFER, "funnel_stages": ["RETENTION"]}]},
        headers=h,
    )
    assert resp.status_code == 400


async def test_an_unknown_business_type_is_rejected(db_session, api_client):
    cid, h = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/business-profile", json={"business_type": "wizardry"},
        headers=h,
    )
    assert resp.status_code == 400


async def test_saving_offers_does_not_disturb_other_commercial_scope_fields(db_session, api_client):
    """The JSON column is shared with the CDD — a settings save must not clobber it."""
    cid, h = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/service-prioritization",
        json={"keyword_pool_target": 3000, "confirmed": False},
        headers=h,
    )
    await api_client.put(
        f"/api/v1/clients/{cid}/business-profile", json={"offers": [OFFER]},
        headers=h,
    )
    prio = (await api_client.get(f"/api/v1/clients/{cid}/service-prioritization", headers=h)).json()
    assert prio is not None
    profile = (await api_client.get(f"/api/v1/clients/{cid}/profile", headers=h)).json()
    assert profile["commercial_scope"]["keyword_pool_target"] == 3000
    assert profile["commercial_scope"]["offers"]


async def test_the_saved_settings_feed_the_context_builder(db_session, api_client):
    """End to end: what the operator saves is what generation reads."""
    from datetime import datetime, timezone

    from app.services.content_signals.context import build_content_context

    cid, h = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/business-profile",
        json={"business_type": "agency", "business_size": "small",
              "location_model": "city_wide", "offers": [OFFER]},
        headers=h,
    )
    await api_client.put(
        f"/api/v1/clients/{cid}/content-settings",
        json={"preset": "local_growth", "framework_choice": "pas"},
        headers=h,
    )
    profile = (await api_client.get(f"/api/v1/clients/{cid}/profile", headers=h)).json()
    commercial = profile["commercial_scope"]
    settings = commercial["content_settings"]

    ctx = build_content_context(
        keyword="seo services melbourne",
        page_type="service",
        commercial=commercial,
        preset=settings["preset"],
        weight_overrides=settings.get("weight_overrides"),
        framework_choice=settings.get("framework_choice"),
        now=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )
    assert ctx["preset"] == "local_growth"
    assert ctx["signals"]["framework"]["data"]["framework"] == "pas"
    assert ctx["signals"]["business_type"]["data"]["type"] == "agency"
    assert ctx["signals"]["business_size"]["data"]["size"] == "small"
    assert ctx["signals"]["reach"]["data"]["model"] == "city_wide"
