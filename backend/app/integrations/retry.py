import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.logging_config import get_logger

log = get_logger("integrations.retry")
T = TypeVar("T")


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    label: str = "call",
) -> T:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            delay = base_delay * (2**i)
            log.warning("retry", label=label, attempt=i + 1, error=str(exc), delay=delay)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
