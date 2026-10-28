"""Keyword pool store: save and reuse place-free keyword fetches across clients."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db import AsyncSessionLocal
from app.models import KeywordPoolEntry, KeywordPoolFetch
from app.services.shared_keyword_pool import store
from app.services.shared_keyword_pool.store import (
    PoolHit,
    SeedSave,
    concept_text,
    load_seed,
    save_seed,
    save_seeds_bulk,
    seed_key,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
LOCATION = {"location_code": 2036, "language_code": "en"}


def _rows(*keywords: str, volume: int | None = 100) -> list[dict]:
    return [{"keyword": keyword, "volume": volume, "cpc": 1.5} for keyword in keywords]


async def _save(seed: str = "seo", rows: list[dict] | None = None, **overrides):
    values = {
        "seed_text": seed,
        "min_volume": 10,
        "limit": 60,
        "rows": _rows("seo", "seo audit") if rows is None else rows,
        "errors": [],
        "now": NOW,
        **LOCATION,
    }
    values.update(overrides)
    return await save_seed(seed_key(seed), **values)


async def _load(seed: str = "seo", **overrides):
    values = {"min_volume": 10, "limit": 60, "now": NOW, **LOCATION}
    values.update(overrides)
    return await load_seed(seed_key(seed), **values)


async def _count(model) -> int:
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _stored_keywords() -> list[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(KeywordPoolEntry.keyword_norm).order_by(KeywordPoolEntry.keyword_norm)
        )
        return list(result.scalars())


async def test_a_complete_fetch_is_reused(db_session):
    status = await _save(rows=_rows("seo", "seo audit", "seo pricing"))

    hit = await _load()

    assert status == "complete"
    assert isinstance(hit, PoolHit)
    assert hit.status == "complete"
    assert {row["keyword"] for row in hit.rows} == {"seo", "seo audit", "seo pricing"}
    assert hit.rows[0]["cpc"] == 1.5
    assert hit.age_days == 0
    assert hit.fetched_at == NOW


async def test_a_fresh_save_is_found_with_the_default_clock(db_session):
    await save_seed(
        seed_key("seo"),
        seed_text="seo",
        min_volume=10,
        limit=60,
        rows=_rows("seo"),
        errors=[],
        **LOCATION,
    )

    hit = await load_seed(seed_key("seo"), min_volume=10, limit=60, **LOCATION)

    assert hit is not None
    assert hit.age_days == 0


async def test_a_fetch_older_than_180_days_is_not_reused(db_session):
    await _save()

    young = await _load(now=NOW + timedelta(days=179))
    old = await _load(now=NOW + timedelta(days=181))

    assert young is not None
    assert young.age_days == 179
    assert old is None


async def test_a_partial_fetch_is_saved_but_never_reused(db_session):
    status = await _save(errors=["provider returned 40501"])

    assert status == "partial"
    assert await _count(KeywordPoolFetch) == 1
    assert await _count(KeywordPoolEntry) == 2
    assert await _load() is None


async def test_an_empty_fetch_is_reused_as_empty(db_session):
    status = await _save(seed="obscure thing", rows=[])

    hit = await _load(seed="obscure thing")

    assert status == "empty"
    assert hit is not None
    assert hit.status == "empty"
    assert hit.rows == []


async def test_place_specific_rows_are_not_stored(db_session):
    await _save(
        seed="teeth whitening",
        rows=_rows("teeth whitening", "teeth whitening perth", "dentist near me"),
    )

    hit = await _load(seed="teeth whitening")

    assert [row["keyword"] for row in hit.rows] == ["teeth whitening"]
    assert await _count(KeywordPoolEntry) == 1


async def test_the_clients_own_places_are_not_stored(db_session):
    await _save(
        seed="teeth whitening",
        rows=_rows("teeth whitening", "teeth whitening smalltown"),
        places=["smalltown"],
    )

    hit = await _load(seed="teeth whitening")

    assert [row["keyword"] for row in hit.rows] == ["teeth whitening"]


async def test_a_smaller_earlier_pull_does_not_satisfy_a_larger_request(db_session):
    await _save(limit=40)

    assert await _load(limit=60) is None
    assert await _load(limit=40) is not None
    assert await _load(limit=25) is not None


async def test_a_pull_with_a_higher_volume_floor_does_not_satisfy_a_lower_one(db_session):
    await _save(min_volume=50)

    assert await _load(min_volume=10) is None
    assert await _load(min_volume=50) is not None


async def test_returned_rows_keep_only_those_meeting_the_requested_volume(db_session):
    await _save(
        rows=[
            {"keyword": "seo", "volume": 500},
            {"keyword": "seo audit", "volume": 30},
            {"keyword": "seo checklist", "volume": 12},
            {"keyword": "seo unmeasured", "volume": None},
        ],
    )

    hit = await _load(min_volume=30)

    assert {row["keyword"] for row in hit.rows} == {"seo", "seo audit", "seo unmeasured"}


async def test_a_fetch_for_another_country_or_language_is_not_reused(db_session):
    await _save()

    assert await _load(location_code=2826) is None
    assert await _load(language_code="fr") is None
    assert await _load(kind="lookup") is None


async def test_saving_twice_leaves_one_fetch(db_session):
    await _save(rows=_rows("seo", "seo audit"))
    await _save(rows=_rows("seo", "seo pricing", "seo tools"), now=NOW + timedelta(days=200))

    hit = await _load(now=NOW + timedelta(days=200))

    assert await _count(KeywordPoolFetch) == 1
    assert await _count(KeywordPoolEntry) == 3
    assert {row["keyword"] for row in hit.rows} == {"seo", "seo pricing", "seo tools"}
    assert hit.age_days == 0


async def test_a_partial_refetch_keeps_a_still_valid_complete_fetch(db_session):
    await _save(rows=_rows("seo", "seo audit"))

    status = await _save(
        rows=_rows("seo", "seo tools", "seo pricing"),
        errors=["budget stop"],
        limit=500,
        now=NOW + timedelta(days=1),
    )

    hit = await _load(now=NOW + timedelta(days=1))
    assert status == "partial"
    assert await _count(KeywordPoolFetch) == 1
    assert await _stored_keywords() == ["seo", "seo audit"]
    assert hit is not None
    assert hit.status == "complete"
    assert {row["keyword"] for row in hit.rows} == {"seo", "seo audit"}
    # The kept fetch keeps its own date and limit, so it is not renewed by the failure.
    assert hit.fetched_at == NOW
    assert hit.age_days == 1
    assert await _load(limit=500, now=NOW + timedelta(days=1)) is None


async def test_a_partial_refetch_keeps_a_still_valid_empty_fetch(db_session):
    await _save(seed="obscure thing", rows=[])

    status = await _save(
        seed="obscure thing",
        rows=_rows("obscure thing"),
        errors=["plan limit"],
        now=NOW + timedelta(days=1),
    )

    hit = await _load(seed="obscure thing", now=NOW + timedelta(days=1))
    assert status == "partial"
    assert hit is not None
    assert hit.status == "empty"
    assert hit.rows == []
    assert await _stored_keywords() == []


async def test_a_partial_refetch_replaces_an_expired_complete_fetch(db_session):
    await _save(rows=_rows("seo", "seo audit"))
    later = NOW + timedelta(days=181)

    status = await _save(rows=_rows("seo tools"), errors=["budget stop"], now=later)

    assert status == "partial"
    assert await _count(KeywordPoolFetch) == 1
    assert await _stored_keywords() == ["seo tools"]
    assert await _load(now=later) is None


async def test_a_partial_refetch_replaces_an_earlier_partial_fetch(db_session):
    await _save(rows=_rows("seo", "seo audit"), errors=["budget stop"])
    await _save(
        rows=_rows("seo tools"), errors=["plan limit"], now=NOW + timedelta(days=1)
    )

    assert await _count(KeywordPoolFetch) == 1
    assert await _stored_keywords() == ["seo tools"]


async def test_a_complete_refetch_replaces_an_earlier_partial_fetch(db_session):
    await _save(rows=_rows("seo"), errors=["budget stop"])
    await _save(rows=_rows("seo", "seo audit"), now=NOW + timedelta(days=1))

    hit = await _load(now=NOW + timedelta(days=1))
    assert await _count(KeywordPoolFetch) == 1
    assert hit is not None
    assert {row["keyword"] for row in hit.rows} == {"seo", "seo audit"}


async def test_a_failed_save_leaves_the_earlier_fetch_loadable(db_session):
    await _save(rows=_rows("seo", "seo audit"))

    with pytest.raises((TypeError, StatementError)):
        await _save(
            rows=[
                {"keyword": "seo tools", "volume": 40},
                {"keyword": "seo pricing", "volume": 30, "bad": object()},
            ],
            now=NOW + timedelta(days=1),
        )

    hit = await _load(now=NOW + timedelta(days=1))
    assert await _count(KeywordPoolFetch) == 1
    assert hit is not None
    assert {row["keyword"] for row in hit.rows} == {"seo", "seo audit"}
    assert hit.fetched_at == NOW


async def test_two_concurrent_saves_of_the_same_seed_do_not_crash(db_session):
    first_rows = ["seo audit", "seo checklist"]
    second_rows = ["seo tools", "seo pricing", "seo reports"]

    first, second = await asyncio.gather(
        _save(rows=_rows(*first_rows)),
        _save(rows=_rows(*second_rows)),
    )

    assert first == second == "complete"
    assert await _count(KeywordPoolFetch) == 1
    # Exactly one writer's rows survive: no mix of the two, no duplicates.
    assert await _stored_keywords() in (sorted(first_rows), sorted(second_rows))
    async with AsyncSessionLocal() as session:
        fetch = (await session.execute(select(KeywordPoolFetch))).scalar_one()
    assert fetch.row_count == await _count(KeywordPoolEntry)
    hit = await _load()
    assert hit is not None
    assert len(hit.rows) == fetch.row_count


async def test_seed_key_ignores_case_spacing_and_provider_noise():
    assert seed_key("  SEO ") == seed_key("seo")
    assert seed_key("Search   Engine\tOptimisation") == "search engine optimisation"
    assert seed_key("Focus service: SEO") == seed_key("seo")
    assert seed_key("Website & Conversion Optimisation (CRO)") == seed_key(
        "website conversion optimisation"
    )


def test_concept_text_is_sorted_and_only_for_services():
    assert concept_text("seo") == concept_text("SEO Services") == "seo"
    assert concept_text("roof repairs") == concept_text("roof repair")
    assert concept_text("whitening teeth") == concept_text("teeth whitening") == "teeth,whiten"
    # A buyer segment is not a service.
    assert concept_text("dentist near me") is None
    assert concept_text("") is None


async def test_a_differently_worded_seed_for_the_same_service_hits(db_session):
    await _save(seed="seo")

    hit = await _load(seed="seo services")

    assert hit is not None
    assert {row["keyword"] for row in hit.rows} == {"seo", "seo audit"}
    # The stored fetch is the one the exact key found, not a second row.
    assert await _count(KeywordPoolFetch) == 1


async def test_the_exact_key_wins_over_a_same_service_match(db_session):
    await _save(seed="seo", rows=_rows("seo", "seo audit"))
    await _save(seed="seo services", rows=_rows("seo services", "seo services cost"))

    hit = await _load(seed="seo services")

    assert {row["keyword"] for row in hit.rows} == {"seo services", "seo services cost"}


async def test_a_same_service_match_still_needs_the_same_country(db_session):
    await _save(seed="seo")

    assert await _load(seed="seo services", location_code=2826) is None


async def test_a_same_service_match_is_not_made_from_an_expired_fetch(db_session):
    await _save(seed="seo")

    assert await _load(seed="seo services", now=NOW + timedelta(days=181)) is None


async def test_a_narrower_seed_does_not_hit_a_broader_one(db_session):
    await _save(seed="seo")

    assert await _load(seed="seo for dentists") is None
    assert await _load(seed="seo melbourne") is None


async def test_a_noisy_seed_is_found_again_by_its_clean_wording(db_session):
    # The concept is taken from the cleaned key on both sides, so provider noise in
    # the saved seed text ("Focus service: SEO") does not hide it from the fallback.
    await _save(seed="Focus service: SEO", rows=_rows("seo", "seo audit"))

    exact = await _load(seed="SEO")
    reworded = await _load(seed="seo services")

    assert exact is not None
    assert reworded is not None
    assert {row["keyword"] for row in reworded.rows} == {"seo", "seo audit"}
    async with AsyncSessionLocal() as session:
        fetch = (await session.execute(select(KeywordPoolFetch))).scalar_one()
    assert fetch.concept_key == concept_text(seed_key("Focus service: SEO")) == "seo"


async def test_an_explicit_concept_key_is_stored_as_given(db_session):
    await _save(seed="seo", concept_key="custom-concept")

    async with AsyncSessionLocal() as session:
        fetch = (await session.execute(select(KeywordPoolFetch))).scalar_one()
    assert fetch.concept_key == "custom-concept"


async def test_the_same_service_match_does_not_cross_language_or_kind(db_session):
    await _save(seed="seo", language_code="fr")
    await _save(seed="seo", kind="lookup")

    # Both fetches carry the concept "seo", but neither is the requested language and kind.
    assert await _load(seed="seo services") is None
    # Each is found when its own language and kind are asked for.
    assert await _load(seed="seo services", language_code="fr") is not None
    assert await _load(seed="seo services", kind="lookup") is not None
    assert await _load(seed="seo services", kind="lookup", language_code="fr") is None


async def test_a_seed_that_adds_only_modifier_words_hits(db_session):
    await _save(seed="email marketing", rows=_rows("email marketing", "email marketing tools"))

    hit = await _load(seed="email marketing services")

    assert hit is not None
    assert {row["keyword"] for row in hit.rows} == {"email marketing", "email marketing tools"}


async def test_a_seed_that_adds_a_real_word_does_not_hit(db_session):
    await _save(seed="ppc")

    assert await _load(seed="ppc management") is None


# ------------------------------------------------------------------ bulk save


def _item(seed: str, rows: list[dict] | None = None, **overrides) -> SeedSave:
    values = {"key": seed_key(seed), "seed_text": seed, "rows": _rows(seed) if rows is None else rows}
    values.update(overrides)
    return SeedSave(**values)


async def _bulk(items, **overrides):
    values = {"min_volume": 10, "limit": 60, "now": NOW, **LOCATION}
    values.update(overrides)
    return await save_seeds_bulk(items, **values)


@pytest.fixture
def commits():
    """Counts every commit any session makes while a test runs."""
    seen: list[int] = []

    def count(_session) -> None:
        seen.append(1)

    event.listen(Session, "after_commit", count)
    yield seen
    event.remove(Session, "after_commit", count)


async def test_a_bulk_save_writes_many_fetches_in_one_commit(db_session, commits):
    items = [_item(f"best seo {n}", _rows(f"best seo {n}")) for n in range(25)]
    del commits[:]

    statuses = await _bulk(items, kind="lookup")

    assert len(commits) == 1
    assert statuses == {seed_key(f"best seo {n}"): "complete" for n in range(25)}
    assert await _count(KeywordPoolFetch) == 25
    assert await _count(KeywordPoolEntry) == 25
    hit = await _load(seed="best seo 7", kind="lookup")
    assert hit is not None and [row["keyword"] for row in hit.rows] == ["best seo 7"]
    assert await _load(seed="best seo 7") is None  # saved under the requested kind only


async def test_a_bulk_save_gives_each_item_its_status(db_session):
    statuses = await _bulk(
        [
            _item("seo", _rows("seo")),
            _item("obscure thing", []),
            _item("ppc", _rows("ppc"), errors=["budget stop"]),
        ]
    )

    assert statuses == {"seo": "complete", "obscure thing": "empty", "ppc": "partial"}
    assert (await _load(seed="obscure thing")).status == "empty"
    assert await _load(seed="ppc") is None  # a partial fetch is saved but never reused


async def test_a_bulk_save_keeps_the_rules_of_a_single_save(db_session):
    # Place keywords are dropped, including the client's own places.
    await _bulk(
        [_item("seo", _rows("seo", "seo melbourne", "seo audit", "seo fitzroy"))], places=("fitzroy",)
    )
    assert await _stored_keywords() == ["seo", "seo audit"]

    # A partial refetch never replaces a still-valid complete fetch.
    await _bulk(
        [_item("seo", _rows("seo tools"), errors=["plan limit"])], now=NOW + timedelta(days=1)
    )
    assert await _stored_keywords() == ["seo", "seo audit"]

    # A complete refetch replaces the old entries: none are left behind.
    await _bulk([_item("seo", _rows("seo pricing"))], now=NOW + timedelta(days=2))
    assert await _count(KeywordPoolFetch) == 1
    assert await _stored_keywords() == ["seo pricing"]

    # An expired complete fetch is replaced even by a partial one.
    await _bulk(
        [_item("seo", _rows("seo reports"), errors=["x"])], now=NOW + timedelta(days=2 + 181)
    )
    assert await _stored_keywords() == ["seo reports"]


async def test_a_bulk_save_carries_the_industry_and_concept_keys(db_session):
    await _bulk([_item("seo", _rows("seo"), industry_key="marketing", concept_key="custom")])
    async with AsyncSessionLocal() as session:
        fetch = (await session.execute(select(KeywordPoolFetch))).scalar_one()
    assert (fetch.industry_key, fetch.concept_key) == ("marketing", "custom")


async def test_the_same_key_twice_in_one_bulk_save_leaves_one_fetch(db_session):
    statuses = await _bulk([_item("seo", _rows("seo")), _item("seo", _rows("seo audit"))])

    assert statuses == {"seo": "complete"}
    assert await _count(KeywordPoolFetch) == 1
    assert await _stored_keywords() == ["seo audit"]


async def test_a_bulk_save_of_nothing_does_nothing(db_session, commits):
    del commits[:]
    assert await _bulk([]) == {}
    assert commits == []


async def test_a_failed_bulk_save_leaves_the_earlier_fetches_loadable(db_session):
    await _bulk([_item("seo", _rows("seo", "seo audit"))])

    with pytest.raises((TypeError, StatementError)):
        await _bulk(
            [
                _item("ppc", _rows("ppc")),
                _item("seo", [{"keyword": "seo tools", "volume": 40, "bad": object()}]),
            ],
            now=NOW + timedelta(days=1),
        )

    # The whole batch rolled back: the earlier fetch is intact and nothing half-saved remains.
    assert await _count(KeywordPoolFetch) == 1
    assert await _stored_keywords() == ["seo", "seo audit"]


async def test_two_bulk_saves_of_the_same_keys_at_once_do_not_crash(db_session):
    first = [_item(f"seo {n}", _rows(f"seo {n} audit")) for n in range(5)]
    second = [_item(f"seo {n}", _rows(f"seo {n} tools")) for n in range(5)]

    one, two = await asyncio.gather(_bulk(first), _bulk(second))

    assert set(one.values()) == set(two.values()) == {"complete"}
    assert await _count(KeywordPoolFetch) == 5
    assert await _count(KeywordPoolEntry) == 5  # one writer's row per key, no duplicates


async def test_a_bulk_save_processes_its_items_in_seed_key_order(db_session, monkeypatch):
    # The same order for every writer, so two concurrent bulk saves lock rows the same way
    # and cannot deadlock on Postgres.
    real = store._write_seed
    order: list[str] = []

    async def recording(session, key, **values):
        order.append(key)
        return await real(session, key, **values)

    monkeypatch.setattr(store, "_write_seed", recording)
    names = ["seo", "cro", "ppc", "email marketing", "analytics", "web design"]

    forward = await _bulk([_item(name) for name in names])
    assert order == sorted(seed_key(name) for name in names)

    del order[:]
    await _bulk([_item(name) for name in reversed(names)], now=NOW + timedelta(days=1))
    assert order == sorted(seed_key(name) for name in names)
    assert set(forward) == {seed_key(name) for name in names}


async def test_a_key_listed_twice_still_lets_the_later_item_win_after_sorting(db_session):
    await _bulk([_item("seo", _rows("seo first")), _item("cro"), _item("seo", _rows("seo second"))])
    assert await _stored_keywords() == ["cro", "seo second"]


async def test_a_lost_insert_race_falls_back_to_saving_each_item(db_session, monkeypatch):
    real = store._write_seed
    calls = {"n": 0}

    async def racing(session, key, **values):
        calls["n"] += 1
        if calls["n"] == 2:  # the second item of the batch hits the unique key
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return await real(session, key, **values)

    monkeypatch.setattr(store, "_write_seed", racing)

    statuses = await _bulk([_item("seo"), _item("ppc"), _item("cro")])

    assert statuses == {"seo": "complete", "ppc": "complete", "cro": "complete"}
    assert await _count(KeywordPoolFetch) == 3
    assert calls["n"] == 2 + 3  # the failed batch, then each item on its own


def test_the_store_imports_without_keyword_seeding():
    """keyword_seeding will import this package, so the store must not import it back."""
    backend = Path(__file__).resolve().parent.parent
    code = (
        "import sys\n"
        "sys.modules['app.services.keyword_seeding'] = None\n"
        "import app.services.shared_keyword_pool.store as store\n"
        "assert sys.modules['app.services.keyword_seeding'] is None\n"
        "print(store.PoolHit.__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PoolHit"
