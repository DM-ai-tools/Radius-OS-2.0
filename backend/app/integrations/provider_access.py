"""Plan, credit, and rate-limit failures are not current access.

A 403 "insufficient plan", a zero-credit response, or a 429 is true only for
the moment it was returned. Later calls must not treat that record as still
active, and they must not treat the silence after it as proof the problem
is gone. Within a short window we skip repeat calls. After the window the
next call hits the provider again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

PLAN = "plan"
CREDIT = "credit"
RATE_LIMIT = "rate_limit"

RECHECK_AFTER = {
    PLAN: 120.0,
    CREDIT: 120.0,
    RATE_LIMIT: 45.0,
}


@dataclass
class AccessFailure:
    kind: str
    code: str
    recorded_at: float


_records: dict[str, AccessFailure] = {}


def _now() -> float:
    return time.monotonic()


def note_access_failure(provider: str, kind: str, code: str) -> None:
    _records[provider] = AccessFailure(kind=kind, code=code, recorded_at=_now())


def active_access_block(provider: str) -> str | None:
    """The recorded failure, only while its recheck window is still open."""
    row = _records.get(provider)
    if row is None:
        return None
    window = RECHECK_AFTER.get(row.kind, 0.0)
    if _now() - row.recorded_at >= window:
        return None
    return row.code


def clear_access_failure(provider: str) -> None:
    _records.pop(provider, None)


def reset_access_failures() -> None:
    _records.clear()
