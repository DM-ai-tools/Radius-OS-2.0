"""Redis cache helpers (competitor research reuse window)."""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("cache")
_client = None


def get_redis():
    global _client
    if _client is not None:
        return _client
    settings = get_settings()
    try:
        import redis

        _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        _client.ping()
        return _client
    except Exception as exc:  # noqa: BLE001
        log.warning("redis_unavailable", error=str(exc))
        _client = False  # type: ignore[assignment]
        return None


def cache_get(key: str) -> Any | None:
    r = get_redis()
    if not r:
        return None
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_get_failed", key=key, error=str(exc))
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    r = get_redis()
    if not r:
        return
    try:
        r.setex(key, ttl_seconds, json.dumps(value))
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_set_failed", key=key, error=str(exc))


def cache_delete(key: str) -> None:
    r = get_redis()
    if not r:
        return
    try:
        r.delete(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache_delete_failed", key=key, error=str(exc))


def competitor_cache_key(client_id: str) -> str:
    # v5 — industry-dynamic competitor discovery (any vertical, not agency-default)
    return f"searchfit:competitor_scan:v5:{client_id}"


def competitor_cache_ttl() -> int:
    settings = get_settings()
    return max(1, settings.competitor_cache_days) * 24 * 3600
