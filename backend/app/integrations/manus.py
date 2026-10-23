"""Manus API v2 client (https://open.manus.ai/docs/v2).

Only the calls the site-preview pipeline uses. Every response is wrapped as
``{"ok": true, "request_id": ..., ...}`` or ``{"ok": false, "error": {code, message}}``;
an error becomes ``ManusError``. Each call is retried up to 4 times with backoff on
429, 5xx and dropped connections, with a 60 s timeout per attempt.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import get_settings
from app.integrations.web_fetch import assert_safe_url
from app.logging_config import get_logger

log = get_logger("manus")

TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 4
_RETRY_STATUS = {429, 500, 502, 503, 504}


class ManusError(RuntimeError):
    def __init__(self, message: str, *, code: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def configured() -> bool:
    return bool(get_settings().manus_api_key)


def _headers() -> dict[str, str]:
    key = get_settings().manus_api_key
    if not key:
        raise ManusError("MANUS_API_KEY is not set", code="not_configured")
    return {"x-manus-api-key": key, "Content-Type": "application/json"}


async def _call(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{get_settings().manus_api_base_url.rstrip('/')}/v2/{path}"
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.request(method, url, params=params, json=body, headers=_headers())
            if resp.status_code in _RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                last = ManusError(f"HTTP {resp.status_code}", status=resp.status_code)
                await asyncio.sleep(min(30.0, 2.0 * (2**attempt)))
                continue
            try:
                data = resp.json()
            except ValueError as exc:
                raise ManusError(f"Non-JSON response (HTTP {resp.status_code})", status=resp.status_code) from exc
            if not resp.is_success or not data.get("ok", False):
                err = data.get("error") or {}
                raise ManusError(
                    str(err.get("message") or f"HTTP {resp.status_code}"),
                    code=str(err.get("code") or ""),
                    status=resp.status_code,
                )
            return data
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last = exc
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(min(30.0, 2.0 * (2**attempt)))
                continue
            raise ManusError(f"Connection failed: {type(exc).__name__}", code="connection") from exc
    raise ManusError(str(last or "request failed"))


# --------------------------------------------------------------------- tasks


async def create_task(
    *,
    content: list[dict[str, Any]] | str,
    title: str | None = None,
    project_id: str | None = None,
    connectors: list[str] | None = None,
    agent_profile: str | None = None,
    structured_output_schema: dict[str, Any] | None = None,
    interactive_mode: bool = True,
    locale: str = "en",
) -> dict[str, Any]:
    """Start a task. Returns ``{task_id, task_title, task_url}``."""
    message: dict[str, Any] = {"content": content}
    if connectors:
        message["connectors"] = connectors
    body: dict[str, Any] = {"message": message, "interactive_mode": interactive_mode, "locale": locale}
    if title:
        body["title"] = title
    if project_id:
        body["project_id"] = project_id
    if agent_profile:
        body["agent_profile"] = agent_profile
    if structured_output_schema:
        body["structured_output_schema"] = structured_output_schema
    return await _call("POST", "task.create", body=body)


async def send_message(
    task_id: str,
    content: list[dict[str, Any]] | str,
    *,
    structured_output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"task_id": task_id, "message": {"content": content}}
    if structured_output_schema:
        body["structured_output_schema"] = structured_output_schema
    return await _call("POST", "task.sendMessage", body=body)


async def confirm_action(task_id: str, event_id: str, input_: dict[str, Any]) -> dict[str, Any]:
    return await _call("POST", "task.confirmAction", body={"task_id": task_id, "event_id": event_id, "input": input_})


async def list_messages(
    task_id: str,
    *,
    limit: int = 60,
    order: str = "desc",
    verbose: bool = True,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Events (newest first by default). ``verbose`` adds tool, plan and reasoning events."""
    params: dict[str, Any] = {
        "task_id": task_id,
        "limit": max(1, min(int(limit), 200)),
        "order": order,
        "verbose": "true" if verbose else "false",
    }
    if cursor:
        params["cursor"] = cursor
    data = await _call("GET", "task.listMessages", params=params)
    for event in data.get("messages") or []:
        ts = event.get("timestamp")
        if isinstance(ts, str):  # documented as integer ms; tolerate a string
            try:
                event["timestamp"] = int(float(ts))
            except ValueError:
                event["timestamp"] = 0
    return data


async def task_detail(task_id: str) -> dict[str, Any]:
    """``task`` with ``status``, ``credit_usage``, ``has_running_background_jobs``, ``task_url``."""
    return (await _call("GET", "task.detail", params={"task_id": task_id})).get("task") or {}


async def stop_task(task_id: str) -> None:
    await _call("POST", "task.stop", body={"task_id": task_id})


# ------------------------------------------------------------------ account


async def list_projects() -> list[dict[str, Any]]:
    return list((await _call("GET", "project.list")).get("data") or [])


async def list_connectors() -> list[dict[str, Any]]:
    return list((await _call("GET", "connector.list")).get("data") or [])


async def available_credits() -> dict[str, Any]:
    """Credit balance. The spec nests it under ``data``; the live API (checked
    2026-10-16) returns the fields at the top level, so both are accepted."""
    raw = await _call("GET", "usage.availableCredits")
    if isinstance(raw.get("data"), dict):
        return raw["data"]
    return {k: v for k, v in raw.items() if k not in ("ok", "request_id")}


async def download(url: str, *, max_bytes: int = 25 * 1024 * 1024, max_redirects: int = 3) -> bytes:
    """Fetch an attachment. Its URL is a temporary signed link, so no key is sent.
    Every hop, redirects included, must be a public address; the size is capped
    while streaming."""
    current = url
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        for _hop in range(max_redirects + 1):
            ok, safe = assert_safe_url(current)
            if not ok:
                raise ManusError(f"Attachment address is not public ({safe})", code="unsafe_url")
            async with client.stream("GET", safe) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ManusError("Redirect without a location", code="bad_redirect")
                    current = urljoin(safe, location)
                    continue
                if resp.status_code != 200:
                    raise ManusError(f"Attachment download failed (HTTP {resp.status_code})", status=resp.status_code)
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ManusError(f"Attachment over {max_bytes // (1024 * 1024)} MB", code="too_large")
                return bytes(body)
    raise ManusError("Too many redirects", code="bad_redirect")
