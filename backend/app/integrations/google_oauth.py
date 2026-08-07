"""Google OAuth helpers for GA4, GSC, GTM, and Google Business Profile."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Least-privilege scopes per product; GBP requires business.manage.
PROVIDER_SCOPES: dict[str, list[str]] = {
    "ga4": ["https://www.googleapis.com/auth/analytics.readonly"],
    "search_console": ["https://www.googleapis.com/auth/webmasters.readonly"],
    "gtm": ["https://www.googleapis.com/auth/tagmanager.readonly"],
    "google_business": ["https://www.googleapis.com/auth/business.manage"],
}

OAUTH_PROVIDERS = tuple(PROVIDER_SCOPES.keys())

PROVIDER_LABELS = {
    "ga4": "Google Analytics 4",
    "search_console": "Google Search Console",
    "gtm": "Google Tag Manager",
    "google_business": "Google Business Profile",
}


def oauth_configured() -> bool:
    s = get_settings()
    return bool(s.google_oauth_client_id and s.google_oauth_client_secret)


def scopes_for(provider: str) -> list[str]:
    return list(PROVIDER_SCOPES.get(provider) or [])


def build_authorize_url(*, state: str, provider: str) -> str:
    s = get_settings()
    scopes = scopes_for(provider)
    if not scopes:
        raise ValueError(f"Unknown OAuth provider: {provider}")
    params = {
        "client_id": s.google_oauth_client_id,
        "redirect_uri": s.oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_oauth_client_id,
                "client_secret": s.google_oauth_client_secret,
                "redirect_uri": s.oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


def pack_token_blob(token_payload: dict[str, Any]) -> str:
    """Serialize tokens for encrypted storage."""
    return json.dumps(
        {
            "access_token": token_payload.get("access_token"),
            "refresh_token": token_payload.get("refresh_token"),
            "token_type": token_payload.get("token_type", "Bearer"),
            "scope": token_payload.get("scope"),
            "id_token": token_payload.get("id_token"),
        }
    )


def expires_at_from(token_payload: dict[str, Any]) -> datetime | None:
    expires_in = token_payload.get("expires_in")
    if not expires_in:
        return None
    try:
        return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def frontend_redirect_base() -> str:
    s = get_settings()
    if s.frontend_url:
        return s.frontend_url.rstrip("/")
    origins = s.cors_origin_list
    return (origins[0] if origins else "http://localhost:5173").rstrip("/")
