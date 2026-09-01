"""Cost tracker API — API spend across platform and per client."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services.cost_tracker import build_client_cost_summary, build_cost_tracker

router = APIRouter(prefix="/cost-tracker", tags=["cost-tracker"])


def _can_view_cost_tracker(user: User) -> bool:
    if get_settings().auth_disabled:
        return True
    role = (user.role.name if user.role else "") or ""
    return role in ("head_of_department", "client_success_manager")


async def require_cost_tracker(user: User = Depends(get_current_user)) -> User:
    if not _can_view_cost_tracker(user):
        raise HTTPException(
            status_code=403,
            detail="Cost tracker requires Head of Department or CSM role.",
        )
    return user


@router.get("")
async def get_cost_tracker(
    days: int = Query(7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_cost_tracker),
):
    return await build_cost_tracker(db, days=days)


@router.get("/clients/{client_id}")
async def get_client_costs(
    client_id: UUID,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_cost_tracker),
):
    return await build_client_cost_summary(db, client_id, days=days)
