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


async def _post(path: str, payload: list[dict[str, Any]]) -> dict[str, Any] | None:
    import time

    from app.services.api_meter import record_api_call

    auth = _auth_header()
    if not auth:
        return None
    url = f"https://api.dataforseo.com/v3{path}"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={"Authorization": auth, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        log.warning("dataforseo_request_failed", path=path, error=str(exc)[:240])
        await record_api_call(
            provider="dataforseo",
            operation=path,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            status="error",
            error_detail=str(exc)[:500],
            request_meta={"tasks": len(payload)},
        )
        return None
    else:
        if resp.status_code >= 400:
            log.warning(
                "dataforseo_http_error",
                path=path,
                status=resp.status_code,
                body=resp.text[:240],
            )
            await record_api_call(
                provider="dataforseo",
                operation=path,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                status="error",
                error_detail=resp.text[:500],
                request_meta={"tasks": len(payload)},
            )
            return None
        body = resp.json()
        status = body.get("status_code")
        if status not in (20000, None) and not body.get("tasks"):
            log.warning(
                "dataforseo_status",
                path=path,
                status=status,
                message=body.get("status_message"),
            )
            await record_api_call(
                provider="dataforseo",
                operation=path,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                status="error",
                error_detail=str(body.get("status_message") or status)[:500],
                request_meta={"tasks": len(payload)},
            )
            return None
        await record_api_call(
            provider="dataforseo",
            operation=path,
            latency_ms=int((time.perf_counter() - t0)  * 1000),
            status="success",
            request_meta={"tasks": len(payload)},
        )
        return body


def _task_items(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not body:
        return []
    tasks = body.get("tasks") or []
    if not tasks:
        return []
    task = tasks[0]
    if not isinstance(task, dict):
        return []
    if task.get("status_code") not in (20000, 20100, None):
        log.warning(
            "dataforseo_task_status",
            status=task.get("status_code"),
            message=task.get("status_message"),
        )
        return []
    results = task.get("result") or []
    if not results:
        return []
    first = results[0]
    items = first.get("items") if isinstance(first, dict) else None
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    return []


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
) -> tuple[list[dict[str, Any]], list[str]]:
    """Google 'searches related to' expansion — the RELATED match source."""
    errors: list[str] = []
    if not seed.strip():
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    body = await _post(
        "/dataforseo_labs/google/related_keywords/live",
        [
            {
                "keyword": sanitize_keyword(seed) or seed.strip(),
                "location_code": coerce_labs_location(location_code),
                "language_code": language_code,
                "limit": min(limit, 100),
                "include_seed_keyword": True,
                "include_serp_info": False,
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
                "limit": min(limit, 100),
                "include_seed_keyword": True,
                "include_serp_info": False,
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
                "limit": min(limit, 100),
                "include_serp_info": False,
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
    seen: set[str] = set()
    batch: list[str] = []
    for raw in keywords:
        cleaned = sanitize_keyword(str(raw or ""))
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        batch.append(cleaned)
        if len(batch) >= 40:
            break
    if not batch:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

    locations = [int(location_code or DEFAULT_LOCATION)]
    country = coerce_labs_location(location_code)
    if country not in locations:
        locations.append(country)

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
            break
        if err:
            errors.append(f"dataforseo_volume:{err}")
            body = None
    if body is None:
        errors.append("dataforseo_volume_failed")
        return [], errors

    out: list[dict[str, Any]] = []
    for item in _task_items(body):
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


async def keyword_difficulty(
    keywords: list[str],
    *,
    location_code: int = DEFAULT_LOCATION,
    language_code: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    seen: set[str] = set()
    batch: list[str] = []
    for raw in keywords:
        cleaned = sanitize_keyword(str(raw or ""))
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        batch.append(cleaned)
        if len(batch) >= 20:
            break
    if not batch:
        return [], errors
    if not _auth_header():
        errors.append("dataforseo_unavailable")
        return [], errors

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
        return [], errors

    out: list[dict[str, Any]] = []
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
        "validated": True,
    }, errors


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
                "limit": min(limit, 100),
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
                    "limit": min(limit, 100),
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
