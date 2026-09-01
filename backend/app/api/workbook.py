"""BW workbook export API — Category Mapping, Search Demand, TOFU/MOFU sheets."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Client, ClientDigitalProfile, User
from app.services.bw_workbook import build_combined_workbook_pack, export_workbook_xlsx

router = APIRouter(tags=["workbook"])


@router.get("/clients/{client_id}/workbook")
async def get_client_workbook(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return BW-format workbook JSON (all three sheets from CDP summaries)."""
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

    pack = build_combined_workbook_pack(
        client_name=client.display_name,
        search_demand=dict(profile.search_demand_summary or {}),
        site_architecture=dict(profile.site_architecture_summary or {}),
        content_strategy=dict(profile.seo_strategy_summary or {}),
    )
    return pack


@router.get("/clients/{client_id}/workbook/export")
async def export_client_workbook(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Download BW-format workbook as .xlsx."""
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

    pack = build_combined_workbook_pack(
        client_name=client.display_name,
        search_demand=dict(profile.search_demand_summary or {}),
        site_architecture=dict(profile.site_architecture_summary or {}),
        content_strategy=dict(profile.seo_strategy_summary or {}),
    )
    data = export_workbook_xlsx(pack)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in client.display_name)[:40]
    filename = f"{safe_name}_Category_Mapping_Search_Demand.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
