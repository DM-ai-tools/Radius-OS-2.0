"""Operational dead man's switch — lock the API if check-ins stop.

Design:
- Off by default (no behaviour change until explicitly enabled).
- Periodic check-in with a shared secret resets the timer.
- When overdue: API routes return 503; health/ops/SPA stay available.
- Never deletes data — maintenance lock only.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger
from app.services.cache import cache_get, cache_set, get_redis

log = get_logger("dead_man_switch")

REDIS_KEY = "radius:ops:dead_man_switch"
FILE_NAME = "dead_man_switch.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _file_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / FILE_NAME


async def _read_state() -> dict[str, Any]:
    cached = await cache_get(REDIS_KEY)
    if isinstance(cached, dict) and cached.get("checked_at"):
        return cached
    path = _file_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("checked_at"):
                return data
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("dead_man_switch_file_read_failed", error=str(exc))
    return {}


async def _write_state(state: dict[str, Any]) -> None:
    settings = get_settings()
    ttl = max(1, int(settings.dead_man_switch_max_days) + 7) * 86400
    await cache_set(REDIS_KEY, state, ttl)
    path = _file_path()
    try:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("dead_man_switch_file_write_failed", error=str(exc))


async def ensure_seeded() -> dict[str, Any]:
    """If enabled and never checked in, seed now so deploy doesn't instantly lock."""
    state = await _read_state()
    if state.get("checked_at"):
        return state
    now = _utc_now().isoformat()
    seeded = {
        "checked_at": now,
        "source": "auto_seed",
        "note": "First observation while switch armed — timer starts now.",
    }
    await _write_state(seeded)
    log.info("dead_man_switch_seeded", checked_at=now)
    return seeded


async def record_check_in(*, note: str | None = None) -> dict[str, Any]:
    now = _utc_now().isoformat()
    state = {
        "checked_at": now,
        "source": "check_in",
        "note": (note or "").strip()[:240] or None,
    }
    await _write_state(state)
    log.info("dead_man_switch_checked_in", checked_at=now)
    return await get_status()


async def get_status() -> dict[str, Any]:
    settings = get_settings()
    enabled = bool(settings.dead_man_switch_enabled)
    max_days = max(1, int(settings.dead_man_switch_max_days or 14))
    if not enabled:
        return {
            "enabled": False,
            "armed": False,
            "tripped": False,
            "max_days": max_days,
            "checked_at": None,
            "expires_at": None,
            "seconds_remaining": None,
            "days_remaining": None,
            "storage": await _storage_backend(),
        }

    state = await ensure_seeded()
    checked = _parse_ts(str(state.get("checked_at") or ""))
    if checked is None:
        # Should not happen after seed — treat as tripped for safety.
        return {
            "enabled": True,
            "armed": True,
            "tripped": True,
            "max_days": max_days,
            "checked_at": None,
            "expires_at": None,
            "seconds_remaining": 0,
            "days_remaining": 0,
            "reason": "missing_check_in",
            "storage": await _storage_backend(),
        }

    expires = checked + timedelta(days=max_days)
    now = _utc_now()
    remaining = int((expires - now).total_seconds())
    tripped = remaining <= 0
    return {
        "enabled": True,
        "armed": True,
        "tripped": tripped,
        "max_days": max_days,
        "checked_at": checked.isoformat(),
        "expires_at": expires.isoformat(),
        "seconds_remaining": max(0, remaining),
        "days_remaining": max(0, remaining) / 86400.0,
        "last_note": state.get("note"),
        "storage": await _storage_backend(),
    }


async def is_tripped() -> bool:
    settings = get_settings()
    if not settings.dead_man_switch_enabled:
        return False
    status = await get_status()
    return bool(status.get("tripped"))


async def _storage_backend() -> str:
    r = await get_redis()
    return "redis+file" if r else "file"
