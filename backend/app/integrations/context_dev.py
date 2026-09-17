"""Context.dev — the only module that constructs a client.

SEO agent design capture uses computed styles, fonts, and screenshots.
Page text uses scrape-markdown. Callers fail independently; one empty reader
must not drop the others.

The key is read from CONTEXT_DEV_API_KEY and sent as a Bearer header by the
SDK. Never put it in a query string, a log line, or a frontend bundle.
"""

from __future__ import annotations

from typing import Any

from context.dev import AsyncContextDev
from context.dev import APIStatusError, APITimeoutError, APIConnectionError

from app.config import get_settings
from app.integrations.web_fetch import assert_safe_url
from app.logging_config import get_logger

log = get_logger("context_dev")

STYLEGUIDE_CREDITS = 10
FONTS_CREDITS = 5
SCREENSHOT_CREDITS = 1
MARKDOWN_CREDITS = 1


def configured() -> bool:
    settings = get_settings()
    return bool(settings.context_dev_api_key) and not settings.use_mock_providers


def _safe(url: str) -> tuple[str | None, str | None]:
    raw = (url or "").strip()
    if not raw:
        return None, "missing_url"
    ok, safe = assert_safe_url(raw)
    if not ok:
        return None, safe
    return safe, None


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return {}


async def _call(method: str, **kwargs: Any) -> tuple[Any, str | None]:
    if not configured():
        return None, "context_dev_not_configured"
    settings = get_settings()
    client = AsyncContextDev(
        api_key=settings.context_dev_api_key,
        max_retries=2,
        timeout=45,
    )
    try:
        response = await getattr(client.web, method)(**kwargs)
    except APIStatusError as exc:
        # SDK already honored Retry-After on 429 and backed off 408/5xx.
        # Validation errors (400) are not retried here.
        log.warning("context_dev_http_error", method=method, status=exc.status_code)
        return None, f"context_dev_http_{exc.status_code}"
    except (APITimeoutError, APIConnectionError) as exc:
        log.warning("context_dev_request_failed", method=method, error=type(exc).__name__)
        return None, "context_dev_request_failed"
    finally:
        await client.close()
    return response, None


async def extract_styleguide(url: str) -> tuple[dict[str, Any] | None, str | None]:
    """GET /web/styleguide — computed colors, type, spacing, components. 10 credits."""
    safe, err = _safe(url)
    if err or not safe:
        return None, err
    response, err = await _call("extract_styleguide", direct_url=safe, color_scheme="light")
    if err:
        return None, err
    guide = _as_dict(getattr(response, "styleguide", None))
    if not (guide.get("colors") or guide.get("typography")):
        return None, "context_dev_styleguide_empty"
    return guide, None


async def extract_fonts(url: str) -> tuple[list[dict[str, Any]], str | None]:
    """GET /web/fonts — typefaces ranked by word share. 5 credits."""
    safe, err = _safe(url)
    if err or not safe:
        return [], err
    response, err = await _call("extract_fonts", direct_url=safe)
    if err:
        return [], err
    fonts = getattr(response, "fonts", None) or []
    return [_as_dict(row) for row in fonts if row], None


async def screenshot(
    url: str,
    *,
    full: bool = False,
    width: int = 1440,
    height: int = 900,
) -> tuple[dict[str, Any] | None, str | None]:
    """GET /web/screenshot — hosted PNG URL. 1 credit. Withhold shots taller than 8000px."""
    safe, err = _safe(url)
    if err or not safe:
        return None, err
    response, err = await _call(
        "screenshot",
        direct_url=safe,
        full_screenshot="true" if full else "false",
        viewport={"width": int(width), "height": int(height)},
        handle_cookie_popup=True,
    )
    if err:
        return None, err
    shot = str(getattr(response, "screenshot", "") or "").strip()
    if not shot.startswith("http"):
        return None, "context_dev_screenshot_empty"
    pixels = getattr(response, "height", None)
    try:
        pixels = int(pixels) if pixels is not None else None
    except (TypeError, ValueError):
        pixels = None
    return {
        "url": shot,
        "width": getattr(response, "width", None),
        "height": pixels,
        "screenshot_type": getattr(response, "screenshot_type", None) or ("fullPage" if full else "viewport"),
        "model_safe": pixels is None or pixels <= 8000,
    }, None


async def scrape_markdown(url: str) -> tuple[dict[str, Any] | None, str | None]:
    """GET /web/scrape/markdown — one page as LLM-ready Markdown. 1 credit."""
    safe, err = _safe(url)
    if err or not safe:
        return None, err
    response, err = await _call("web_scrape_md", url=safe, max_age_ms=0)
    if err:
        return None, err
    text = str(getattr(response, "markdown", "") or "")
    if not text.strip():
        return None, "context_dev_markdown_empty"
    return {
        "markdown": text,
        "url": safe,
        "request_id": str(getattr(response, "request_id", "") or ""),
        "content_length": getattr(response, "content_length", None),
    }, None
