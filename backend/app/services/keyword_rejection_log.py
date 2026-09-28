"""Persist every keyword the relevance gate rejected.

``filter_relevant_keywords`` always returned the full exclusion list, but only a
12-row sample ever survived into the stored audit — and nothing wrote the rest
anywhere. That made false negatives invisible after a run finished and left no
data to tune thresholds against.

This module is the single place that turns those in-memory exclusions into
``keyword_rejections`` rows.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import KeywordRejection

log = get_logger("keyword_rejection_log")

# Coarse buckets, so rejections can be sliced by where the keyword came from
# without having to know every fine-grained filter label.
SOURCE_PATHS = ("provider", "fallback", "competitor", "chat", "opportunity", "unknown")

_LABEL_PREFIX_TO_PATH: tuple[tuple[str, str], ...] = (
    ("seeding", "provider"),
    ("semrush", "provider"),
    ("ahrefs", "provider"),
    ("dataforseo", "provider"),
    ("competitor_page", "competitor"),
    ("chat", "chat"),
    ("opportunity", "opportunity"),
)


def classify_source_path(
    source_label: str | None,
    *,
    provider_fallback: bool = False,
) -> str:
    """Map a filter's ``source_label`` to a coarse source path."""
    if provider_fallback:
        return "fallback"
    label = str(source_label or "").strip().lower()
    if not label:
        return "unknown"
    for prefix, path in _LABEL_PREFIX_TO_PATH:
        if label.startswith(prefix):
            return path
    return "unknown"


def _coerce_volume(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def record_keyword_rejections(
    db: AsyncSession,
    *,
    client_id: UUID,
    agent_key: str,
    rejections: list[dict[str, Any]],
    run_id: UUID | None = None,
) -> int:
    """Write every rejection row. Returns how many were persisted.

    Rows without a keyword are skipped — they carry no information. Nothing is
    truncated or sampled.
    """
    written = 0
    for row in rejections or []:
        if not isinstance(row, dict):
            continue
        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        source_label = row.get("source")
        db.add(
            KeywordRejection(
                client_id=client_id,
                run_id=run_id,
                agent_key=agent_key,
                keyword=keyword[:500],
                seed=(str(row.get("seed")).strip()[:500] if row.get("seed") else None),
                reason=str(row.get("reason") or "unknown")[:120],
                source_path=classify_source_path(
                    source_label,
                    provider_fallback=bool(row.get("provider_fallback")),
                ),
                source_label=(str(source_label)[:120] if source_label else None),
                match_class=(
                    str(row.get("match_class"))[:40] if row.get("match_class") else None
                ),
                volume=_coerce_volume(row.get("volume")),
            )
        )
        written += 1

    if written:
        await db.flush()
        log.info(
            "keyword_rejections_recorded",
            client_id=str(client_id),
            agent_key=agent_key,
            count=written,
        )
    return written
