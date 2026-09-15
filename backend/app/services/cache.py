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


async def cache_delete(key: str) -> None:
    r = await get_redis()
    if not r:
        return
    try:
        await asyncio.to_thread(r.delete, key)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_delete_failed", key=key, error=str(exc))


def competitor_cache_key(client_id: str) -> str:
    # v5 — industry-dynamic competitor discovery (any vertical, not agency-default)
    return f"searchfit:competitor_scan:v5:{client_id}"


def competitor_cache_ttl() -> int:
    settings = get_settings()
    return max(1, settings.competitor_cache_days) * 24 * 3600
