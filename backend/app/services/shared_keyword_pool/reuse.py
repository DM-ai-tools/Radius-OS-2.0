"""Reuse a saved seed fetch for a later client, and look up that client's own places.

``pooled_seed_fetch`` wraps the provider fetch (``expand_seed_dataforseo``). When an
earlier client already fetched the same seed for the same country and language, its
place-free rows are served instead of calling the provider again. The saved rows hold
no place keywords, so a reused seed also gets a small, targeted lookup of
"<seed> <place>" for the client's own places. Those rows belong to that client only
and are never saved.

A pool failure never fails the call: it is logged and the seed is fetched as it would
be without a pool.

``keyword_seeding`` imports this package, so its helpers are imported inside the
functions that use them, never at module level.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.integrations import dataforseo
from app.logging_config import get_logger
from app.services.shared_keyword_pool.location import (
    is_country_name,
    is_location_specific,
    is_region_name,
)
from app.services.shared_keyword_pool.store import load_seed, save_seed, seed_key

log = get_logger("keyword_pool")

FetchFn = Callable[..., Awaitable[tuple[list[dict], list[str]]]]

# A saved fetch stands for all three match classes. A fetch of fewer classes is
# narrower than the key says, so it is never saved or reused.
_POOLED_CLASSES = frozenset({"phrase", "related", "broad"})
# One provider endpoint per match class. Follow-up pages (a setting) are not counted,
# so the figure is a floor.
_CALLS_PER_FETCH = len(_POOLED_CLASSES)
_TOPUP_MIN_VOLUME = 10
_TOPUP_ERROR = "keyword_pool_place_topup_error"
_UNASSIGNED = "unassigned"


@dataclass
class PoolReport:
    """What the pool did for each seed, summarised per service."""

    records: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        service: str | None,
        seed: str,
        outcome: str,
        rows: int,
        age_days: int | None = None,
        calls_avoided: int = 0,
        topup_rows: int = 0,
    ) -> None:
        """Note one seed: ``rows`` is how many rows the caller got back, place lookups included."""
        self.records.append(
            {
                "service": (service or "").strip() or _UNASSIGNED,
                "seed": seed,
                "outcome": outcome,
                "rows": int(rows),
                "age_days": age_days,
                "calls_avoided": int(calls_avoided),
                "topup_rows": int(topup_rows),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        """Per service (and in total): seeds, outcome counts, rows, calls avoided, oldest saved age."""
        services: dict[str, dict[str, Any]] = {}
        for item in self.records:
            entry = services.setdefault(
                item["service"],
                {
                    "seeds": 0,
                    "outcomes": {},
                    "rows": 0,
                    "calls_avoided": 0,
                    "place_topup_rows": 0,
                    "oldest_age_days": None,
                },
            )
            entry["seeds"] += 1
            entry["outcomes"][item["outcome"]] = entry["outcomes"].get(item["outcome"], 0) + 1
            entry["rows"] += item["rows"]
            entry["calls_avoided"] += item["calls_avoided"]
            entry["place_topup_rows"] += item["topup_rows"]
            if item["age_days"] is not None:
                entry["oldest_age_days"] = max(entry["oldest_age_days"] or 0, item["age_days"])
        totals: dict[str, Any] = {
            "seeds": 0,
            "outcomes": {},
            "rows": 0,
            "calls_avoided": 0,
            "place_topup_rows": 0,
        }
        for entry in services.values():
            for name in ("seeds", "rows", "calls_avoided", "place_topup_rows"):
                totals[name] += entry[name]
            for outcome, count in entry["outcomes"].items():
                totals["outcomes"][outcome] = totals["outcomes"].get(outcome, 0) + count
        return {"services": services, "totals": totals}


@dataclass
class PoolContext:
    """Per-run pool settings: the client's own places, the switch, the industry, the report."""

    places: tuple[str, ...] = ()
    enabled: bool = True
    industry_key: str | None = None
    report: PoolReport = field(default_factory=PoolReport)


def _query(seed: str) -> str:
    """The seed as the provider receives it."""
    from app.services.keyword_seeding import clean_provider_seed

    return " ".join(clean_provider_seed(seed).split())


def _log_error(stage: str, seed: str, exc: BaseException) -> None:
    log.warning("keyword_pool_error", stage=stage, seed=seed, error=type(exc).__name__)


def _topup_places(places: Iterable[str], cap: int) -> list[str]:
    """The places to look up: never a country, the client's own areas before wider regions.

    A country spends no slot: when a focus is unmapped the geography resolver names the
    country as the location, and "seo united states" is not one of the client's places.
    A state or territory comes after the finer places (city, suburbs) so it only takes
    a slot that would otherwise stay empty.
    """
    chosen: list[str] = []
    for place in places:
        name = " ".join(str(place or "").lower().split())
        if name and name not in chosen and not is_country_name(name):
            chosen.append(name)
    ordered = [p for p in chosen if not is_region_name(p)] + [p for p in chosen if is_region_name(p)]
    return ordered[: max(0, int(cap))]


def _retarget(row: dict, seed: str, query: str) -> dict:
    """A copy of a saved row, re-pointed at the seed that is asking for it.

    The match class is worked out again against this seed, the same way a fresh fetch
    would (the endpoint that first returned the row still counts for one-word seeds).
    """
    from app.services.keyword_seeding import classify_expansion

    copy = dict(row)
    if "parent_topic" in copy and copy["parent_topic"] == copy.get("seed"):
        copy["parent_topic"] = seed
    copy["seed"] = seed
    copy["match_class"] = classify_expansion(
        str(copy.get("keyword") or ""), query, source_hint=copy.get("ahrefs_endpoint")
    )
    return copy


async def _place_topup(
    seed: str,
    query: str,
    known: set[str],
    places: Iterable[str],
    *,
    location_code: int,
    language_code: str,
) -> tuple[list[dict], list[str], bool]:
    """Measured "<seed> <place>" keywords for the client's places: (rows, errors, lookup_made)."""
    from app.services.keyword_seeding import classify_expansion, dataforseo_rows, volume_qualifies

    chosen = _topup_places(places, get_settings().keyword_pool_place_cap)
    if not chosen:
        return [], [], False
    candidates = [f"{query.lower()} {place}" for place in chosen]
    try:
        found, errors = await dataforseo.search_volume(
            candidates, location_code=location_code, language_code=language_code
        )
    except Exception as exc:  # noqa: BLE001 - a failed lookup must not fail the seed
        _log_error("place_topup", seed, exc)
        return [], [_TOPUP_ERROR], True
    if errors:
        log.warning("keyword_pool_place_topup_errors", seed=seed, errors=list(errors)[:3])

    wanted = set(candidates)
    measured: list[dict] = []
    for item in found:
        keyword = " ".join(str(item.get("keyword") or "").lower().split())
        if keyword in wanted and keyword not in known and volume_qualifies(
            item.get("volume"), min_volume=_TOPUP_MIN_VOLUME
        ):
            known.add(keyword)
            measured.append(item)
    rows = dataforseo_rows(measured, endpoint="search_volume", seed=seed, query=query)
    for row in rows:
        row["match_class"] = classify_expansion(row["keyword"], query)
        row["place_topup"] = True
    return rows, [_TOPUP_ERROR] if errors else [], True


async def _serve_hit(
    seed: str,
    key: str,
    pool: PoolContext,
    service: str | None,
    *,
    location_code: int,
    language_code: str,
    min_volume: int,
    limit: int,
) -> tuple[list[dict], list[str]] | None:
    """The saved rows for ``seed`` plus the client's place lookups, or ``None`` when nothing is saved."""
    hit = await load_seed(
        key,
        location_code=location_code,
        language_code=language_code,
        min_volume=min_volume,
        limit=limit,
    )
    if hit is None:
        return None
    query = _query(seed)
    rows = [_retarget(row, seed, query) for row in hit.rows]
    known = {" ".join(str(row.get("keyword") or "").lower().split()) for row in rows}
    topup, errors, lookup_made = await _place_topup(
        seed,
        query,
        known,
        pool.places,
        location_code=location_code,
        language_code=language_code,
    )
    pool.report.record(
        service,
        seed,
        "reused" if hit.status == "complete" else "reused_empty",
        len(rows) + len(topup),
        age_days=hit.age_days,
        calls_avoided=max(0, _CALLS_PER_FETCH - (1 if lookup_made else 0)),
        topup_rows=len(topup),
    )
    return rows + topup, errors


async def pooled_seed_fetch(
    seed: str,
    *,
    pool: PoolContext,
    service: str | None,
    fetch: FetchFn,
    location_code: int,
    language_code: str | None,
    min_volume: int,
    limit: int,
    classes: tuple[str, ...],
) -> tuple[list[dict], list[str]]:
    """``fetch`` the seed, or serve the rows an earlier client already fetched for it.

    Outcomes recorded on ``pool.report``: ``reused`` and ``reused_empty`` (a saved fetch
    served), ``fetched`` (fetched and saved), ``fetched_not_saved`` (fetched; the answer
    was partial), ``not_pooled`` (fetched; the seed cannot be pooled), ``pool_error`` (the
    pool failed and the seed was fetched as without a pool). With the pool off, no
    language, or the provider in sandbox mode (its data is fake and must never be saved),
    the call goes straight to ``fetch`` and nothing is recorded.
    """

    async def plain() -> tuple[list[dict], list[str]]:
        return await fetch(
            seed,
            location_code=location_code,
            min_volume=min_volume,
            limit=limit,
            classes=classes,
            language_code=language_code,
        )

    if not pool.enabled or not language_code or getattr(get_settings(), "dataforseo_sandbox", False):
        return await plain()

    # Everything that reads the pool or builds rows from it sits in one guard: whatever
    # fails there, the seed is fetched as without a pool. ``fetch`` itself is outside it,
    # so a provider failure is never retried.
    try:
        key = seed_key(seed)
        wanted_classes = set(classes) if classes else set(_POOLED_CLASSES)
        # A seed that names a place saves almost nothing (its place rows are dropped), and
        # a fetch of fewer match classes is narrower than the key says. Neither is pooled.
        pooled = (
            bool(key)
            and not is_location_specific(key, pool.places)
            and _POOLED_CLASSES <= wanted_classes
        )
        reused = (
            await _serve_hit(
                seed,
                key,
                pool,
                service,
                location_code=location_code,
                language_code=language_code,
                min_volume=min_volume,
                limit=limit,
            )
            if pooled
            else None
        )
    except Exception as exc:  # noqa: BLE001 - any pool failure falls back to a normal fetch
        _log_error("read", seed, exc)
        rows, errors = await plain()
        pool.report.record(service, seed, "pool_error", len(rows))
        return rows, errors

    if reused is not None:
        return reused

    rows, errors = await plain()
    if not pooled:
        pool.report.record(service, seed, "not_pooled", len(rows))
        return rows, errors

    outcome = "fetched"
    try:
        status = await save_seed(
            key,
            seed_text=_query(seed),
            location_code=location_code,
            language_code=language_code,
            min_volume=min_volume,
            limit=limit,
            rows=rows,
            errors=errors,
            places=pool.places,
            industry_key=pool.industry_key,
        )
        if status == "partial":
            outcome = "fetched_not_saved"
    except Exception as exc:  # noqa: BLE001 - the rows were fetched; only the save failed
        _log_error("save", seed, exc)
        outcome = "pool_error"
    pool.report.record(service, seed, outcome, len(rows))
    return rows, errors
