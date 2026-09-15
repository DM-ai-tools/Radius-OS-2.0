"""Dead man's switch — operational lock when check-ins stop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import clear_settings_cache
from app.main import app
from app.services import dead_man_switch as dms


@pytest.fixture(autouse=True)
def _isolate_dms(tmp_path, monkeypatch):
    clear_settings_cache()
    monkeypatch.setenv("DEAD_MAN_SWITCH_ENABLED", "false")
    monkeypatch.setenv("DEAD_MAN_SWITCH_TOKEN", "test-dms-token-please-change")
    monkeypatch.setenv("DEAD_MAN_SWITCH_MAX_DAYS", "14")
    clear_settings_cache()

    file_path = tmp_path / "dead_man_switch.json"
    monkeypatch.setattr(dms, "_file_path", lambda: file_path)

    async def _no_redis_get(_key: str):
        return None

    async def _no_redis_set(_key: str, _value, _ttl: int):
        return None

    monkeypatch.setattr(dms, "cache_get", _no_redis_get)
    monkeypatch.setattr(dms, "cache_set", _no_redis_set)
    yield
    clear_settings_cache()


@pytest.mark.asyncio
async def test_disabled_by_default_api_works(monkeypatch):
    monkeypatch.setenv("DEAD_MAN_SWITCH_ENABLED", "false")
    clear_settings_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        body = res.json()
        assert body["dead_man_switch"]["enabled"] is False
        assert body["dead_man_switch"]["tripped"] is False


@pytest.mark.asyncio
async def test_check_in_requires_token(monkeypatch):
    monkeypatch.setenv("DEAD_MAN_SWITCH_ENABLED", "true")
    clear_settings_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad = await client.post("/api/v1/ops/dead-man-switch/check-in")
        assert bad.status_code == 401
        ok = await client.post(
            "/api/v1/ops/dead-man-switch/check-in",
            headers={"X-Dead-Man-Switch-Token": "test-dms-token-please-change"},
            json={"note": "weekly ping"},
        )
        assert ok.status_code == 200
        assert ok.json()["tripped"] is False
        assert ok.json()["enabled"] is True


@pytest.mark.asyncio
async def test_tripped_blocks_api_but_allows_check_in(monkeypatch):
    monkeypatch.setenv("DEAD_MAN_SWITCH_ENABLED", "true")
    monkeypatch.setenv("DEAD_MAN_SWITCH_MAX_DAYS", "1")
    clear_settings_cache()

    stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    path: Path = dms._file_path()
    path.write_text(
        '{"checked_at": "%s", "source": "test"}' % stale,
        encoding="utf-8",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        blocked = await client.get("/api/v1/clients")
        assert blocked.status_code == 503
        detail = blocked.json()["detail"]
        assert detail["code"] == "dead_man_switch_tripped"

        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["dead_man_switch"]["tripped"] is True

        revive = await client.post(
            "/api/v1/ops/dead-man-switch/check-in",
            headers={"Authorization": "Bearer test-dms-token-please-change"},
        )
        assert revive.status_code == 200
        assert revive.json()["tripped"] is False

        # After check-in, API traffic is allowed again (auth may still 401).
        after = await client.get("/api/v1/clients")
        assert after.status_code != 503
