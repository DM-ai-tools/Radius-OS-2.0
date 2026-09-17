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


def wordpress_gate(*, stored: bool, result: dict | None = None) -> dict:
    """Single status the Workspace and Publishing card both display.

    ready: live REST check passed and the user can publish posts.
    limited: credentials work, but the WordPress user cannot publish.
    failed: a connection is stored, but the live check did not pass.
    not_connected: nothing stored for this client.
    """
    if not stored:
        return {
            "state": "not_connected",
            "label": "Not connected",
            "ok": False,
            "detail": "No WordPress site is saved for this client.",
        }
    result = result or {}
    if not result.get("ok"):
        return {
            "state": "failed",
            "label": "Connection failed",
            "ok": False,
            "detail": str(result.get("error") or "WordPress did not accept the saved credentials."),
        }
    if result.get("capabilities_publish") is False:
        return {
            "state": "limited",
            "label": "Connected — cannot publish",
            "ok": False,
            "detail": "Credentials work, but this WordPress user cannot publish posts.",
        }
    return {
        "state": "ready",
        "label": "Connected",
        "ok": True,
        "detail": str(
            (result or {}).get("detail")
            or "Live WordPress check passed. Draft publishing is allowed."
        ),
    }


@router.get("/{client_id}/integrations/wordpress")
async def wordpress_status(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Connection status only — never returns the Application Password."""
    conn = await wordpress.load_connection(db, client_id)
    if not conn:
        return {"connected": False, "gate": wordpress_gate(stored=False)}
    result = await wordpress.verify_connection(conn)
    discovered = str(result.get("auth_transport") or "")
    discovered_url = str(result.get("base_url") or "")
    if result.get("ok") and (
        (discovered and discovered != conn.auth_transport)
        or (discovered_url and discovered_url.rstrip("/") != conn.base_url.rstrip("/"))
    ):
        updated = WordPressConnection(
            base_url=discovered_url or conn.base_url,
            username=conn.username,
            app_password=conn.app_password,
            auth_transport=discovered or conn.auth_transport,
        )
        row = (
            await db.execute(
                select(ApiCredential).where(
                    ApiCredential.client_id == client_id,
                    ApiCredential.provider == CREDENTIAL_PROVIDER,
                    ApiCredential.revoked_at.is_(None),
                )
            )
        ).scalars().first()
        if row is not None:
            row.encrypted_token = encrypt_token(wordpress.pack_connection(updated))
            await db.commit()
            conn = updated
    return {
        "connected": bool(result.get("ok")),
        "base_url": conn.base_url,
        "username": conn.username,
        "wp_user": result.get("user"),
        "can_publish": result.get("capabilities_publish"),
        "auth_transport": conn.auth_transport,
        "error": result.get("error") if not result.get("ok") else None,
        "gate": wordpress_gate(stored=True, result=result),
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
    result = await wordpress.verify_connection(conn, use_browser=True)
    if not result.get("ok"):
        error = str(result.get("error") or "unknown error")
        detail = str(result.get("detail") or "").strip()
        attempted = result.get("attempted_url")
        message = f"Could not connect to WordPress: {error}."
        if detail and detail not in error:
            message = f"{message} {detail}"
        if attempted:
            message = f"{message} Tried {attempted}."
        raise HTTPException(400, message)
    # Store the root that actually answered, not a pasted /wp-admin or page path.
    working_url = str(result.get("base_url") or conn.base_url)
    conn = WordPressConnection(
        base_url=working_url,
        username=conn.username,
        app_password=conn.app_password,
        auth_transport=str(result.get("auth_transport") or "rest"),
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
        "gate": wordpress_gate(stored=True, result=result),
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
    return {"connected": False, "gate": wordpress_gate(stored=False)}
