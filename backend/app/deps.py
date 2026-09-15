from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_db
from app.models import RolePermission, User
from app.security import decode_access_token, parse_uuid

bearer = HTTPBearer(auto_error=False)


async def _demo_user(db: AsyncSession) -> User:
    """Any seeded user works; prefer CSM as the default operator."""
    result = await db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.email == "csm@trafficradius.com")
    )
    user = result.scalar_one_or_none()
    if user:
        return user
    result = await db.execute(select(User).options(selectinload(User.role)).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=500, detail="No users seeded")
    return user


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    settings = get_settings()
    if settings.auth_disabled:
        return await _demo_user(db)

    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_access_token(creds.credentials)
        user_id = parse_uuid(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    return user


async def require_permission(
    user: User,
    db: AsyncSession,
    agent_key: str,
    *,
    need_trigger: bool = False,
    need_approve: bool = False,
) -> None:
    if get_settings().auth_disabled:
        return

    role_name = user.role.name if user.role else ""
    # Head of Department has full trigger + approve on every skill.
    if role_name == "head_of_department":
        return

    result = await db.execute(
        select(RolePermission).where(
            RolePermission.role_id == user.role_id,
            RolePermission.agent_key == agent_key,
        )
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=403, detail=f"No permission for {agent_key}")
    if need_trigger and not perm.can_trigger:
        raise HTTPException(status_code=403, detail=f"Cannot trigger {agent_key}")
    if need_approve and not perm.can_approve:
        raise HTTPException(status_code=403, detail=f"Cannot approve {agent_key}")


def agent_feature_enabled(agent_key: str) -> bool:
    s = get_settings()
    flags = {
        "discovery_agent": s.feature_discovery_agent,
        "tracking_access_agent": s.feature_tracking_agent,
        "website_situation_agent": s.feature_website_agent,
        "competitor_market_agent": s.feature_competitor_agent,
        "search_demand": s.feature_search_demand_agent,
        "content_strategy": s.feature_content_strategy_agent,
        "site_architecture": s.feature_site_architecture_agent,
        "technical_seo": s.feature_technical_seo_agent,
        "content_audit": s.feature_content_audit_agent,
        "content_planning": s.feature_content_planning_agent,
        "content_production": s.feature_content_production_agent,
        "on_page_seo": s.feature_on_page_seo_agent,
        "publishing": s.feature_publishing_agent,
        "readiness_gate": True,
    }
    return flags.get(agent_key, False)
