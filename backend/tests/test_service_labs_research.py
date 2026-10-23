"""Per-service DataForSEO Labs keyword and competitor research."""

import pytest

from app.services.service_labs_research import (
    collect_competitor_keywords,
    isolated_seeds,
    partition_keywords_by_service,
    research_keywords_for_service,
    select_keywords_for_service,
)
from app.services.service_seeding import ServiceSeedPlan


def _plan(name: str, seeds: list[str]) -> ServiceSeedPlan:
    return ServiceSeedPlan(
        service=name,
        service_id=name.lower().replace(" ", "-"),
        seeds=list(seeds),
    )


SOLAR = ["solar installation", "residential solar"]
ROOF = ["commercial roofing", "industrial roofing"]


def test_isolated_seeds_drop_the_other_services_phrases():
    assert isolated_seeds(SOLAR, ROOF) == SOLAR
    assert isolated_seeds(ROOF, SOLAR) == ROOF
    # A phrase this service also listed stays. The other service's remaining seed does not.
    assert isolated_seeds(["solar installation", "commercial roofing"], ROOF) == [
        "solar installation",
        "commercial roofing",
    ]
    assert "industrial roofing" not in isolated_seeds(
        ["solar installation", "commercial roofing"], ROOF
    )


@pytest.mark.asyncio
async def test_keyword_research_sends_only_that_services_seeds(monkeypatch):
    seen: list[str] = []

    async def fake_expand(seed, **_kwargs):
        seen.append(seed)
        return [{"keyword": f"{seed} cost", "volume": 40, "difficulty": 22, "intent": "commercial"}], []

    monkeypatch.setattr(
        "app.services.keyword_seeding.expand_seed_dataforseo",
        fake_expand,
    )
    solar_rows, solar_errs = await research_keywords_for_service(
        SOLAR,
        location_code=2840,
        language_code="en",
        foreign_seeds=ROOF,
    )
    roof_rows, roof_errs = await research_keywords_for_service(
        ROOF,
        location_code=2840,
        language_code="en",
        foreign_seeds=SOLAR,
    )
    assert solar_errs == []
    assert roof_errs == []
    assert seen[:2] == SOLAR
    assert seen[2:] == ROOF
    assert {r["seed"] for r in solar_rows} == set(SOLAR)
    assert {r["seed"] for r in roof_rows} == set(ROOF)
    assert not ({r["keyword"] for r in solar_rows} & set(ROOF))


@pytest.mark.asyncio
async def test_competitor_research_parses_metrics_and_stays_on_the_service(monkeypatch):
    async def fake_competitors(domain, **kwargs):
        assert domain == "client.example"
        assert kwargs["location_code"] == 2036
        assert kwargs["language_code"] == "en"
        return [{"name": "Rival Solar", "url": "https://rival.example"}]

    async def fake_ranked(domain, **_kwargs):
        return [
            {
                "keyword": "residential solar panel installation",
                "volume": 90,
                "difficulty": 28,
                "cpc": 14.5,
                "intent": "commercial",
                "dataforseo_endpoint": "ranked_keywords",
            },
            {
                "keyword": "commercial roofing",
                "volume": 8000,
                "difficulty": 10,
                "cpc": 9.0,
                "intent": "commercial",
                "dataforseo_endpoint": "ranked_keywords",
            },
        ], []

    async def fake_site(_domain, **_kwargs):
        return [
            {
                "keyword": "industrial roofing contractors",
                "volume": 120,
                "difficulty": 30,
                "intent": "transactional",
                "dataforseo_endpoint": "keywords_for_site",
            }
        ], []

    monkeypatch.setattr("app.integrations.dataforseo.competitors_domain", fake_competitors)
    monkeypatch.setattr("app.integrations.dataforseo.ranked_keywords", fake_ranked)
    monkeypatch.setattr("app.integrations.dataforseo.keywords_for_site", fake_site)

    rows, errs = await collect_competitor_keywords(
        client_domain="https://www.client.example",
        competitor_domains=["known.example"],
        location_code=2036,
        language_code="en",
    )
    assert "competitor_research_unavailable" not in errs
    solar = _plan("Residential Solar Installation", SOLAR)
    roof = _plan("Commercial Roofing", ROOF)
    grouped = partition_keywords_by_service(rows, [solar, roof])
    solar_kws = {r["keyword"] for r in grouped[solar.service_id]}
    roof_kws = {r["keyword"] for r in grouped[roof.service_id]}
    assert "residential solar panel installation" in solar_kws
    assert "commercial roofing" not in solar_kws
    assert "industrial roofing contractors" not in solar_kws
    assert "commercial roofing" in roof_kws
    assert "industrial roofing contractors" in roof_kws
    assert "residential solar panel installation" not in roof_kws
    solar_row = next(
        r for r in grouped[solar.service_id] if r["keyword"] == "residential solar panel installation"
    )
    assert solar_row["volume"] == 90
    assert solar_row["difficulty"] == 28
    assert solar_row["cpc"] == 14.5
    assert solar_row["service"] == solar.service
    assert solar_row["competitor_domain"] in {"rival.example", "known.example"}


def test_relevant_low_volume_keyword_outranks_irrelevant_high_volume():
    kept, rejected = select_keywords_for_service(
        [
            {
                "keyword": "cheap roofing",
                "volume": 50000,
                "difficulty": 5,
                "intent": "commercial",
                "cpc": 8,
            },
            {
                "keyword": "residential solar panel installation",
                "volume": 30,
                "difficulty": 35,
                "intent": "commercial",
                "cpc": 11,
            },
            {
                "keyword": "solar installation quote",
                "volume": 400,
                "difficulty": 20,
                "intent": "transactional",
                "cpc": 18,
                "gap_flag": True,
            },
        ],
        service="Residential Solar Installation",
        service_id="residential-solar-installation",
        seeds=SOLAR,
        other_phrases=["Commercial Roofing", *ROOF],
    )
    assert kept[0]["keyword"] == "solar installation quote"
    assert "residential solar panel installation" in {r["keyword"] for r in kept}
    assert "cheap roofing" not in {r["keyword"] for r in kept}
    assert any(r["keyword"] == "cheap roofing" for r in rejected)
    assert kept[0]["labs_score"] >= kept[-1]["labs_score"]


def test_service_keywords_do_not_include_the_other_services_seeds():
    solar = _plan("Residential Solar Installation", SOLAR)
    roof = _plan("Commercial Roofing", ROOF)
    rows = [
        {"keyword": "commercial roofing", "volume": 4000, "intent": "commercial"},
        {"keyword": "industrial roofing", "volume": 900, "intent": "commercial"},
        {"keyword": "solar installation", "volume": 200, "intent": "commercial"},
        {"keyword": "residential solar", "volume": 80, "intent": "commercial"},
    ]
    grouped = partition_keywords_by_service(rows, [solar, roof])
    solar_kws = {r["keyword"] for r in grouped[solar.service_id]}
    roof_kws = {r["keyword"] for r in grouped[roof.service_id]}
    assert solar_kws.isdisjoint(set(ROOF))
    assert roof_kws.isdisjoint(set(SOLAR))
    assert solar_kws == {"solar installation", "residential solar"}
    assert roof_kws == {"commercial roofing", "industrial roofing"}


@pytest.mark.asyncio
async def test_competitor_research_failure_does_not_invent_keywords(monkeypatch):
    async def fake_competitors(_domain, **_kwargs):
        raise TimeoutError("labs timeout")

    async def fake_ranked(_domain, **_kwargs):
        raise TimeoutError("labs timeout")

    async def fake_site(_domain, **_kwargs):
        return [], ["dataforseo_keywords_for_site_failed"]

    monkeypatch.setattr("app.integrations.dataforseo.competitors_domain", fake_competitors)
    monkeypatch.setattr("app.integrations.dataforseo.ranked_keywords", fake_ranked)
    monkeypatch.setattr("app.integrations.dataforseo.keywords_for_site", fake_site)

    rows, errs = await collect_competitor_keywords(
        client_domain="client.example",
        competitor_domains=["known.example"],
        location_code=2840,
        language_code="en",
    )
    assert rows == []
    assert any("dataforseo_competitors_domain_error" in e for e in errs)
    assert any("known.example" in e for e in errs)

    empty, empty_errs = await collect_competitor_keywords(
        client_domain=None,
        competitor_domains=[],
        location_code=2840,
        language_code="en",
    )
    assert empty == []
    assert empty_errs == ["competitor_research_unavailable"]


@pytest.mark.asyncio
async def test_one_seed_failing_keeps_the_other_seeds_keywords(monkeypatch):
    async def fake_expand(seed, **_kwargs):
        if seed == "residential solar":
            raise RuntimeError("provider down")
        return [{"keyword": "solar installation cost", "volume": 50, "intent": "commercial"}], []

    monkeypatch.setattr(
        "app.services.keyword_seeding.expand_seed_dataforseo",
        fake_expand,
    )
    rows, errs = await research_keywords_for_service(
        SOLAR,
        location_code=2840,
        language_code="en",
        foreign_seeds=ROOF,
    )
    assert [r["keyword"] for r in rows] == ["solar installation cost"]
    assert any("RuntimeError" in e for e in errs)
