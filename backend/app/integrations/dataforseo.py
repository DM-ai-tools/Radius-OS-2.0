"""DataForSEO Labs helpers — competitors, keywords, SERP, keyword gap."""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("dataforseo")

DEFAULT_LOCATION = 2840  # United States
DEFAULT_LANGUAGE = "en"

# Labs Google endpoints only accept country-level codes (not city/metro).
_LABS_COUNTRY_CODES = {
    2840,  # US
    2826,  # GB
    2124,  # CA
    2036,  # AU
    2356,  # IN
    2702,  # SG
    2784,  # AE
    2554,  # NZ
    2372,  # IE
    2276,  # DE
    2250,  # FR
}
_CITY_TO_LABS_COUNTRY = {
    1013962: 2840,
    1023191: 2840,
    1013366: 2840,
    1013988: 2840,
    1013990: 2840,
    1013983: 2840,
    1014040: 2840,
    1014041: 2840,
    1013755: 2840,
    1014044: 2840,
    1013445: 2840,
    1013997: 2840,
    1014047: 2840,
    1013764: 2840,
    1014221: 2840,
    1013485: 2840,
    1013996: 2840,
    1013433: 2840,
    9047038: 2826,
    9047081: 2826,
    1002417: 2124,
    1002514: 2124,
    1000286: 2036,  # Sydney
    1000259: 2036,  # Melbourne
    1007751: 2356,
    1007745: 2356,
    1007740: 2356,
    9044034: 2784,
}


def coerce_labs_location(location_code: int | None) -> int:
    """Map city/metro codes to the country Labs endpoints will accept."""
    try:
        code = int(location_code or 0)
    except (TypeError, ValueError):
        return DEFAULT_LOCATION
    if code in _LABS_COUNTRY_CODES:
        return code
    mapped = _CITY_TO_LABS_COUNTRY.get(code)
    if mapped:
        if mapped != code:
            log.info("dataforseo_labs_location_coerced", from_code=code, to_code=mapped)
        return mapped
    if code >= 1_000_000:
        log.warning("dataforseo_labs_location_fallback", from_code=code, to_code=DEFAULT_LOCATION)
        return DEFAULT_LOCATION
    return code or DEFAULT_LOCATION


def sanitize_keyword(keyword: str) -> str:
    """Strip characters DataForSEO rejects (parentheses, symbols) without inventing terms."""
    q = re.sub(r"\([^)]*\)", " ", keyword or "")
    q = q.replace("&", " ").replace("/", " ")
    q = re.sub(r"[^\w\s'-]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:80]


# Documented ceiling for the bulk keyword endpoints (google_ads/search_volume,
# bulk_keyword_difficulty, search_intent): 1000 keywords per task.
MAX_KEYWORDS_PER_TASK = 1000
# Documented ceiling for the Labs `limit` request field.
MAX_LABS_LIMIT = 1000


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    """Sanitize, drop blanks and case-insensitive duplicates, keep input order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in keywords:
        cleaned = sanitize_keyword(str(raw or ""))
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _chunks(items: list[str], size: int) -> list[list[str]]:
    size = max(1, int(size))
    return [items[i : i + size] for i in range(0, len(items), size)]


def _labs_query_fields(
    *,
    limit: int,
    min_volume: int | None,
    volume_field: str = "keyword_info.search_volume",
) -> dict[str, Any]:
    """limit / filters / order_by, pushed to the provider rather than done here.

    Labs accepts up to 8 filters and 3 order_by rules; `limit` maxes at 1000.
    Sorting by volume server-side means a truncated page keeps the keywords
    worth having instead of an arbitrary slice.

    ``volume_field`` differs by endpoint. Suggestions and ideas use
    ``keyword_info.search_volume``. Related keywords and ranked keywords nest
    it under ``keyword_data.keyword_info.search_volume``.
    """
    fields: dict[str, Any] = {
        "limit": max(1, min(int(limit), MAX_LABS_LIMIT)),
        "order_by": [f"{volume_field},desc"],
    }
    if min_volume is not None:
        fields["filters"] = [volume_field, ">=", int(min_volume)]
    return fields


def normalize_target(value: str) -> str:
    """Bare registrable host for the Labs `target` field — no scheme, no path, no www."""
    raw = (value or "").strip()
    if "://" in raw:
        raw = urlparse(raw).hostname or raw
    return raw.split("/")[0].lower().removeprefix("www.").strip()


def _is_locale_rejection(error: str | None) -> bool:
    """True only when the provider explicitly refused a locale field.

    A missing body ("no_body") is a transport failure, not the provider telling
    us location_code is unsupported.
    """
    if not error:
        return False
    text = error.lower()
    if "no_body" in text:
        return False
    return "location" in text or "language" in text


def _task_error(body: dict[str, Any] | None) -> str | None:
    if not body:
        return "no_body"
    tasks = body.get("tasks") or []
    if not tasks or not isinstance(tasks[0], dict):
        return None
    code = tasks[0].get("status_code")
    if code in (20000, 20100, None):
        return None
    return str(tasks[0].get("status_message") or code)

# Kept well under the per-agent turn budget so one slow endpoint cannot stall a phase.
REQUEST_TIMEOUT = 35


def _auth_header() -> str | None:
    settings = get_settings()
    if not settings.dataforseo_login or not settings.dataforseo_password:
        return None
    if settings.use_mock_providers:
        return None
    token = base64.b64encode(
        f"{settings.dataforseo_login}:{settings.dataforseo_password}".encode()
    ).decode()
    return f"Basic {token}"


def api_base() -> str:
    """Live API, or the sandbox host when DATAFORSEO_SANDBOX is set."""
    settings = get_settings()
    host = "sandbox.dataforseo.com" if settings.dataforseo_sandbox else "api.dataforseo.com"
    return f"https://{host}/v3"


def should_retry_dataforseo(
    *,
    http_status: int | None,
    body_status: int | None,
    transport_error: bool,
) -> bool:
    """Retry timeouts, 5xx, and rate limits. Never retry 40xxx validation errors."""
    if transport_error:
        return True
    if http_status == 429 or (http_status is not None and http_status >= 500):
        return True
    if http_status is not None and 400 <= http_status < 500:
        return False
    if body_status is None:
        return False
    if 40000 <= int(body_status) < 50000:
        return False
    return int(body_status) >= 50000


async def _post(path: str, payload: list[dict[str, Any]]) -> dict[str, Any] | None:
    import asyncio
    import time

    from app.services.api_meter import record_api_call

    auth = _auth_header()
    if not auth:
        return None
    if await _over_daily_budget():
        await record_api_call(
            provider="dataforseo",
            operation=path,
            status="error",
            error_detail="dataforseo_daily_budget_exceeded",
            estimated_cost_usd=0,
            request_meta={"tasks": len(payload), "blocked": True},
        )
        return None
    url = f"{api_base()}{path}"
    delays = (0.4, 1.2)
    last_body: dict[str, Any] | None = None
    for attempt in range(3):
        t0 = time.perf_counter()
        transport_error = False
        http_status: int | None = None
        body: dict[str, Any] | None = None
        error_detail = ""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": auth, "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            transport_error = True
            error_detail = str(exc)[:500]
            log.warning("dataforseo_request_failed", path=path, error=error_detail[:240])
        else:
            http_status = resp.status_code
            if resp.status_code >= 400:
                error_detail = resp.text[:500]
                log.warning(
                    "dataforseo_http_error",
                    path=path,
                    status=resp.status_code,
                    body=resp.text[:240],
                )
            else:
                body = resp.json()
                last_body = body
                status = body.get("status_code")
                if status not in (20000, None) and not body.get("tasks"):
                    error_detail = str(body.get("status_message") or status)[:500]
                    log.warning(
                        "dataforseo_status",
                        path=path,
                        status=status,
                        message=body.get("status_message"),
                    )
                else:
                    await record_api_call(
                        provider="dataforseo",
                        operation=path,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        status="success",
                        estimated_cost_usd=_response_cost(body),
                        request_meta={"tasks": len(payload)},
                    )
                    return body
        body_status = None
        if isinstance(last_body, dict) and last_body.get("status_code") is not None:
            try:
                body_status = int(last_body.get("status_code"))
            except (TypeError, ValueError):
                body_status = None
        retry = should_retry_dataforseo(
            http_status=http_status,
            body_status=body_status if body is not None or transport_error else body_status,
            transport_error=transport_error,
        )
        await record_api_call(
            provider="dataforseo",
            operation=path,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            status="error",
            error_detail=error_detail,
            request_meta={"tasks": len(payload), "attempt": attempt + 1},
        )
        if not retry or attempt >= 2:
            return None
        await asyncio.sleep(delays[attempt])
    return None


# Assumed cost of the next Labs/SERP call. The real price arrives only after
# the response, so the cap stops when today's spend plus this reserve would
# cross the configured budget.
_BUDGET_RESERVE_USD = 0.02


async def _over_daily_budget() -> bool:
    """True when another DataForSEO call would cross today's configured cap.

    A cap of 0 disables the check. A meter that cannot be read does not block.
    """
    from app.services.api_meter import provider_spend_today_usd

    cap = float(get_settings().dataforseo_daily_budget_usd or 0)
    if cap <= 0:
        return False
    spent = await provider_spend_today_usd("dataforseo")
    if spent is None:
        return False
    if spent + _BUDGET_RESERVE_USD >= cap:
        log.warning(
            "dataforseo_budget_blocked",
            spent_usd=round(spent, 4),
            cap_usd=cap,
        )
        return True
    return False


def _response_cost(body: dict[str, Any] | None) -> float | None:
    """Actual USD cost reported by DataForSEO, or None if it didn't say."""
    if not body:
        return None
    raw = body.get("cost")
    if raw is None:
        tasks = body.get("tasks") or []
        totals = [
            t.get("cost")
            for t in tasks
            if isinstance(t, dict) and isinstance(t.get("cost"), (int, float))
        ]
        if not totals:
            return None
        return float(sum(totals))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _task_items(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not body:
        return []
    tasks = body.get("tasks") or []
    if not tasks:
        return []
    # A POST may carry several tasks. Reading tasks[0] alone silently dropped
    # every task after the first, so anything that batches must merge them —
    # and one failed task must not discard its healthy siblings.
    out: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("status_code") not in (20000, 20100, None):
            log.warning(
                "dataforseo_task_status",
                status=task.get("status_code"),
                message=task.get("status_message"),
            )
            continue
        out.extend(_result_items(task.get("result") or []))
    return out


def _result_items(results: list[Any]) -> list[dict[str, Any]]:
    """Normalize one task's `result` into a flat list of item dicts."""
    if not results:
        return []
    first = results[0]
    if not isinstance(first, dict):
        return []
    # Two documented shapes:
    #   Labs            -> result[0].items = [...]   (items may be null/empty)
    #   keywords_data   -> result            = [...]  (keyword objects directly)
    # Assuming only the Labs shape made search_volume() silently return nothing
    # on every call. The discriminator is whether the wrapper DECLARES items:
    # an explicit "items": null means an empty Labs result, not a flat payload.
    if "items" in first or "items_count" in first:
        out: list[dict[str, Any]] = []
        for wrapper in results:
            if not isinstance(wrapper, dict):
                continue
            out.extend(i for i in (wrapper.get("items") or []) if isinstance(i, dict))
        return out
    return [row for row in results if isinstance(row, dict)]


def _successful_result_rows(body: dict[str, Any] | None) -> int:
    """How many result objects a successful task actually returned.

    Used to tell "the provider sent keyword rows we failed to parse" apart
    from "the provider sent an empty result".
    """
    count = 0
    for task in (body or {}).get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if task.get("status_code") not in (20000, 20100, None):
            continue
        result = task.get("result")
        if isinstance(result, list):
            count += sum(1 for row in result if isinstance(row, dict))
        elif isinstance(result, dict):
            count += 1
    return count


async def competitors_domain(domain: str, *, limit: int = 10) -> list[dict[str, str]]:
    """Return organic SERP competitors for a domain via DataForSEO Labs."""
    target = domain.lower().removeprefix("www.")
    body = await _post(
        "/dataforseo_labs/google/competitors_domain/live",
        [
            {
                "target": target,
                "location_code": DEFAULT_LOCATION,
                "language_code": DEFAULT_LANGUAGE,
                "exclude_top_domains": True,
                "exclude_domains": [target, f"www.{target}"],
                "limit": limit,
                "item_types": ["organic"],
            }
        ],
    )
    out: list[dict[str, str]] = []
    for item in _task_items(body):
        d = (item.get("domain") or "").strip().lower().removeprefix("www.")
        if not d or d == target:
            continue
        label = d.split(".")[0].replace("-", " ").title()
        out.append({"name": label, "url": f"https://{d}"})
    log.info("competitors_domain_ok", domain=target, count=len(out))
    return out


def _keyword_rows(body: dict[str, Any] | None, *, endpoint: str) -> list[dict[str, Any]]:
    """Parse DataForSEO Labs keyword items.

    Handles both the nested shape (``related_keywords`` wraps each result in
    ``keyword_data``) and the flat shape used by ``keyword_suggestions`` /
    ``keyword_ideas``."""
    out: list[dict[str, Any]] = []
    for item in _task_items(body):
        nested = item.get("keyword_data")
        kw_data = nested if isinstance(nested, dict) else item
        kw = str(kw_data.get("keyword") or item.get("keyword") or "").strip()
        if not kw:
            continue
        info = kw_data.get("keyword_info")
        if not isinstance(info, dict):
            info = {}
        props = kw_data.get("keyword_properties")
        if not isinstance(props, dict):
            props = item.get("keyword_properties") or {}
        out.append(
            {
                "keyword": kw,
                "volume": _int_or_none(info.get("search_volume")),
                "competition": _float_or_none(info.get("competition")),
                "cpc": _float_or_none(info.get("cpc")),
                "difficulty": _int_or_none(
                    props.get("keyword_difficulty") if isinstance(props, dict) else None
                ),
                "source": "dataforseo",
                "dataforseo_endpoint": endpoint,
            }
        )
    return out


async def related_keywords(
    seed: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 50,
    min_volume: int | None = None,
    depth: int = 1,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Google 'searches related to' expansion — the RELATED match source.

    ``depth`` is 0–4. 1 is one hop from the seed (DataForSEO's useful default).
    """
    errors: list[str] = []
    if not seed.strip():
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors
    if not language_code:
        errors.append("dataforseo_language_missing")
        return [], errors

    body = await _post(
        "/dataforseo_labs/google/related_keywords/live",
        [
            {
                "keyword": sanitize_keyword(seed) or seed.strip(),
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "depth": max(0, min(int(depth), 4)),
                "include_seed_keyword": True,
                "include_serp_info": False,
                **_labs_query_fields(
                    limit=limit,
                    min_volume=min_volume,
                    volume_field="keyword_data.keyword_info.search_volume",
                ),
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_related_failed")
        return [], errors

    out = _keyword_rows(body, endpoint="related_keywords")
    log.info("related_keywords_ok", seed=seed, count=len(out))
    return out, errors


async def keyword_suggestions(
    seed: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 50,
    min_volume: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Full-text search for queries CONTAINING the seed — the PHRASE match source."""
    errors: list[str] = []
    if not seed.strip():
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    body = await _post(
        "/dataforseo_labs/google/keyword_suggestions/live",
        [
            {
                "keyword": sanitize_keyword(seed) or seed.strip(),
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "include_seed_keyword": True,
                "include_serp_info": False,
                **_labs_query_fields(limit=limit, min_volume=min_volume),
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_suggestions_failed")
        return [], errors

    out = _keyword_rows(body, endpoint="keyword_suggestions")
    log.info("keyword_suggestions_ok", seed=seed, count=len(out))
    return out, errors


async def keyword_ideas(
    seed: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 50,
    min_volume: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Category-based ideas that need NOT contain the seed — the BROAD match source."""
    errors: list[str] = []
    if not seed.strip():
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    body = await _post(
        "/dataforseo_labs/google/keyword_ideas/live",
        [
            {
                "keywords": [sanitize_keyword(seed) or seed.strip()],
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "include_serp_info": False,
                **_labs_query_fields(limit=limit, min_volume=min_volume),
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_ideas_failed")
        return [], errors

    out = _keyword_rows(body, endpoint="keyword_ideas")
    log.info("keyword_ideas_ok", seed=seed, count=len(out))
    return out, errors


async def search_volume(
    keywords: list[str],
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    cleaned = _dedupe_keywords(keywords)
    if not cleaned:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    locations = [int(location_code or DEFAULT_LOCATION)]
    country = coerce_labs_location(location_code)
    if country not in locations:
        locations.append(country)

    out: list[dict[str, Any]] = []
    ok_batches = 0
    # The endpoint takes up to MAX_KEYWORDS_PER_TASK per task; send as many
    # tasks as the pool needs. Truncating to one batch left the rest of the
    # pool with no measured volume at all.
    for batch in _chunks(cleaned, MAX_KEYWORDS_PER_TASK):
        body: dict[str, Any] | None = None
        for loc in locations:
            body = await _post(
                "/keywords_data/google_ads/search_volume/live",
                [
                    {
                        "keywords": batch,
                        "location_code": loc,
                        "language_code": language_code,
                    }
                ],
            )
            err = _task_error(body)
            if err and "location_code" in err.lower():
                errors.append(f"dataforseo_volume_location:{err}")
                continue
            if body is not None and not err:
                # This location works — don't re-probe it on later batches.
                if locations[0] != loc:
                    locations = [loc]
                break
            if err:
                errors.append(f"dataforseo_volume:{err}")
                body = None
        if body is None:
            errors.append("dataforseo_volume_failed")
            continue
        ok_batches += 1
        items = _task_items(body)
        # Google Ads puts keyword rows directly in result[]. If a 200 body
        # still yields nothing, the volume was discarded — say so.
        if not items and _successful_result_rows(body):
            errors.append("dataforseo_volume_unparsed")
            log.warning(
                "dataforseo_volume_unparsed",
                result_rows=_successful_result_rows(body),
            )
        for item in items:
            kw = str(item.get("keyword") or "").strip()
            if not kw:
                continue
            monthly = item.get("monthly_searches") or []
            trend = _trend_from_monthly(monthly)
            out.append(
                {
                    "keyword": kw,
                    "volume": _int_or_none(item.get("search_volume")),
                    "competition": _float_or_none(item.get("competition")),
                    "cpc": _float_or_none(item.get("cpc")),
                    "trend": trend,
                    "source": "dataforseo",
                }
            )
    return out, errors


async def clickstream_search_volume(
    keywords: list[str],
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Clickstream search volume. Stored beside Google Ads volume, never blended.

    Endpoint: POST /keywords_data/clickstream_data/bulk_search_volume/live
    Up to 1000 keywords per task. language_code is required by this app even
    though the published schema keys the series by location.
    """
    errors: list[str] = []
    cleaned = _dedupe_keywords(keywords)
    if not cleaned:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors
    if not location_code or not language_code:
        errors.append("dataforseo_locale_missing")
        return [], errors

    out: list[dict[str, Any]] = []
    for batch in _chunks(cleaned, MAX_KEYWORDS_PER_TASK):
        body = await _post(
            "/keywords_data/clickstream_data/bulk_search_volume/live",
            [
                {
                    "keywords": batch,
                    "location_code": int(location_code),
                    "language_code": language_code,
                }
            ],
        )
        if body is None:
            errors.append("dataforseo_clickstream_volume_failed")
            continue
        for item in _task_items(body):
            kw = str(item.get("keyword") or "").strip()
            if not kw:
                continue
            out.append(
                {
                    "keyword": kw,
                    "volume_clickstream": _int_or_none(item.get("search_volume")),
                    "source": "dataforseo",
                    "dataforseo_endpoint": "clickstream_bulk_search_volume",
                }
            )
    return out, errors


async def keyword_difficulty(
    keywords: list[str],
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    cleaned = _dedupe_keywords(keywords)
    if not cleaned:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    out: list[dict[str, Any]] = []
    for batch in _chunks(cleaned, MAX_KEYWORDS_PER_TASK):
        body = await _post(
            "/dataforseo_labs/google/bulk_keyword_difficulty/live",
            [
                {
                    "keywords": batch,
                    "location_code": coerce_labs_location(location_code),
                    "language_code": language_code,
                }
            ],
        )
        if body is None:
            errors.append("dataforseo_kd_failed")
            continue
        for item in _task_items(body):
            kw = str(item.get("keyword") or "").strip()
            if not kw:
                continue
            out.append(
                {
                    "keyword": kw,
                    "difficulty": _int_or_none(item.get("keyword_difficulty")),
                    "source": "dataforseo",
                }
            )
    return out, errors


async def serp_overview(
    keyword: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    depth: int = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not keyword.strip():
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    body = await _post(
        "/serp/google/organic/live/regular",
        [
            {
                "keyword": keyword.strip(),
                "location_code": location_code,
                "language_code": language_code,
                "depth": depth,
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_serp_failed")
        return [], errors

    out: list[dict[str, Any]] = []
    for item in _task_items(body):
        if item.get("type") and item.get("type") != "organic":
            continue
        domain = str(item.get("domain") or "").lower().removeprefix("www.")
        out.append(
            {
                "keyword": keyword.strip(),
                "position": _int_or_none(item.get("rank_group") or item.get("rank_absolute")),
                "domain": domain,
                "url": item.get("url"),
                "title": item.get("title"),
                "source": "dataforseo",
            }
        )
    return out, errors


async def serp_advanced(
    keyword: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    depth: int = 10,
) -> tuple[dict[str, Any], list[str]]:
    """Organic SERP plus features (PAA, featured snippet) when the advanced endpoint is available."""
    errors: list[str] = []
    empty: dict[str, Any] = {
        "organic": [],
        "people_also_ask": [],
        "featured_snippet": None,
        "item_types": [],
        "ai_overview": False,
        "validated": False,
    }
    if not keyword.strip():
        return empty, errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return empty, errors

    body = await _post(
        "/serp/google/organic/live/advanced",
        [
            {
                "keyword": keyword.strip(),
                "location_code": location_code,
                "language_code": language_code,
                "depth": depth,
            }
        ],
    )
    items = _task_items(body)
    if not items:
        organic, err = await serp_overview(
            keyword, location_code=location_code, language_code=language_code, depth=depth
        )
        errors.extend(err)
        if organic:
            return {
                "organic": organic,
                "people_also_ask": [],
                "featured_snippet": None,
                "item_types": ["organic"],
                "ai_overview": False,
                "validated": True,
                "note": "Advanced SERP unavailable — organic titles only; PAA unvalidated.",
            }, errors
        errors.append("dataforseo_serp_failed")
        return empty, errors

    organic: list[dict[str, Any]] = []
    paa: list[str] = []
    snippet: dict[str, Any] | None = None
    types: list[str] = []
    for item in items:
        itype = str(item.get("type") or "organic")
        if itype not in types:
            types.append(itype)
        if itype in ("organic", "featured_snippet"):
            domain = str(item.get("domain") or "").lower().removeprefix("www.")
            row = {
                "keyword": keyword.strip(),
                "position": _int_or_none(item.get("rank_group") or item.get("rank_absolute")),
                "domain": domain,
                "url": item.get("url"),
                "title": item.get("title"),
                "description": item.get("description") or item.get("snippet"),
                "source": "dataforseo",
                "type": itype,
            }
            if itype == "featured_snippet" and snippet is None:
                snippet = {
                    **row,
                    "snippet_format": item.get("featured_title")
                    or item.get("table") and "table"
                    or item.get("items") and "list"
                    or "paragraph",
                }
            if itype == "organic":
                organic.append(row)
        elif itype == "people_also_ask":
            for q in item.get("items") or []:
                if isinstance(q, dict) and q.get("title"):
                    paa.append(str(q["title"]))
                elif isinstance(q, str):
                    paa.append(q)
    return {
        "organic": organic[:10],
        "people_also_ask": paa[:8],
        "featured_snippet": snippet,
        "item_types": types,
        "ai_overview": any("ai_overview" in t for t in types),
        "validated": True,
    }, errors


async def ranked_keywords(
    domain: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 50,
    min_volume: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Organic keywords a domain already ranks for (DataForSEO Labs).

    Replaces the SEMrush ``organic_keywords`` report — this deployment has no
    SEMrush subscription, so client and competitor ranking data comes from Labs.
    """
    errors: list[str] = []
    target = normalize_target(domain)
    if not target:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    body = await _post(
        "/dataforseo_labs/google/ranked_keywords/live",
        [
            {
                "target": target,
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "item_types": ["organic"],
                **_labs_query_fields(
                    limit=limit,
                    min_volume=min_volume,
                    volume_field="keyword_data.keyword_info.search_volume",
                ),
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_ranked_keywords_failed")
        return [], errors

    out: list[dict[str, Any]] = []
    for item in _task_items(body):
        kw_data = item.get("keyword_data") if isinstance(item, dict) else None
        kw_data = kw_data if isinstance(kw_data, dict) else {}
        kw = str(kw_data.get("keyword") or item.get("keyword") or "").strip()
        if not kw:
            continue
        info = kw_data.get("keyword_info")
        info = info if isinstance(info, dict) else {}
        serp = item.get("ranked_serp_element")
        serp_item = (serp or {}).get("serp_item") if isinstance(serp, dict) else None
        serp_item = serp_item if isinstance(serp_item, dict) else {}
        out.append(
            {
                "keyword": kw,
                "volume": _int_or_none(info.get("search_volume")),
                "cpc": _float_or_none(info.get("cpc")),
                "competition": _float_or_none(info.get("competition")),
                "position": _int_or_none(serp_item.get("rank_absolute")),
                "traffic": _int_or_none(serp_item.get("etv")),
                "domain": target,
                "source": "dataforseo",
            }
        )
    log.info("dataforseo_ranked_keywords_ok", domain=target, count=len(out))
    return out, errors


async def keyword_gap(
    target: str,
    competitors: list[str],
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 40,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keywords competitors rank for that target may miss (intersection / gap)."""
    errors: list[str] = []
    t = target.lower().removeprefix("www.")
    comps = [c.lower().removeprefix("www.") for c in competitors if c][:3]
    if not t or not comps:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    body = await _post(
        "/dataforseo_labs/google/domain_intersection/live",
        [
            {
                "target1": comps[0],
                "target2": comps[1] if len(comps) > 1 else comps[0],
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "intersections": True,
                "limit": max(1, min(int(limit), MAX_LABS_LIMIT)),
                "item_types": ["organic"],
            }
        ],
    )
    # Fallback: ranked keywords for first competitor
    if body is None:
        body = await _post(
            "/dataforseo_labs/google/ranked_keywords/live",
            [
                {
                    "target": comps[0],
                    "location_code": coerce_labs_location(location_code),
                    "language_code": language_code,
                    "limit": max(1, min(int(limit), MAX_LABS_LIMIT)),
                    "item_types": ["organic"],
                }
            ],
        )
        if body is None:
            errors.append("dataforseo_gap_failed")
            return [], errors

    out: list[dict[str, Any]] = []
    for item in _task_items(body):
        kw_data = item.get("keyword_data") or {}
        kw = str(
            (kw_data.get("keyword") if isinstance(kw_data, dict) else None)
            or item.get("keyword")
            or ""
        ).strip()
        if not kw:
            continue
        info = (kw_data.get("keyword_info") if isinstance(kw_data, dict) else None) or {}
        out.append(
            {
                "keyword": kw,
                "volume": _int_or_none(info.get("search_volume") if isinstance(info, dict) else None),
                "competitor_domain": comps[0],
                "gap_flag": True,
                "source": "dataforseo",
            }
        )
    log.info("keyword_gap_ok", target=t, count=len(out))
    return out, errors


def _trend_from_monthly(monthly: list[Any]) -> str:
    """rising | stable | declining from Google Ads monthly_searches."""
    if not isinstance(monthly, list) or len(monthly) < 3:
        return "stable"
    vols: list[int] = []
    for row in monthly[-6:]:
        if isinstance(row, dict):
            v = _int_or_none(row.get("search_volume"))
            if v is not None:
                vols.append(v)
    if len(vols) < 3:
        return "stable"
    early = sum(vols[: len(vols) // 2]) / max(1, len(vols) // 2)
    late = sum(vols[len(vols) // 2 :]) / max(1, len(vols) - len(vols) // 2)
    if early <= 0:
        return "rising" if late > 0 else "stable"
    change = (late - early) / early
    if change >= 0.2:
        return "rising"
    if change <= -0.2:
        return "declining"
    return "stable"


async def indexed_site_urls(domain: str, *, limit: int = 100) -> list[str]:
    """Google-indexed URLs for a domain via `site:` search. Works when the live
    site is behind a WAF and our crawler cannot fetch HTML."""
    target = domain.lower().removeprefix("www.").strip()
    if not target or not _auth_header():
        return []
    location = 2036 if target.endswith(".au") else DEFAULT_LOCATION
    queries = [f"site:{target}", f"site:{target}/blogs", f"site:{target}/blog"]
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if len(out) >= limit:
            break
        body = await _post(
            "/serp/google/organic/live/regular",
            [
                {
                    "keyword": q,
                    "location_code": location,
                    "language_code": DEFAULT_LANGUAGE,
                    "depth": min(max(limit, 10), 100),
                }
            ],
        )
        for item in _task_items(body):
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            host = urlparse(url).netloc.lower().removeprefix("www.")
            if host != target:
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
            if len(out) >= limit:
                break
    log.info("indexed_site_urls_ok", domain=target, count=len(out))
    return out


def _int_or_none(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def search_intent(
    keywords: list[str],
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Provider-labelled search intent (DataForSEO Labs).

    Returns the label and probability exactly as the provider gives them. When
    the provider says nothing for a keyword the intent stays None — nothing
    downstream may guess a label from the keyword string.

    The documented request fields are `keywords` and `tag` only, so a locale
    rejection falls back to a locale-free retry rather than losing the batch.
    """
    errors: list[str] = []
    cleaned = _dedupe_keywords(keywords)
    if not cleaned:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    send_locale = True
    out: list[dict[str, Any]] = []
    for batch in _chunks(cleaned, MAX_KEYWORDS_PER_TASK):
        body: dict[str, Any] | None = None
        transient_retried = False
        while True:
            task: dict[str, Any] = {"keywords": batch}
            if send_locale:
                task["location_code"] = coerce_labs_location(location_code)
                task["language_code"] = language_code
            body = await _post("/dataforseo_labs/google/search_intent/live", [task])
            err = _task_error(body)
            if body is not None and not err:
                break
            if send_locale and _is_locale_rejection(err):
                # Locale fields are undocumented here; drop them and retry once.
                # Only an explicit field rejection disables them — a transport
                # failure must not silently re-target later batches at another
                # market.
                log.info("dataforseo_intent_locale_rejected", error=str(err)[:120])
                send_locale = False
                body = None
                continue
            if not transient_retried:
                # One retry for a transport blip, keeping the locale intact.
                transient_retried = True
                body = None
                continue
            if err:
                errors.append(f"dataforseo_intent:{err}")
            body = None
            break
        if body is None:
            errors.append("dataforseo_intent_failed")
            continue
        for item in _task_items(body):
            kw = str(item.get("keyword") or "").strip()
            if not kw:
                continue
            primary = item.get("keyword_intent")
            primary = primary if isinstance(primary, dict) else {}
            secondary = [
                {
                    "label": str(s.get("label")),
                    "probability": _float_or_none(s.get("probability")),
                }
                for s in (item.get("secondary_keyword_intents") or [])
                if isinstance(s, dict) and s.get("label")
            ]
            label = primary.get("label")
            out.append(
                {
                    "keyword": kw,
                    "intent": str(label) if label else None,
                    "intent_probability": _float_or_none(primary.get("probability")),
                    "secondary_intents": secondary,
                    "source": "dataforseo",
                }
            )
    return out, errors


async def keywords_for_site(
    domain: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 100,
    min_volume: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keywords a site is relevant for (DataForSEO Labs), metrics included."""
    errors: list[str] = []
    target = normalize_target(domain)
    if not target:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    body = await _post(
        "/dataforseo_labs/google/keywords_for_site/live",
        [
            {
                "target": target,
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                **_labs_query_fields(limit=limit, min_volume=min_volume),
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_keywords_for_site_failed")
        return [], errors
    return _keyword_rows(body, endpoint="keywords_for_site"), errors


def _norm_keyword(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def pick_ranking_url(searches: list[dict[str, Any]], keyword: str) -> dict[str, Any] | None:
    """The top-search row whose keyword is this query and that has a ranking URL.

    A near miss is not a match. No URL means the row cannot point a fetch.
    """
    want = _norm_keyword(keyword)
    if not want:
        return None
    for row in searches:
        if not isinstance(row, dict):
            continue
        if _norm_keyword(row.get("keyword")) != want:
            continue
        url = str(row.get("url") or "").strip()
        if url:
            return row
    return None


async def relevant_pages(
    domain: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pages on a domain that rank, with organic traffic when DataForSEO measured it."""
    errors: list[str] = []
    target = normalize_target(domain)
    if not target:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors
    body = await _post(
        "/dataforseo_labs/google/relevant_pages/live",
        [
            {
                "target": target,
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "limit": max(1, min(int(limit), MAX_LABS_LIMIT)),
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_relevant_pages_failed")
        return [], errors
    out: list[dict[str, Any]] = []
    for item in _task_items(body):
        url = str(item.get("page_address") or "").strip()
        if not url:
            continue
        organic = ((item.get("metrics") or {}).get("organic") or {}) if isinstance(item.get("metrics"), dict) else {}
        if not isinstance(organic, dict):
            organic = {}
        etv = _float_or_none(organic.get("etv"))
        out.append(
            {
                "url": url,
                "organic_traffic": int(round(etv)) if etv is not None else None,
                "keyword_count": _int_or_none(organic.get("count")),
                "source": "dataforseo",
            }
        )
    return out, errors


async def top_searches(
    domain: str,
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Queries a domain ranks for, including the URL in that result when present."""
    errors: list[str] = []
    target = normalize_target(domain)
    if not target:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors
    body = await _post(
        "/dataforseo_labs/google/top_searches/live",
        [
            {
                "target": target,
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "limit": max(1, min(int(limit), MAX_LABS_LIMIT)),
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_top_searches_failed")
        return [], errors
    out: list[dict[str, Any]] = []
    for item in _task_items(body):
        kw_data = item.get("keyword_data") if isinstance(item.get("keyword_data"), dict) else item
        kw = str(kw_data.get("keyword") or item.get("keyword") or "").strip()
        if not kw:
            continue
        info = kw_data.get("keyword_info") if isinstance(kw_data.get("keyword_info"), dict) else {}
        serp = item.get("ranked_serp_element") if isinstance(item.get("ranked_serp_element"), dict) else {}
        serp_item = serp.get("serp_item") if isinstance(serp.get("serp_item"), dict) else {}
        out.append(
            {
                "keyword": kw,
                "url": str(serp_item.get("url") or serp_item.get("relative_url") or "").strip() or None,
                "position": _int_or_none(serp_item.get("rank_absolute") or serp_item.get("rank_group")),
                "volume": _int_or_none(info.get("search_volume")),
                "domain": target,
                "source": "dataforseo",
            }
        )
    return out, errors


async def bulk_traffic_estimation(
    domains: list[str],
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Estimated organic traffic for each domain. Missing targets are omitted."""
    errors: list[str] = []
    targets = []
    seen: set[str] = set()
    for raw in domains:
        target = normalize_target(raw)
        if not target or target in seen:
            continue
        seen.add(target)
        targets.append(target)
    if not targets:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors
    body = await _post(
        "/dataforseo_labs/google/bulk_traffic_estimation/live",
        [
            {
                "targets": targets[:1000],
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "item_types": ["organic"],
            }
        ],
    )
    if body is None:
        errors.append("dataforseo_traffic_failed")
        return [], errors
    out: list[dict[str, Any]] = []
    for item in _task_items(body):
        target = normalize_target(str(item.get("target") or ""))
        if not target:
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        organic = metrics.get("organic") if isinstance(metrics.get("organic"), dict) else {}
        etv = _float_or_none(organic.get("etv"))
        cost = _float_or_none(organic.get("estimated_paid_traffic_cost"))
        if etv is None and cost is None:
            continue
        out.append(
            {
                "domain": target,
                "organic_traffic": int(round(etv)) if etv is not None else None,
                "organic_traffic_cost": cost,
                "source": "dataforseo",
            }
        )
    return out, errors
