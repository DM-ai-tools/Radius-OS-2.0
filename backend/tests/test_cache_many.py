"""Batched Redis reads/writes — the keyword gate looks up hundreds of keys."""

from __future__ import annotations

import json

from app.services import cache


class _FakeRedis:
    def __init__(self, data: dict[str, str] | None = None):
        self.data = dict(data or {})
        self.mget_calls: list[list[str]] = []
        self.setex_calls: list[tuple[str, int, str]] = []

    def mget(self, keys):
        self.mget_calls.append(list(keys))
        return [self.data.get(k) for k in keys]

    def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.data[key] = value

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: _FakeRedis):
        self.redis = redis
        self.queued: list[tuple[str, int, str]] = []

    def setex(self, key, ttl, value):
        self.queued.append((key, ttl, value))
        return self

    def execute(self):
        for key, ttl, value in self.queued:
            self.redis.setex(key, ttl, value)
        return [True] * len(self.queued)


async def test_get_many_returns_decoded_values_in_one_call(monkeypatch):
    fake = _FakeRedis({"a": json.dumps({"v": 1}), "b": json.dumps({"v": 2})})

    async def _get_redis():
        return fake

    monkeypatch.setattr(cache, "get_redis", _get_redis)
    out = await cache.cache_get_many(["a", "b", "missing"])
    assert out == {"a": {"v": 1}, "b": {"v": 2}}
    assert len(fake.mget_calls) == 1, "must be one MGET, not one GET per key"


async def test_get_many_with_no_keys_makes_no_call(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(cache, "get_redis", _get_redis)
    assert await cache.cache_get_many([]) == {}
    assert fake.mget_calls == []


async def test_get_many_returns_empty_without_redis(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr(cache, "get_redis", _no_redis)
    assert await cache.cache_get_many(["a"]) == {}


async def test_get_many_survives_a_redis_error(monkeypatch):
    class _Boom(_FakeRedis):
        def mget(self, keys):
            raise RuntimeError("redis down")

    async def _get_redis():
        return _Boom()

    monkeypatch.setattr(cache, "get_redis", _get_redis)
    assert await cache.cache_get_many(["a"]) == {}


async def test_get_many_skips_undecodable_values(monkeypatch):
    fake = _FakeRedis({"a": "not json", "b": json.dumps({"v": 2})})

    async def _get_redis():
        return fake

    monkeypatch.setattr(cache, "get_redis", _get_redis)
    assert await cache.cache_get_many(["a", "b"]) == {"b": {"v": 2}}


async def test_set_many_writes_every_pair(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(cache, "get_redis", _get_redis)
    await cache.cache_set_many({"a": {"v": 1}, "b": {"v": 2}}, 60)
    assert {k for k, _ttl, _v in fake.setex_calls} == {"a", "b"}
    assert json.loads(fake.data["a"]) == {"v": 1}


async def test_set_many_survives_a_redis_error(monkeypatch):
    class _Boom(_FakeRedis):
        def pipeline(self):
            raise RuntimeError("redis down")

    async def _get_redis():
        return _Boom()

    monkeypatch.setattr(cache, "get_redis", _get_redis)
    await cache.cache_set_many({"a": {"v": 1}}, 60)  # must not raise
