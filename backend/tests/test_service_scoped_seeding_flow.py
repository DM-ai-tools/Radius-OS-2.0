"""run_search_demand fetches keywords per service, from that service's own seeds.

The real agent function is driven up to the keyword fetch. The fetcher is replaced by a
recorder that raises once it has seen its arguments, so the test observes exactly what
``run_search_demand`` hands to each per-service fetch.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.agents.search_demand import run_search_demand


class _Stop(Exception):
    """Raised by the recorder so the run ends right after the fetches we care about."""


def _client():
    return SimpleNamespace(
        id=uuid4(),
        display_name="Acme Digital",
        primary_url="https://acme-digital.example",
        industry="digital marketing agency",
    )


def _profile(*, scope=None):
    prioritization = {
        "confirmed_at": "2026-10-01T00:00:00+00:00",
        "selected_service_ids": ["seo", "meta ads"],
        "selected_subservice_ids": [],
        "service_catalog": [
            {
                "id": "seo",
                "name": "SEO",
                "source": "website",
                "subservices": [
                    {"id": "local seo", "name": "Local SEO", "source": "website"},
                    {"id": "link building", "name": "Link Building", "source": "industry"},
                ],
            },
            {
                "id": "meta ads",
                "name": "Meta Ads",
                "source": "website",
                "subservices": [
                    {"id": "instagram ads", "name": "Instagram Ads", "source": "website"},
                    {"id": "tiktok ads", "name": "TikTok Ads", "source": "industry"},
                ],
            },
        ],
        "service_scope": scope
        or {
            "seo": {"name": "SEO", "offered": ["Local SEO"], "excluded": ["Link Building"]},
            "meta ads": {"name": "Meta Ads", "offered": ["Instagram Ads"], "excluded": ["TikTok Ads"]},
        },
        "competitor_trees": [{"name": "Rival", "competitor_id": "rival", "services": []}],
    }
    return SimpleNamespace(
        discovery_status="complete",
        tracking_status="complete",
        website_status="complete",
        competitor_status="complete",
        search_demand_status="not_started",
        seo_strategy_status="not_started",
        site_architecture_status="not_started",
        technical_seo_status="not_started",
        content_audit_status="not_started",
        commercial_scope={
            "products": "SEO, Meta Ads",
            "business_keywords": "seo melbourne, meta ads agency, facebook ads cost",
            "service_prioritization": prioritization,
        },
        marketing_context={},
        competitive_landscape_summary={},
        website_situation_summary={},
        search_demand_summary={},
    )


async def _drive(profile, recorder):
    with (
        patch("app.agents.search_demand.get_profile", AsyncMock(return_value=profile)),
        patch("app.agents.search_demand.load_skill", return_value=""),
        patch("app.agents.search_demand.load_skill_file", return_value=""),
        patch("app.agents.search_demand._load_cdd_fields", AsyncMock(return_value={})),
        patch("app.agents.search_demand.resolve_competitors", AsyncMock(return_value=[])),
        patch(
            "app.agents.search_demand._harvest_competitor_page_keywords",
            AsyncMock(return_value={"keywords": [], "snapshots": [], "stats": {}}),
        ),
        patch("app.agents.search_demand.run_multi_mode_seeding", recorder),
        pytest.raises(_Stop),
    ):
        await run_search_demand(
            AsyncMock(),
            client=_client(),
            session_id=uuid4(),
            user_id=uuid4(),
            message="Run keyword research",
        )


def _recorder():
    calls: list[dict] = []

    async def record(seeds, *, seed_targets=None, **kw):
        calls.append({"seeds": list(seeds), "targets": dict(seed_targets or {}), "kw": kw})
        raise _Stop()

    return calls, record


async def test_search_demand_fetches_keywords_once_per_service_from_that_services_seeds():
    calls, record = _recorder()
    await _drive(_profile(), record)

    assert len(calls) == 2, "one keyword fetch per selected service, not one global fetch"
    by_service = {next(iter(c["targets"].values()))["service"]: c for c in calls}
    assert set(by_service) == {"SEO", "Meta Ads"}

    seo, meta = by_service["SEO"], by_service["Meta Ads"]
    seo_seeds, meta_seeds = {s.lower() for s in seo["seeds"]}, {s.lower() for s in meta["seeds"]}
    assert "seo" in seo_seeds and "meta ads" in meta_seeds
    assert not (seo_seeds & meta_seeds), "no seed is shared between the two services here"
    # each fetch holds only its own service's material
    assert "seo melbourne" not in seo_seeds
    assert "local seo" not in seo_seeds
    assert "meta ads agency" not in meta_seeds
    assert "facebook ads cost" not in meta_seeds
    assert "instagram ads" not in meta_seeds
    assert not ({"meta ads", "instagram ads", "meta ads agency", "facebook ads cost"} & seo_seeds)
    assert not ({"seo", "seo melbourne"} & meta_seeds)
    # sub-services the operator did not offer are never seeded
    assert "link building" not in seo_seeds and "tiktok ads" not in meta_seeds


async def test_every_seed_target_handed_to_a_fetch_belongs_to_that_fetchs_service():
    calls, record = _recorder()
    await _drive(_profile(), record)
    for call in calls:
        services = {t["service"] for t in call["targets"].values()}
        assert len(services) == 1, f"a single fetch mixed services: {services}"
        assert set(call["targets"]) == {s.lower() for s in call["seeds"]}


def _llm_with_synonyms(answers: dict[str, list[str]]):
    """Stands in for the synonym LLM call; answers per service named in the prompt."""
    asked: list[str] = []

    async def fake(system, user, **kw):
        service = next(
            line.split(": ", 1)[1] for line in user.splitlines() if line.startswith("Service: ")
        )
        asked.append(service)
        return json.dumps({"synonyms": answers.get(service, [])})

    return asked, fake


async def test_each_service_is_also_seeded_by_its_own_synonyms():
    asked, fake_llm = _llm_with_synonyms(
        {
            "SEO": ["search engine optimization", "organic search"],
            "Meta Ads": ["facebook advertising", "meta advertising"],
        }
    )
    calls, record = _recorder()
    with patch("app.services.seed_synonyms.generator.keyword_analysis_chat", fake_llm):
        await _drive(_profile(), record)

    assert sorted(asked) == ["Meta Ads", "SEO"], "one model call per service in the run"
    by_service = {next(iter(c["targets"].values()))["service"]: c for c in calls}
    seo, meta = by_service["SEO"], by_service["Meta Ads"]
    # right after the service's own seed, tagged with where they came from
    assert seo["seeds"][:3] == ["SEO", "search engine optimization", "organic search"]
    assert meta["seeds"][:3] == ["Meta Ads", "facebook advertising", "meta advertising"]
    for call, owner in ((seo, "SEO"), (meta, "Meta Ads")):
        tagged = [t for t in call["targets"].values() if t.get("seed_origin") == "synonym"]
        assert len(tagged) == 2
        assert {t["synonym_of"] for t in tagged} == {owner}
    # no cross-service mixing, and nothing the service had is displaced
    assert not {"facebook advertising", "meta advertising"} & {s.lower() for s in seo["seeds"]}
    assert not {"search engine optimization", "organic search"} & {s.lower() for s in meta["seeds"]}
    assert "seo melbourne" not in {s.lower() for s in seo["seeds"]}
    assert "local seo" not in {s.lower() for s in seo["seeds"]}
    assert "meta ads agency" not in {s.lower() for s in meta["seeds"]}
    assert "facebook ads cost" not in {s.lower() for s in meta["seeds"]}
    assert "instagram ads" not in {s.lower() for s in meta["seeds"]}


async def test_when_the_model_fails_each_service_is_seeded_without_synonyms():
    baseline_calls, baseline = _recorder()
    await _drive(_profile(), baseline)

    calls, record = _recorder()
    failing = AsyncMock(side_effect=RuntimeError("anthropic unavailable"))
    with patch("app.services.seed_synonyms.generator.keyword_analysis_chat", failing):
        await _drive(_profile(), record)

    assert failing.await_count == 2, "asked once per service, then carried on"
    seeds = {next(iter(c["targets"].values()))["service"]: c["seeds"] for c in calls}
    expected = {next(iter(c["targets"].values()))["service"]: c["seeds"] for c in baseline_calls}
    assert seeds == expected
    assert all(
        t.get("seed_origin") != "synonym" for c in calls for t in c["targets"].values()
    )


async def test_the_shared_seed_budget_is_split_so_neither_service_is_starved():
    calls, record = _recorder()
    await _drive(_profile(), record)
    assert all(len(c["seeds"]) >= 1 for c in calls)
    # the service root always leads its own fetch
    firsts = {next(iter(c["targets"].values()))["service"]: c["seeds"][0] for c in calls}
    assert firsts == {"SEO": "SEO", "Meta Ads": "Meta Ads"}


def _llm_with_subservices(answers: dict[str, list[str]]):
    """Stands in for the sub-service expansion call; records each question."""
    asked: list[str] = []
    questions: dict[str, str] = {}

    async def fake(system, user, **kw):
        service = next(
            line.split(": ", 1)[1] for line in user.splitlines() if line.startswith("Service: ")
        )
        asked.append(service)
        questions[service] = user
        return {"sub_services": answers.get(service, [])}

    return asked, questions, fake


async def test_sub_services_are_not_fetched_as_seeds():
    _, no_synonyms = _llm_with_synonyms({})
    calls, record = _recorder()
    with patch("app.services.seed_synonyms.generator.keyword_analysis_chat", no_synonyms):
        await _drive(_profile(), record)

    by_service = {next(iter(c["targets"].values()))["service"]: c for c in calls}
    seo_seeds = {s.lower() for s in by_service["SEO"]["seeds"]}
    meta_seeds = {s.lower() for s in by_service["Meta Ads"]["seeds"]}
    assert "seo" in seo_seeds and "seo melbourne" not in seo_seeds
    assert "local seo" not in seo_seeds and "link building" not in seo_seeds
    assert meta_seeds == {"meta ads"}
    assert "instagram ads" not in meta_seeds and "tiktok ads" not in meta_seeds
    for call in (by_service["SEO"], by_service["Meta Ads"]):
        assert not any(t.get("target_type") == "sub_service" for t in call["targets"].values())


async def test_when_the_expansion_model_fails_the_seeds_are_unchanged():
    _, no_synonyms = _llm_with_synonyms({})
    baseline_calls, baseline = _recorder()
    with patch("app.services.seed_synonyms.generator.keyword_analysis_chat", no_synonyms):
        await _drive(_profile(), baseline)

    calls, record = _recorder()
    failing = AsyncMock(side_effect=RuntimeError("anthropic unavailable"))
    with (
        patch("app.services.subservice_expansion.generator.synthesize_json", failing),
        patch("app.services.seed_synonyms.generator.keyword_analysis_chat", no_synonyms),
    ):
        await _drive(_profile(), record)

    assert failing.await_count == 0, "sub-services are not asked for as seeds"
    seeds = {next(iter(c["targets"].values()))["service"]: c["seeds"] for c in calls}
    expected = {next(iter(c["targets"].values()))["service"]: c["seeds"] for c in baseline_calls}
    assert seeds == expected
