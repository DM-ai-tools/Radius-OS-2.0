"""Keyword pool reuse wrapper: reuse saved seed fetches and top up the client's own places."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import AsyncSessionLocal, engine
from app.integrations import dataforseo
from app.models import KeywordPoolEntry, KeywordPoolFetch
from app.services.competitive_context import resolve_geographic_focus
from app.services.keyword_seeding import MATCH_CLASSES, build_seed_clusters, dataforseo_rows
from app.services.shared_keyword_pool import reuse
from app.services.shared_keyword_pool.location import (
    client_places,
    is_country_name,
    is_region_name,
)
from app.services.shared_keyword_pool.reuse import PoolContext, PoolReport, pooled_seed_fetch

LOCATION_CODE = 2036
LANGUAGE = "en"
CLASSES = ("phrase", "related", "broad")


def _provider_rows(seed: str, *keywords: str, volume: int | None = 100) -> list[dict]:
    """Rows shaped like the real fetch returns them: stamped with the seed and a match class."""
    return dataforseo_rows(
        [{"keyword": keyword, "volume": volume, "cpc": 2.0} for keyword in keywords],
        endpoint="keyword_suggestions",
        seed=seed,
    )


class FakeFetch:
    """Stands in for ``expand_seed_dataforseo``: counts calls, returns canned rows per seed."""

    def __init__(self, rows_by_seed: dict[str, tuple[str, ...]] | None = None, errors=None):
        self.rows_by_seed = rows_by_seed or {}
        self.errors = list(errors or [])
        self.calls: list[str] = []

    async def __call__(self, seed, *, location_code, min_volume, limit, classes, language_code):
        self.calls.append(seed)
        keywords = self.rows_by_seed.get(seed, (seed, f"{seed} audit", f"best {seed} agency"))
        return _provider_rows(seed, *keywords), list(self.errors)


class FakeVolumeLookup:
    """Stands in for ``dataforseo.search_volume``: records the lookups, answers from a table."""

    def __init__(self, volumes: dict[str, int | None] | None = None, errors=None, boom=False):
        self.volumes = volumes or {}
        self.errors = list(errors or [])
        self.boom = boom
        self.calls: list[dict] = []

    async def __call__(self, keywords, *, location_code, language_code):
        self.calls.append(
            {"keywords": list(keywords), "location_code": location_code, "language": language_code}
        )
        if self.boom:
            raise RuntimeError("provider down")
        rows = [
            {"keyword": keyword, "volume": self.volumes[keyword], "cpc": 3.0, "source": "dataforseo"}
            for keyword in keywords
            if keyword in self.volumes
        ]
        return rows, list(self.errors)


@pytest.fixture
def lookup(monkeypatch) -> FakeVolumeLookup:
    fake = FakeVolumeLookup()
    monkeypatch.setattr(dataforseo, "search_volume", fake)
    return fake


async def _run(seed, *, pool, fetch, service=None, language_code=LANGUAGE, classes=CLASSES):
    return await pooled_seed_fetch(
        seed,
        pool=pool,
        service=service,
        fetch=fetch,
        location_code=LOCATION_CODE,
        language_code=language_code,
        min_volume=10,
        limit=60,
        classes=classes,
    )


async def _stored_keywords() -> set[str]:
    async with AsyncSessionLocal() as session:
        return set((await session.execute(select(KeywordPoolEntry.keyword_norm))).scalars())


async def _fetch_count() -> int:
    async with AsyncSessionLocal() as session:
        return len((await session.execute(select(KeywordPoolFetch.id))).scalars().all())


def _keywords(rows: list[dict]) -> set[str]:
    return {row["keyword"] for row in rows}


def _outcomes(pool: PoolContext, service: str = "unassigned") -> dict[str, int]:
    return pool.report.as_dict()["services"][service]["outcomes"]


async def test_second_client_reuses_shared_seeds_and_fetches_only_new_ones(db_session, lookup):
    first_fetch = FakeFetch()
    first = PoolContext()
    for seed in ("seo", "ppc", "cro"):
        await _run(seed, pool=first, fetch=first_fetch)
    assert first_fetch.calls == ["seo", "ppc", "cro"]
    assert _outcomes(first) == {"fetched": 3}

    second_fetch = FakeFetch()
    second = PoolContext()
    results = {}
    for seed in ("seo", "ppc", "email marketing"):
        results[seed], _ = await _run(seed, pool=second, fetch=second_fetch)

    assert second_fetch.calls == ["email marketing"]
    assert _keywords(results["seo"]) == {"seo", "seo audit", "best seo agency"}
    assert _outcomes(second) == {"reused": 2, "fetched": 1}
    # Nothing here has places, so no volume lookup is made for either client.
    assert lookup.calls == []


async def test_a_place_keyword_from_client_a_never_reaches_client_b(db_session, lookup):
    fetch_a = FakeFetch({"seo": ("seo", "seo audit", "seo melbourne")})
    pool_a = PoolContext(places=("melbourne",))
    rows_a, _ = await _run("seo", pool=pool_a, fetch=fetch_a)

    # The client that fetched keeps every row it was given, place keywords included.
    assert "seo melbourne" in _keywords(rows_a)
    assert "seo melbourne" not in await _stored_keywords()

    fetch_b = FakeFetch()
    rows_b, _ = await _run("seo", pool=PoolContext(places=("perth",)), fetch=fetch_b)

    assert fetch_b.calls == []
    assert "seo audit" in _keywords(rows_b)
    assert not any("melbourne" in keyword for keyword in _keywords(rows_b))


async def test_reused_rows_carry_the_requesting_seed(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())

    fetch_b = FakeFetch()
    rows, errors = await _run("seo services", pool=PoolContext(), fetch=fetch_b)

    assert fetch_b.calls == []
    assert errors == []
    assert rows
    assert {row["seed"] for row in rows} == {"seo services"}
    assert {row["parent_topic"] for row in rows} == {"seo services"}
    assert all(row["match_class"] in MATCH_CLASSES for row in rows)
    # Funnel, intent and the measured fields come back with the rows.
    assert all(row["intent"] and row["funnel"] for row in rows)
    assert {row["volume"] for row in rows} == {100}

    clusters = build_seed_clusters(rows, ["seo services"])
    assert len(clusters) == 1
    assert clusters[0]["seed"] == "seo services"
    assert clusters[0]["keyword_count"] == len(rows)
    clustered = {
        entry["keyword"] for name in MATCH_CLASSES for entry in clusters[0][name]
    }
    assert clustered == _keywords(rows)


async def test_the_place_topup_looks_up_seed_plus_place_and_keeps_only_measured(
    db_session, lookup
):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    lookup.volumes = {"seo perth": 50, "seo fremantle": None}

    pool = PoolContext(places=("perth", "fremantle"))
    fetch_b = FakeFetch()
    rows, errors = await _run("seo", pool=pool, fetch=fetch_b)

    assert fetch_b.calls == []
    assert errors == []
    assert lookup.calls == [
        {
            "keywords": ["seo perth", "seo fremantle"],
            "location_code": LOCATION_CODE,
            "language": LANGUAGE,
        }
    ]
    topup = [row for row in rows if row.get("place_topup")]
    assert [row["keyword"] for row in topup] == ["seo perth"]
    row = topup[0]
    assert row["volume"] == 50
    assert row["source"] == "dataforseo"
    assert row["seed"] == "seo"
    assert row["match_class"] in MATCH_CLASSES
    assert row["intent"] and row["funnel"]
    assert not any(r.get("place_topup") for r in rows if r["keyword"] != "seo perth")

    # Belongs to that client only: not in the pool, not served to the next client.
    assert "seo perth" not in await _stored_keywords()
    rows_c, _ = await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    assert "seo perth" not in _keywords(rows_c)
    assert pool.report.as_dict()["services"]["unassigned"]["place_topup_rows"] == 1


async def test_a_topup_below_ten_searches_is_dropped(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    lookup.volumes = {"seo perth": 9, "seo fremantle": 10}

    rows, _ = await _run("seo", pool=PoolContext(places=("perth", "fremantle")), fetch=FakeFetch())

    assert [row["keyword"] for row in rows if row.get("place_topup")] == ["seo fremantle"]


async def test_no_places_means_no_topup_call(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())

    rows, _ = await _run("seo", pool=PoolContext(places=()), fetch=FakeFetch())

    assert rows
    assert lookup.calls == []
    assert not any(row.get("place_topup") for row in rows)


async def test_a_fresh_fetch_makes_no_topup_call(db_session, lookup):
    # The fetch already brought the provider's own place rows; the client keeps them.
    rows, _ = await _run("seo", pool=PoolContext(places=("perth",)), fetch=FakeFetch())

    assert rows
    assert lookup.calls == []


async def test_a_failed_topup_lookup_does_not_fail_the_reuse(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    lookup.boom = True

    rows, errors = await _run("seo", pool=PoolContext(places=("perth",)), fetch=FakeFetch())

    assert _keywords(rows) == {"seo", "seo audit", "best seo agency"}
    assert errors == ["keyword_pool_place_topup_error"]


async def test_lookup_errors_are_passed_on_but_measured_rows_are_kept(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    lookup.volumes = {"seo perth": 80}
    lookup.errors = ["dataforseo_volume_failed"]

    rows, errors = await _run("seo", pool=PoolContext(places=("perth",)), fetch=FakeFetch())

    assert "seo perth" in _keywords(rows)
    assert errors == ["keyword_pool_place_topup_error"]


async def test_the_topup_skips_country_names_and_tries_the_clients_places(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    # An unmapped focus: the resolver falls back to the country as the location name.
    geo = resolve_geographic_focus({"geographic_focus": "Texas"})
    assert client_places(geo) == ["united states", "texas"]

    await _run("seo", pool=PoolContext(places=tuple(client_places(geo))), fetch=FakeFetch())

    assert lookup.calls[0]["keywords"] == ["seo texas"]


async def test_the_topup_tries_focus_places_before_the_state(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    geo = resolve_geographic_focus({"geographic_focus": "Sydney, Bondi"})
    assert client_places(geo) == ["sydney", "new south wales", "australia", "bondi"]

    await _run("seo", pool=PoolContext(places=tuple(client_places(geo))), fetch=FakeFetch())

    assert lookup.calls[0]["keywords"] == ["seo sydney", "seo bondi", "seo new south wales"]


async def test_a_country_never_spends_one_of_the_place_slots(db_session, lookup, monkeypatch):
    monkeypatch.setattr(get_settings(), "keyword_pool_place_cap", 2)
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())

    places = ("united states", "texas", "dallas", "plano")
    await _run("seo", pool=PoolContext(places=places), fetch=FakeFetch())

    assert lookup.calls[0]["keywords"] == ["seo texas", "seo dallas"]


async def test_the_topup_is_capped_by_the_setting(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    places = tuple(f"suburb{n}" for n in range(8))

    await _run("seo", pool=PoolContext(places=places), fetch=FakeFetch())

    assert get_settings().keyword_pool_place_cap == 5
    assert lookup.calls[0]["keywords"] == [f"seo suburb{n}" for n in range(5)]


async def test_a_clients_only_places_being_countries_means_no_topup_call(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())

    await _run("seo", pool=PoolContext(places=("australia",)), fetch=FakeFetch())

    assert lookup.calls == []


async def test_a_pool_failure_falls_back_to_a_normal_fetch(db_session, lookup, monkeypatch):
    async def broken_load(*args, **kwargs):
        raise RuntimeError("pool unavailable")

    monkeypatch.setattr(reuse, "load_seed", broken_load)
    fetch = FakeFetch()
    pool = PoolContext()

    rows, errors = await _run("seo", pool=pool, fetch=fetch)

    expected, _ = await FakeFetch()(
        "seo", location_code=1, min_volume=1, limit=1, classes=CLASSES, language_code="en"
    )
    assert rows == expected
    assert errors == []
    assert fetch.calls == ["seo"]
    assert _outcomes(pool) == {"pool_error": 1}


async def test_a_save_failure_keeps_the_fetched_rows(db_session, lookup, monkeypatch):
    async def broken_save(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(reuse, "save_seed", broken_save)
    fetch = FakeFetch()
    pool = PoolContext()

    rows, errors = await _run("seo", pool=pool, fetch=fetch)

    assert _keywords(rows) == {"seo", "seo audit", "best seo agency"}
    assert errors == []
    assert fetch.calls == ["seo"]
    assert _outcomes(pool) == {"pool_error": 1}


@pytest.mark.parametrize(
    "target",
    [
        "app.services.keyword_seeding.dataforseo_rows",
        "app.services.shared_keyword_pool.reuse._retarget",
        "app.services.shared_keyword_pool.reuse.seed_key",
    ],
)
async def test_a_failure_anywhere_in_the_pool_path_falls_back_to_a_plain_fetch(
    db_session, lookup, monkeypatch, target
):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    lookup.volumes = {"seo perth": 50}

    def broken(*args, **kwargs):
        raise ValueError("unexpected row")

    monkeypatch.setattr(target, broken)
    fetch = FakeFetch()
    pool = PoolContext(places=("perth",))

    rows, errors = await _run("seo", pool=pool, fetch=fetch)

    assert fetch.calls == ["seo"]
    assert _keywords(rows) == {"seo", "seo audit", "best seo agency"}
    assert not any(row.get("place_topup") for row in rows)
    assert errors == []
    assert _outcomes(pool) == {"pool_error": 1}


async def test_a_provider_failure_is_not_retried_or_hidden(db_session, lookup):
    class Boom(FakeFetch):
        async def __call__(self, seed, **kwargs):
            self.calls.append(seed)
            raise RuntimeError("provider down")

    fetch = Boom()

    with pytest.raises(RuntimeError, match="provider down"):
        await _run("seo", pool=PoolContext(), fetch=fetch)

    assert fetch.calls == ["seo"]


async def test_sandbox_data_is_never_saved_or_reused(db_session, lookup, monkeypatch):
    # The sandbox host answers with fake data and no errors, so it would be saved as complete.
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    monkeypatch.setattr(get_settings(), "dataforseo_sandbox", True)

    async def forbidden(*args, **kwargs):
        raise AssertionError("the store must not be used")

    monkeypatch.setattr(reuse, "load_seed", forbidden)
    monkeypatch.setattr(reuse, "save_seed", forbidden)
    fetch = FakeFetch({"ppc": ("ppc sandbox",)})
    pool = PoolContext(places=("perth",))

    seo_rows, _ = await _run("seo", pool=pool, fetch=fetch)
    ppc_rows, _ = await _run("ppc", pool=pool, fetch=fetch)

    assert fetch.calls == ["seo", "ppc"]
    assert _keywords(ppc_rows) == {"ppc sandbox"}
    assert lookup.calls == []
    assert pool.report.as_dict()["totals"]["seeds"] == 0
    # Only the real fetch made before the switch is in the pool.
    assert await _fetch_count() == 1
    assert "ppc sandbox" not in await _stored_keywords()


async def test_a_missing_table_falls_back_to_a_normal_fetch(db_session, lookup):
    async with engine.begin() as conn:
        await conn.run_sync(KeywordPoolEntry.__table__.drop)
        await conn.run_sync(KeywordPoolFetch.__table__.drop)
    fetch = FakeFetch()
    pool = PoolContext(places=("perth",))

    rows, errors = await _run("seo", pool=pool, fetch=fetch)

    assert _keywords(rows) == {"seo", "seo audit", "best seo agency"}
    assert errors == []
    assert fetch.calls == ["seo"]
    assert _outcomes(pool) == {"pool_error": 1}


async def test_disabled_pool_never_touches_the_store(db_session, lookup, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("the store must not be used")

    monkeypatch.setattr(reuse, "load_seed", forbidden)
    monkeypatch.setattr(reuse, "save_seed", forbidden)
    fetch = FakeFetch()
    pool = PoolContext(enabled=False, places=("perth",))

    rows, _ = await _run("seo", pool=pool, fetch=fetch)

    assert _keywords(rows) == {"seo", "seo audit", "best seo agency"}
    assert fetch.calls == ["seo"]
    assert lookup.calls == []
    assert await _fetch_count() == 0
    assert pool.report.as_dict()["totals"]["seeds"] == 0


@pytest.mark.parametrize("language", [None, ""])
async def test_a_missing_language_fetches_without_the_pool(db_session, lookup, language):
    fetch = FakeFetch()
    pool = PoolContext()

    rows, _ = await _run("seo", pool=pool, fetch=fetch, language_code=language)

    assert rows
    assert fetch.calls == ["seo"]
    assert await _fetch_count() == 0
    assert pool.report.as_dict()["totals"]["seeds"] == 0


async def test_a_partial_fetch_is_returned_but_never_reused(db_session, lookup):
    flaky = FakeFetch(errors=["dataforseo_related_keywords_all_intents_error"])
    pool = PoolContext()

    rows, errors = await _run("seo", pool=pool, fetch=flaky)

    assert rows
    assert errors == ["dataforseo_related_keywords_all_intents_error"]
    assert _outcomes(pool) == {"fetched_not_saved": 1}

    again = FakeFetch()
    await _run("seo", pool=PoolContext(), fetch=again)
    assert again.calls == ["seo"]


async def test_an_empty_answer_is_saved_and_reused_as_empty(db_session, lookup):
    nothing = FakeFetch({"obscure thing": ()})
    first = PoolContext()
    rows, _ = await _run("obscure thing", pool=first, fetch=nothing)
    assert rows == []
    assert _outcomes(first) == {"fetched": 1}

    again = FakeFetch()
    second = PoolContext()
    rows, errors = await _run("obscure thing", pool=second, fetch=again)

    assert rows == [] and errors == []
    assert again.calls == []
    assert _outcomes(second) == {"reused_empty": 1}


async def test_a_seed_that_names_a_place_is_fetched_not_pooled(db_session, lookup):
    fetch = FakeFetch()
    pool = PoolContext(places=("bondi",))

    await _run("dentist bondi", pool=pool, fetch=fetch)
    await _run("seo melbourne", pool=PoolContext(), fetch=fetch)

    assert fetch.calls == ["dentist bondi", "seo melbourne"]
    assert await _fetch_count() == 0
    assert _outcomes(pool) == {"not_pooled": 1}
    assert lookup.calls == []


async def test_a_fetch_for_only_some_match_classes_is_never_pooled(db_session, lookup):
    narrow = FakeFetch()
    pool = PoolContext()
    await _run("seo", pool=pool, fetch=narrow, classes=("phrase",))
    await _run("seo", pool=pool, fetch=narrow, classes=("phrase",))

    assert narrow.calls == ["seo", "seo"]
    assert await _fetch_count() == 0
    assert _outcomes(pool) == {"not_pooled": 2}


async def test_the_saved_fetch_holds_the_seed_and_industry_only(db_session, lookup):
    pool = PoolContext(places=("perth",), industry_key="marketing")
    await _run("seo", pool=pool, fetch=FakeFetch())

    async with AsyncSessionLocal() as session:
        fetch_row = (await session.execute(select(KeywordPoolFetch))).scalar_one()
    assert fetch_row.seed_text == "seo"
    assert fetch_row.industry_key == "marketing"
    assert fetch_row.location_code == LOCATION_CODE
    assert fetch_row.language_code == LANGUAGE


async def test_the_report_counts_reused_and_fetched_per_service(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    await _run("ppc", pool=PoolContext(), fetch=FakeFetch())

    pool = PoolContext()
    fetch = FakeFetch()
    await _run("seo", pool=pool, fetch=fetch, service="SEO")
    await _run("link building", pool=pool, fetch=fetch, service="SEO")
    await _run("ppc", pool=pool, fetch=fetch, service="PPC")

    summary = pool.report.as_dict()
    assert fetch.calls == ["link building"]
    seo, ppc = summary["services"]["SEO"], summary["services"]["PPC"]
    assert seo["seeds"] == 2
    assert seo["outcomes"] == {"reused": 1, "fetched": 1}
    assert seo["rows"] == 6
    assert seo["calls_avoided"] == 3
    assert seo["oldest_age_days"] == 0
    assert ppc["outcomes"] == {"reused": 1}
    assert ppc["rows"] == 3
    assert ppc["calls_avoided"] == 3
    assert summary["totals"] == {
        "seeds": 3,
        "outcomes": {"reused": 2, "fetched": 1},
        "rows": 9,
        "calls_avoided": 6,
        "place_topup_rows": 0,
    }


async def test_the_report_keeps_not_pooled_apart_from_a_partial_fetch(db_session, lookup):
    pool = PoolContext(places=("bondi",))
    await _run("dentist bondi", pool=pool, fetch=FakeFetch(), service="Dental")
    await _run("braces", pool=pool, fetch=FakeFetch(errors=["dataforseo_error"]), service="Dental")
    await _run("whitening", pool=pool, fetch=FakeFetch(), service="Dental", classes=("phrase",))
    await _run("implants", pool=pool, fetch=FakeFetch(), service="Dental")

    summary = pool.report.as_dict()
    assert summary["services"]["Dental"]["outcomes"] == {
        "not_pooled": 2,
        "fetched_not_saved": 1,
        "fetched": 1,
    }
    assert summary["totals"]["outcomes"] == summary["services"]["Dental"]["outcomes"]


async def test_calls_avoided_are_net_of_the_place_lookup(db_session, lookup):
    await _run("seo", pool=PoolContext(), fetch=FakeFetch())
    lookup.volumes = {"seo perth": 40}
    pool = PoolContext(places=("perth",))

    await _run("seo", pool=pool, fetch=FakeFetch(), service="SEO")

    seo = pool.report.as_dict()["services"]["SEO"]
    assert seo["calls_avoided"] == 2
    assert seo["rows"] == 4
    assert seo["place_topup_rows"] == 1


def test_an_empty_report_is_empty():
    assert PoolReport().as_dict() == {
        "services": {},
        "totals": {
            "seeds": 0,
            "outcomes": {},
            "rows": 0,
            "calls_avoided": 0,
            "place_topup_rows": 0,
        },
    }


def test_country_and_region_names():
    for country in ("United States", "australia", "India", "New  Zealand", "UK"):
        assert is_country_name(country)
        assert not is_region_name(country)
    for region in ("new south wales", "Victoria", "qld"):
        assert is_region_name(region)
        assert not is_country_name(region)
    for place in ("perth", "bondi", "texas", ""):
        assert not is_country_name(place)
        assert not is_region_name(place)
