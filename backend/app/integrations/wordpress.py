"""WordPress REST API client for Phase 12 publishing.

Connection is per-client, not a deployment-wide setting: this tool runs many clients
through the same Radius OS instance, each with their own WordPress site, so the site
URL and credentials come from the caller (loaded from that client's stored, encrypted
``ApiCredential`` — see ``app.api.integrations``) rather than from environment config.

Safety model — this is the only module in the system that writes to a client's live site:

* **Draft by default.** ``status`` is whatever ``WORDPRESS_DEFAULT_STATUS`` says, and that
  ships as ``draft``. Going live additionally requires ``WORDPRESS_ALLOW_LIVE_PUBLISH``,
  so a misconfigured call cannot publish by accident. These two remain deployment-level
  settings — they are safety policy, not per-site credentials.
* **Idempotent.** Posts are matched on slug before writing, so re-running Phase 12 updates
  the existing draft instead of littering the site with duplicates.
* **Application Passwords only.** Never the account password; the connection is supplied
  by the caller and never logged or echoed into agent output.
* **Verify after write.** ``fetch_post`` re-reads what the CMS actually stored so Phase 12
  can confirm rather than assume (v1.9 step 14: never a silent republish).
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("wordpress")

TIMEOUT = 45.0
ALLOWED_STATUSES = ("draft", "pending", "publish", "private")
CREDENTIAL_PROVIDER = "wordpress"


class WordPressError(RuntimeError):
    """Raised for unrecoverable CMS errors that the caller must surface to a human."""


@dataclass(frozen=True)
class WordPressConnection:
    """One client's WordPress site + Application Password credential."""

    base_url: str
    username: str
    app_password: str

    def api_root(self) -> str:
        base = (self.base_url or "").strip()
        if not base:
            raise WordPressError("No WordPress site URL configured for this client")
        if "://" not in base:
            base = f"https://{base}"
        return urljoin(base.rstrip("/") + "/", "wp-json/wp/v2/")

    def auth_header(self) -> dict[str, str]:
        token = base64.b64encode(
            f"{self.username}:{self.app_password}".encode()
        ).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def edit_link(self, post_id: Any) -> str | None:
        if not post_id:
            return None
        base = (self.base_url or "").rstrip("/")
        if not base:
            return None
        if "://" not in base:
            base = f"https://{base}"
        return f"{base}/wp-admin/post.php?post={post_id}&action=edit"


def connection_configured(conn: WordPressConnection | None) -> bool:
    return bool(conn and conn.base_url and conn.username and conn.app_password)


def pack_connection(conn: WordPressConnection) -> str:
    """Serialize a connection for encrypted storage (paired with unpack_connection)."""
    return json.dumps(
        {
            "base_url": conn.base_url,
            "username": conn.username,
            "app_password": conn.app_password,
        }
    )


def unpack_connection(raw: str) -> WordPressConnection:
    data = json.loads(raw)
    return WordPressConnection(
        base_url=str(data.get("base_url") or ""),
        username=str(data.get("username") or ""),
        app_password=str(data.get("app_password") or ""),
    )


async def load_connection(db: AsyncSession, client_id: UUID) -> WordPressConnection | None:
    """Load and decrypt the client's active WordPress connection, if any.

    Shared by the connect/status API routes and the Phase 12 agent so there is exactly
    one place that turns a stored ``ApiCredential`` row back into a usable connection.
    Imports are local to avoid a models/security import at module load for callers
    (e.g. tests) that only need the pure pack/unpack helpers above.
    """
    from app.models import ApiCredential
    from app.security import decrypt_token

    cred = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.client_id == client_id,
                ApiCredential.provider == CREDENTIAL_PROVIDER,
                ApiCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not cred:
        return None
    return unpack_connection(decrypt_token(cred.encrypted_token))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:190] or "untitled"


def slug_from_url(url: str) -> str:
    path = urlparse(url if "://" in url else f"https://{url}").path or ""
    parts = [p for p in path.split("/") if p]
    return slugify(parts[-1]) if parts else ""


def resolve_status(requested: str | None) -> tuple[str, str | None]:
    """Return (effective_status, downgrade_reason).

    A caller asking to go live is honoured only when the deployment has opted in;
    otherwise the write is downgraded to draft and the reason is reported upward. This
    stays a deployment-level policy (not per-client) — it is a floor no client
    connection can override.
    """
    s = get_settings()
    want = (requested or s.wordpress_default_status or "draft").lower()
    if want not in ALLOWED_STATUSES:
        return "draft", f"unknown status '{want}' — wrote draft instead"
    if want == "publish" and not s.wordpress_allow_live_publish:
        return (
            "draft",
            "live publish blocked: WORDPRESS_ALLOW_LIVE_PUBLISH is false — wrote draft",
        )
    return want, None


async def _request(
    client: httpx.AsyncClient,
    conn: WordPressConnection,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    url = urljoin(conn.api_root(), path.lstrip("/"))
    resp = await client.request(method, url, headers=conn.auth_header(), **kwargs)
    if resp.status_code == 401:
        raise WordPressError(
            "WordPress rejected the credentials (401). Check the username and that "
            "the Application Password is still valid."
        )
    if resp.status_code == 403:
        raise WordPressError(
            "WordPress refused the request (403). This account likely lacks publish rights."
        )
    return resp


async def verify_connection(conn: WordPressConnection | None) -> dict[str, Any]:
    """Confirm the endpoint answers and the credentials can see the posts collection."""
    if not connection_configured(conn):
        return {"ok": False, "error": "wordpress_not_connected"}
    assert conn is not None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await _request(client, conn, "GET", "users/me")
            if resp.status_code >= 400:
                return {"ok": False, "error": f"http_{resp.status_code}"}
            me = resp.json()
            return {
                "ok": True,
                "user": me.get("name") or me.get("slug"),
                "capabilities_publish": bool(
                    (me.get("capabilities") or {}).get("publish_posts", False)
                ),
                "site": conn.base_url,
            }
    except WordPressError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("wordpress_verify_failed", error=str(exc))
        return {"ok": False, "error": f"request_failed: {exc}"}


async def find_post_by_slug(
    conn: WordPressConnection, slug: str
) -> dict[str, Any] | None:
    """Look up an existing post so re-runs update instead of duplicating."""
    if not slug:
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await _request(
            client,
            conn,
            "GET",
            "posts",
            params={"slug": slug, "status": "draft,pending,publish,private", "per_page": 1},
        )
        if resp.status_code >= 400:
            return None
        rows = resp.json()
        if isinstance(rows, list) and rows:
            return rows[0]
    return None


async def fetch_post(conn: WordPressConnection, post_id: int) -> dict[str, Any] | None:
    """Re-read a post from the CMS to verify what was actually stored."""
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await _request(
            client, conn, "GET", f"posts/{post_id}", params={"context": "edit"}
        )
        if resp.status_code >= 400:
            return None
        return resp.json()


async def upsert_post(
    conn: WordPressConnection | None,
    *,
    title: str,
    content_html: str,
    slug: str,
    excerpt: str = "",
    status: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a post. Returns the write result including the effective status.

    Never raises for ordinary CMS rejections — the caller needs to report them to a human
    rather than crash the phase.
    """
    if not connection_configured(conn):
        return {"ok": False, "error": "wordpress_not_connected", "slug": slug}
    assert conn is not None

    effective_status, downgrade = resolve_status(status)
    payload: dict[str, Any] = {
        "title": title,
        "content": content_html,
        "slug": slug,
        "status": effective_status,
    }
    if excerpt:
        payload["excerpt"] = excerpt
    if meta:
        payload["meta"] = meta

    try:
        existing = await find_post_by_slug(conn, slug)
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            if existing and existing.get("id"):
                resp = await _request(
                    client, conn, "POST", f"posts/{existing['id']}", json=payload
                )
                action = "updated"
            else:
                resp = await _request(client, conn, "POST", "posts", json=payload)
                action = "created"

            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = str(resp.json().get("message") or "")[:200]
                except Exception:  # noqa: BLE001
                    detail = resp.text[:200]
                log.warning(
                    "wordpress_write_failed",
                    slug=slug,
                    status=resp.status_code,
                    action=action,
                )
                return {
                    "ok": False,
                    "error": f"http_{resp.status_code}",
                    "detail": detail,
                    "slug": slug,
                }

            body = resp.json()
    except WordPressError as exc:
        return {"ok": False, "error": str(exc), "slug": slug}
    except Exception as exc:  # noqa: BLE001
        log.warning("wordpress_write_exception", slug=slug, error=str(exc))
        return {"ok": False, "error": f"request_failed: {exc}", "slug": slug}

    log.info("wordpress_write_ok", slug=slug, action=action, status=effective_status)
    return {
        "ok": True,
        "action": action,
        "post_id": body.get("id"),
        "slug": body.get("slug") or slug,
        "status": body.get("status") or effective_status,
        "requested_status": (status or get_settings().wordpress_default_status or "draft"),
        "downgraded": bool(downgrade),
        "downgrade_reason": downgrade,
        "link": body.get("link"),
        "edit_link": conn.edit_link(body.get("id")),
    }
