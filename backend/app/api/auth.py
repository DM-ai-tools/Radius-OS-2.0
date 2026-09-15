from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.deps import get_current_user
from app.models import Role, User
from app.schemas.auth import (
    LoginRequest,
    PermissionOut,
    RoleOut,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.services.role_skills import (
    BOOTSTRAP_ADMIN_ROLE,
    SELF_SERVICE_ROLES,
    SEO_ROLES,
    permissions_for_role,
    role_label,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    name = user.role.name if user.role else ""
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_name=name,
        role_label=role_label(name),
        is_active=user.is_active,
        permissions=[PermissionOut(**p) for p in permissions_for_role(name)],
    )


async def _hod_exists(db: AsyncSession) -> bool:
    result = await db.execute(
        select(User.id).join(Role).where(Role.name == BOOTSTRAP_ADMIN_ROLE).limit(1)
    )
    return result.scalar_one_or_none() is not None


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: AsyncSession = Depends(get_db)):
    """SEO roles available for self-service signup.

    Head of Department is only offered while no Head of Department account
    exists yet (first-run org bootstrap) — once one exists, it drops out of
    this list and can no longer be self-assigned.
    """
    allowed = set(SELF_SERVICE_ROLES)
    if not await _hod_exists(db):
        allowed.add(BOOTSTRAP_ADMIN_ROLE)
    return [
        RoleOut(name=r["name"], label=r["label"], description=r["description"])
        for r in SEO_ROLES
        if r["name"] in allowed
    ]


@router.post("/signup", response_model=TokenResponse)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)):
    if body.role_name == BOOTSTRAP_ADMIN_ROLE:
        if await _hod_exists(db):
            raise HTTPException(400, "This role can't be self-assigned — ask an admin to invite you")
    elif body.role_name not in SELF_SERVICE_ROLES:
        raise HTTPException(400, "This role can't be self-assigned — ask an admin to invite you")

    existing = (
        await db.execute(select(User).where(User.email == body.email.lower().strip()))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Email already registered")

    role = (
        await db.execute(select(Role).where(Role.name == body.role_name))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(500, "Role catalog not seeded")

    user = User(
        email=body.email.lower().strip(),
        full_name=body.full_name.strip(),
        role_id=role.id,
        hashed_password=hash_password(body.password),
        is_active=True,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()
    token = create_access_token(str(user.id), extra={"role": role.name})
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.email == body.email.lower().strip())
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    token = create_access_token(str(user.id), extra={"role": user.role.name})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return _user_out(user)
