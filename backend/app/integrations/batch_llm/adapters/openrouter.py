"""OpenRouter chat + optional Batch API adapter."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.integrations.batch_llm.adapters.base import AdapterResponse, LLMAdapter
from app.logging_config import get_logger

log = get_logger("batch_llm.openrouter")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_BATCH_URL = "https://openrouter.ai/api/beta/batches"


class OpenRouterAdapter(LLMAdapter):
    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.openrouter_api_key or "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://radius-os.local",
            "X-Title": "Radius OS batch_llm",
        }

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        use_prompt_cache: bool = False,
    ) -> AdapterResponse:
        if not self.api_key:
            return AdapterResponse(
                text="",
                error="openrouter_not_configured",
                status_code=401,
                retryable=False,
            )

        system_content: Any
        if use_prompt_cache:
            system_content = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_content = system

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user},
            ],
        }
        if "gemini" in model.lower():
            answer_reserve = min(2048, max(512, max_tokens // 2))
            body["reasoning"] = {"max_tokens": max(128, max_tokens - answer_reserve)}

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    OPENROUTER_URL, headers=self._headers(), json=body
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return AdapterResponse(
                text="",
                error=f"transport:{type(exc).__name__}",
                retryable=True,
            )

        retry_after = None
        raw_ra = resp.headers.get("Retry-After")
        if raw_ra:
            try:
                retry_after = float(raw_ra)
            except ValueError:
                retry_after = None

        if resp.status_code == 429:
            return AdapterResponse(
                text="",
                error="rate_limited",
                status_code=429,
                retryable=True,
                retry_after=retry_after,
            )
        if resp.status_code in (401, 403):
            return AdapterResponse(
                text="",
                error=f"http_{resp.status_code}",
                status_code=resp.status_code,
                retryable=False,
            )
        if resp.status_code >= 500:
            return AdapterResponse(
                text="",
                error=f"http_{resp.status_code}",
                status_code=resp.status_code,
                retryable=True,
                retry_after=retry_after,
            )
        if resp.status_code >= 400:
            return AdapterResponse(
                text="",
                error=f"http_{resp.status_code}:{resp.text[:200]}",
                status_code=resp.status_code,
                retryable=False,
            )

        data = resp.json()
        choices = data.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(str(part.get("text") or ""))
            content = "".join(parts)
        text = str(content or "").strip()
        usage = data.get("usage") or {}
        finish = choice.get("finish_reason") or choice.get("native_finish_reason")
        truncated = str(finish or "").lower() in {
            "length",
            "max_tokens",
            "max_tokens_exceeded",
        }
        return AdapterResponse(
            text=text,
            prompt_tokens=int(usage["prompt_tokens"])
            if usage.get("prompt_tokens") is not None
            else None,
            completion_tokens=int(usage["completion_tokens"])
            if usage.get("completion_tokens") is not None
            else None,
            finish_reason=str(finish) if finish else None,
            truncated=truncated,
            raw=data if isinstance(data, dict) else None,
            status_code=resp.status_code,
        )

    async def submit_batch(
        self,
        *,
        model: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("openrouter_not_configured")
        body = {
            "endpoint": "/v1/chat/completions",
            "model": model,
            "completion_window": "24h",
            "requests": requests,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OPENROUTER_BATCH_URL, headers=self._headers(), json=body
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"batch_submit_http_{resp.status_code}:{resp.text[:300]}")
            return resp.json()

    async def fetch_batch(self, batch_id: str) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("openrouter_not_configured")
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"{OPENROUTER_BATCH_URL}/{batch_id}", headers=self._headers()
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"batch_fetch_http_{resp.status_code}:{resp.text[:300]}")
            return resp.json()
