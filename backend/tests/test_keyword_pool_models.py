"""Cross-client keyword pool tables: schema, uniqueness, cascade, migration 009."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import Base, engine
from app.models import KeywordPoolEntry, KeywordPoolFetch

MIGRATIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _fetch(**overrides) -> KeywordPoolFetch:
    values = {
        "kind": "seed",
        "seed_key": "plumber",
        "seed_text": "plumber",
        "concept_key": "plumber",
        "industry_key": "plumbing",
        "location_code": 2036,
        "language_code": "en",
        "status": "complete",
        "row_count": 2,
        "min_volume": 10,
        "row_limit": 700,
        "errors": [],
    }
    values.update(overrides)
    return KeywordPoolFetch(**values)


def _entry(fetch_id: uuid.UUID, keyword_norm: str = "emergency plumber") -> KeywordPoolEntry:
    return KeywordPoolEntry(
        fetch_id=fetch_id,
        keyword_norm=keyword_norm,
        keyword=keyword_norm.title(),
        volume=320,
        row={"cpc": 4.2, "competition": 0.4, "intent": "commercial", "funnel": "BOFU"},
    )


def _load_migration(filename: str):
    spec = importlib.util.spec_from_file_location(
        filename.removesuffix(".py"), MIGRATIONS / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_tables_exist_after_create_all(db_session):
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    assert {"keyword_pool_fetches", "keyword_pool_entries"} <= names

    fetch = _fetch()
    db_session.add(fetch)
    await db_session.flush()
    entry = _entry(fetch.id)
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(fetch)
    await db_session.refresh(entry)

    assert isinstance(fetch.id, uuid.UUID)
    assert fetch.fetched_at is not None
    assert fetch.errors == []
    assert entry.row["funnel"] == "BOFU"
    assert entry.volume == 320


async def test_the_pool_holds_no_client_identity_columns(db_session):
    for table in (KeywordPoolFetch.__table__, KeywordPoolEntry.__table__):
        assert "client_id" not in table.c
        assert all(fk.column.table.name != "clients" for fk in table.foreign_keys)


async def test_a_fetch_is_unique_per_kind_seed_location_language(db_session):
    db_session.add(_fetch())
    await db_session.commit()

    db_session.add(_fetch(seed_text="plumber (again)"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    # Any one key part differing is a different fetch.
    db_session.add_all(
        [
            _fetch(kind="lookup"),
            _fetch(seed_key="drain cleaning"),
            _fetch(location_code=2826),
            _fetch(language_code="fr"),
        ]
    )
    await db_session.commit()


async def test_an_entry_is_unique_per_fetch_and_keyword(db_session):
    first, second = _fetch(), _fetch(seed_key="drain cleaning")
    db_session.add_all([first, second])
    await db_session.flush()
    first_id, second_id = first.id, second.id
    db_session.add(_entry(first_id))
    await db_session.commit()

    db_session.add(_entry(first_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    # The same keyword under another fetch is fine.
    db_session.add(_entry(second_id))
    await db_session.commit()


async def test_entries_are_deleted_with_their_fetch(db_session):
    fetch = _fetch()
    db_session.add(fetch)
    await db_session.flush()
    db_session.add_all([_entry(fetch.id, "emergency plumber"), _entry(fetch.id, "blocked drain")])
    await db_session.commit()
    fetch_id = fetch.id

    # SQLite ignores foreign keys unless asked; enforce them on this connection
    # only, and put the pragma back so the pooled connection stays as found.
    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    try:
        await db_session.execute(
            sa.delete(KeywordPoolFetch).where(KeywordPoolFetch.id == fetch_id)
        )
        await db_session.commit()
        remaining = (
            await db_session.execute(
                sa.select(sa.func.count())
                .select_from(KeywordPoolEntry)
                .where(KeywordPoolEntry.fetch_id == fetch_id)
            )
        ).scalar_one()
    finally:
        await db_session.execute(text("PRAGMA foreign_keys=OFF"))
    assert remaining == 0


def test_migration_009_follows_008():
    migration = _load_migration("009_keyword_pool.py")
    assert migration.revision == "009"
    assert migration.down_revision == "008"
    assert _load_migration("008_cdd_versions.py").revision == "008"


def _schema(sync_conn):
    insp = inspect(sync_conn)
    out = {}
    for table in ("keyword_pool_fetches", "keyword_pool_entries"):
        out[table] = {
            "columns": {
                c["name"]: (type(c["type"]).__name__, c["nullable"])
                for c in insp.get_columns(table)
            },
            "unique": {tuple(u["column_names"]) for u in insp.get_unique_constraints(table)},
            "indexes": {
                (i["name"], tuple(i["column_names"])) for i in insp.get_indexes(table)
            },
            "fks": {
                (tuple(f["constrained_columns"]), f["referred_table"], f["options"].get("ondelete"))
                for f in insp.get_foreign_keys(table)
            },
        }
    return out


def test_migration_009_builds_the_schema_the_models_declare_and_downgrades():
    migration = _load_migration("009_keyword_pool.py")

    model_engine = sa.create_engine("sqlite://")
    pool_tables = [
        Base.metadata.tables["keyword_pool_fetches"],
        Base.metadata.tables["keyword_pool_entries"],
    ]
    Base.metadata.create_all(model_engine, tables=pool_tables)
    with model_engine.connect() as conn:
        from_models = _schema(conn)

    migrated = sa.create_engine("sqlite://")
    with migrated.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
    with migrated.connect() as conn:
        from_migration = _schema(conn)
    assert from_migration == from_models
    assert from_migration["keyword_pool_entries"]["fks"] == {
        (("fetch_id",), "keyword_pool_fetches", "CASCADE")
    }

    with migrated.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
    with migrated.connect() as conn:
        assert inspect(conn).get_table_names() == []
