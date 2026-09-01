"""Google Search Console helpers for Phase 7 technical indexation checks."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ApiCredential
from app.security import decrypt_token

GSC_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
GSC_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _site_candidates(primary_url: str) -> list[str]:
    parsed = urlparse(primary_url if "://" in primary_url else f"https://{primary_url}")
    host = (parsed.netloc or parsed.path or primary_url).lower().removeprefix("www.")
    if not host:
        return []
    return [
        f"sc-domain:{host}",
        f"https://{host}/",
        f"http://{host}/",
        f"https://www.{host}/",
    ]


def _parse_token_blob(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"access_token": raw}


async def _refresh_access_token(refresh_token: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            GSC_TOKEN_URL,
            data={
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _access_token_from_credential(cred: ApiCredential) -> str | None:
    blob = _parse_token_blob(decrypt_token(cred.encrypted_token))
    access = blob.get("access_token")
    refresh = blob.get("refresh_token")
    if refresh and get_settings().google_oauth_client_id:
        try:
            refreshed = await _refresh_access_token(str(refresh))
            access = refreshed.get("access_token") or access
        except httpx.HTTPError:
            pass
    return str(access) if access else None


async def _list_sites(access_token: str) -> list[str]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            GSC_SITES_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        payload = resp.json()
    return [
        str(row.get("siteUrl") or "")
        for row in (payload.get("siteEntry") or [])
        if row.get("siteUrl")
    ]


def _resolve_site_url(sites: list[str], primary_url: str) -> str | None:
    site_set = {s.rstrip("/") for s in sites}
    for candidate in _site_candidates(primary_url):
        norm = candidate.rstrip("/")
        if candidate in sites or norm in site_set:
            return candidate if candidate in sites else next(
                (s for s in sites if s.rstrip("/") == norm),
                None,
            )
    host = urlparse(
        primary_url if "://" in primary_url else f"https://{primary_url}"
    ).netloc.lower().removeprefix("www.")
    for site in sites:
        if host and host in site.lower():
            return site
    return None


async def _fetch_sitemaps(access_token: str, site_url: str) -> list[dict[str, Any]]:
    from urllib.parse import quote

    path_site = quote(site_url, safe="")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GSC_SITES_URL}/{path_site}/sitemaps",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        payload = resp.json()
    rows: list[dict[str, Any]] = []
    for row in payload.get("sitemap") or []:
        if not isinstance(row, dict):
            continue
        errors = int(row.get("errors") or 0)
        warnings = int(row.get("warnings") or 0)
        rows.append(
            {
                "path": row.get("path"),
                "last_submitted": row.get("lastSubmitted"),
                "last_downloaded": row.get("lastDownloaded"),
                "errors": errors,
                "warnings": warnings,
                "is_pending": bool(row.get("isPending")),
                "is_sitemaps_index": bool(row.get("isSitemapsIndex")),
            }
        )
    return rows


async def fetch_gsc_technical_snapshot(
    *,
    access_token: str,
    primary_url: str,
) -> dict[str, Any]:
    """Sitemap + property verification snapshot for Phase 7 indexation context."""
    result: dict[str, Any] = {
        "status": "NOT_AVAILABLE",
        "property": None,
        "sitemaps": [],
        "issues": [],
        "note": "",
    }
    try:
        sites = await _list_sites(access_token)
    except httpx.HTTPError as exc:
        result["note"] = f"GSC site list failed: {str(exc)[:120]}"
        return result

    if not sites:
        result["note"] = "No Search Console properties on connected account."
        return result

    property_url = _resolve_site_url(sites, primary_url)
    if not property_url:
        result["note"] = (
            f"No GSC property matched {primary_url}. "
            f"Connected properties: {', '.join(sites[:5])}"
        )
        result["available_properties"] = sites[:10]
        return result

    try:
        sitemaps = await _fetch_sitemaps(access_token, property_url)
    except httpx.HTTPError as exc:
        result.update(
            {
                "status": "available",
                "property": property_url,
                "note": f"Property matched but sitemap fetch failed: {str(exc)[:120]}",
            }
        )
        return result

    issues: list[dict[str, Any]] = []
    for sm in sitemaps:
        path = sm.get("path") or "sitemap"
        if int(sm.get("errors") or 0) > 0:
            issues.append(
                {
                    "rule_id": f"gsc_sitemap_error_{path}",
                    "title": f"Sitemap errors on {path}",
                    "severity": "High",
                    "category": "Indexability",
                    "affected_url_count": int(sm.get("errors") or 0),
                    "recommended_action": "Fix sitemap URLs/errors in Search Console",
                    "source": "gsc",
                    "confidence": "high",
                }
            )
        if int(sm.get("warnings") or 0) > 0:
            issues.append(
                {
                    "rule_id": f"gsc_sitemap_warning_{path}",
                    "title": f"Sitemap warnings on {path}",
                    "severity": "Medium",
                    "category": "Indexability",
                    "affected_url_count": int(sm.get("warnings") or 0),
                    "recommended_action": "Review sitemap warnings in Search Console",
                    "source": "gsc",
                    "confidence": "high",
                }
            )
        if sm.get("is_pending"):
            issues.append(
                {
                    "rule_id": f"gsc_sitemap_pending_{path}",
                    "title": f"Sitemap pending processing: {path}",
                    "severity": "Low",
                    "category": "Indexability",
                    "affected_url_count": 1,
                    "recommended_action": "Wait for GSC to finish processing or resubmit",
                    "source": "gsc",
                    "confidence": "medium",
                }
            )

    result.update(
        {
            "status": "available",
            "property": property_url,
            "sitemaps": sitemaps,
            "issues": issues,
            "sitemap_count": len(sitemaps),
            "error_sitemaps": sum(1 for s in sitemaps if int(s.get("errors") or 0) > 0),
            "warning_sitemaps": sum(1 for s in sitemaps if int(s.get("warnings") or 0) > 0),
            "note": (
                f"GSC property {property_url}: {len(sitemaps)} sitemap(s), "
                f"{len(issues)} indexation signal(s)."
            ),
        }
    )
    return result


async def load_gsc_snapshot_for_client(
    db: AsyncSession,
    *,
    client_id: UUID,
    primary_url: str,
) -> dict[str, Any]:
    """Load GSC technical snapshot when search_console OAuth credential exists."""
    settings = get_settings()
    if settings.use_mock_providers:
        return {
            "status": "NOT_AVAILABLE",
            "note": "Mock providers enabled — GSC API skipped.",
        }
    if not settings.google_oauth_client_id:
        return {
            "status": "NOT_AVAILABLE",
            "note": "Google OAuth not configured — connect GSC in Phase 2 tracking.",
        }

    cred = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == "search_console",
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not cred:
        return {
            "status": "NOT_AVAILABLE",
            "note": "Search Console not connected — grant access in Phase 2 tracking.",
        }

    token = await _access_token_from_credential(cred)
    if not token:
        return {
            "status": "NOT_AVAILABLE",
            "note": "Search Console credential present but access token unavailable.",
        }

    return await fetch_gsc_technical_snapshot(access_token=token, primary_url=primary_url)
