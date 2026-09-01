"""Per-client third-party CMS connections (currently WordPress, Phase 12 publish target).

Distinct from app.api.oauth: those routes broker Google's OAuth consent flow for
GA4/GSC/GTM. This is a direct username + Application Password connection, entered by
the operator in the app and tested before it is trusted — there is no consent redirect.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_permission
from app.integrations import wordpress
from app.integrations.wordpress import CREDENTIAL_PROVIDER, WordPressConnection
from app.models import ApiCredential, Client, User
from app.security import encrypt_token
from app.services.audit import log_event

router = APIRouter(prefix="/clients", tags=["integrations"])


class WordPressConnectBody(BaseModel):
    base_url: str = Field(min_length=1)
    username: str = Field(min_length=1)
    app_password: str = Field(min_length=1)


async def _get_client(db: AsyncSession, client_id: UUID) -> Client:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    return client


@router.get("/{client_id}/integrations/wordpress")
async def wordpress_status(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Connection status only — never returns the Application Password."""
    conn = await wordpress.load_connection(db, client_id)
    if not conn:
        return {"connected": False}
    result = await wordpress.verify_connection(conn)
    return {
        "connected": bool(result.get("ok")),
        "base_url": conn.base_url,
        "username": conn.username,
        "wp_user": result.get("user"),
        "can_publish": result.get("capabilities_publish"),
        "error": result.get("error") if not result.get("ok") else None,
    }


@router.post("/{client_id}/integrations/wordpress")
async def wordpress_connect(
    client_id: UUID,
    body: WordPressConnectBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Test the credentials against WordPress before storing anything.

    A bad Application Password should fail here, in the connect form, not silently at
    publish time — so this calls the real REST API before touching the database.
    """
    await require_permission(user, db, "publishing", need_trigger=True)
    await _get_client(db, client_id)

    conn = WordPressConnection(
        base_url=body.base_url.strip(),
        username=body.username.strip(),
        app_password=body.app_password.strip(),
    )
    result = await wordpress.verify_connection(conn)
    if not result.get("ok"):
        raise HTTPException(
            400,
            f"Could not connect to WordPress: {result.get('error') or 'unknown error'}. "
            "Check the site URL and that the Application Password hasn't been revoked.",
        )

    prior = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == CREDENTIAL_PROVIDER,
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for row in prior:
        row.revoked_at = datetime.now(timezone.utc)

    db.add(
        ApiCredential(
            client_id=client_id,
            provider=CREDENTIAL_PROVIDER,
            encrypted_token=encrypt_token(wordpress.pack_connection(conn)),
            scope="publish",
        )
    )
    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="wordpress_connected",
        event_detail={"base_url": conn.base_url, "username": conn.username},
    )
    await db.commit()
    return {
        "connected": True,
        "base_url": conn.base_url,
        "username": conn.username,
        "wp_user": result.get("user"),
        "can_publish": result.get("capabilities_publish"),
    }


@router.delete("/{client_id}/integrations/wordpress")
async def wordpress_disconnect(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "publishing", need_trigger=True)
    await _get_client(db, client_id)

    rows = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == CREDENTIAL_PROVIDER,
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for row in rows:
        row.revoked_at = datetime.now(timezone.utc)
    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="wordpress_disconnected",
        event_detail={},
    )
    await db.commit()
    return {"connected": False}
