"""The keyword pool wired into seeding: ``expand_seed`` and ``run_multi_mode_seeding``.

The provider fetch (``expand_seed_dataforseo``) is replaced by a recorder, so each test
sees exactly when a seed is fetched and when a saved fetch is served instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.search_demand import _keyword_pool_notice, run_search_demand
from app.config import get_settings
from app.integrations import dataforseo
from app.services import keyword_seeding
from app.services.keyword_seeding import (
    dataforseo_rows,
    expand_seed,
    run_multi_mode_seeding,
)
from app.services.service_seeding import ServiceSeedPlan, run_service_scoped_seeding
from app.services.shared_keyword_pool import reuse
from app.services.shared_keyword_pool.reuse import PoolContext
from tests.test_service_scoped_seeding_flow import _client, _profile

LOCATION_CODE = 2036
LANGUAGE = "en"
CLASSES = ("phrase", "related", "broad")


class FakeProvider:
    """Stands in for ``expand_seed_dataforseo``: records each fetch, answers from a table."""

    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.calls: list[dict] = []

    async def __call__(
        self, seed, *, location_code, min_volume=10, limit=60, classes=None, language_code=None
    ):
        self.calls.append(
            {
                "seed": seed,
                "location_code": location_code,
                "min_volume": min_volume,
                "limit": limit,
                "classes": classes,
                "language_code": language_code,
            }
        )
        rows = dataforseo_rows(
            [
                {"keyword": f"{seed} audit", "volume": 300, "cpc": 2.0},
                {"keyword": f"best {seed} agency", "volume": 200, "cpc": 3.0},
            ],
            endpoint="keyword_suggestions",
            seed=seed,
        )
        return rows, list(self.errors)

    @property
    def seeds(self) -> list[str]:
        return [call["seed"] for call in self.calls]


@pytest.fixture
def live(monkeypatch) -> FakeProvider:
    """Live-provider mode (the default test mode is mock) with the provider fetch faked."""
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "dataforseo_sandbox", False)
    monkeypatch.setattr(settings, "keyword_pool_enabled", True)
    provider = FakeProvider()
    monkeypatch.setattr(keyword_seeding, "expand_seed_dataforseo", provider)
    return provider


@pytest.fixture
def overview(monkeypatch) -> AsyncMock:
    """``run_multi_mode_seeding`` measures every seed once after expanding; answer it with nothing."""
    fake = AsyncMock(return_value=([], []))
    monkeypatch.setattr(dataforseo, "search_volume", fake)
    return fake


def _keywords(rows: list[dict]) -> set[str]:
    return {row["keyword"] for row in rows}


def _outcomes(pool: PoolContext, service: str) -> dict[str, int]:
    return pool.report.as_dict()["services"][service]["outcomes"]


async def _expand(seed: str, *, pool: PoolContext | None, **kw):
    return await expand_seed(
        seed,
        country="au",
        min_volume=10,
        limit_per_mode=40,
        location_code=LOCATION_CODE,
        language_code=LANGUAGE,
        service="SEO",
        pool=pool,
        **kw,
    )


async def test_expand_seed_uses_the_pool_when_given_one(db_session, live):
    first = PoolContext()
    first_rows, _ = await _expand("seo", pool=first)
    assert live.seeds == ["seo"]
    assert _outcomes(first, "SEO") == {"fetched": 1}

    # A later client with a fresh context and the same seed fetches nothing.
    second = PoolContext()
    second_rows, second_errors = await _expand("seo", pool=second)
    assert live.seeds == ["seo"], "the saved fetch must be served, not fetched again"
    assert _outcomes(second, "SEO") == {"reused": 1}
    assert second.report.as_dict()["totals"]["calls_avoided"] > 0
    assert "provider_expansion_unavailable" not in second_errors

    assert _keywords(second_rows) == _keywords(first_rows) == {"seo audit", "best seo agency"}
    assert all(row["seed"] == "seo" and row["source"] == "dataforseo" for row in second_rows)


async def test_expand_seed_is_unchanged_without_a_pool(db_session, live, monkeypatch):
    load, save = AsyncMock(), AsyncMock()
    monkeypatch.setattr(reuse, "load_seed", load)
    monkeypatch.setattr(reuse, "save_seed", save)

    rows, errors = await _expand("seo", pool=None)
    again, _ = await _expand("seo", pool=None)

    # No pool: the provider is called every time, with exactly the arguments it always got.
    assert live.calls == [
        {
            "seed": "seo",
            "location_code": LOCATION_CODE,
            "min_volume": 10,
            "limit": 40,
            "classes": CLASSES,
            "language_code": LANGUAGE,
        }
    ] * 2
    load.assert_not_awaited()
    save.assert_not_awaited()
    assert _keywords(rows) == _keywords(again) == {"seo audit", "best seo agency"}
    assert errors == []


async def test_mock_provider_mode_never_touches_the_pool(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", True)
    load, save = AsyncMock(), AsyncMock()
    monkeypatch.setattr(reuse, "load_seed", load)
    monkeypatch.setattr(reuse, "save_seed", save)
    provider = FakeProvider()
    monkeypatch.setattr(keyword_seeding, "expand_seed_dataforseo", provider)

    pool = PoolContext()
    rows, _ = await _expand("seo", pool=pool)

    assert rows, "mock mode still returns its canned expansion"
    assert provider.calls == []
    load.assert_not_awaited()
    save.assert_not_awaited()
    assert pool.report.records == []


async def test_both_fetch_branches_go_through_the_pool(db_session, live, overview, monkeypatch):
    """The extra-idea-pulls branch is pooled too; only the client's own variant pulls are not."""
    monkeypatch.setattr(get_settings(), "keyword_extra_idea_pulls", True)
    ideas = AsyncMock(return_value=([], []))
    monkeypatch.setattr(dataforseo, "keyword_ideas", ideas)

    async def variants() -> list[str]:
        return ["how does seo work"]

    first = PoolContext()
    await _expand("seo", pool=first, variants=variants)
    pulls_in_first_run = ideas.await_count
    assert live.seeds == ["seo"] and pulls_in_first_run >= 1
    assert _outcomes(first, "SEO") == {"fetched": 1}

    second = PoolContext()
    await _expand("seo", pool=second, variants=variants)
    assert live.seeds == ["seo"], "the seed's main fetch is reused on this branch too"
    assert _outcomes(second, "SEO") == {"reused": 1}
    # The client's own variant pulls depend on its filters, so they run again.
    assert ideas.await_count == 2 * pulls_in_first_run


async def test_run_multi_mode_seeding_reports_reuse(db_session, live, overview):
    await _expand("seo", pool=PoolContext())  # an earlier client saved "seo"
    assert live.seeds == ["seo"]

    pool = PoolContext()
    result = await run_multi_mode_seeding(
        ["seo", "ppc"],
        country="au",
        location_code=LOCATION_CODE,
        language_code=LANGUAGE,
        seed_targets={
            "seo": {"target": "seo", "target_type": "keyword", "service": "SEO"},
            "ppc": {"target": "ppc", "target_type": "keyword", "service": "SEO"},
        },
        pool=pool,
    )

    assert live.seeds == ["seo", "ppc"], "only the seed nobody saved is fetched"
    assert _outcomes(pool, "SEO") == {"reused": 1, "fetched": 1}
    assert {r["seed"] for r in result["keyword_dataset"]} == {"seo", "ppc"}
    assert result["provider_expansion_unavailable"] is False


async def test_run_multi_mode_seeding_without_a_pool_fetches_every_seed(db_session, live, overview):
    await _expand("seo", pool=PoolContext())
    result = await run_multi_mode_seeding(
        ["seo", "ppc"], country="au", location_code=LOCATION_CODE, language_code=LANGUAGE
    )
    assert live.seeds == ["seo", "seo", "ppc"]
    assert {r["seed"] for r in result["keyword_dataset"]} == {"seo", "ppc"}


def _plan(service: str, seeds: list[str]) -> ServiceSeedPlan:
    return ServiceSeedPlan(
        service=service,
        service_id=service.lower(),
        seeds=list(seeds),
        seed_targets={
            s.lower(): {
                "target": s,
                "target_type": "keyword",
                "service": service,
                "service_id": service.lower(),
            }
            for s in seeds
        },
    )


async def test_the_place_lookup_error_does_not_fail_a_service_or_flag_an_outage(
    db_session, live, overview, monkeypatch
):
    await _expand("seo", pool=PoolContext())  # saved by an earlier client

    # The client's place lookup (the only search_volume call that names a language) fails.
    async def lookup(keywords, *, location_code, language_code=None):
        return [], (["rate_limited"] if language_code else [])

    monkeypatch.setattr(dataforseo, "search_volume", lookup)

    pool = PoolContext(places=("melbourne",))
    result = await run_service_scoped_seeding(
        [_plan("SEO", ["seo"])],
        fetch=run_multi_mode_seeding,
        total_cap=10,
        country="au",
        location_code=LOCATION_CODE,
        language_code=LANGUAGE,
        pool=pool,
    )

    assert live.seeds == ["seo"], "the seed itself was served from the pool"
    assert _outcomes(pool, "SEO") == {"reused": 1}
    assert "keyword_pool_place_topup_error" in result["provider_errors"]
    assert not [e for e in result["provider_errors"] if e.startswith("service_seeding_failed")]
    assert result["provider_expansion_unavailable"] is False
    assert result["provider_fallback_used"] is False
    assert [run["status"] for run in result["service_runs"]] == ["ok"]
    assert _keywords(result["keyword_dataset"]) >= {"seo audit", "best seo agency"}


async def test_the_pool_reaches_every_service_fetch():
    seen: list[PoolContext | None] = []

    async def fetch(seeds, *, seed_targets=None, **kw):
        seen.append(kw.get("pool"))
        return {"seeds": list(seeds), "seed_clusters": [], "keyword_dataset": [], "provider_errors": []}

    pool = PoolContext()
    await run_service_scoped_seeding(
        [_plan("SEO", ["seo"]), _plan("PPC", ["ppc"])], fetch=fetch, total_cap=10, pool=pool
    )
    assert seen == [pool, pool]



# --- the Search Demand phase ---------------------------------------------------------


def _canned_result(seeds: list[str]) -> dict:
    """What a per-service fetch returns, with one keyword per seed."""
    clusters, dataset = [], []
    for seed in seeds:
        row = {"keyword": f"{seed} cost", "seed": seed, "match_class": "exact", "volume": 100}
        clusters.append(
            {
                "seed": seed,
                "target": seed,
                "target_type": "keyword",
                "exact": [dict(row)],
                "phrase": [],
                "related": [],
                "broad": [],
                "keyword_count": 1,
            }
        )
        dataset.append({**row, "target": seed, "target_type": "keyword"})
    return {
        "seeds": list(seeds),
        "seed_clusters": clusters,
        "keyword_dataset": dataset,
        "keyword_count": len(dataset),
        "class_counts": {"exact": len(dataset), "phrase": 0, "related": 0, "broad": 0},
        "target_type_counts": {"keyword": len(seeds)},
        "class_coverage": [],
        "seeds_with_all_classes": 0,
        "provider_errors": [],
        "providers": ["dataforseo"],
        "excluded_keywords": [],
        "keyword_cleaning": {},
    }


async def _run_to_the_end(fetch, profile=None):
    """Run the whole phase with ``fetch`` as the per-service keyword fetch."""
    profile = profile or _profile()
    profile.id = uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
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
        patch("app.agents.search_demand.run_multi_mode_seeding", fetch),
    ):
        events = await run_search_demand(
            db,
            client=_client(),
            session_id=uuid4(),
            user_id=uuid4(),
            message="Run keyword research",
        )
    return profile.search_demand_summary, events


async def test_search_demand_summary_carries_keyword_pool():
    async def fetch(seeds, *, seed_targets=None, pool=None, **kw):
        service = next(iter(seed_targets.values()))["service"]
        pool.report.record(service, seeds[0], "reused", 12, age_days=9, calls_avoided=3)
        return _canned_result(seeds)

    summary, events = await _run_to_the_end(fetch)

    report = summary["keyword_seeding"]["keyword_pool"]
    # The industry layer's seeds go through the pool too, recorded under the industry name.
    assert set(report["services"]) == {"SEO", "Meta Ads", "digital marketing agency"}
    assert report["services"]["SEO"] == {
        "seeds": 1,
        "outcomes": {"reused": 1},
        "rows": 12,
        "calls_avoided": 3,
        "place_topup_rows": 0,
        "oldest_age_days": 9,
    }
    assert report["totals"]["seeds"] == 3 and report["totals"]["calls_avoided"] == 9

    # The card the app shows carries it too, and the operator is told in one plain line.
    card = next(e["payload"] for e in events if e.get("type") == "structured_card")
    assert card["keyword_seeding"]["keyword_pool"] == report
    notices = [
        e["content"]
        for e in events
        if e.get("type") == "system_notice" and "saved keywords" in e.get("content", "")
    ]
    assert notices == ["Reused saved keywords for 3 of 3 seed(s); fetched 0 (9 provider calls avoided)."]


async def test_search_demand_says_nothing_about_the_pool_when_it_did_nothing():
    async def fetch(seeds, *, seed_targets=None, pool=None, **kw):
        return _canned_result(seeds)

    summary, events = await _run_to_the_end(fetch)

    report = summary["keyword_seeding"]["keyword_pool"]
    assert report["services"] == {} and report["totals"]["seeds"] == 0
    assert not [e for e in events if "saved keywords" in str(e.get("content", ""))]


def _pool_spy():
    """A per-service fetch that only remembers the pool it was handed."""
    seen: list[dict] = []

    async def fetch(seeds, *, seed_targets=None, pool=None, **kw):
        seen.append({"pool": pool, "location_code": kw.get("location_code")})
        return _canned_result(seeds)

    return seen, fetch


async def test_search_demand_builds_the_pool_from_the_clients_geography():
    profile = _profile()
    profile.commercial_scope["geographic_focus"] = "Melbourne, Victoria"
    seen, fetch = _pool_spy()
    await _run_to_the_end(fetch, profile)

    assert len(seen) == 3, "one fetch per service, plus the industry layer's"
    pool = seen[0]["pool"]
    assert isinstance(pool, PoolContext)
    assert pool.enabled is True
    assert "melbourne" in pool.places
    assert all(call["pool"] is pool for call in seen), "every service shares one pool"
    assert seen[0]["location_code"] == 2036, "the pool key is the country-level code"


async def test_search_demand_pool_follows_the_switch(monkeypatch):
    monkeypatch.setattr(get_settings(), "keyword_pool_enabled", False)
    seen, fetch = _pool_spy()
    await _run_to_the_end(fetch)
    assert seen[0]["pool"].enabled is False


def test_the_pool_notice_is_plain_and_has_no_money_in_it():
    assert _keyword_pool_notice({}) is None
    assert _keyword_pool_notice({"services": {}, "totals": {"seeds": 0, "outcomes": {}}}) is None

    totals = {
        "seeds": 5,
        "outcomes": {"reused": 2, "reused_empty": 1, "fetched": 2},
        "calls_avoided": 9,
        "place_topup_rows": 4,
    }
    assert _keyword_pool_notice({"totals": totals}) == (
        "Reused saved keywords for 3 of 5 seed(s); fetched 2 (9 provider calls avoided); "
        "added 4 place keyword(s) for this client's own areas."
    )

    one = {"seeds": 1, "outcomes": {"reused": 1}, "calls_avoided": 1}
    assert _keyword_pool_notice({"totals": one}) == (
        "Reused saved keywords for 1 of 1 seed(s); fetched 0 (1 provider call avoided)."
    )

    first_client = {"seeds": 3, "outcomes": {"fetched": 2, "pool_error": 1}, "calls_avoided": 0}
    line = _keyword_pool_notice({"totals": first_client})
    assert line == (
        "Reused saved keywords for 0 of 3 seed(s); fetched 3; "
        "saved keywords were unavailable for 1 seed(s), which were fetched directly."
    )
    assert "$" not in line
