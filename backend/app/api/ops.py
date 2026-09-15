"""Ops endpoints — dead man's switch check-in / status."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services import dead_man_switch as dms

router = APIRouter(prefix="/ops/dead-man-switch", tags=["ops"])


class CheckInBody(BaseModel):
    note: str | None = Field(default=None, max_length=240)


def _require_token(token: str | None) -> None:
    settings = get_settings()
    expected = (settings.dead_man_switch_token or "").strip()
    if not expected:
        raise HTTPException(
            503,
            "Dead man's switch token is not configured (DEAD_MAN_SWITCH_TOKEN).",
        )
    provided = (token or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "Invalid dead man's switch token")


def _token_from_headers(
    x_dead_man_switch_token: str | None,
    authorization: str | None,
) -> str | None:
    if x_dead_man_switch_token:
        return x_dead_man_switch_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@router.get("/status")
async def dead_man_switch_status(
    x_dead_man_switch_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Inspect timer — requires the ops token (not a user JWT)."""
    _require_token(_token_from_headers(x_dead_man_switch_token, authorization))
    return await dms.get_status()


@router.post("/check-in")
async def dead_man_switch_check_in(
    body: CheckInBody | None = None,
    x_dead_man_switch_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Reset the dead man's switch timer."""
    _require_token(_token_from_headers(x_dead_man_switch_token, authorization))
    settings = get_settings()
    if not settings.dead_man_switch_enabled:
        # Still record so enabling later has a fresh check-in.
        status = await dms.record_check_in(note=(body.note if body else None))
        status["warning"] = "Switch is currently disabled; check-in stored for when you enable it."
        return status
    return await dms.record_check_in(note=(body.note if body else None))
