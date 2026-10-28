"""Save and reuse place-free keyword fetches across clients.

A fetch is one provider call for one seed, country and language. Only the
place-free keywords it returned are stored, and a stored fetch is trusted for
``keyword_pool_ttl_days`` days. Nothing about the client that triggered the
fetch (name, id, URL) is stored.

``keyword_seeding`` will import this package, so it is never imported here at
module level; ``clean_provider_seed`` is imported inside ``seed_key``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models.keyword_pool import KeywordPoolEntry, KeywordPoolFetch
from app.services.service_taxonomy import analyze_name
from app.services.service_taxonomy import concept_key as _taxonomy_concept_key
from app.services.shared_keyword_pool.location import (
    MODIFIER_WORDS,
    is_location_specific,
)

# Statuses a later client may reuse. A ``partial`` fetch is saved for the record
# but is always fetched again.
_REUSABLE_STATUSES = ("complete", "empty")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Longest phrases first so "how to" is cut as a phrase, not left as two words.
_MODIFIER_PHRASES: tuple[tuple[str, ...], ...] = tuple(
    sorted((tuple(phrase.split()) for phrase in MODIFIER_WORDS), key=len, reverse=True)
)

_Entry = tuple[str, str, int | None, dict]


@dataclass(frozen=True)
class PoolHit:
    """A reusable saved fetch: its place-free rows and how old it is."""

    rows: list[dict]
    status: str
    fetched_at: datetime
    age_days: int


def seed_key(seed: str) -> str:
    """The pool's identity of a seed: lowercase, single-spaced, free of provider noise."""
    from app.services.keyword_seeding import clean_provider_seed

    return " ".join(clean_provider_seed(seed or "").lower().split())


def concept_text(seed: str) -> str | None:
    """Sorted, comma-joined concept key of a service seed; ``None`` when it is not a service."""
    if analyze_name(seed).kind != "service":
        return None
    parts = sorted(_taxonomy_concept_key(seed))
    return ",".join(parts) if parts else None


def _utc(moment: datetime | None) -> datetime:
    """A timezone-aware UTC time; the current time when ``moment`` is ``None``.

    SQLite hands back naive datetimes, which are UTC here, so naive means UTC.
    """
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _core_tokens(text: str) -> frozenset[str]:
    """The words of ``text`` that are not generic modifiers ("services", "best", "near me")."""
    tokens = _TOKEN_RE.findall((text or "").lower())
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        for phrase in _MODIFIER_PHRASES:
            if tuple(tokens[index : index + len(phrase)]) == phrase:
                index += len(phrase)
                break
        else:
            kept.append(tokens[index])
            index += 1
    return frozenset(kept)


def _status(rows: Sequence[dict], errors: Sequence[str]) -> str:
    if errors:
        return "partial"
    if not rows:
        return "empty"
    return "complete"


def _volume(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _entries(rows: Iterable[dict], places: Sequence[str]) -> list[_Entry]:
    """(keyword_norm, keyword, volume, row) for each place-free row; the first spelling wins."""
    seen: set[str] = set()
    kept: list[_Entry] = []
    for row in rows:
        keyword = " ".join(str(row.get("keyword") or "").split())
        norm = keyword.lower()
        if not norm or norm in seen or is_location_specific(keyword, places):
            continue
        seen.add(norm)
        kept.append((norm, keyword, _volume(row.get("volume")), dict(row)))
    return kept


def _reusable(
    fetch: KeywordPoolFetch, *, now: datetime, ttl: timedelta, min_volume: int, limit: int
) -> bool:
    return (
        fetch.status in _REUSABLE_STATUSES
        and now - _utc(fetch.fetched_at) <= ttl
        and fetch.row_limit >= limit
        and fetch.min_volume <= min_volume
    )


async def _rows_of(session: AsyncSession, fetch: KeywordPoolFetch, min_volume: int) -> list[dict]:
    result = await session.execute(
        select(KeywordPoolEntry).where(
            KeywordPoolEntry.fetch_id == fetch.id,
            or_(KeywordPoolEntry.volume.is_(None), KeywordPoolEntry.volume >= min_volume),
        )
    )
    entries = list(result.scalars())
    # No order is stored: highest volume first, unmeasured last, then alphabetical.
    entries.sort(key=lambda e: (e.volume is None, -(e.volume or 0), e.keyword_norm))
    return [dict(entry.row) for entry in entries]


async def load_seed(
    key: str,
    *,
    location_code: int,
    language_code: str,
    min_volume: int,
    limit: int,
    kind: str = "seed",
    now: datetime | None = None,
) -> PoolHit | None:
    """The saved fetch for ``key`` when it is still good enough to reuse, else ``None``.

    The exact key is tried first. Failing that, a fetch of the same service worded
    differently ("seo services" for a saved "seo") is used, but only when ``key``
    names no place and differs from the saved seed by modifier words alone.
    """
    current = _utc(now)
    ttl = timedelta(days=get_settings().keyword_pool_ttl_days)
    same_scope = (
        KeywordPoolFetch.kind == kind,
        KeywordPoolFetch.location_code == location_code,
        KeywordPoolFetch.language_code == language_code,
    )
    async with AsyncSessionLocal() as session:
        found = (
            await session.execute(
                select(KeywordPoolFetch).where(*same_scope, KeywordPoolFetch.seed_key == key)
            )
        ).scalar_one_or_none()
        if found is not None and not _reusable(
            found, now=current, ttl=ttl, min_volume=min_volume, limit=limit
        ):
            found = None

        concept = concept_text(key) if found is None and not is_location_specific(key) else None
        if concept is not None:
            wanted = _core_tokens(key)
            candidates = (
                await session.execute(
                    select(KeywordPoolFetch)
                    .where(
                        *same_scope,
                        KeywordPoolFetch.concept_key == concept,
                        KeywordPoolFetch.seed_key != key,
                        KeywordPoolFetch.status.in_(_REUSABLE_STATUSES),
                    )
                    .order_by(KeywordPoolFetch.fetched_at.desc())
                )
            ).scalars()
            for candidate in candidates:
                if (
                    wanted
                    and _core_tokens(candidate.seed_key) == wanted
                    and _reusable(
                        candidate, now=current, ttl=ttl, min_volume=min_volume, limit=limit
                    )
                ):
                    found = candidate
                    break

        if found is None:
            return None
        fetched_at = _utc(found.fetched_at)
        return PoolHit(
            rows=await _rows_of(session, found, min_volume),
            status=found.status,
            fetched_at=fetched_at,
            age_days=max(0, (current - fetched_at).days),
        )


async def _write_seed(
    session: AsyncSession,
    key: str,
    *,
    kind: str,
    location_code: int,
    language_code: str,
    seed_text: str,
    concept: str | None,
    industry_key: str | None,
    status: str,
    min_volume: int,
    limit: int,
    errors: list[str],
    entries: list[_Entry],
    when: datetime,
    ttl: timedelta,
) -> None:
    fetch = (
        await session.execute(
            select(KeywordPoolFetch).where(
                KeywordPoolFetch.kind == kind,
                KeywordPoolFetch.seed_key == key,
                KeywordPoolFetch.location_code == location_code,
                KeywordPoolFetch.language_code == language_code,
            )
        )
    ).scalar_one_or_none()
    if fetch is None:
        fetch = KeywordPoolFetch(
            kind=kind, seed_key=key, location_code=location_code, language_code=language_code
        )
        session.add(fetch)
    elif (
        status == "partial"
        and fetch.status in _REUSABLE_STATUSES
        and when - _utc(fetch.fetched_at) <= ttl
    ):
        # A failed refetch (provider error, plan limit, budget stop) must not
        # destroy a good saved fetch that is still inside its lifetime.
        return
    else:
        # The app's SQLite engine does not enforce foreign keys, so ON DELETE
        # CASCADE cannot be relied on to clear the old entries.
        await session.execute(
            delete(KeywordPoolEntry).where(KeywordPoolEntry.fetch_id == fetch.id)
        )
    fetch.seed_text = seed_text
    fetch.concept_key = concept
    fetch.industry_key = industry_key
    fetch.status = status
    fetch.row_count = len(entries)
    fetch.min_volume = min_volume
    fetch.row_limit = limit
    fetch.errors = errors
    fetch.fetched_at = when
    await session.flush()
    session.add_all(
        KeywordPoolEntry(
            fetch_id=fetch.id, keyword_norm=norm, keyword=keyword, volume=volume, row=row
        )
        for norm, keyword, volume, row in entries
    )
    await session.flush()


async def save_seed(
    key: str,
    *,
    seed_text: str,
    location_code: int,
    language_code: str,
    min_volume: int,
    limit: int,
    rows: list[dict],
    errors: list[str],
    places: Sequence[str] = (),
    kind: str = "seed",
    industry_key: str | None = None,
    concept_key: str | None = None,
    now: datetime | None = None,
) -> str:
    """Save one provider fetch, replacing any earlier fetch of the same seed, and return its status.

    Place-specific rows are dropped first (``places`` adds the client's own places to
    the built-in place list). The status describes the provider call: ``partial`` when
    there were errors, ``empty`` when it returned nothing, otherwise ``complete``.
    A ``partial`` save never overwrites an earlier ``complete`` or ``empty`` fetch of
    the same key that is still inside the lifetime; it replaces a ``partial`` or an
    expired one. ``concept_key`` defaults to ``concept_text(key)``, the same input
    ``load_seed`` uses.
    """
    errors = [str(error) for error in errors]
    status = _status(rows, errors)
    values = {
        "kind": kind,
        "location_code": location_code,
        "language_code": language_code,
        "seed_text": seed_text,
        "concept": concept_key if concept_key is not None else concept_text(key),
        "industry_key": industry_key,
        "status": status,
        "min_volume": min_volume,
        "limit": limit,
        "errors": errors,
        "entries": _entries(rows, places),
        "when": _utc(now),
        "ttl": timedelta(days=get_settings().keyword_pool_ttl_days),
    }
    # Two clients saving the same seed at once both see "no fetch yet". The loser
    # of the insert race hits the unique key and retries once, now finding the
    # winner's row and updating it.
    try:
        await _save_in_new_session(key, values)
    except IntegrityError:
        await _save_in_new_session(key, values)
    return status


async def _save_in_new_session(key: str, values: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        await _write_seed(session, key, **values)
        await session.commit()


@dataclass(frozen=True)
class SeedSave:
    """One fetch for ``save_seeds_bulk``: the same fields ``save_seed`` takes per fetch."""

    key: str
    seed_text: str
    rows: Sequence[dict]
    errors: Sequence[str] = field(default_factory=tuple)
    industry_key: str | None = None
    concept_key: str | None = None


async def save_seeds_bulk(
    items: Sequence[SeedSave],
    *,
    location_code: int,
    language_code: str,
    min_volume: int,
    limit: int,
    places: Sequence[str] = (),
    kind: str = "seed",
    now: datetime | None = None,
) -> dict[str, str]:
    """Save many fetches of one scope in a single session and commit; return ``{key: status}``.

    Each item follows the rules of ``save_seed``: place-specific rows are dropped, the
    status is ``partial``, ``empty`` or ``complete``, a ``partial`` item never overwrites a
    still-valid ``complete`` or ``empty`` fetch, and a replaced fetch has its old entries
    deleted. The batch is all-or-nothing. When another writer wins an insert race
    (``IntegrityError``) the batch is rolled back and each item is saved on its own, each
    with ``save_seed``'s one retry. Any other error is raised after the rollback, with the
    earlier fetches left as they were. A key listed twice is saved twice, the later one
    winning. Items are processed in seed-key order whatever order they come in, so two
    concurrent bulk saves take their row locks in the same order and cannot deadlock.
    """
    if not items:
        return {}
    when = _utc(now)
    ttl = timedelta(days=get_settings().keyword_pool_ttl_days)
    prepared: list[tuple[str, dict[str, Any]]] = []
    statuses: dict[str, str] = {}
    for item in items:
        errors = [str(error) for error in item.errors]
        rows = list(item.rows)
        status = _status(rows, errors)
        statuses[item.key] = status
        prepared.append(
            (
                item.key,
                {
                    "kind": kind,
                    "location_code": location_code,
                    "language_code": language_code,
                    "seed_text": item.seed_text,
                    "concept": item.concept_key if item.concept_key is not None else concept_text(item.key),
                    "industry_key": item.industry_key,
                    "status": status,
                    "min_volume": min_volume,
                    "limit": limit,
                    "errors": errors,
                    "entries": _entries(rows, places),
                    "when": when,
                    "ttl": ttl,
                },
            )
        )
    # A stable sort: a key listed twice keeps its input order, so the later item still wins.
    prepared.sort(key=lambda pair: pair[0])
    try:
        async with AsyncSessionLocal() as session:
            for key, values in prepared:
                await _write_seed(session, key, **values)
            await session.commit()
    except IntegrityError:
        for key, values in prepared:
            try:
                await _save_in_new_session(key, values)
            except IntegrityError:
                await _save_in_new_session(key, values)
    return statuses
