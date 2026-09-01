"""Tests for Search Console Phase 7 adapter."""

from __future__ import annotations

import pytest

from app.integrations.search_console import (
    _resolve_site_url,
    _site_candidates,
    fetch_gsc_technical_snapshot,
)


def test_site_candidates_domain_property():
    candidates = _site_candidates("https://www.example.com/page")
    assert "sc-domain:example.com" in candidates
    assert "https://example.com/" in candidates


def test_resolve_site_url_prefers_exact_match():
    sites = ["sc-domain:example.com", "https://other.com/"]
    assert _resolve_site_url(sites, "https://example.com") == "sc-domain:example.com"


@pytest.mark.asyncio
async def test_fetch_gsc_technical_snapshot_maps_sitemap_errors(monkeypatch):
    async def fake_list_sites(_token: str):
        return ["sc-domain:example.com"]

    async def fake_fetch_sitemaps(_token: str, _site: str):
        return [
            {
                "path": "https://example.com/sitemap.xml",
                "errors": 2,
                "warnings": 1,
                "isPending": False,
            }
        ]

    monkeypatch.setattr(
        "app.integrations.search_console._list_sites",
        fake_list_sites,
    )
    monkeypatch.setattr(
        "app.integrations.search_console._fetch_sitemaps",
        fake_fetch_sitemaps,
    )

    result = await fetch_gsc_technical_snapshot(
        access_token="token",
        primary_url="https://example.com",
    )
    assert result["status"] == "available"
    assert result["property"] == "sc-domain:example.com"
    assert len(result["issues"]) == 2
    assert result["issues"][0]["source"] == "gsc"
