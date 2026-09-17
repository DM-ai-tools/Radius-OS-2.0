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
import contextvars
import json
import mimetypes
import re
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.chromium_sync import ChromiumSession
from app.integrations.web_fetch import DEFAULT_HEADERS, assert_safe_url  # SSRF guard, shared
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
    # rest: Authorization header reached WordPress. xmlrpc: this host drops that
    # header (Cloudflare/SiteGround), so the official app path is the one that works.
    auth_transport: str = "rest"

    def api_root(self) -> str:
        return urljoin(self.site_root() + "/", "wp-json/wp/v2/")

    def auth_header(self, content_type: str | None = "application/json") -> dict[str, str]:
        # WordPress displays Application Passwords with spaces and ignores them.
        password = (self.app_password or "").replace(" ", "")
        token = base64.b64encode(f"{self.username}:{password}".encode()).decode()
        headers = {
            "Authorization": f"Basic {token}",
            # Some hosts strip Authorization but forward this alias to PHP.
            "X-Authorization": f"Basic {token}",
            "User-Agent": _wp_user_agent(self.site_root()),
            "Accept": "application/json",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def site_root(self) -> str:
        return normalize_site_url(self.base_url)

    def edit_link(self, post_id: Any) -> str | None:
        if not post_id:
            return None
        try:
            base = self.site_root()
        except WordPressError:
            return None
        return f"{base}/wp-admin/post.php?post={post_id}&action=edit"


# Path segments that are WordPress admin/API entry points, not the site root.
# Pasting /wp-admin or /wp-json makes /wp-json/wp/v2/users/me 404. The official
# WordPress app discovers the REST root; we strip these the same way.
_WP_ENTRY_SEGMENTS = frozenset({"wp-admin", "wp-login.php", "xmlrpc.php", "wp-json"})


def normalize_site_url(raw: str) -> str:
    """Public site origin the REST API actually lives on.

    ``https://site.test/wp-admin`` and ``https://site.test/wp-json/wp/v2`` both
    become ``https://site.test``. A real subdirectory install (``/blog``) is kept.
    """
    base = (raw or "").strip()
    if not base:
        raise WordPressError("No WordPress site URL configured for this client")
    if "://" not in base:
        base = f"https://{base}"
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise WordPressError("Site URL must be an http(s) address")
    parts: list[str] = []
    for part in (parsed.path or "").split("/"):
        if not part:
            continue
        if part.lower() in _WP_ENTRY_SEGMENTS:
            break
        parts.append(part)
    path = "/" + "/".join(parts) if parts else ""
    return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")


def candidate_site_roots(raw: str) -> list[str]:
    """URLs to try, in order, when the pasted address is not the REST root.

    The WordPress app succeeds on the same password because it does not trust
    the pasted path: it uses the site origin, and the apex/www host that actually
    serves ``/wp-json``. A page path or ``/wp-admin`` must not be the only try.
    """
    primary = normalize_site_url(raw)
    roots: list[str] = []

    def add(url: str) -> None:
        cleaned = url.rstrip("/")
        if cleaned and cleaned not in roots:
            roots.append(cleaned)

    add(primary)
    parsed = urlparse(primary)
    add(f"{parsed.scheme}://{parsed.netloc}")
    host = (parsed.hostname or "").lower()
    if host and not host.replace(".", "").isdigit():
        alt = host[4:] if host.startswith("www.") else f"www.{host}"
        port = ""
        if parsed.port:
            port = f":{parsed.port}"
        alt_netloc = f"{alt}{port}"
        add(f"{parsed.scheme}://{alt_netloc}{parsed.path or ''}".rstrip("/"))
        add(f"{parsed.scheme}://{alt_netloc}")
    return roots


def connection_configured(conn: WordPressConnection | None) -> bool:
    return bool(conn and conn.base_url and conn.username and conn.app_password)


def pack_connection(conn: WordPressConnection) -> str:
    """Serialize a connection for encrypted storage (paired with unpack_connection)."""
    return json.dumps(
        {
            "base_url": conn.base_url,
            "username": conn.username,
            "app_password": conn.app_password,
            "auth_transport": conn.auth_transport or "rest",
        }
    )


def unpack_connection(raw: str) -> WordPressConnection:
    data = json.loads(raw)
    return WordPressConnection(
        base_url=str(data.get("base_url") or ""),
        username=str(data.get("username") or ""),
        app_password=str(data.get("app_password") or ""),
        auth_transport=str(data.get("auth_transport") or "rest"),
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


async def _request_once(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    hops: int = 4,
    **kwargs: Any,
) -> httpx.Response:
    """Send one REST call, re-attaching the Application Password on every redirect.

    httpx drops Authorization when a redirect changes host. A 301 from apex to www
    then looks logged-out, and the write that was supposed to update a page fails.
    """
    current = url
    last: httpx.Response | None = None
    for _ in range(hops):
        ok, safe = assert_safe_url(current)
        if not ok:
            raise WordPressError(
                f"Refusing to contact this WordPress site — {safe.replace('_', ' ')}"
            )
        last = await client.request(
            method, safe, headers=headers, follow_redirects=False, **kwargs
        )
        if last.status_code not in (301, 302, 303, 307, 308):
            return last
        location = last.headers.get("location")
        if not location:
            return last
        current = urljoin(str(last.url), location)
    assert last is not None
    return last


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
            resp = await _request_once(client, method, safe_url, headers, **kwargs)
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

        if resp.status_code == 401 and not _should_fall_back_to_xmlrpc(resp):
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


def _publish_capability(me: dict[str, Any]) -> bool | None:
    """Whether this user can write posts or pages.

    WordPress omits ``capabilities`` unless ``context=edit`` was requested. Missing
    caps are unknown, not a denial — treating them as False blocks a working editor.
    """
    caps = me.get("capabilities")
    if isinstance(caps, dict) and caps:
        if caps.get("publish_posts") or caps.get("publish_pages"):
            return True
        if "publish_posts" in caps or "publish_pages" in caps:
            return False
    roles = me.get("roles") or []
    if isinstance(roles, list) and roles:
        return any(str(role).lower() in _PUBLISH_ROLES for role in roles)
    return None


def _users_me_url(root: str) -> str:
    return urljoin(root.rstrip("/") + "/", "wp-json/wp/v2/users/me?context=edit")


def _users_me_route(root: str) -> str:
    return root.rstrip("/") + "/?rest_route=/wp/v2/users/me&context=edit"


def _should_fall_back_to_xmlrpc(resp: httpx.Response) -> bool:
    """Host saw an anonymous request, or answered with a bot page instead of JSON."""
    if resp.status_code in (202, 520) or _is_waf_challenge(resp):
        return True
    return resp.status_code == 401 and _rest_error_code(resp) == "rest_not_logged_in"


def _rest_error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(body, dict):
        return ""
    return str(body.get("code") or "")


def _xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _xml_value_text(value: ET.Element | None) -> str:
    if value is None:
        return ""
    for tag in ("string", "int", "i4", "boolean"):
        node = value.find(tag)
        if node is not None and node.text:
            return node.text
        node = value.find(f".//{tag}")
        if node is not None and node.text:
            return node.text
    return ""


def _struct_fields(struct: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for member in struct.findall("member"):
        name = member.findtext("name") or ""
        fields[name] = _xml_value_text(member.find("value"))
    return fields


def _wp_user_agent(site: str) -> str:
    # Browser UAs get a SiteGround/Cloudflare 202 challenge (and sometimes a 520).
    # The official WordPress app UA is the path this host already allows.
    return f"WordPress/6.6; {site.rstrip('/')}"


def _is_waf_challenge(resp: httpx.Response) -> bool:
    if resp.status_code in (202, 520):
        return True
    text = (resp.text or "").lstrip()
    if resp.status_code < 400 and (
        text.startswith("<?xml") or text.startswith("{") or text.startswith("[")
    ):
        return False
    ctype = (resp.headers.get("content-type") or "").lower()
    return "html" in ctype and "xml" not in ctype[:20]


def _xmlrpc_request(method: str, params: list[tuple[str, str]]) -> str:
    chunks = []
    for kind, value in params:
        if kind == "int":
            inner = f"<int>{int(value)}</int>"
        else:
            inner = f"<string>{_xml_escape(value)}</string>"
        chunks.append(f"<param><value>{inner}</value></param>")
    body = "".join(chunks)
    return (
        '<?xml version="1.0"?>'
        f"<methodCall><methodName>{method}</methodName><params>{body}</params></methodCall>"
    )


def _parse_xmlrpc(text: str) -> dict[str, Any]:
    root = ET.fromstring(text)
    fault = root.find("fault")
    if fault is not None:
        fields = _struct_fields(fault.find(".//struct")) if fault.find(".//struct") is not None else {}
        return {
            "ok": False,
            "fault_code": int(fields.get("faultCode") or 0),
            "fault_string": fields.get("faultString") or "XML-RPC rejected the request",
        }
    records = [
        _struct_fields(struct)
        for struct in root.findall("./params/param/value/array/data/value/struct")
    ]
    single = root.find("./params/param/value/struct")
    if single is not None and not records:
        records = [_struct_fields(single)]
    if not records:
        struct = root.find(".//struct")
        if struct is not None:
            records = [_struct_fields(struct)]
    fields = records[0] if records else {}
    scalar = ""
    value = root.find("./params/param/value")
    if value is not None and value.find("struct") is None and value.find("array") is None:
        scalar = _xml_value_text(value)
    roles = []
    for member in root.findall(".//member"):
        if (member.findtext("name") or "") == "roles":
            roles = [node.text for node in member.findall(".//string") if node.text]
            break
    return {"ok": True, "fields": fields, "roles": roles, "records": records, "scalar": scalar}


_PUBLISH_ROLES = frozenset({"administrator", "editor", "author"})


async def _verify_xmlrpc(
    client: httpx.AsyncClient,
    root: str,
    conn: WordPressConnection,
) -> dict[str, Any]:
    """Same login the WordPress app uses: credentials in the XML body, not a header.

    Click Trends (Cloudflare in front of SiteGround) drops Authorization before PHP
    sees it, so REST users/me is anonymous even when the password is valid. xmlrpc.php
    still checks the password, which is why the app connects and we did not.
    """
    url = root.rstrip("/") + "/xmlrpc.php"
    headers = {
        "Content-Type": "text/xml",
        "User-Agent": _wp_user_agent(root),
        "Accept": "text/xml, application/xml",
    }
    blogs = await _xmlrpc_post(
        client,
        url,
        headers,
        _xmlrpc_request(
            "wp.getUsersBlogs",
            [("string", conn.username), ("string", conn.app_password)],
        ),
    )
    if not blogs.get("ok"):
        return _xmlrpc_failure(blogs, url)
    fields = blogs.get("fields") or {}
    blog_id = str(fields.get("blogid") or "1")
    profile = await _xmlrpc_post(
        client,
        url,
        headers,
        _xmlrpc_request(
            "wp.getProfile",
            [("int", blog_id), ("string", conn.username), ("string", conn.app_password)],
        ),
    )
    roles = profile.get("roles") or []
    if roles:
        can_publish = any(role.lower() in _PUBLISH_ROLES for role in roles)
    else:
        can_publish = str(fields.get("isAdmin") or "") in {"1", "true"}
    user = ""
    if profile.get("ok"):
        user = str((profile.get("fields") or {}).get("display_name") or "")
    user = user or conn.username
    return {
        "ok": True,
        "user": user,
        "capabilities_publish": can_publish,
        "site": root,
        "base_url": root,
        "auth_transport": "xmlrpc",
        "detail": (
            "Credentials accepted the same way the WordPress app connects. "
            "This host drops the REST Authorization header, so drafts — pages and posts — "
            "are written over XML-RPC."
        ),
    }


async def _xmlrpc_post(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: str,
) -> dict[str, Any]:
    ok, safe = assert_safe_url(url)
    if not ok:
        raise WordPressError(
            f"Refusing to contact this WordPress site — {safe.replace('_', ' ')}"
        )
    last: dict[str, Any] | None = None
    for attempt in range(2):
        resp = await client.post(safe, content=body.encode(), headers=headers)
        parsed = _xmlrpc_from_http(resp.status_code, resp.text or "", url, waf=_is_waf_challenge(resp))
        if parsed.get("ok") or parsed.get("fault_code") == 403:
            return parsed
        last = parsed
        if attempt == 0 and (resp.status_code in (202, 520) or _is_waf_challenge(resp)):
            await asyncio.sleep(0.25)
            continue
        break
    if last and last.get("waf_challenge") and _allow_browser.get():
        via_browser = await _xmlrpc_post_via_chromium(safe, headers, body)
        if via_browser.get("ok") or via_browser.get("fault_code") == 403:
            return via_browser
        if via_browser.get("chromium_error") and last is not None:
            last = {
                **last,
                "chromium_error": via_browser.get("chromium_error"),
                "chromium_error_detail": via_browser.get("chromium_error_detail"),
            }
    return last or {
        "ok": False,
        "fault_code": 0,
        "fault_string": "xmlrpc_blocked",
        "attempted_url": url,
    }


def _xmlrpc_from_http(
    status: int,
    text: str,
    url: str,
    *,
    waf: bool = False,
) -> dict[str, Any]:
    if status < 400 and text.lstrip().startswith("<?xml"):
        try:
            parsed = _parse_xmlrpc(text)
        except ET.ParseError:
            return {
                "ok": False,
                "fault_code": status,
                "fault_string": "xmlrpc_blocked",
                "attempted_url": url,
                "waf_challenge": waf,
            }
        parsed["attempted_url"] = url
        return parsed
    return {
        "ok": False,
        "fault_code": status,
        "fault_string": "xmlrpc_blocked",
        "attempted_url": url,
        "waf_challenge": waf or status in (202, 520),
    }


_XMLRPC_BLOCKED = {
    "ok": False,
    "fault_code": 202,
    "fault_string": "xmlrpc_blocked",
    "waf_challenge": True,
}

_chromium_xmlrpc: contextvars.ContextVar["_ChromiumXmlrpcSession | None"] = contextvars.ContextVar(
    "wordpress_chromium_xmlrpc", default=None
)
_allow_browser: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "wordpress_allow_browser", default=False
)


class _ChromiumXmlrpcSession:
    """One Chromium for a verify/publish burst so we do not relaunch per XML-RPC call.

    Runs Playwright in a worker thread. Uvicorn on Windows cannot spawn the
    Playwright driver on the request loop (NotImplementedError).
    """

    def __init__(self) -> None:
        self._inner: ChromiumSession | None = None

    def _ensure(self) -> ChromiumSession:
        if self._inner is None:
            self._inner = ChromiumSession(user_agent="")
        return self._inner

    async def post(self, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
        blocked = {**_XMLRPC_BLOCKED, "attempted_url": url}
        if not get_settings().enable_playwright_rendering or not _allow_browser.get():
            return blocked
        parsed_url = urlparse(url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}/"
        origin_ok, safe_origin = assert_safe_url(origin)
        extra = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
        origin = safe_origin if origin_ok else None
        try:
            status, text = await asyncio.to_thread(
                self._ensure().post_xmlrpc, url, extra, body, origin
            )
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}".strip() or type(exc).__name__
            log.warning("wordpress_xmlrpc_chromium_failed", error=detail[:240])
            launch = any(
                marker in detail
                for marker in (
                    "NotImplementedError",
                    "did not start",
                    "did not become ready",
                    "worker exit",
                    "ImportError",
                )
            )
            return {
                **blocked,
                "chromium_error": "launch_failed" if launch else "request_failed",
                "chromium_error_detail": detail[:240],
            }
        parsed = _xmlrpc_from_http(status, text, url, waf=False)
        if parsed.get("ok"):
            log.info("wordpress_xmlrpc_chromium_ok", url=url)
        else:
            parsed["waf_challenge"] = parsed.get("waf_challenge") or status in (202, 520)
            log.warning(
                "wordpress_xmlrpc_chromium_rejected",
                status=status,
                xml=str(text or "")[:80],
            )
        return parsed

    async def rest(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        parsed_url = urlparse(url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}/"
        origin_ok, safe_origin = assert_safe_url(origin)
        extra = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
        return await asyncio.to_thread(
            self._ensure().rest_get,
            url,
            extra,
            safe_origin if origin_ok else None,
        )

    async def close(self) -> None:
        inner = self._inner
        self._inner = None
        if inner is not None:
            await asyncio.to_thread(inner.close)


@asynccontextmanager
async def _chromium_xmlrpc_scope():
    if _chromium_xmlrpc.get() is not None:
        yield
        return
    session = _ChromiumXmlrpcSession()
    token = _chromium_xmlrpc.set(session)
    try:
        yield
    finally:
        _chromium_xmlrpc.reset(token)
        await session.close()


async def _xmlrpc_post_via_chromium(
    url: str,
    headers: dict[str, str],
    body: str,
) -> dict[str, Any]:
    """XML-RPC over the same Chromium stack live site scans already use.

    SiteGround answers 202 to the plain HTTP client. Chromium first loads the
    public origin (as live scans do) so any JS cookies are present, then POSTs
    xmlrpc.php. Credentials stay in the XML body — never sent through Firecrawl.
    """
    session = _chromium_xmlrpc.get()
    owned = False
    if session is None:
        session = _ChromiumXmlrpcSession()
        owned = True
    try:
        return await session.post(url, headers, body)
    finally:
        if owned:
            await session.close()


def _xmlrpc_failure(result: dict[str, Any], url: str) -> dict[str, Any]:
    code = int(result.get("fault_code") or 0)
    fault = str(result.get("fault_string") or "")
    if code == 403 or "incorrect username or password" in fault.lower():
        return {
            "ok": False,
            "error": (
                "WordPress rejected the username or password. Use the WordPress "
                "username (not the display name) and the same password the WordPress app accepted."
            ),
            "attempted_url": url,
            "auth_transport": "xmlrpc",
        }
    if fault == "xmlrpc_blocked" or code >= 500 or code in (202, 520):
        if result.get("chromium_error") == "launch_failed":
            detail = (
                "The host returned a bot check (HTTP 202) to the plain HTTP client. "
                "Chromium could not start on this API process "
                f"({result.get('chromium_error_detail') or 'unknown error'}). "
                "Restart the API and connect again."
            )
        elif result.get("chromium_error") == "request_failed":
            detail = (
                "The host returned a bot check (HTTP 202) to the plain HTTP client. "
                "Chromium reached the site but XML-RPC was still dropped "
                f"({result.get('chromium_error_detail') or 'connection reset'}). "
                "Ask SiteGround/Cloudflare to allow /xmlrpc.php for this server, then connect again."
            )
        elif code in (202, 520) or result.get("waf_challenge"):
            detail = (
                "The host returned a bot check (HTTP 202) instead of a WordPress login. "
                "SiteGround is dropping both the REST login and /xmlrpc.php from this computer. "
                "This is not a wrong password. In SiteGround Site Tools, pause Bot Protection "
                "for this site, connect again, then turn it back on."
            )
        else:
            detail = (
                "The host dropped the REST login header, and XML-RPC (the WordPress app path) "
                "was blocked too."
            )
        return {
            "ok": False,
            "error": "xmlrpc_blocked",
            "detail": detail,
            "attempted_url": url,
            "auth_transport": "xmlrpc",
        }
    return {
        "ok": False,
        "error": fault or f"xmlrpc_{code or 'failed'}",
        "attempted_url": url,
        "auth_transport": "xmlrpc",
    }


def _looks_like_rest_json(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" in ctype:
        return True
    text = (resp.text or "").lstrip()
    return text.startswith("{") or text.startswith("[")


async def _get_with_auth(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    hops: int = 4,
) -> httpx.Response:
    """GET that keeps the Application Password across host redirects.

    httpx drops Authorization when a redirect changes host (apex to www, or the
    reverse). Sites that 301 the REST root then look logged-out, and some WAFs
    answer that with 404 instead of 401. Re-attach credentials on every hop.
    """
    current = url
    last: httpx.Response | None = None
    for _ in range(hops):
        ok, safe = assert_safe_url(current)
        if not ok:
            raise WordPressError(
                f"Refusing to contact this WordPress site — {safe.replace('_', ' ')}"
            )
        last = await client.get(safe, headers=headers)
        if last.status_code not in (301, 302, 303, 307, 308):
            return last
        location = last.headers.get("location")
        if not location:
            return last
        current = urljoin(str(last.url), location)
    assert last is not None
    return last


async def _check_users_me(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> tuple[dict[str, Any] | None, int | None]:
    """Return (terminal result, status). None means try the next candidate URL."""
    last: httpx.Response | None = None
    last = await _get_with_auth(client, url, headers)
    resp = last
    assert resp is not None
    if resp.status_code == 401:
        code = _rest_error_code(resp)
        # Header never reached PHP. Same response as an anonymous request, so this
        # is not proof the password is wrong — the WordPress app does not use this path.
        if code == "rest_not_logged_in":
            return {
                "ok": False,
                "error": "rest_auth_header_ignored",
                "code": code,
                "attempted_url": url,
            }, 401
        raise WordPressError(
            "WordPress rejected the credentials (401). Check the username and that "
            "the Application Password is still valid."
        )
    if resp.status_code == 403:
        raise WordPressError(
            "WordPress refused the request (403). This account likely lacks publish rights."
        )
    if resp.status_code == 404 or not _looks_like_rest_json(resp):
        return None, resp.status_code
    if resp.status_code >= 400:
        return {"ok": False, "error": f"http_{resp.status_code}", "attempted_url": url}, resp.status_code
    me = resp.json()
    if not isinstance(me, dict) or not (me.get("name") or me.get("slug") or me.get("id")):
        return None, resp.status_code
    return {
        "ok": True,
        "user": me.get("name") or me.get("slug"),
        "capabilities_publish": _publish_capability(me),
    }, resp.status_code


def _me_from_rest_body(status: int, text: str, url: str) -> dict[str, Any] | None:
    stripped = (text or "").lstrip()
    if status == 401:
        if "rest_not_logged_in" in stripped:
            return {
                "ok": False,
                "error": "rest_auth_header_ignored",
                "attempted_url": url,
            }
        if stripped.startswith("{") or "password" in stripped.lower() or "username" in stripped.lower():
            return {
                "ok": False,
                "error": (
                    "WordPress rejected the username or password. Use the WordPress "
                    "username (not the display name) and the same password the WordPress app accepted."
                ),
                "attempted_url": url,
            }
        return None
    if not stripped.startswith("{") and not stripped.startswith("["):
        return None
    try:
        me = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(me, dict) or not (me.get("name") or me.get("slug") or me.get("id")):
        return None
    return {
        "ok": True,
        "user": me.get("name") or me.get("slug"),
        "capabilities_publish": _publish_capability(me),
        "site": "",
        "attempted_url": url,
    }


async def _verify_rest_via_chromium(
    conn: WordPressConnection,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    """REST login from installed Chrome after the public site has loaded.

    This host resets XML-RPC and answers 202 to the plain HTTP client. A visible
    Chrome session is what actually reaches WordPress.
    """
    if not get_settings().enable_playwright_rendering:
        return None
    session = _chromium_xmlrpc.get()
    if session is None:
        return None
    last_rejected: dict[str, Any] | None = None
    for root in candidate_site_roots(conn.base_url):
        url = _users_me_url(root)
        try:
            status, text = await session.rest(url, headers)
        except Exception as exc:  # noqa: BLE001
            log.warning("wordpress_rest_chromium_failed", error=f"{type(exc).__name__}: {exc}"[:240])
            return None
        parsed = _me_from_rest_body(status, text, url)
        if parsed and parsed.get("ok"):
            parsed["site"] = root
            parsed["base_url"] = root
            parsed["auth_transport"] = "rest"
            log.info("wordpress_rest_chromium_ok", url=url)
            return parsed
        if parsed and "rejected the username" in str(parsed.get("error") or ""):
            return parsed
        if status in (202, 520) or "sgcaptcha" in (text or ""):
            last_rejected = {
                "ok": False,
                "error": "xmlrpc_blocked",
                "attempted_url": url,
                "waf_challenge": True,
                "detail": (
                    "Chrome opened the site but WordPress login is still behind a "
                    "SiteGround security check. Complete that check in the Chrome window "
                    "if it is still open, then connect again."
                ),
            }
            continue
        log.warning("wordpress_rest_chromium_rejected", status=status, snippet=(text or "")[:80])
    return last_rejected


async def verify_connection(
    conn: WordPressConnection | None,
    *,
    use_browser: bool = False,
) -> dict[str, Any]:
    """Confirm the credentials, discovering the REST root and falling back to XML-RPC.

    A pasted /wp-admin URL 404s if we append wp-json to it. The WordPress app still
    connects: it uses the site origin, and on hosts that drop the Authorization
    header it logs in through XML-RPC with the password in the request body.
    """
    if not connection_configured(conn):
        return {"ok": False, "error": "wordpress_not_connected"}
    assert conn is not None
    headers = conn.auth_header()
    last_url = ""
    last_status: int | None = None
    header_ignored: list[str] = []
    try:
        token = _allow_browser.set(use_browser)
        async with _chromium_xmlrpc_scope():
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
                for root in candidate_site_roots(conn.base_url):
                    pretty = _users_me_url(root)
                    found, status = await _check_users_me(client, pretty, headers)
                    last_url, last_status = pretty, status
                    if found is None and status == 404:
                        route = _users_me_route(root)
                        found, status = await _check_users_me(client, route, headers)
                        last_url, last_status = route, status
                    if found and found.get("ok"):
                        found["site"] = root
                        found["base_url"] = root
                        found["auth_transport"] = "rest"
                        return found
                    if found and found.get("error") == "rest_auth_header_ignored":
                        header_ignored.append(root)
                        continue
                    if found is None:
                        continue
                    found.setdefault("attempted_url", last_url)
                    return found

                if use_browser:
                    browser = await _verify_rest_via_chromium(conn, headers)
                    if browser and (
                        browser.get("ok")
                        or browser.get("waf_challenge")
                        or "rejected the username" in str(browser.get("error") or "")
                    ):
                        return browser

                xml_failure: dict[str, Any] | None = None
                xml_roots = candidate_site_roots(conn.base_url)
                if header_ignored:
                    xml_roots = list(dict.fromkeys([*header_ignored, *xml_roots]))
                for root in xml_roots:
                    xml = await _verify_xmlrpc(client, root, conn)
                    if xml.get("ok"):
                        return xml
                    xml_failure = xml
                    # 403 means this site saw the password and rejected it. Don't keep hunting.
                    if "rejected the username or password" in str(xml.get("error") or ""):
                        return xml
                if xml_failure:
                    return xml_failure
    except WordPressError as exc:
        return {"ok": False, "error": str(exc), "attempted_url": last_url}
    except Exception as exc:  # noqa: BLE001
        log.warning("wordpress_verify_failed", error=str(exc))
        return {"ok": False, "error": f"request_failed: {exc}", "attempted_url": last_url}
    finally:
        _allow_browser.reset(token)

    detail = (
        "WordPress REST was not found at that address. "
        "Use the public site URL, not /wp-admin, /wp-json, or a page path."
    )
    return {
        "ok": False,
        "error": f"http_{last_status}" if last_status and last_status >= 400 else "http_404",
        "attempted_url": last_url,
        "detail": detail,
    }


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


def _xml_string(value: str) -> str:
    return f"<string>{_xml_escape(value)}</string>"


def _xml_struct(fields: dict[str, str]) -> str:
    members = [
        f"<member><name>{_xml_escape(name)}</name><value>{inner}</value></member>"
        for name, inner in fields.items()
    ]
    return f"<struct>{''.join(members)}</struct>"


def _xml_array(inners: list[str]) -> str:
    data = "".join(f"<value>{inner}</value>" for inner in inners)
    return f"<array><data>{data}</data></array>"


def _xmlrpc_type_name(post_type: str) -> str:
    return "page" if post_type in (POST_TYPE_PAGE, "page") else "post"


def _rest_post_type(wp_type: str) -> str:
    return POST_TYPE_PAGE if wp_type == "page" else POST_TYPE_POST


def _rest_shape_from_xmlrpc(fields: dict[str, str], *, post_type: str) -> dict[str, Any]:
    raw_id = str(fields.get("post_id") or fields.get("postid") or "")
    title = fields.get("post_title") or ""
    content = fields.get("post_content") or ""
    excerpt = fields.get("post_excerpt") or ""
    wp_type = fields.get("post_type") or _xmlrpc_type_name(post_type)
    thumb = str(fields.get("post_thumbnail") or "")
    return {
        "id": int(raw_id) if raw_id.isdigit() else raw_id,
        "slug": fields.get("post_name") or "",
        "status": fields.get("post_status") or "draft",
        "title": {"raw": title, "rendered": title},
        "content": {"raw": content, "rendered": content},
        "excerpt": {"raw": excerpt, "rendered": excerpt},
        "link": fields.get("link") or "",
        "featured_media": int(thumb) if thumb.isdigit() else 0,
        "meta": {},
        "type": wp_type,
        "post_type": _rest_post_type(wp_type),
    }


def _xmlrpc_auth_params(conn: WordPressConnection, blog_id: str = "1") -> str:
    return (
        f"<param><value><int>{int(blog_id or 1)}</int></value></param>"
        f"<param><value>{_xml_string(conn.username)}</value></param>"
        f"<param><value>{_xml_string(conn.app_password)}</value></param>"
    )


def _xmlrpc_envelope(method: str, params_xml: str) -> str:
    return (
        '<?xml version="1.0"?>'
        f"<methodCall><methodName>{method}</methodName><params>{params_xml}</params></methodCall>"
    )


async def _xmlrpc_call(
    conn: WordPressConnection,
    method: str,
    extra_params: str = "",
    *,
    blog_id: str = "1",
) -> dict[str, Any]:
    root = conn.site_root()
    url = root.rstrip("/") + "/xmlrpc.php"
    body = _xmlrpc_envelope(method, _xmlrpc_auth_params(conn, blog_id) + extra_params)
    headers = {
        "Content-Type": "text/xml",
        "User-Agent": _wp_user_agent(root),
        "Accept": "text/xml, application/xml",
    }
    async with _chromium_xmlrpc_scope():
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            return await _xmlrpc_post(client, url, headers, body)


async def _public_object_by_slug(
    conn: WordPressConnection, slug: str, post_type: str
) -> dict[str, Any] | None:
    """Published objects are readable without the Authorization header this host drops."""
    url = urljoin(conn.site_root() + "/", f"wp-json/wp/v2/{post_type}")
    ok, safe = assert_safe_url(url)
    if not ok:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                safe,
                params={"slug": slug, "per_page": 1},
                headers={"User-Agent": DEFAULT_HEADERS["User-Agent"], "Accept": "application/json"},
            )
    except (httpx.TimeoutException, httpx.TransportError):
        return None
    if resp.status_code >= 400:
        return None
    try:
        rows = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        row = rows[0]
        row.setdefault("post_type", post_type)
        return row
    return None


async def _xmlrpc_find_by_slug(
    conn: WordPressConnection, slug: str, post_type: str
) -> dict[str, Any] | None:
    """Find a page or post by slug when REST auth never reaches PHP."""
    statuses = _xml_array(
        [_xml_string(status) for status in ("draft", "pending", "publish", "private", "future")]
    )
    filt = _xml_struct(
        {
            "post_type": _xml_string(_xmlrpc_type_name(post_type)),
            "number": "<int>100</int>",
            "s": _xml_string(slug),
            "post_status": statuses,
        }
    )
    listed = await _xmlrpc_call(conn, "wp.getPosts", f"<param><value>{filt}</value></param>")
    if listed.get("ok"):
        for fields in listed.get("records") or []:
            if str(fields.get("post_name") or "") == slug:
                return _rest_shape_from_xmlrpc(fields, post_type=post_type)
    return await _public_object_by_slug(conn, slug, post_type)


async def _xmlrpc_fetch(conn: WordPressConnection, object_id: int, post_type: str) -> dict[str, Any] | None:
    result = await _xmlrpc_call(
        conn,
        "wp.getPost",
        f"<param><value><int>{int(object_id)}</int></value></param>",
    )
    if not result.get("ok"):
        return None
    fields = result.get("fields") or {}
    if not fields:
        return None
    shaped = _rest_shape_from_xmlrpc(fields, post_type=post_type)
    if not shaped.get("id"):
        shaped["id"] = int(object_id)
    return shaped


def _xml_custom_fields(meta: dict[str, Any]) -> str:
    """WordPress XML-RPC custom_fields array. REST meta does not travel any other way."""
    rows: list[str] = []
    for key, value in meta.items():
        if value is None or not str(key).strip():
            continue
        text = value if isinstance(value, str) else json.dumps(value)
        rows.append(
            _xml_struct(
                {
                    "key": _xml_string(str(key)),
                    "value": _xml_string(text),
                }
            )
        )
    return _xml_array(rows)


def _content_struct(
    *,
    title: str,
    content_html: str,
    slug: str,
    excerpt: str,
    status: str,
    post_type: str,
    featured_media: int | None,
    categories: list[int] | None,
    tags: list[int] | None,
    meta: dict[str, Any] | None = None,
) -> dict[str, str]:
    fields = {
        "post_type": _xml_string(_xmlrpc_type_name(post_type)),
        "post_status": _xml_string(status),
        "post_title": _xml_string(title),
        "post_content": _xml_string(content_html),
        "post_name": _xml_string(slug),
    }
    if excerpt:
        fields["post_excerpt"] = _xml_string(excerpt)
    if featured_media:
        fields["post_thumbnail"] = f"<int>{int(featured_media)}</int>"
    terms: dict[str, str] = {}
    if categories:
        terms["category"] = _xml_array([f"<int>{int(item)}</int>" for item in categories])
    if tags:
        terms["post_tag"] = _xml_array([f"<int>{int(item)}</int>" for item in tags])
    if terms:
        fields["terms"] = _xml_struct(terms)
    if meta:
        fields["custom_fields"] = _xml_custom_fields(meta)
    return fields


async def _xmlrpc_upsert(
    conn: WordPressConnection,
    *,
    title: str,
    content_html: str,
    slug: str,
    excerpt: str,
    status: str | None,
    post_type: str,
    featured_media: int | None,
    categories: list[int] | None,
    tags: list[int] | None,
    meta: dict[str, Any] | None,
    match_any_post_type: bool,
    downgrade: str | None,
    requested_status: str,
) -> dict[str, Any]:
    effective_status, resolved_downgrade = resolve_status(status)
    downgrade = downgrade if downgrade is not None else resolved_downgrade
    if match_any_post_type:
        existing, found_type = None, None
        for candidate in (POST_TYPE_PAGE, POST_TYPE_POST):
            found = await _xmlrpc_find_by_slug(conn, slug, candidate)
            if found:
                existing, found_type = found, candidate
                break
        if existing and found_type and found_type != post_type:
            post_type = found_type
    else:
        existing = await _xmlrpc_find_by_slug(conn, slug, post_type)

    fields = _content_struct(
        title=title,
        content_html=content_html,
        slug=slug,
        excerpt=excerpt,
        status=effective_status,
        post_type=post_type,
        featured_media=featured_media,
        categories=categories,
        tags=tags,
        meta=meta,
    )
    action = "updated" if existing and existing.get("id") else "created"

    async def _write(struct_fields: dict[str, str]) -> dict[str, Any]:
        struct = _xml_struct(struct_fields)
        if existing and existing.get("id"):
            extra = (
                f"<param><value><int>{int(existing['id'])}</int></value></param>"
                f"<param><value>{struct}</value></param>"
            )
            return await _xmlrpc_call(conn, "wp.editPost", extra)
        return await _xmlrpc_call(conn, "wp.newPost", f"<param><value>{struct}</value></param>")

    written = await _write(fields)
    if not written.get("ok"):
        slim = {
            key: fields[key]
            for key in ("post_type", "post_status", "post_title", "post_content", "post_excerpt", "post_name")
            if key in fields
        }
        if set(slim) != set(fields):
            written = await _write(slim)
    if not written.get("ok"):
        failed = _xmlrpc_failure(written, str(written.get("attempted_url") or ""))
        return {
            "ok": False,
            "error": failed.get("error") or written.get("fault_string") or "xmlrpc_write_failed",
            "detail": failed.get("detail"),
            "slug": slug,
            "post_type": post_type,
            "transport": "xmlrpc",
        }

    post_id = existing.get("id") if existing else None
    scalar = str(written.get("scalar") or "")
    if action == "created" and scalar.isdigit():
        post_id = int(scalar)
    stored = await _xmlrpc_fetch(conn, int(post_id), post_type) if post_id else None
    body = stored or {
        "id": post_id,
        "slug": slug,
        "status": effective_status,
        "link": "",
        "meta": {},
    }
    log.info(
        "wordpress_write_ok",
        slug=slug,
        post_type=post_type,
        action=action,
        status=effective_status,
        transport="xmlrpc",
    )
    return {
        "ok": True,
        "action": action,
        "post_id": body.get("id") or post_id,
        "post_type": post_type,
        "slug": body.get("slug") or slug,
        "status": body.get("status") or effective_status,
        "requested_status": requested_status,
        "downgraded": bool(downgrade),
        "downgrade_reason": downgrade,
        "link": body.get("link") or "",
        "edit_link": conn.edit_link(body.get("id") or post_id),
        "stored_meta": {},
        "transport": "xmlrpc",
    }


async def find_object_by_slug(
    conn: WordPressConnection,
    slug: str,
    *,
    post_type: str = POST_TYPE_POST,
) -> dict[str, Any] | None:
    """Look up an existing post/page so re-runs update instead of duplicating."""
    if not slug:
        return None
    if conn.auth_transport == "xmlrpc":
        return await _xmlrpc_find_by_slug(conn, slug, post_type)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        resp = await _request(
            client,
            conn,
            "GET",
            post_type,
            params={"slug": slug, "status": ALL_STATUSES, "per_page": 1, "context": "edit"},
        )
        if _should_fall_back_to_xmlrpc(resp):
            return await _xmlrpc_find_by_slug(conn, slug, post_type)
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
    if conn.auth_transport == "xmlrpc":
        return await _xmlrpc_fetch(conn, object_id, post_type)
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
    if conn.auth_transport == "xmlrpc":
        return await _xmlrpc_upsert(
            conn,
            title=title,
            content_html=content_html,
            slug=slug,
            excerpt=excerpt,
            status=status,
            post_type=post_type,
            featured_media=featured_media,
            categories=categories,
            tags=tags,
            meta=meta,
            match_any_post_type=match_any_post_type,
            downgrade=downgrade,
            requested_status=(status or get_settings().wordpress_default_status or "draft"),
        )
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

        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            if existing and existing.get("id"):
                resp = await _request(
                    client, conn, "POST", f"{post_type}/{existing['id']}", json=payload
                )
                action = "updated"
            else:
                resp = await _request(client, conn, "POST", post_type, json=payload)
                action = "created"

            if _should_fall_back_to_xmlrpc(resp):
                log.info("wordpress_write_rest_fallback", slug=slug, status=resp.status_code)
                return await _xmlrpc_upsert(
                    conn,
                    title=title,
                    content_html=content_html,
                    slug=slug,
                    excerpt=excerpt,
                    status=status,
                    post_type=post_type,
                    featured_media=featured_media,
                    categories=categories,
                    tags=tags,
                    meta=meta,
                    match_any_post_type=match_any_post_type,
                    downgrade=downgrade,
                    requested_status=(status or get_settings().wordpress_default_status or "draft"),
                )

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
