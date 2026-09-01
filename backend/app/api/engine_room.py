"""Engine room — ops dashboard API (HoD / admin)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services.cost_tracker import build_client_cost_summary
from app.services.engine_room import build_engine_room

router = APIRouter(prefix="/engine-room", tags=["engine-room"])


def _can_view_engine_room(user: User) -> bool:
    from app.config import get_settings

    if get_settings().auth_disabled:
        return True
    role = (user.role.name if user.role else "") or ""
    return role in ("head_of_department", "client_success_manager")


async def require_engine_room(user: User = Depends(get_current_user)) -> User:
    if not _can_view_engine_room(user):
        raise HTTPException(
            status_code=403,
            detail="Engine room requires Head of Department or CSM role.",
        )
    return user


@router.get("")
async def get_engine_room(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_engine_room),
):
    return await build_engine_room(db, days=days)


@router.get("/clients/{client_id}/costs")
async def get_client_costs(
    client_id: UUID,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_engine_room),
):
    return await build_client_cost_summary(db, client_id, days=days)
