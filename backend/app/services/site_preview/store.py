"""Where a preview run lives: Redis (survives restarts), mirrored in memory.

A preview is a review aid, never the source of truth (WordPress receives the
approved draft), so it lives outside the profile summaries that phase re-runs and
approvals overwrite. The run state is small and read on every poll; the page HTML
and the undo copy are separate keys read only when needed. When Redis is down the
in-memory copy keeps the run working for this server process.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.services.cache import cache_delete, cache_get, cache_set

TTL_SECONDS = 30 * 24 * 3600
_memory: dict[str, Any] = {}


def page_key(draft: dict[str, Any]) -> str:
    """One preview per draft page: its URL, else its keyword, else its title."""
    ident = str(draft.get("url") or draft.get("keyword") or draft.get("title") or "").strip().lower()
    return hashlib.sha1(ident.encode()).hexdigest()[:16]


def _key(client_id: str, key: str, part: str) -> str:
    return f"searchfit:sitepreview:v1:{client_id}:{key}:{part}"


async def _get(name: str) -> Any:
    value = await cache_get(name)
    if value is None:
        value = _memory.get(name)
    return value


async def _set(name: str, value: Any) -> None:
    _memory[name] = value
    await cache_set(name, value, TTL_SECONDS)


async def _drop(name: str) -> None:
    _memory.pop(name, None)
    await cache_delete(name)


async def load_state(client_id: str, key: str) -> dict[str, Any] | None:
    state = await _get(_key(client_id, key, "state"))
    return state if isinstance(state, dict) else None


async def save_state(client_id: str, key: str, state: dict[str, Any]) -> None:
    await _set(_key(client_id, key, "state"), state)


async def load_html(client_id: str, key: str, *, previous: bool = False) -> str:
    value = await _get(_key(client_id, key, "previous_html" if previous else "html"))
    return value if isinstance(value, str) else ""


async def save_html(client_id: str, key: str, html: str, *, previous: bool = False) -> None:
    name = _key(client_id, key, "previous_html" if previous else "html")
    if html:
        await _set(name, html)
    else:
        await _drop(name)
