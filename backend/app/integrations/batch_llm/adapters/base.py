"""Provider adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AdapterResponse:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    truncated: bool = False
    raw: dict[str, Any] | None = None
    retry_after: float | None = None
    status_code: int | None = None
    error: str | None = None
    retryable: bool = False


class LLMAdapter(ABC):
    @abstractmethod
    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        use_prompt_cache: bool = False,
    ) -> AdapterResponse:
        raise NotImplementedError

    async def submit_batch(
        self,
        *,
        model: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise NotImplementedError("Batch API not supported by this adapter")

    async def fetch_batch(self, batch_id: str) -> dict[str, Any]:
        raise NotImplementedError("Batch API not supported by this adapter")
