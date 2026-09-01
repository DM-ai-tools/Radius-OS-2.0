"""Ahrefs API v3 helpers for keyword research and organic rankings."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("ahrefs")

BASE = "https://api.ahrefs.com/v3"

# Kept well under the per-agent turn budget so one slow endpoint cannot stall a phase.
REQUEST_TIMEOUT = 35


def _configured() -> bool:
    settings = get_settings()
    return bool(settings.ahrefs_api_key) and not settings.use_mock_providers


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().ahrefs_api_key}"}


async def _get(path: str, params: dict[str, Any], *, max_attempts: int = 3) -> dict[str, Any] | None:
    import asyncio
    import time

    from app.services.api_meter import record_api_call

    settings = get_settings()
    if not settings.ahrefs_api_key:
        return None
    if settings.use_mock_providers:
        return None
    clean = {k: v for k, v in params.items() if v is not None}

    for attempt in range(max_attempts):
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(f"{BASE}{path}", params=clean, headers=_headers())
        except httpx.HTTPError as exc:
            log.warning(
                "ahrefs_request_failed",
                path=path,
                attempt=attempt + 1,
                error=str(exc)[:240],
            )
            await record_api_call(
                provider="ahrefs",
                operation=path,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                status="error",
                error_detail=str(exc)[:500],
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(2**attempt)
                continue
            return None

        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning(
                "ahrefs_http_retry",
                path=path,
                status=resp.status_code,
                attempt=attempt + 1,
            )
            await record_api_call(
                provider="ahrefs",
                operation=path,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                status="error",
                error_detail=resp.text[:500],
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(2**attempt)
                continue
            return None

        if resp.status_code >= 400:
            log.warning(
                "ahrefs_http_error",
                path=path,
                status=resp.status_code,
                body=resp.text[:240],
            )
            await record_api_call(
                provider="ahrefs",
                operation=path,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                status="error",
                error_detail=resp.text[:500],
            )
            return None

        await record_api_call(
            provider="ahrefs",
            operation=path,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            status="success",
        )
        return resp.json()

    return None


async def keyword_overview(
    keywords: list[str],
    *,
    country: str = "us",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return metrics for seed keywords. Never invents volume."""
    errors: list[str] = []
    if not keywords:
        return [], errors
    if not _configured():
        errors.append("ahrefs_unavailable")
        return [], errors

    # Ahrefs accepts comma-separated keywords
    batch = [k.strip() for k in keywords if k and k.strip()][:40]
    if not batch:
        return [], errors

    data = await _get(
        "/keywords-explorer/overview",
        {
            "country": country.lower(),
            "keywords": ",".join(batch),
            "select": "keyword,volume,difficulty,traffic_potential,cpc,intents,parent_topic",
        },
    )
    if data is None:
        errors.append("ahrefs_overview_failed")
        return [], errors

    rows = data.get("keywords") or data.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kw = str(row.get("keyword") or "").strip()
        if not kw:
            continue
        intents = row.get("intents") or {}
        intent = ""
        if isinstance(intents, dict):
            intent = next((k for k, v in intents.items() if v), "") or ""
        elif isinstance(intents, list) and intents:
            intent = str(intents[0])
        out.append(
            {
                "keyword": kw,
                "volume": _int_or_none(row.get("volume")),
                "difficulty": _int_or_none(row.get("difficulty")),
                "traffic_potential": _int_or_none(row.get("traffic_potential")),
                "cpc": _float_or_none(row.get("cpc")),
                "intent": intent,
                "parent_topic": row.get("parent_topic"),
                "source": "ahrefs",
            }
        )
    log.info("ahrefs_keyword_overview_ok", count=len(out))
    return out, errors


def _parse_keyword_rows(
    data: dict[str, Any] | None,
    *,
    source_endpoint: str,
    match_mode: str | None = None,
) -> list[dict[str, Any]]:
    if not data:
        return []
    rows = data.get("keywords") or data.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kw = str(row.get("keyword") or "").strip()
        if not kw:
            continue
        intents = row.get("intents") or {}
        intent = ""
        if isinstance(intents, dict):
            intent = next((k for k, v in intents.items() if v), "") or ""
        elif isinstance(intents, list) and intents:
            intent = str(intents[0])
        item: dict[str, Any] = {
            "keyword": kw,
            "volume": _int_or_none(row.get("volume")),
            "difficulty": _int_or_none(row.get("difficulty")),
            "traffic_potential": _int_or_none(row.get("traffic_potential")),
            "cpc": _float_or_none(row.get("cpc")),
            "intent": intent,
            "parent_topic": row.get("parent_topic"),
            "source": "ahrefs",
            "ahrefs_endpoint": source_endpoint,
        }
        if match_mode:
            item["ahrefs_match_mode"] = match_mode
        out.append(item)
    return out


async def matching_terms(
    seed: str,
    *,
    country: str = "us",
    limit: int = 50,
    match_mode: str = "terms",
    min_volume: int | None = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keywords that contain the seed (phrase = exact order; terms = any order)."""
    errors: list[str] = []
    if not seed.strip():
        return [], errors
    if not _configured():
        errors.append("ahrefs_unavailable")
        return [], errors

    mode = match_mode if match_mode in ("terms", "phrase") else "terms"
    params: dict[str, Any] = {
        "country": country.lower(),
        "keywords": seed.strip(),
        "limit": min(limit, 100),
        "select": "keyword,volume,difficulty,traffic_potential,cpc,intents,parent_topic",
        "order_by": "volume:desc",
        "match_mode": mode,
    }

    data = await _get("/keywords-explorer/matching-terms", params)
    if data is None:
        errors.append("ahrefs_matching_terms_failed")
        return [], errors

    out = _parse_keyword_rows(data, source_endpoint="matching-terms", match_mode=mode)
    if min_volume is not None:
        out = [r for r in out if (r.get("volume") or 0) > min_volume]
    return out, errors


async def related_terms(
    seed: str,
    *,
    country: str = "us",
    limit: int = 50,
    terms: str = "all",
    min_volume: int | None = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Thematically related keywords (also_rank_for / also_talk_about) — broad class."""
    errors: list[str] = []
    if not seed.strip():
        return [], errors
    if not _configured():
        errors.append("ahrefs_unavailable")
        return [], errors

    terms_mode = terms if terms in ("all", "also_rank_for", "also_talk_about") else "all"
    params: dict[str, Any] = {
        "country": country.lower(),
        "keywords": seed.strip(),
        "limit": min(limit, 100),
        "select": "keyword,volume,difficulty,traffic_potential,cpc,intents,parent_topic",
        "order_by": "volume:desc",
        "terms": terms_mode,
    }

    data = await _get("/keywords-explorer/related-terms", params)
    if data is None:
        errors.append("ahrefs_related_terms_failed")
        return [], errors

    out = _parse_keyword_rows(data, source_endpoint="related-terms")
    if min_volume is not None:
        out = [r for r in out if (r.get("volume") or 0) > min_volume]
    return out, errors


async def organic_keywords(
    domain: str,
    *,
    country: str = "us",
    limit: int = 50,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    target = domain.lower().removeprefix("www.").strip()
    if not target:
        return [], errors
    if not _configured():
        errors.append("ahrefs_unavailable")
        return [], errors

    today = date.today().isoformat()
    data = await _get(
        "/site-explorer/organic-keywords",
        {
            "target": target,
            "mode": "subdomains",
            "country": country.lower(),
            "date": today,
            "limit": min(limit, 100),
            "select": "keyword,volume,keyword_difficulty,best_position,sum_traffic",
            "order_by": "sum_traffic:desc",
        },
    )
    if data is None:
        errors.append("ahrefs_organic_keywords_failed")
        return [], errors

    rows = data.get("keywords") or data.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kw = str(row.get("keyword") or "").strip()
        if not kw:
            continue
        out.append(
            {
                "keyword": kw,
                "volume": _int_or_none(row.get("volume")),
                "difficulty": _int_or_none(
                    row.get("keyword_difficulty") or row.get("difficulty")
                ),
                "position": _int_or_none(row.get("best_position") or row.get("position")),
                "traffic": _int_or_none(row.get("sum_traffic") or row.get("traffic")),
                "domain": target,
                "source": "ahrefs",
            }
        )
    log.info("ahrefs_organic_keywords_ok", domain=target, count=len(out))
    return out, errors


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


# --- Site Audit (Phase 7 Technical SEO) ----------------------------------------

PAGE_EXPLORER_SELECT = (
    "url,http_code,compliant,canonical,canonical_code,depth,title,meta_description,"
    "h1,internal_links,content_length,duplicate_title,is_redirect_loop,final_redirect"
)


async def site_audit_projects(
    *,
    project_url: str | None = None,
    project_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """List Site Audit projects (FREE — no API units)."""
    errors: list[str] = []
    if not _configured():
        errors.append("ahrefs_unavailable")
        return [], errors
    params: dict[str, Any] = {"output": "json"}
    if project_id is not None:
        params["project_id"] = project_id
    if project_url:
        params["project_url"] = project_url
    data = await _get("/site-audit/projects", params)
    if data is None:
        errors.append("ahrefs_site_audit_projects_failed")
        return [], errors
    rows = data.get("healthscores") or data.get("projects") or []
    log.info("ahrefs_site_audit_projects_ok", count=len(rows))
    return [r for r in rows if isinstance(r, dict)], errors


async def site_audit_issues(
    project_id: int,
    *,
    date: str | None = None,
    date_compared: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch all issues for a project (50 API units)."""
    errors: list[str] = []
    if not _configured():
        errors.append("ahrefs_unavailable")
        return [], errors
    params: dict[str, Any] = {"project_id": project_id, "output": "json"}
    if date:
        params["date"] = date
    if date_compared:
        params["date_compared"] = date_compared
    data = await _get("/site-audit/issues", params)
    if data is None:
        errors.append("ahrefs_site_audit_issues_failed")
        return [], errors
    rows = data.get("issues") or []
    log.info("ahrefs_site_audit_issues_ok", project_id=project_id, count=len(rows))
    return [r for r in rows if isinstance(r, dict)], errors


async def site_audit_page_explorer(
    project_id: int,
    *,
    issue_id: str | None = None,
    limit: int = 1000,
    offset: int = 0,
    select: str = PAGE_EXPLORER_SELECT,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Paginated page-level crawl data (50 API units per request)."""
    errors: list[str] = []
    if not _configured():
        errors.append("ahrefs_unavailable")
        return [], errors
    params: dict[str, Any] = {
        "project_id": project_id,
        "output": "json",
        "limit": min(limit, 1000),
        "offset": offset,
        "select": select,
    }
    if issue_id:
        params["issue_id"] = issue_id
    data = await _get("/site-audit/page-explorer", params)
    if data is None:
        errors.append("ahrefs_site_audit_page_explorer_failed")
        return [], errors
    rows = data.get("pages") or []
    log.info(
        "ahrefs_site_audit_page_explorer_ok",
        project_id=project_id,
        issue_id=issue_id,
        count=len(rows),
        offset=offset,
    )
    return [r for r in rows if isinstance(r, dict)], errors


async def resolve_site_audit_project(
    primary_url: str,
    *,
    project_id: int | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve Ahrefs Site Audit project by explicit id or target URL (free lookup)."""
    errors: list[str] = []
    if project_id is not None:
        projects, errs = await site_audit_projects(project_id=project_id)
        errors.extend(errs)
        return (projects[0] if projects else None), errors

    from app.integrations.llm import extract_domain

    domain = extract_domain(primary_url)
    if not domain:
        errors.append("invalid_domain")
        return None, errors

    for candidate in (f"https://{domain}", f"https://www.{domain}", domain):
        projects, errs = await site_audit_projects(project_url=candidate)
        errors.extend(errs)
        if projects:
            return projects[0], errors

    errors.append("ahrefs_site_audit_project_not_found")
    return None, errors
