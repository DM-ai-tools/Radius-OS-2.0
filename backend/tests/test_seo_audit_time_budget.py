"""SEO audit should return measured pages before an outer wait_for cancels it."""

from __future__ import annotations

import time
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_run_seo_audit_respects_time_budget_for_partials(monkeypatch):
    from app.integrations import providers

    monkeypatch.setattr(
        providers,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "use_mock_providers": False,
                "effective_seo_audit_max_pages": 20,
            },
        )(),
    )

    urls = [f"https://example.com/p{i}" for i in range(12)]
    urls[0] = "https://example.com/"
    clock = {"now": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])

    async def fake_discover(_url: str, max_pages: int = 40) -> list[str]:
        _ = max_pages
        clock["now"] += 1.0
        return list(urls)

    async def fake_fetch_url(_url: str, timeout: int = 10) -> dict[str, Any]:
        _ = timeout
        clock["now"] += 0.2
        return {
            "status_code": 200,
            "text": "<html><title>Home page title long</title></html>",
            "url": _url,
            "error": None,
        }

    async def costly_fetch_page_html(page_url: str, timeout: int = 15):
        _ = timeout
        clock["now"] += 6.0
        html = (
            f"<html><head><title>Page {page_url[-4:]} long enough title xx</title>"
            f'<meta name="description" content="A meta description that is long enough to pass."/>'
            f'<meta name="viewport" content="width=device-width"/>'
            f'<link rel="canonical" href="{page_url}"/>'
            f"</head><body><h1>Hello</h1></body></html>"
        )
        return html, {"final_url": page_url, "status_code": 200}

    monkeypatch.setattr("app.integrations.web_fetch.discover_site_urls", fake_discover)
    monkeypatch.setattr("app.integrations.web_fetch.fetch_url", fake_fetch_url)
    monkeypatch.setattr("app.integrations.web_fetch.fetch_page_html", costly_fetch_page_html)
    monkeypatch.setattr(
        "app.services.page_clusters.prioritize_urls_for_audit",
        lambda discovered, **_kw: list(discovered),
    )
    monkeypatch.setattr(
        "app.integrations.site_research.research_ready",
        lambda: False,
    )

    # Above the 20s discovery-skip floor; first wave of 5×6s burns the rest.
    result = await providers.run_seo_audit(
        "https://example.com",
        display_name="Example",
        max_pages=12,
        time_budget_seconds=25.0,
    )

    assert result.get("pages_analyzed", 0) >= 1
    assert result.get("pages_analyzed", 0) < 12
    assert result.get("partial_timeout") is True
    assert any(
        "Time budget" in (w.get("issue") or "") for w in (result.get("warnings") or [])
    )


@pytest.mark.asyncio
async def test_run_seo_audit_tight_budget_still_measures_home(monkeypatch):
    """Under a very tight budget, prefer homepage over a cancelled empty report."""
    from app.integrations import providers

    monkeypatch.setattr(
        providers,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "use_mock_providers": False,
                "effective_seo_audit_max_pages": 20,
            },
        )(),
    )

    async def fake_fetch_url(_url: str, timeout: int = 10) -> dict[str, Any]:
        _ = timeout
        return {
            "status_code": 200,
            "text": "<html><title>Home page title long</title></html>",
            "url": _url,
            "error": None,
        }

    async def fast_fetch_page_html(page_url: str, timeout: int = 15):
        _ = timeout
        html = (
            f"<html><head><title>Home page title long enough</title>"
            f'<meta name="description" content="A meta description that is long enough to pass."/>'
            f'<meta name="viewport" content="width=device-width"/>'
            f'<link rel="canonical" href="{page_url}"/>'
            f"</head><body><h1>Hello</h1></body></html>"
        )
        return html, {"final_url": page_url, "status_code": 200}

    monkeypatch.setattr("app.integrations.web_fetch.fetch_url", fake_fetch_url)
    monkeypatch.setattr("app.integrations.web_fetch.fetch_page_html", fast_fetch_page_html)
    monkeypatch.setattr(
        "app.integrations.site_research.research_ready",
        lambda: False,
    )

    result = await providers.run_seo_audit(
        "https://example.com",
        display_name="Example",
        max_pages=12,
        time_budget_seconds=5.0,
    )

    assert result.get("pages_analyzed", 0) >= 1
    assert result.get("partial_timeout") is True
