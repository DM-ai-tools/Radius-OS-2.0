"""Measured keyword metrics — the only door metrics enter the pipeline through.

Every number here comes from a DataForSEO response. Nothing is derived,
inferred or filled in from a formula: a keyword the provider did not answer
for keeps ``None`` and is reported as unmeasured, so the workbook can print
"N/A" instead of a plausible-looking invention.

The provider batches are sized from the documented per-task maxima
(1000 keywords for search_volume, bulk_keyword_difficulty and search_intent),
so the whole pool is priced rather than an arbitrary head slice of it.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.config import get_settings
from app.integrations import dataforseo
from app.logging_config import get_logger
from app.services.keyword_opportunity import normalize_provider_intent

log = get_logger("keyword_metrics")

METRIC_FIELDS = (
    "volume",
    "cpc",
    "competition",
    "trend",
    "difficulty",
    "intent",
    "intent_probability",
    "secondary_intents",
)


class MissingLocaleError(ValueError):
    """Raised when a request would go out without an explicit location/language.

    Falling back to a default country silently produced metrics for the wrong
    market, which is worse than no metrics at all.
    """


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def volumes_disagree(ads: Any, click: Any, ratio: float) -> bool:
    """True when two measured volumes differ by more than ``ratio``.

    A missing side is not a discrepancy. Zero against a positive volume is.
    """
    if ads is None or click is None:
        return False
    try:
        left, right = int(ads), int(click)
    except (TypeError, ValueError):
        return False
    if left < 0 or right < 0:
        return False
    if left == 0 or right == 0:
        return left != right
    return (max(left, right) / min(left, right)) > float(ratio)


def _empty_metrics() -> dict[str, Any]:
    return {field: None for field in METRIC_FIELDS}


async def fetch_keyword_metrics(
    keywords: list[str],
    *,
    location_code: int | None,
    language_code: str | None,
    labs_location_code: int | None = None,
    with_intent: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Measured metrics for every keyword, keyed by normalized keyword.

    Returns ``(metrics, errors)``. Volume, CPC and competition come from
    Google Ads search volume; difficulty from bulk keyword difficulty; intent
    from the search intent endpoint. Any field the provider stayed silent on
    is ``None``.
    """
    if not location_code:
        raise MissingLocaleError("location_code is required for keyword metrics")
    if not language_code:
        raise MissingLocaleError("language_code is required for keyword metrics")

    wanted = [k for k in dict.fromkeys(_norm(k) for k in keywords) if k]
    if not wanted:
        return {}, []

    labs_location = labs_location_code or dataforseo.coerce_labs_location(location_code)
    errors: list[str] = []

    calls = [
        dataforseo.search_volume(
            wanted, location_code=location_code, language_code=language_code
        ),
        dataforseo.clickstream_search_volume(
            wanted, location_code=location_code, language_code=language_code
        ),
        dataforseo.keyword_difficulty(
            wanted, location_code=labs_location, language_code=language_code
        ),
    ]
    if with_intent:
        calls.append(
            dataforseo.search_intent(
                wanted, location_code=labs_location, language_code=language_code
            )
        )

    results = await asyncio.gather(*calls, return_exceptions=True)

    metrics: dict[str, dict[str, Any]] = {kw: _empty_metrics() for kw in wanted}
    ratio = float(get_settings().volume_discrepancy_ratio or 2)

    def _rows(index: int, label: str) -> list[dict[str, Any]]:
        if index >= len(results):
            return []
        result = results[index]
        if isinstance(result, BaseException):
            log.warning("keyword_metrics_call_failed", call=label, error=str(result)[:240])
            errors.append(f"{label}_exception")
            return []
        rows, errs = result
        errors.extend(errs)
        return rows

    for row in _rows(0, "dataforseo_volume"):
        entry = metrics.get(_norm(row.get("keyword")))
        if entry is None:
            continue
        entry["volume"] = row.get("volume")
        entry["cpc"] = row.get("cpc")
        entry["competition"] = row.get("competition")
        entry["trend"] = row.get("trend")

    for row in _rows(1, "dataforseo_clickstream"):
        entry = metrics.get(_norm(row.get("keyword")))
        if entry is None:
            continue
        entry["volume_clickstream"] = row.get("volume_clickstream")

    for row in _rows(2, "dataforseo_kd"):
        entry = metrics.get(_norm(row.get("keyword")))
        if entry is None:
            continue
        entry["difficulty"] = row.get("difficulty")

    if with_intent:
        for row in _rows(3, "dataforseo_intent"):
            entry = metrics.get(_norm(row.get("keyword")))
            if entry is None:
                continue
            entry["intent"] = row.get("intent")
            entry["intent_probability"] = row.get("intent_probability")
            entry["secondary_intents"] = row.get("secondary_intents") or []

    for entry in metrics.values():
        ads = entry.get("volume")
        click = entry.get("volume_clickstream")
        entry["volume_discrepancy"] = volumes_disagree(ads, click, ratio)

    measured = sum(1 for m in metrics.values() if m.get("volume") is not None)
    log.info(
        "keyword_metrics_fetched",
        requested=len(wanted),
        measured_volume=measured,
        errors=len(errors),
    )
    return metrics, errors


def merge_metrics_into_rows(
    rows: list[dict[str, Any]],
    metrics: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stamp measured metrics onto keyword rows without inventing anything.

    A measured value already on the row is never replaced by a null, and a row
    the provider never answered for is marked ``unmeasured`` so downstream
    reports can show "N/A" rather than a blank that reads as zero.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        merged = dict(row)
        measured = metrics.get(_norm(row.get("keyword"))) or {}
        for field in METRIC_FIELDS:
            value = measured.get(field)
            if value is not None:
                merged[field] = value
            else:
                merged.setdefault(field, None)
        if "volume_clickstream" in measured:
            merged["volume_clickstream"] = measured.get("volume_clickstream")
        else:
            merged.setdefault("volume_clickstream", None)
        merged["volume_discrepancy"] = bool(measured.get("volume_discrepancy"))
        merged["volume_basis"] = "measured" if merged.get("volume") is not None else "unmeasured"
        # Intent is a provider metric under the same rule as volume. Downstream
        # heuristics still fill a blank intent from keyword text; this keeps a
        # guess distinguishable from a measured label. Only this response's label
        # makes a row "measured": an intent the row already held may be a guess.
        provider_intent = normalize_provider_intent(measured.get("intent"))
        if provider_intent:
            merged["intent_basis"] = "measured"
            merged["provider_intent"] = provider_intent
        else:
            merged["intent_basis"] = row.get("intent_basis") or "unmeasured"
            merged.setdefault("provider_intent", None)
        out.append(merged)
    return out


async def measured_rows_for_pool(
    keywords: list[str],
    *,
    location_code: int | None,
    language_code: str | None,
    labs_location_code: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Provider-shaped volume and difficulty rows for an entire keyword pool.

    Phase 5 consumes these through the same relevance filters as any other
    provider feed. Keywords the provider returned no measurement for are left
    out entirely rather than entering the pool as a zero.
    """
    metrics, errors = await fetch_keyword_metrics(
        keywords,
        location_code=location_code,
        language_code=language_code,
        labs_location_code=labs_location_code,
    )

    volume_rows: list[dict[str, Any]] = []
    kd_rows: list[dict[str, Any]] = []
    for keyword, measured in metrics.items():
        if measured.get("volume") is not None:
            volume_rows.append(
                {
                    "keyword": keyword,
                    "volume": measured.get("volume"),
                    "cpc": measured.get("cpc"),
                    "competition": measured.get("competition"),
                    "trend": measured.get("trend"),
                    "intent": measured.get("intent"),
                    "intent_probability": measured.get("intent_probability"),
                    "secondary_intents": measured.get("secondary_intents") or [],
                    "volume_basis": "measured",
                    "intent_basis": "measured" if normalize_provider_intent(measured.get("intent")) else "unmeasured",
                    "provider_intent": normalize_provider_intent(measured.get("intent")),
                    "source": "dataforseo",
                }
            )
        if measured.get("difficulty") is not None:
            kd_rows.append(
                {
                    "keyword": keyword,
                    "difficulty": measured.get("difficulty"),
                    "source": "dataforseo",
                }
            )
    return volume_rows, kd_rows, errors
