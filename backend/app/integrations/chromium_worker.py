"""Child process: Playwright Chromium on a Proactor event loop.

Uvicorn on Windows installs SelectorEventLoopPolicy on the API process. Playwright's
driver is a subprocess, so any Playwright call that shares that policy raises
``NotImplementedError``. This module is started with ``python -m`` so it gets a
fresh process and an explicit Proactor loop.

Protocol (JSON lines on stdin/stdout; credentials stay off argv):
  child -> {"ready": true}
  parent -> {"op": "post", "url", "headers", "body", "origin"?}
  child -> {"ok": true, "status": int, "text": str}
  parent -> {"op": "close"}
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from html import unescape
from urllib.parse import urljoin


def playwright_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsProactorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


def install_playwright_loop_policy() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(playwright_loop_policy())


async def _launch_browser(pw):
    """Visible installed Chrome, not Playwright's automation banner.

    This host serves a security check to automated browsers. A normal Chrome
    window is what loads the public site.
    """
    last: Exception | None = None
    attempts = (
        {
            "channel": "chrome",
            "headless": False,
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
        },
        {"channel": "chrome", "headless": False},
        {"headless": True},
    )
    for kwargs in attempts:
        try:
            return await pw.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"Chromium could not start: {last}")


_IN_PAGE_XMLRPC = """
async ({ body, contentType }) => {
  const url = new URL('/xmlrpc.php', location.origin).href;
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': contentType || 'text/xml',
      'Accept': 'text/xml, application/xml',
    },
    body,
    credentials: 'include',
  });
  return { status: resp.status, text: await resp.text(), url };
}
"""


def _looks_like_challenge(html: str) -> bool:
    lowered = (html or "").lower()
    if len(lowered) > 1200 and "http-equiv" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            'http-equiv="refresh"',
            "http-equiv='refresh'",
            "cf-browser-verification",
            "checking your browser",
            "sgcaptcha",
            "captcha-bypass",
        )
    )


def _meta_refresh_target(html: str, base: str) -> str | None:
    """SiteGround's bot interstitial is a meta refresh, not a WordPress response."""
    patterns = (
        r"""http-equiv=["']refresh["'][^>]*content=["']\s*\d+\s*;\s*(?:url=)?([^"'>\s]+)""",
        r"""content=["']\s*\d+\s*;\s*(?:url=)?([^"'>\s]+)["'][^>]*http-equiv=["']refresh["']""",
    )
    for pattern in patterns:
        match = re.search(pattern, html or "", re.I)
        if not match:
            continue
        target = unescape(match.group(1).strip())
        if not target or target.startswith("data:"):
            continue
        return urljoin(base or "", target)
    return None


async def _stable_html(page) -> str:
    last = ""
    for _ in range(8):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
            return await page.content()
        except Exception:  # noqa: BLE001
            await page.wait_for_timeout(400)
    return last


def _page_blocked(page_url: str, html: str) -> bool:
    url = (page_url or "").lower()
    if any(part in url for part in ("/captcha", "sgcaptcha", "challenge-platform")):
        return True
    return _looks_like_challenge(html)


async def load_origin(page, origin: str) -> None:
    await page.goto(origin, wait_until="domcontentloaded", timeout=25_000)
    seen: set[str] = set()
    for _ in range(20):
        html = await _stable_html(page)
        if html and not _page_blocked(page.url, html):
            return
        nxt = _meta_refresh_target(html, page.url or origin)
        if nxt and nxt not in seen:
            seen.add(nxt)
            try:
                await page.goto(nxt, wait_until="domcontentloaded", timeout=25_000)
            except Exception:  # noqa: BLE001
                await page.wait_for_timeout(700)
            continue
        await page.wait_for_timeout(1000)
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:  # noqa: BLE001
        pass


async def xmlrpc_from_page(
    page,
    *,
    origin: str | None,
    body: str,
    content_type: str,
) -> dict:
    if origin:
        try:
            await load_origin(page, origin)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"origin_failed: {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()
    result = await page.evaluate(
        _IN_PAGE_XMLRPC,
        {"body": body or "", "contentType": content_type or "text/xml"},
    )
    if not isinstance(result, dict):
        raise RuntimeError("in-page XML-RPC returned a non-object")
    return result


def _json_from_page(text: str) -> str:
    stripped = (text or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("[") or stripped.startswith("<?xml"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start and '"id"' in stripped[start:end + 1]:
        return stripped[start : end + 1]
    return text or ""


async def rest_from_page(page, *, origin: str | None, url: str, headers: dict) -> dict:
    """Load the public site in Chrome, then open the REST URL as a real navigation.

    Playwright's separate request client is reset on this host. A page navigation
    follows the SiteGround check and keeps the cookie for the login call.
    """
    if origin:
        try:
            await load_origin(page, origin)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"origin_failed: {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()

    async def _route(route):
        merged = dict(route.request.headers)
        merged.update({k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"})
        await route.continue_(headers=merged)

    await page.route("**/*", _route)
    try:
        last_status = 0
        last_text = ""
        target = url
        for _ in range(4):
            resp = await page.goto(target, wait_until="domcontentloaded", timeout=25_000)
            last_status = int(resp.status) if resp else 0
            try:
                last_text = await resp.text() if resp else ""
            except Exception:  # noqa: BLE001
                last_text = await _stable_html(page)
            last_text = _json_from_page(last_text)
            if last_text.lstrip().startswith("{") or last_text.lstrip().startswith("["):
                return {"status": last_status, "text": last_text}
            nxt = _meta_refresh_target(last_text, page.url or target)
            if not nxt:
                html = await _stable_html(page)
                nxt = _meta_refresh_target(html, page.url or target)
                if html.lstrip().startswith("{") or '"code"' in html:
                    last_text = _json_from_page(html)
                    return {"status": last_status, "text": last_text}
            if nxt and nxt != target:
                target = nxt
                continue
            await page.wait_for_timeout(1000)
        return {"status": last_status, "text": last_text}
    finally:
        await page.unroute("**/*", _route)


async def _handle(page, origin_loaded: list[str | None], msg: dict) -> dict:
    origin = (msg.get("origin") or "").strip() or None
    should_load = origin if origin and origin_loaded[0] != origin else None
    if origin:
        origin_loaded[0] = origin
    headers = msg.get("headers") or {}
    if msg.get("op") == "rest":
        result = await rest_from_page(page, origin=should_load, url=msg["url"], headers=headers)
        return {"ok": True, "status": result["status"], "text": result["text"]}
    result = await xmlrpc_from_page(
        page,
        origin=should_load,
        body=msg.get("body") or "",
        content_type=str(headers.get("Content-Type") or "text/xml"),
    )
    return {
        "ok": True,
        "status": int(result.get("status") or 0),
        "text": str(result.get("text") or ""),
    }


async def _run() -> None:
    from playwright.async_api import async_playwright

    loop = asyncio.get_running_loop()
    incoming: asyncio.Queue[str | None] = asyncio.Queue()

    def _read_stdin() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(incoming.put_nowait, line)
        loop.call_soon_threadsafe(incoming.put_nowait, None)

    threading.Thread(target=_read_stdin, name="chromium-stdin", daemon=True).start()

    user_agent = os.environ.get("WP_CHROMIUM_UA") or None
    origin_loaded: list[str | None] = [None]
    async with async_playwright() as pw:
        browser = await _launch_browser(pw)
        try:
            page_kwargs = {}
            if user_agent:
                page_kwargs["user_agent"] = user_agent
            page = await browser.new_page(**page_kwargs)
            sys.stdout.write(json.dumps({"ready": True}) + "\n")
            sys.stdout.flush()
            while True:
                line = await incoming.get()
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("op") == "close":
                    break
                try:
                    result = await _handle(page, origin_loaded, msg)
                except Exception as exc:  # noqa: BLE001
                    result = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}".strip() or type(exc).__name__,
                    }
                sys.stdout.write(json.dumps(result) + "\n")
                sys.stdout.flush()
        finally:
            await browser.close()


def main() -> None:
    install_playwright_loop_policy()
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}".strip() or type(exc).__name__,
                }
            )
            + "\n"
        )
        sys.stdout.flush()
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
