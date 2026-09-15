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

import asyncio
import base64
import json
import mimetypes
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.web_fetch import assert_safe_url  # SSRF guard, shared
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

    def auth_header(self, content_type: str | None = "application/json") -> dict[str, str]:
        token = base64.b64encode(
            f"{self.username}:{self.app_password}".encode()
        ).decode()
        headers = {"Authorization": f"Basic {token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def site_root(self) -> str:
        base = (self.base_url or "").strip()
        if not base:
            raise WordPressError("No WordPress site URL configured for this client")
        if "://" not in base:
            base = f"https://{base}"
        return base.rstrip("/")

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


# Transient conditions worth a second attempt. Auth/permission/validation errors are
# deliberately absent — retrying a 401 just burns time and can trip a login limiter.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.75


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """Honour Retry-After when WordPress (or a WAF in front of it) sends one."""
    raw = resp.headers.get("Retry-After", "")
    try:
        if raw:
            return max(0.0, min(30.0, float(raw)))
    except (TypeError, ValueError):
        pass
    return RETRY_BASE_DELAY * (2**attempt)


async def _request(
    client: httpx.AsyncClient,
    conn: WordPressConnection,
    method: str,
    path: str,
    *,
    content_type: str | None = "application/json",
    extra_headers: dict[str, str] | None = None,
    attempts: int = RETRY_ATTEMPTS,
    **kwargs: Any,
) -> httpx.Response:
    """One authenticated REST call, with retries for transient failures only.

    Idempotency note: WordPress writes go to a specific object id (or are preceded by
    a slug lookup), so a retried write updates the same object rather than creating a
    second one. Creates are the exception and are handled by the caller re-running the
    slug lookup — see ``upsert_object``.
    """
    url = urljoin(conn.api_root(), path.lstrip("/"))
    url_ok, safe_url = assert_safe_url(url)
    if not url_ok:
        raise WordPressError(
            f"Refusing to contact this WordPress site — {safe_url.replace('_', ' ')}"
        )
    headers = conn.auth_header(content_type)
    if extra_headers:
        headers.update(extra_headers)

    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            resp = await client.request(method, safe_url, headers=headers, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                log.warning(
                    "wordpress_request_retry",
                    path=path,
                    attempt=attempt + 1,
                    reason=type(exc).__name__,
                )
                await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))
                continue
            raise WordPressError(
                f"WordPress did not respond after {attempts} attempts ({type(exc).__name__})."
            ) from exc

        if resp.status_code == 401:
            raise WordPressError(
                "WordPress rejected the credentials (401). Check the username and that "
                "the Application Password is still valid."
            )
        if resp.status_code == 403:
            raise WordPressError(
                "WordPress refused the request (403). This account likely lacks publish rights."
            )
        if resp.status_code in RETRYABLE_STATUS and attempt < attempts - 1:
            delay = _retry_after_seconds(resp, attempt)
            log.warning(
                "wordpress_request_retry",
                path=path,
                attempt=attempt + 1,
                status=resp.status_code,
                delay=delay,
            )
            await asyncio.sleep(delay)
            continue
        return resp

    assert last_exc is not None
    raise WordPressError(str(last_exc))


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


POST_TYPE_POST = "posts"
POST_TYPE_PAGE = "pages"
ALL_STATUSES = "draft,pending,publish,private,future"

# URL paths that are pages rather than blog posts on essentially every WordPress
# site. Used only as a hint — an existing object found at the slug always wins.
_PAGE_PATH_HINTS = (
    "/services", "/service", "/about", "/contact", "/pricing", "/locations",
    "/location", "/solutions", "/products", "/product", "/industries", "/company",
)
_POST_PATH_HINTS = ("/blog", "/news", "/articles", "/insights", "/resources", "/guides")


def infer_post_type(url_or_path: str, *, page_type: str | None = None) -> str:
    """Best guess at whether a target URL is a WordPress page or a blog post.

    Publishing a service page as a *post* puts it at the wrong URL under the blog
    permalink structure and leaves the real page untouched — so this matters even
    though the caller can override it.
    """
    hint = str(page_type or "").strip().lower()
    if hint in ("post", "article", "blog", "guide", "listicle", "comparison"):
        return POST_TYPE_POST
    if hint in ("page", "service", "subservice", "sub_service", "landing", "location", "product", "home", "hub", "pillar"):
        return POST_TYPE_PAGE

    raw = str(url_or_path or "").strip().lower()
    path = urlparse(raw if "://" in raw else f"https://x{raw if raw.startswith('/') else '/' + raw}").path or "/"
    for marker in _POST_PATH_HINTS:
        if path.startswith(marker) or f"{marker}/" in path:
            return POST_TYPE_POST
    for marker in _PAGE_PATH_HINTS:
        if path.startswith(marker) or f"{marker}/" in path:
            return POST_TYPE_PAGE
    return POST_TYPE_POST


async def find_object_by_slug(
    conn: WordPressConnection,
    slug: str,
    *,
    post_type: str = POST_TYPE_POST,
) -> dict[str, Any] | None:
    """Look up an existing post/page so re-runs update instead of duplicating."""
    if not slug:
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await _request(
            client,
            conn,
            "GET",
            post_type,
            params={"slug": slug, "status": ALL_STATUSES, "per_page": 1, "context": "edit"},
        )
        if resp.status_code >= 400:
            return None
        rows = resp.json()
        if isinstance(rows, list) and rows:
            return rows[0]
    return None


async def find_object_anywhere(
    conn: WordPressConnection, slug: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Find a slug across both pages and posts.

    Prevents the duplicate-object failure mode: if a page already owns this slug,
    creating a post with the same slug yields a second object and WordPress silently
    renames one of them to ``slug-2``.
    """
    for post_type in (POST_TYPE_PAGE, POST_TYPE_POST):
        found = await find_object_by_slug(conn, slug, post_type=post_type)
        if found:
            return found, post_type
    return None, None


async def find_post_by_slug(
    conn: WordPressConnection, slug: str
) -> dict[str, Any] | None:
    """Backwards-compatible wrapper — posts only. Prefer ``find_object_by_slug``."""
    return await find_object_by_slug(conn, slug, post_type=POST_TYPE_POST)


async def fetch_object(
    conn: WordPressConnection, object_id: int, *, post_type: str = POST_TYPE_POST
) -> dict[str, Any] | None:
    """Re-read an object from the CMS to verify what was actually stored.

    ``context=edit`` is required for ``meta`` to come back at all, which is what
    makes Elementor verification possible.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await _request(
            client, conn, "GET", f"{post_type}/{object_id}", params={"context": "edit"}
        )
        if resp.status_code >= 400:
            return None
        return resp.json()


async def fetch_post(conn: WordPressConnection, post_id: int) -> dict[str, Any] | None:
    """Backwards-compatible wrapper — posts only. Prefer ``fetch_object``."""
    return await fetch_object(conn, post_id, post_type=POST_TYPE_POST)


async def fetch_public_url(url: str) -> dict[str, Any]:
    """Fetch the live page anonymously — the only proof that a visitor sees content.

    Deliberately unauthenticated: a draft that renders for an editor but 404s for the
    public is not published, and an authenticated fetch would hide that.
    """
    url_ok, safe_url = assert_safe_url(url)
    if not url_ok:
        return {"ok": False, "error": f"unsafe_url: {safe_url}"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(safe_url, headers={"User-Agent": "RadiusOS-PublishVerify/1.0"})
        return {
            "ok": resp.status_code < 400,
            "status_code": resp.status_code,
            "final_url": str(resp.url),
            "html": resp.text if resp.status_code < 400 else "",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("wordpress_public_fetch_failed", error=str(exc))
        return {"ok": False, "error": f"request_failed: {exc}"}


async def upload_media(
    conn: WordPressConnection,
    *,
    filename: str,
    data: bytes,
    alt_text: str = "",
    title: str = "",
    caption: str = "",
) -> dict[str, Any]:
    """Upload a file to the media library and return its id + public source URL.

    Generated images live as local/relative paths that a visitor's browser cannot
    resolve. Without this step every generated figure publishes broken.
    """
    if not data:
        return {"ok": False, "error": "empty_media"}
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await _request(
                client,
                conn,
                "POST",
                "media",
                content_type=mime,
                extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                content=data,
            )
            if resp.status_code >= 400:
                detail = _error_detail(resp)
                log.warning("wordpress_media_upload_failed", status=resp.status_code)
                return {"ok": False, "error": f"http_{resp.status_code}", "detail": detail}
            body = resp.json()
            media_id = body.get("id")

            # alt/caption are a second call: core ignores them on the binary upload.
            if media_id and (alt_text or title or caption):
                meta_payload: dict[str, Any] = {}
                if alt_text:
                    meta_payload["alt_text"] = alt_text
                if title:
                    meta_payload["title"] = title
                if caption:
                    meta_payload["caption"] = caption
                await _request(client, conn, "POST", f"media/{media_id}", json=meta_payload)
    except WordPressError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("wordpress_media_exception", error=str(exc))
        return {"ok": False, "error": f"request_failed: {exc}"}

    log.info("wordpress_media_uploaded", media_id=media_id, filename=filename)
    return {
        "ok": True,
        "media_id": media_id,
        "source_url": body.get("source_url"),
        "alt_text": alt_text,
    }


def _error_detail(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message") or "")[:200]
    except Exception:  # noqa: BLE001
        return resp.text[:200]


async def detect_site_capabilities(conn: WordPressConnection) -> dict[str, Any]:
    """What this particular site can actually accept.

    Nothing here is assumed from the plugin list alone: a plugin being installed does
    not mean its fields are writable over REST. Callers use this to pick a strategy
    and to raise an honest blocker instead of writing something that will not render.
    """
    caps: dict[str, Any] = {
        "elementor_active": False,
        "elementor_version": None,
        "seo_plugin": None,
        "rest_namespaces": [],
        "pages_endpoint": False,
        "media_endpoint": False,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            root = urljoin(conn.site_root() + "/", "wp-json/")
            url_ok, safe_url = assert_safe_url(root)
            if not url_ok:
                return caps | {"error": "unsafe_url"}
            resp = await client.get(safe_url, headers=conn.auth_header())
            if resp.status_code >= 400:
                return caps | {"error": f"http_{resp.status_code}"}
            body = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("wordpress_capability_probe_failed", error=str(exc))
        return caps | {"error": f"request_failed: {exc}"}

    namespaces = [str(n) for n in (body.get("namespaces") or [])]
    routes = body.get("routes") if isinstance(body.get("routes"), dict) else {}
    caps["rest_namespaces"] = namespaces
    caps["pages_endpoint"] = "/wp/v2/pages" in routes
    caps["media_endpoint"] = "/wp/v2/media" in routes

    # Elementor registers its own REST namespace when active.
    caps["elementor_active"] = any(n.startswith("elementor") for n in namespaces) or any(
        str(r).startswith("/elementor") for r in routes
    )

    for namespace, name in (
        ("yoast", "yoast_seo"),
        ("rankmath", "rank_math"),
        ("aioseo", "all_in_one_seo"),
        ("seopress", "seopress"),
    ):
        if any(namespace in n.lower() for n in namespaces):
            caps["seo_plugin"] = name
            break
    return caps


async def probe_meta_writable(
    conn: WordPressConnection, object_id: int, *, post_type: str, meta_key: str
) -> bool:
    """Can this meta key actually be written through the REST API?

    WordPress rejects meta keys it does not have registered with ``show_in_rest``,
    and keys beginning with ``_`` are protected by default. ``_elementor_data`` is
    **not** registered by core or by Elementor, so on a stock site this returns False
    — which is exactly the case the caller must surface rather than silently "succeed".
    """
    current = await fetch_object(conn, object_id, post_type=post_type)
    if current is None:
        return False
    meta = current.get("meta") if isinstance(current.get("meta"), dict) else {}
    return meta_key in meta


async def upsert_object(
    conn: WordPressConnection | None,
    *,
    title: str,
    content_html: str,
    slug: str,
    excerpt: str = "",
    status: str | None = None,
    meta: dict[str, Any] | None = None,
    post_type: str = POST_TYPE_POST,
    featured_media: int | None = None,
    categories: list[int] | None = None,
    tags: list[int] | None = None,
    author: int | None = None,
    date: str | None = None,
    extra_fields: dict[str, Any] | None = None,
    match_any_post_type: bool = True,
) -> dict[str, Any]:
    """Create or update a post/page. Returns the write result and effective status.

    Never raises for ordinary CMS rejections — the caller needs to report them to a
    human rather than crash the phase.

    Idempotency: the slug is looked up across *both* pages and posts before writing,
    so a re-run updates the existing object instead of letting WordPress mint a
    ``slug-2`` duplicate. When an object already exists under the other post type, we
    update it where it is rather than creating a competing one.
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
    if featured_media is not None:
        payload["featured_media"] = featured_media
    if categories:
        payload["categories"] = categories
    if tags:
        payload["tags"] = tags
    if author is not None:
        payload["author"] = author
    if date:
        payload["date"] = date
    if extra_fields:
        payload.update(extra_fields)

    try:
        if match_any_post_type:
            existing, found_type = await find_object_anywhere(conn, slug)
            if existing and found_type and found_type != post_type:
                log.info(
                    "wordpress_post_type_adjusted",
                    slug=slug,
                    requested=post_type,
                    found=found_type,
                )
                post_type = found_type
        else:
            existing = await find_object_by_slug(conn, slug, post_type=post_type)

        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            if existing and existing.get("id"):
                resp = await _request(
                    client, conn, "POST", f"{post_type}/{existing['id']}", json=payload
                )
                action = "updated"
            else:
                resp = await _request(client, conn, "POST", post_type, json=payload)
                action = "created"

            if resp.status_code >= 400:
                detail = _error_detail(resp)
                log.warning(
                    "wordpress_write_failed",
                    slug=slug,
                    post_type=post_type,
                    status=resp.status_code,
                    action=action,
                )
                return {
                    "ok": False,
                    "error": f"http_{resp.status_code}",
                    "detail": detail,
                    "slug": slug,
                    "post_type": post_type,
                }

            body = resp.json()
    except WordPressError as exc:
        return {"ok": False, "error": str(exc), "slug": slug, "post_type": post_type}
    except Exception as exc:  # noqa: BLE001
        log.warning("wordpress_write_exception", slug=slug, error=str(exc))
        return {
            "ok": False,
            "error": f"request_failed: {exc}",
            "slug": slug,
            "post_type": post_type,
        }

    log.info(
        "wordpress_write_ok",
        slug=slug,
        post_type=post_type,
        action=action,
        status=effective_status,
    )
    return {
        "ok": True,
        "action": action,
        "post_id": body.get("id"),
        "post_type": post_type,
        "slug": body.get("slug") or slug,
        "status": body.get("status") or effective_status,
        "requested_status": (status or get_settings().wordpress_default_status or "draft"),
        "downgraded": bool(downgrade),
        "downgrade_reason": downgrade,
        "link": body.get("link"),
        "edit_link": conn.edit_link(body.get("id")),
        "stored_meta": body.get("meta") if isinstance(body.get("meta"), dict) else {},
    }


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
    """Create or update a blog post. Thin wrapper kept for existing callers."""
    return await upsert_object(
        conn,
        title=title,
        content_html=content_html,
        slug=slug,
        excerpt=excerpt,
        status=status,
        meta=meta,
        post_type=POST_TYPE_POST,
    )
