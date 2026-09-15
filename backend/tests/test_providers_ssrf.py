"""SSRF guard on check_broken_links / optimize_on_page (AUDIT-003).

Both functions used to open a raw httpx.AsyncClient directly against a
client-supplied primary_url (or an anchor discovered on that page) with no
host validation, bypassing the SSRF guard the rest of the codebase already
relies on (web_fetch._is_public_host / assert_safe_url). A user able to set
a client's primary_url to an internal address could trigger an outbound
request to it. These pin that both live-mode paths refuse private/loopback/
metadata hosts before ever opening a connection — for the primary URL itself
and for links discovered on the fetched page (second-order SSRF).

All targets here are IP literals so the test never depends on real DNS.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.integrations.providers import check_broken_links, optimize_on_page


async def test_check_broken_links_blocks_private_primary_url(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    result = await check_broken_links("http://169.254.169.254/")
    assert result["error"] == "blocked_unsafe_host"
    assert result["broken_count"] == 0
    assert result["pages_scanned"] == 0


async def test_optimize_on_page_blocks_private_primary_url(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    result = await optimize_on_page("http://10.0.0.5/", display_name="Acme")
    assert result["error"] == "blocked_unsafe_host"
    assert result["current_score"] == 0
    assert result["optimized_score"] == 0


async def test_check_broken_links_skips_unsafe_anchor_targets(monkeypatch):
    """Even with a safe primary_url, a discovered anchor pointing at an
    internal host must not be HEAD-requested."""
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    visited: list[str] = []

    class _FakeResponse:
        def __init__(self, status_code: int = 200, text: str = ""):
            self.status_code = status_code
            self.text = text

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **kw):
            return _FakeResponse(
                200,
                '<a href="http://169.254.169.254/latest/meta-data/">internal</a>'
                '<a href="/some-page">safe relative link</a>',
            )

        async def head(self, url, *a, **kw):
            visited.append(url)
            return _FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await check_broken_links("https://8.8.8.8/")

    assert not any("169.254.169.254" in v for v in visited)
    assert any("8.8.8.8/some-page" in v for v in visited)
    assert result["total_links_checked"] == 2
