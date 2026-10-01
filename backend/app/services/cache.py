"""Redis cache helpers (competitor research reuse window).

Every public function here is async and offloads the underlying sync `redis`
client calls via asyncio.to_thread — the redis-py client is blocking, and
calling it directly from an async def would stall the whole event loop for
every other concurrent request during each round trip.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("cache")
_client = None


def _connect_redis():
    """Blocking connect+ping. Only ever call this via asyncio.to_thread."""
    global _client
    if _client is not None:
        return _client
    settings = get_settings()
    try:
        import redis

        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        _client.ping()
        return _client
    except Exception as exc:  # noqa: BLE001
        log.warning("redis_unavailable", error=str(exc))
        _client = False  # type: ignore[assignment]
        return None


async def get_redis():
    return await asyncio.to_thread(_connect_redis)


async def cache_get(key: str) -> Any | None:
    r = await get_redis()
    if not r:
        return None
    try:
        raw = await asyncio.to_thread(r.get, key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_get_failed", key=key, error=str(exc))
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    r = await get_redis()
    if not r:
        return
    try:
        await asyncio.to_thread(r.setex, key, ttl_seconds, json.dumps(value))
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_set_failed", key=key, error=str(exc))


async def cache_get_many(keys: list[str]) -> dict[str, Any]:
    """Batch read. One MGET instead of one GET (and one thread hop) per key.

    Callers that look up hundreds of keys — the keyword validation gate — would
    otherwise pay a thread round-trip each. Missing and undecodable values are
    simply absent from the result.
    """
    if not keys:
        return {}
    r = await get_redis()
    if not r:
        return {}
    try:
        raws = await asyncio.to_thread(r.mget, list(keys))
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_get_many_failed", count=len(keys), error=str(exc))
        return {}
    out: dict[str, Any] = {}
    for key, raw in zip(keys, raws or []):
        if not raw:
            continue
        try:
            out[key] = json.loads(raw)
        except (TypeError, ValueError):
            continue
    return out


async def cache_set_many(values: dict[str, Any], ttl_seconds: int) -> None:
    """Batch write via a single pipeline round-trip."""
    if not values:
        return
    r = await get_redis()
    if not r:
        return

    def _write() -> None:
        pipe = r.pipeline()
        for key, value in values.items():
            pipe.setex(key, ttl_seconds, json.dumps(value))
        pipe.execute()

    try:
        await asyncio.to_thread(_write)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_set_many_failed", count=len(values), error=str(exc))


async def cache_delete(key: str) -> None:
    r = await get_redis()
    if not r:
        return
    try:
        await asyncio.to_thread(r.delete, key)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_delete_failed", key=key, error=str(exc))


def competitor_cache_key(client_id: str, *, fingerprint: str | None = None) -> str:
    """Cache key for a competitor scan.

    The fingerprint carries the CDD inputs the scan depends on (industry,
    services). Without it a 14-day cached scan survives a CDD update that
    changes the client's industry — the cache would keep serving competitors
    from the old vertical. Including it makes the cache self-invalidating.
    """
    # v5 — industry-dynamic competitor discovery (any vertical, not agency-default)
    suffix = f":{fingerprint}" if fingerprint else ""
    return f"searchfit:competitor_scan:v5:{client_id}{suffix}"


def competitor_cache_ttl() -> int:
    settings = get_settings()
    return max(1, settings.competitor_cache_days) * 24 * 3600
