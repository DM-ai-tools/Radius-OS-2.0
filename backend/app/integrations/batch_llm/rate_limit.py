"""Sliding-window rate limiter updated from Retry-After / optional rpm-tpm."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimitState:
    rpm: int | None = None
    tpm_in: int | None = None
    tpm_out: int | None = None
    request_times: deque[float] = field(default_factory=deque)
    token_events: deque[tuple[float, int, int]] = field(default_factory=deque)
    cooldown_until: float = 0.0

    def note_request(self, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        now = time.monotonic()
        self.request_times.append(now)
        self.token_events.append((now, prompt_tokens, completion_tokens))
        self._prune(now)

    def note_rate_limit(self, retry_after: float | None = None) -> None:
        wait = float(retry_after) if retry_after is not None else 5.0
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + max(0.5, wait))
        # Shrink effective capacity after a 429
        if self.rpm and self.rpm > 1:
            self.rpm = max(1, self.rpm // 2)

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self.request_times and self.request_times[0] < cutoff:
            self.request_times.popleft()
        while self.token_events and self.token_events[0][0] < cutoff:
            self.token_events.popleft()

    def headroom_ratio(self) -> float:
        """1.0 = full headroom, near 0 = saturated."""
        now = time.monotonic()
        self._prune(now)
        if now < self.cooldown_until:
            return 0.0
        ratios: list[float] = []
        if self.rpm:
            ratios.append(1.0 - (len(self.request_times) / max(1, self.rpm)))
        if self.tpm_in:
            used = sum(p for _, p, _ in self.token_events)
            ratios.append(1.0 - (used / max(1, self.tpm_in)))
        if self.tpm_out:
            used = sum(c for _, _, c in self.token_events)
            ratios.append(1.0 - (used / max(1, self.tpm_out)))
        if not ratios:
            return 1.0
        return max(0.0, min(1.0, min(ratios)))

    def suggested_concurrency(self, base: int) -> int:
        ratio = self.headroom_ratio()
        if ratio <= 0.05:
            return 1
        if ratio < 0.35:
            return max(1, base // 2)
        return max(1, base)

    async def wait_if_needed(self) -> None:
        now = time.monotonic()
        if now < self.cooldown_until:
            await asyncio.sleep(self.cooldown_until - now)
        self._prune(time.monotonic())
        if self.rpm and len(self.request_times) >= self.rpm:
            oldest = self.request_times[0]
            await asyncio.sleep(max(0.05, 60.0 - (time.monotonic() - oldest)))
