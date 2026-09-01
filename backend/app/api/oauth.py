from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.integrations.google_oauth import (
    OAUTH_PROVIDERS,
    PROVIDER_LABELS,
    build_authorize_url,
    exchange_code,
    expires_at_from,
    frontend_redirect_base,
    oauth_configured,
    pack_token_blob,
    scopes_for,
)
from app.models import ApiCredential, Client, User
from app.security import create_access_token, decode_access_token, encrypt_token
from app.services.audit import log_event

router = APIRouter(prefix="/oauth", tags=["oauth"])


class MockGrant(BaseModel):
    client_id: UUID
    provider: str  # ga4 | search_console | gtm | google_business
    scope: str = "read"


class AuthorizeBody(BaseModel):
    client_id: UUID
    provider: str


async def _revoke_prior(db: AsyncSession, client_id: UUID, provider: str) -> None:
    existing = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == provider,
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for e in existing:
        e.revoked_at = datetime.now(timezone.utc)


async def _store_credential(
    db: AsyncSession,
    *,
    client_id: UUID,
    provider: str,
    raw_token: str,
    scope: str,
    expires_at: datetime | None,
    user_id: UUID | None,
) -> ApiCredential:
    await _revoke_prior(db, client_id, provider)
    cred = ApiCredential(
        client_id=client_id,
        provider=provider,
        encrypted_token=encrypt_token(raw_token),
        scope=scope,
        expires_at=expires_at,
    )
    db.add(cred)
    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user_id,
        event_type="oauth_granted",
        event_detail={"provider": provider, "mode": "google" if oauth_configured() else "mock"},
    )
    await db.flush()
    return cred


@router.get("/config")
async def oauth_config(_: User = Depends(get_current_user)):
    from app.config import get_settings

    s = get_settings()
    return {
        "configured": oauth_configured(),
        "providers": list(OAUTH_PROVIDERS),
        "labels": PROVIDER_LABELS,
        "redirect_uri": s.oauth_redirect_uri,
    }


@router.post("/authorize-url")
async def authorize_url(
    body: AuthorizeBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return a Google consent URL, or signal mock mode when OAuth is not configured."""
    if body.provider not in OAUTH_PROVIDERS:
        raise HTTPException(400, f"Invalid provider. Allowed: {', '.join(OAUTH_PROVIDERS)}")

    client = (
        await db.execute(select(Client).where(Client.id == body.client_id))
    ).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    if not oauth_configured():
        return {
            "mode": "mock",
            "url": None,
            "provider": body.provider,
            "message": "Google OAuth client ID/secret not set — use mock grant.",
        }

    state = create_access_token(
        subject=str(user.id),
        extra={
            "oauth": True,
            "client_id": str(body.client_id),
            "provider": body.provider,
            "uid": str(user.id),
        },
    )
    url = build_authorize_url(state=state, provider=body.provider)
    return {
        "mode": "google",
        "url": url,
        "provider": body.provider,
        "scopes": scopes_for(body.provider),
    }


@router.get("/callback")
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Google redirects here after consent. Stores token and sends user back to chat."""
    base = frontend_redirect_base()

    def fail(msg: str, client_id: str | None = None) -> RedirectResponse:
        q = urlencode({"oauth": "error", "message": msg})
        if client_id:
            return RedirectResponse(f"{base}/clients/{client_id}/chat?{q}")
        return RedirectResponse(f"{base}/app?{q}")

    if error:
        return fail(error)
    if not code or not state:
        return fail("Missing code or state from Google")

    try:
        payload = decode_access_token(state)
    except ValueError:
        return fail("Invalid or expired OAuth state")

    if not payload.get("oauth"):
        return fail("Invalid OAuth state")

    client_id_str = str(payload.get("client_id") or "")
    provider = str(payload.get("provider") or "")
    user_id_str = str(payload.get("uid") or payload.get("sub") or "")

    if provider not in OAUTH_PROVIDERS or not client_id_str:
        return fail("Invalid provider in OAuth state")

    try:
        client_id = UUID(client_id_str)
        user_id = UUID(user_id_str) if user_id_str else None
    except ValueError:
        return fail("Invalid client in OAuth state")

    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if not client:
        return fail("Client not found")

    try:
        token_payload = await exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        return fail(f"Token exchange failed: {exc}", client_id_str)

    if not token_payload.get("access_token"):
        return fail("Google did not return an access token", client_id_str)

    scope = token_payload.get("scope") or " ".join(scopes_for(provider))
    await _store_credential(
        db,
        client_id=client_id,
        provider=provider,
        raw_token=pack_token_blob(token_payload),
        scope=str(scope),
        expires_at=expires_at_from(token_payload),
        user_id=user_id,
    )
    await db.commit()

    q = urlencode(
        {
            "oauth": "ok",
            "provider": provider,
            "label": PROVIDER_LABELS.get(provider, provider),
        }
    )
    return RedirectResponse(f"{base}/clients/{client_id}/chat?{q}")


@router.post("/mock-grant")
async def mock_grant(
    body: MockGrant,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Dev/demo OAuth grant — stores encrypted placeholder token.

    Still available when Google OAuth is configured (useful for demos).
    Prefer /authorize-url → Google consent for real connections.
    """
    if body.provider not in OAUTH_PROVIDERS:
        raise HTTPException(400, f"Invalid provider. Allowed: {', '.join(OAUTH_PROVIDERS)}")
    client = (
        await db.execute(select(Client).where(Client.id == body.client_id))
    ).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    cred = await _store_credential(
        db,
        client_id=body.client_id,
        provider=body.provider,
        raw_token=f"mock-token-{body.provider}-{body.client_id}",
        scope=body.scope,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        user_id=user.id,
    )
    await db.commit()
    return {"ok": True, "provider": body.provider, "credential_id": str(cred.id), "mode": "mock"}


@router.get("/clients/{client_id}/credentials")
async def list_credentials(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ApiCredential).where(
            ApiCredential.client_id == client_id,
            ApiCredential.revoked_at.is_(None),
        )
    )
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "provider": r.provider,
            "scope": r.scope,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rows
    ]
