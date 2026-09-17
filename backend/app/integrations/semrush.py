"""SEMrush Analytics API — keyword expansion and organic keywords.

Replaces Ahrefs Keywords Explorer and Site Explorer organic keywords.
The key is sent as a header, never in the query string, so request logs
do not capture it.

Site Audit is not called here. SEMrush Site Audit is a separate crawl
product, not a drop-in for Ahrefs project issues.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("semrush")

ANALYTICS = "https://api.semrush.com/"
BACKLINKS = "https://api.semrush.com/analytics/v1/"

_DATABASE = {
    "gb": "uk",
    "uk": "uk",
    "us": "us",
    "au": "au",
    "ca": "ca",
    "nz": "nz",
    "in": "in",
    "ie": "ie",
    "de": "de",
    "fr": "fr",
}

_units_blocked: str | None = None


def explorer_blocked() -> str | None:
    return _units_blocked


def keywords_explorer_blocked() -> str | None:
    """Same name keyword seeding already checks, so the quota switch still works."""
    return _units_blocked


def reset_keywords_explorer_block() -> None:
    global _units_blocked
    _units_blocked = None


def _mark_blocked(reason: str) -> None:
    global _units_blocked
    _units_blocked = reason
    log.warning("semrush_explorer_disabled", reason=reason)


def _configured() -> bool:
    settings = get_settings()
    return bool(settings.semrush_api_key) and not settings.use_mock_providers


def database_for(country: str) -> str:
    code = (country or "us").strip().lower()
    return _DATABASE.get(code, code or "us")


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_csv(text: str) -> list[dict[str, str]]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].split(";")]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = line.split(";")
        rows.append({headers[i]: cells[i].strip() if i < len(cells) else "" for i in range(len(headers))})
    return rows


def _keyword_row(raw: dict[str, str], *, endpoint: str) -> dict[str, Any] | None:
    keyword = (raw.get("Keyword") or raw.get("Ph") or "").strip()
    if not keyword:
        return None
    intent = (raw.get("Intent") or raw.get("In") or "").strip()
    return {
        "keyword": keyword,
        "volume": _int_or_none(raw.get("Search Volume") or raw.get("Nq")),
        "difficulty": _int_or_none(raw.get("Keyword Difficulty") or raw.get("Kd")),
        "traffic_potential": _int_or_none(raw.get("Traffic") or raw.get("Tr")),
        "cpc": _float_or_none(raw.get("CPC") or raw.get("Cp")),
        "intent": intent.lower(),
        "parent_topic": None,
        "source": "semrush",
        "ahrefs_endpoint": endpoint,
    }


async def _fetch(url: str, params: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (body, error). ERROR 50 (nothing found) is an empty success."""
    if _units_blocked:
        return None, _units_blocked
    if not _configured():
        return None, "semrush_unavailable"
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"key": settings.semrush_api_key, "Accept": "text/plain"},
            )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        log.warning("semrush_request_failed", error=type(exc).__name__)
        return None, "semrush_request_failed"
    text = resp.text or ""
    if text.lstrip().upper().startswith("ERROR"):
        upper = text.upper()
        if "NOTHING FOUND" in upper or "ERROR 50" in upper:
            return "", None
        if any(token in upper for token in ("ERROR 131", "ERROR 132", "ERROR 133", "API UNITS", "LIMIT EXCEEDED")):
            _mark_blocked("semrush_units_exhausted")
            return None, "semrush_units_exhausted"
        if "ERROR 120" in upper or "WRONG KEY" in upper or resp.status_code in (401, 403):
            _mark_blocked("semrush_auth_failed")
            return None, "semrush_auth_failed"
        log.warning("semrush_http_error", status=resp.status_code, snippet=text[:120])
        return None, "semrush_http_error"
    if resp.status_code >= 400:
        log.warning("semrush_http_error", status=resp.status_code, snippet=text[:120])
        return None, "semrush_http_error"
    return text, None


async def matching_terms(
    seed: str,
    *,
    country: str = "us",
    limit: int = 50,
    match_mode: str = "terms",
    min_volume: int | None = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Phrase match (phrase_this) or all-words match (phrase_fullsearch).

    Classification (phrase vs related) is done from the keyword text, not the
    endpoint, which is how keyword seeding already treats Ahrefs feeds.
    """
    errors: list[str] = []
    seed = (seed or "").strip()
    if not seed:
        return [], errors
    report = "phrase_this" if match_mode == "phrase" else "phrase_fullsearch"
    text, err = await _fetch(
        ANALYTICS,
        {
            "type": report,
            "phrase": seed,
            "database": database_for(country),
            "display_limit": min(limit, 100),
            "display_sort": "nq_desc",
            "export_columns": "Ph,Nq,Cp,Kd",
        },
    )
    if err:
        errors.append(err)
        return [], errors
    out = [row for raw in _parse_csv(text or "") if (row := _keyword_row(raw, endpoint=report))]
    if min_volume is not None:
        out = [row for row in out if (row.get("volume") or 0) > min_volume]
    return out, errors


async def related_terms(
    seed: str,
    *,
    country: str = "us",
    limit: int = 50,
    terms: str = "all",
    min_volume: int | None = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    seed = (seed or "").strip()
    if not seed:
        return [], errors
    text, err = await _fetch(
        ANALYTICS,
        {
            "type": "phrase_related",
            "phrase": seed,
            "database": database_for(country),
            "display_limit": min(limit, 100),
            "display_sort": "nq_desc",
            "export_columns": "Ph,Nq,Cp,Kd",
        },
    )
    if err:
        errors.append(err)
        return [], errors
    out = [row for raw in _parse_csv(text or "") if (row := _keyword_row(raw, endpoint="phrase_related"))]
    if min_volume is not None:
        out = [row for row in out if (row.get("volume") or 0) > min_volume]
    return out, errors


async def keyword_overview(
    keywords: list[str],
    *,
    country: str = "us",
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    batch = [k.strip() for k in keywords if k and str(k).strip()][:100]
    if not batch:
        return [], errors
    text, err = await _fetch(
        ANALYTICS,
        {
            "type": "phrase_these",
            "phrase": ";".join(batch),
            "database": database_for(country),
            "export_columns": "Ph,Nq,Cp,Kd",
        },
    )
    if err:
        errors.append(err)
        return [], errors
    out = [row for raw in _parse_csv(text or "") if (row := _keyword_row(raw, endpoint="phrase_these"))]
    log.info("semrush_keyword_overview_ok", count=len(out))
    return out, errors


async def organic_keywords(
    domain: str,
    *,
    country: str = "us",
    limit: int = 50,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    raw_domain = (domain or "").strip()
    if "://" in raw_domain:
        raw_domain = urlparse(raw_domain).hostname or raw_domain
    target = raw_domain.lower().removeprefix("www.").strip("/")
    if not target:
        return [], errors
    text, err = await _fetch(
        ANALYTICS,
        {
            "type": "domain_organic",
            "domain": target,
            "database": database_for(country),
            "display_limit": min(limit, 100),
            "display_sort": "tr_desc",
            "export_columns": "Ph,Po,Nq,Cp,Kd,Tr",
        },
    )
    if err:
        errors.append("semrush_organic_keywords_failed" if err == "semrush_http_error" else err)
        return [], errors
    out: list[dict[str, Any]] = []
    for raw in _parse_csv(text or ""):
        row = _keyword_row(raw, endpoint="domain_organic")
        if not row:
            continue
        row["position"] = _int_or_none(raw.get("Position") or raw.get("Po"))
        row["traffic"] = _int_or_none(raw.get("Traffic") or raw.get("Tr"))
        row["domain"] = target
        out.append(row)
    log.info("semrush_organic_keywords_ok", domain=target, count=len(out))
    return out, errors


async def backlinks_overview(domain: str) -> dict[str, Any]:
    """Referring domains and authority score. Raises if the key cannot answer."""
    target = (domain or "").lower().removeprefix("www.").strip()
    text, err = await _fetch(
        BACKLINKS,
        {
            "type": "backlinks_overview",
            "target": target,
            "target_type": "root_domain",
            "export_columns": "ascore,domains_num,total",
        },
    )
    if err or not text:
        raise RuntimeError(err or "semrush_backlinks_empty")
    rows = _parse_csv(text)
    if not rows:
        raise RuntimeError("semrush_backlinks_empty")
    row = rows[0]
    domains = _int_or_none(row.get("domains_num") or row.get("Domains"))
    score = _float_or_none(row.get("ascore") or row.get("Score"))
    if domains is None:
        raise RuntimeError("semrush_backlinks_unparsed")
    return {
        "referring_domains": domains,
        "authority_score": score if score is not None else 0.0,
        "top_anchor_text": {},
        "sample_links": [],
        "note": "Referring domains and authority from SEMrush Backlinks.",
    }
