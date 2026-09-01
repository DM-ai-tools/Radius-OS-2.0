"""Phase 7 technical SEO read APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Client, ClientDigitalProfile, User

router = APIRouter(tags=["technical-seo"])


def _issue_urls(issues: list, rule_id: str) -> list[str]:
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("rule_id") or "") != rule_id:
            continue
        urls = issue.get("affected_urls") or issue.get("sample_urls") or []
        return [str(u) for u in urls if u]
    return []


@router.get("/clients/{client_id}/technical-seo/issues/{rule_id}/urls")
async def list_technical_seo_issue_urls(
    client_id: UUID,
    rule_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Paginate affected URLs for a Phase 7 issue from the latest technical_seo_summary."""
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Client profile not found")

    summary = dict(profile.technical_seo_summary or {})
    issues = list(summary.get("issues") or [])
    urls = _issue_urls(issues, rule_id)
    total = len(urls)
    page = urls[offset : offset + limit]
    issue_meta = next(
        (i for i in issues if isinstance(i, dict) and str(i.get("rule_id") or "") == rule_id),
        None,
    )
    return {
        "client_id": str(client_id),
        "rule_id": rule_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "urls": page,
        "issue": issue_meta,
    }


@router.get("/clients/{client_id}/technical-seo/suggestion-items")
async def list_technical_seo_suggestions(
    client_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return suggestion ledger items from the latest Phase 7 report."""
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Client profile not found")

    summary = dict(profile.technical_seo_summary or {})
    items = list(summary.get("suggestion_items") or [])
    total = len(items)
    return {
        "client_id": str(client_id),
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items[offset : offset + limit],
    }
