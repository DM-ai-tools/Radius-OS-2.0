"""Per-service competitor endpoints + the benchmark endpoint.

Feature 1 acceptance: add, save, reload, select/deselect, remove, and content
generation for a service with no competitors is unchanged.
"""

from __future__ import annotations

from unittest.mock import patch

from app.models import Client, ClientDigitalProfile, CompetitorProfile
from tests.conftest import make_user

_EMAIL = iter(f"svc-comp-hod-{n}@test.com" for n in range(300))

CATALOG = [
    {"id": "seo", "name": "SEO Services", "subservices": [{"id": "local-seo", "name": "Local SEO"}]},
    {"id": "ppc", "name": "PPC Management"},
]


async def _setup(db_session, *, with_competitors: bool = True):
    client = Client(
        legal_name="Bench Co",
        display_name="Bench Co",
        primary_url="https://bench.example.com",
        industry="Digital Marketing",
        tier="B",
        status="onboarding",
    )
    db_session.add(client)
    await db_session.flush()
    db_session.add(
        ClientDigitalProfile(
            client_id=client.id,
            commercial_scope={"service_prioritization": {"service_catalog": CATALOG}},
        )
    )
    ids: dict[str, str] = {}
    if with_competitors:
        for name, url, source, confirmed in (
            ("King Kong", "https://kingkong.co", "manual", True),
            ("WebFX", "https://webfx.com", "dataforseo", False),
        ):
            row = CompetitorProfile(
                client_id=client.id, name=name, url=url, source=source, confirmed=confirmed
            )
            db_session.add(row)
            await db_session.flush()
            ids[name] = str(row.id)
    await db_session.flush()
    _user, token = await make_user(db_session, "head_of_department", next(_EMAIL))
    await db_session.commit()
    return str(client.id), {"Authorization": f"Bearer {token}"}, ids


# --- Feature 1: selection --------------------------------------------------


async def test_get_lists_available_competitors_with_none_selected(db_session, api_client):
    cid, h, _ids = await _setup(db_session)
    resp = await api_client.get(f"/api/v1/clients/{cid}/services/seo/competitors", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["selected_competitor_ids"] == []
    assert {c["name"] for c in body["available"]} == {"King Kong", "WebFX"}
    assert all(c["selected"] is False for c in body["available"])
    assert all(c["domain"] for c in body["available"])


async def test_selection_persists_and_reloads(db_session, api_client):
    cid, h, ids = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["WebFX"], ids["King Kong"]]},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["selected_competitor_ids"] == [ids["WebFX"], ids["King Kong"]]

    again = (
        await api_client.get(f"/api/v1/clients/{cid}/services/seo/competitors", headers=h)
    ).json()
    # Order the operator chose is preserved on reload.
    assert [c["name"] for c in again["selected"]] == ["WebFX", "King Kong"]


async def test_services_hold_independent_selections(db_session, api_client):
    cid, h, ids = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["King Kong"]]}, headers=h,
    )
    await api_client.put(
        f"/api/v1/clients/{cid}/services/ppc/competitors",
        json={"competitor_ids": [ids["WebFX"]]}, headers=h,
    )
    seo = (await api_client.get(f"/api/v1/clients/{cid}/services/seo/competitors", headers=h)).json()
    ppc = (await api_client.get(f"/api/v1/clients/{cid}/services/ppc/competitors", headers=h)).json()
    assert seo["selected_competitor_ids"] == [ids["King Kong"]]
    assert ppc["selected_competitor_ids"] == [ids["WebFX"]]


async def test_deselecting_everything_clears_the_service(db_session, api_client):
    cid, h, ids = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["King Kong"]]}, headers=h,
    )
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": []}, headers=h,
    )
    body = (await api_client.get(f"/api/v1/clients/{cid}/services/seo/competitors", headers=h)).json()
    assert body["selected_competitor_ids"] == []


async def test_a_competitor_from_another_client_is_ignored(db_session, api_client):
    cid, h, ids = await _setup(db_session)
    resp = await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["King Kong"], "00000000-0000-0000-0000-000000000000"]},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["selected_competitor_ids"] == [ids["King Kong"]]
    assert any("not on this client" in w for w in resp.json()["warnings"])


# --- Feature 1: inline creation -------------------------------------------


async def test_adding_a_competitor_inline_creates_and_attaches_it(db_session, api_client):
    cid, h, _ids = await _setup(db_session)
    resp = await api_client.post(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"name": "Neil Patel", "website": "https://neilpatel.com/digital/", "notes": "content-led"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["competitor"]["domain"] == "neilpatel.com"
    assert body["competitor"]["notes"] == "content-led"

    listing = (
        await api_client.get(f"/api/v1/clients/{cid}/services/seo/competitors", headers=h)
    ).json()
    assert body["competitor"]["id"] in listing["selected_competitor_ids"]


async def test_adding_the_same_domain_twice_reuses_the_row(db_session, api_client):
    """Competitors are reusable — never two rows for one domain on a client."""
    cid, h, _ids = await _setup(db_session)
    first = await api_client.post(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"name": "King Kong", "website": "kingkong.co"}, headers=h,
    )
    assert first.json()["created"] is False, "should reuse the seeded King Kong row"

    second = await api_client.post(
        f"/api/v1/clients/{cid}/services/ppc/competitors",
        json={"name": "King Kong AU", "website": "https://www.kingkong.co/"}, headers=h,
    )
    assert second.json()["created"] is False
    assert second.json()["competitor"]["id"] == first.json()["competitor"]["id"]


async def test_attaching_an_already_attached_competitor_warns_without_duplicating(
    db_session, api_client
):
    cid, h, _ids = await _setup(db_session)
    await api_client.post(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"name": "King Kong", "website": "kingkong.co"}, headers=h,
    )
    again = await api_client.post(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"name": "King Kong", "website": "kingkong.co"}, headers=h,
    )
    assert any("already attached" in w for w in again.json()["warnings"])
    listing = (
        await api_client.get(f"/api/v1/clients/{cid}/services/seo/competitors", headers=h)
    ).json()
    assert len(listing["selected_competitor_ids"]) == 1


async def test_an_invalid_website_is_rejected(db_session, api_client):
    cid, h, _ids = await _setup(db_session)
    for bad in ("not a domain", "", "localhost", "/services"):
        resp = await api_client.post(
            f"/api/v1/clients/{cid}/services/seo/competitors",
            json={"name": "Bad", "website": bad}, headers=h,
        )
        assert resp.status_code in (400, 422), (bad, resp.status_code)


async def test_a_missing_name_is_rejected(db_session, api_client):
    cid, h, _ids = await _setup(db_session)
    resp = await api_client.post(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"name": "   ", "website": "example.com"}, headers=h,
    )
    assert resp.status_code == 400


# --- Feature 1: removal ---------------------------------------------------


async def test_detaching_leaves_the_competitor_available_to_other_services(
    db_session, api_client
):
    cid, h, ids = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["King Kong"]]}, headers=h,
    )
    resp = await api_client.delete(
        f"/api/v1/clients/{cid}/services/seo/competitors/{ids['King Kong']}", headers=h
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is False
    listing = (
        await api_client.get(f"/api/v1/clients/{cid}/services/seo/competitors", headers=h)
    ).json()
    assert listing["selected_competitor_ids"] == []
    # Still on the client, pickable elsewhere.
    assert ids["King Kong"] in {c["id"] for c in listing["available"]}


async def test_deleting_everywhere_removes_the_row_and_all_links(db_session, api_client):
    cid, h, ids = await _setup(db_session)
    for service in ("seo", "ppc"):
        await api_client.put(
            f"/api/v1/clients/{cid}/services/{service}/competitors",
            json={"competitor_ids": [ids["King Kong"]]}, headers=h,
        )
    resp = await api_client.delete(
        f"/api/v1/clients/{cid}/services/seo/competitors/{ids['King Kong']}"
        "?delete_everywhere=true",
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    for service in ("seo", "ppc"):
        listing = (
            await api_client.get(
                f"/api/v1/clients/{cid}/services/{service}/competitors", headers=h
            )
        ).json()
        assert listing["selected_competitor_ids"] == []
        assert ids["King Kong"] not in {c["id"] for c in listing["available"]}


# --- Feature 1: generation integration ------------------------------------


async def test_service_competitors_reach_the_generation_context(db_session, api_client):
    """The selected competitors must be what the content signal sees."""
    from app.services.content_signals.context import build_content_context
    from app.services.service_competitors import resolve_service_competitors

    cid, h, ids = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["WebFX"]]}, headers=h,
    )
    profile = (await api_client.get(f"/api/v1/clients/{cid}/profile", headers=h)).json()
    commercial = profile["commercial_scope"]

    rows = [
        {"id": ids["King Kong"], "name": "King Kong", "url": "https://kingkong.co", "source": "manual"},
        {"id": ids["WebFX"], "name": "WebFX", "url": "https://webfx.com", "source": "dataforseo"},
    ]
    scoped = resolve_service_competitors(commercial, service_id="seo", competitor_rows=rows)
    assert [c["name"] for c in scoped] == ["WebFX"]

    ctx = build_content_context(
        keyword="seo services melbourne", page_type="service",
        commercial=commercial, competitors=scoped,
    )
    signal = ctx["signals"]["competitors_selected"]
    assert signal["available"] is True
    # WebFX was AI-detected client-wide, but an explicit per-service pick is the
    # operator confirming it.
    assert [c["name"] for c in signal["data"]["competitors"]] == ["WebFX"]


async def test_a_service_with_no_competitors_generates_unchanged(db_session, api_client):
    """Acceptance criterion: existing services keep working with none selected."""
    from app.services.content_signals.context import build_content_context

    cid, h, _ids = await _setup(db_session)
    profile = (await api_client.get(f"/api/v1/clients/{cid}/profile", headers=h)).json()
    ctx = build_content_context(
        keyword="ppc management", page_type="service",
        commercial=profile["commercial_scope"], competitors=[],
    )
    assert ctx["signals"]["competitors_selected"]["available"] is False
    # Still a usable context — generation proceeds without competitor input.
    assert ctx["signals"]["funnel"]["available"] is True
    assert ctx["relevance"]["score"] >= 0


# --- Feature 2: the benchmark endpoint ------------------------------------


async def test_benchmark_defaults_to_the_services_selected_competitors(db_session, api_client):
    from app.services.content_benchmark import fetch as fetch_mod
    from app.services.content_benchmark.fetch import PageContent

    cid, h, ids = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["King Kong"]]}, headers=h,
    )

    seen: list[str] = []

    async def fake_live(competitor, *, service_terms):
        seen.append(competitor["name"])
        return PageContent(
            competitor_id=competitor["id"], name=competitor["name"],
            domain="kingkong.co", url="https://kingkong.co/seo/", source="live",
            title="SEO Services", h1s=["SEO Services"], h2s=["Link building"],
            body_text="We build links. " * 80, depth_available=True,
        )

    with patch.object(fetch_mod, "from_live_fetch", fake_live):
        resp = await api_client.post(
            f"/api/v1/clients/{cid}/services/seo/benchmark",
            json={"target_keywords": ["seo services", "link building"]}, headers=h,
        )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert seen == ["King Kong"], "only the service's selected competitor is benchmarked"
    assert report["service_name"] == "SEO Services"
    assert len(report["competitors"]) == 1
    assert report["competitors"][0]["dimensions"]


async def test_benchmark_with_no_selection_returns_a_clear_note(db_session, api_client):
    cid, h, _ids = await _setup(db_session)
    resp = await api_client.post(
        f"/api/v1/clients/{cid}/services/ppc/benchmark", json={}, headers=h
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["competitors"] == []
    assert any("No competitors are selected" in n for n in body["notes"])


async def test_benchmark_degrades_when_a_competitor_cannot_be_fetched(db_session, api_client):
    from app.services.content_benchmark import fetch as fetch_mod
    from app.services.content_benchmark.fetch import PageContent

    cid, h, ids = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["King Kong"], ids["WebFX"]]}, headers=h,
    )

    async def half(competitor, *, service_terms):
        out = PageContent(
            competitor_id=competitor["id"], name=competitor["name"], domain="x.com"
        )
        if competitor["name"] == "WebFX":
            out.error = "timeout"
            return out
        out.source = "live"
        out.title = "SEO Services"
        out.h2s = ["Link building"]
        out.body_text = "We build links. " * 80
        out.depth_available = True
        return out

    with patch.object(fetch_mod, "from_live_fetch", half):
        resp = await api_client.post(
            f"/api/v1/clients/{cid}/services/seo/benchmark",
            json={"target_keywords": ["seo services"]}, headers=h,
        )
    report = resp.json()
    rows = {r["name"]: r for r in report["competitors"]}
    assert rows["King Kong"]["ok"] is True
    assert rows["WebFX"]["ok"] is False
    assert report["overall"]["competitors_failed"] == 1
    assert report["overall"]["competitors_compared"] == 1


async def test_benchmark_accepts_pasted_competitor_content(db_session, api_client):
    cid, h, ids = await _setup(db_session)
    await api_client.put(
        f"/api/v1/clients/{cid}/services/seo/competitors",
        json={"competitor_ids": [ids["King Kong"]]}, headers=h,
    )
    resp = await api_client.post(
        f"/api/v1/clients/{cid}/services/seo/benchmark",
        json={
            "pasted": {ids["King Kong"]: "# SEO\n\n## Link building\n\n" + ("words " * 200)},
            "allow_live_fetch": False,
            "target_keywords": ["link building"],
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    row = report["competitors"][0]
    assert row["source"] == "pasted"
    assert row["content"]["depth_available"] is True
