"""AUDIT-012: cache.py's Redis helpers must be async and never block the
event loop. Previously get_redis()/cache_get()/cache_set()/cache_delete()
called the synchronous redis-py client directly from inside async callers
(competitor.py, review.py, phase_validation/apply.py) — a real round trip to
Redis would stall every other concurrent request on the same worker."""

from __future__ import annotations

import inspect

from app.services import cache


def test_public_cache_functions_are_coroutines():
    assert inspect.iscoroutinefunction(cache.get_redis)
    assert inspect.iscoroutinefunction(cache.cache_get)
    assert inspect.iscoroutinefunction(cache.cache_set)
    assert inspect.iscoroutinefunction(cache.cache_delete)


async def test_cache_get_returns_none_when_redis_unavailable(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr(cache, "get_redis", _no_redis)
    assert await cache.cache_get("some-key") is None


async def test_cache_set_and_get_round_trip_through_a_fake_client(monkeypatch):
    store: dict[str, str] = {}

    class _FakeRedis:
        def get(self, key):
            return store.get(key)

        def setex(self, key, ttl, value):
            store[key] = value

        def delete(self, key):
            store.pop(key, None)

    fake = _FakeRedis()

    async def _fake_get_redis():
        return fake

    monkeypatch.setattr(cache, "get_redis", _fake_get_redis)

    await cache.cache_set("k", {"a": 1}, 60)
    assert await cache.cache_get("k") == {"a": 1}

    await cache.cache_delete("k")
    assert await cache.cache_get("k") is None


async def test_cache_get_survives_client_raising(monkeypatch):
    class _BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis gone")

    async def _fake_get_redis():
        return _BrokenRedis()

    monkeypatch.setattr(cache, "get_redis", _fake_get_redis)
    assert await cache.cache_get("k") is None
