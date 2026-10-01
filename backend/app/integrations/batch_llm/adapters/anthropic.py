"""Direct Anthropic Messages adapter (when LLM_PROVIDER=anthropic)."""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.integrations.batch_llm.adapters.base import AdapterResponse, LLMAdapter
from app.logging_config import get_logger

log = get_logger("batch_llm.anthropic")


class AnthropicAdapter(LLMAdapter):
    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.anthropic_api_key or "").strip()

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
                error="anthropic_not_configured",
                status_code=401,
                retryable=False,
            )
        try:
            import anthropic
        except ImportError:
            return AdapterResponse(
                text="", error="anthropic_sdk_missing", retryable=False
            )

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        # Strip openrouter prefix if present
        model_id = model.split("/")[-1] if "/" in model else model
        system_arg: Any = system
        if use_prompt_cache:
            system_arg = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        try:
            msg = await client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                system=system_arg,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.RateLimitError as exc:
            retry_after = None
            headers = getattr(exc, "headers", None) or {}
            if hasattr(headers, "get"):
                raw = headers.get("retry-after")
                try:
                    retry_after = float(raw) if raw else None
                except (TypeError, ValueError):
                    retry_after = None
            return AdapterResponse(
                text="",
                error="rate_limited",
                status_code=429,
                retryable=True,
                retry_after=retry_after,
            )
        except anthropic.APIStatusError as exc:
            code = getattr(exc, "status_code", 500) or 500
            return AdapterResponse(
                text="",
                error=f"http_{code}",
                status_code=code,
                retryable=code >= 500 or code == 429,
            )
        except Exception as exc:  # noqa: BLE001
            return AdapterResponse(
                text="",
                error=f"{type(exc).__name__}",
                retryable=True,
            )

        parts = []
        for block in msg.content or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        usage = getattr(msg, "usage", None)
        stop = getattr(msg, "stop_reason", None)
        return AdapterResponse(
            text="".join(parts).strip(),
            prompt_tokens=getattr(usage, "input_tokens", None),
            completion_tokens=getattr(usage, "output_tokens", None),
            finish_reason=str(stop) if stop else None,
            truncated=str(stop or "") == "max_tokens",
        )
