"""Tests for Ahrefs Site Audit integration (mocked — no live API calls)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.technical_seo_ahrefs import fetch_ahrefs_technical_seo
from app.services.technical_seo_normalize import normalize_ahrefs_page
from app.services.technical_seo_rules import (
    issues_from_ahrefs,
    issues_from_pages,
    merge_issues,
)
from app.services.technical_seo_schemas import TechnicalSEOPage

MOCK_PROJECT = {
    "project_id": "12345",
    "project_name": "Example",
    "target_url": "https://example.com",
    "health_score": 82,
    "date": "2026-08-01T12:00:00",
    "total": 500,
    "urls_with_errors": 12,
}

MOCK_ISSUES = [
    {
        "issue_id": "broken_internal",
        "name": "4xx page",
        "category": "Internal pages",
        "importance": "Error",
        "crawled": 5,
    },
    {
        "issue_id": "dup_title",
        "name": "Duplicate title",
        "category": "Duplicates",
        "importance": "Warning",
        "crawled": 10,
    },
]

MOCK_PAGES = [
    {
        "url": "https://example.com/a",
        "http_code": 404,
        "compliant": False,
        "title": ["Page A"],
        "depth": 2,
    },
    {
        "url": "https://example.com/b",
        "http_code": 200,
        "compliant": True,
        "title": ["Page B"],
        "h1": ["Heading"],
        "depth": 5,
        "internal_links": [],
        "content_length": 50,
    },
]


def test_normalize_ahrefs_page_maps_fields():
    page = normalize_ahrefs_page(MOCK_PAGES[1])
    assert page is not None
    assert page.url == "https://example.com/b"
    assert page.status_code == 200
    assert page.indexable is True
    assert page.title == "Page B"
    assert page.h1 == "Heading"
    assert page.depth == 5
    assert page.internal_link_count == 0


def test_issues_from_ahrefs_maps_severity_and_rule_id():
    issues = issues_from_ahrefs(MOCK_ISSUES)
    assert len(issues) == 2
    assert issues[0].rule_id.startswith("AHREFS_")
    assert issues[0].affected_url_count == 5
    assert issues[0].severity in ("Critical", "High", "Medium", "Low")


def test_issues_from_pages_detects_4xx_and_orphans():
    pages = [normalize_ahrefs_page(r) for r in MOCK_PAGES]
    pages = [p for p in pages if p]
    issues = issues_from_pages(pages)
    rule_ids = {i.rule_id for i in issues}
    assert "HTTP_4XX" in rule_ids
    assert "ORPHAN_PAGE" in rule_ids or "DEEP_CLICK_DEPTH" in rule_ids


def test_merge_issues_dedupes_by_title():
    primary = issues_from_ahrefs(MOCK_ISSUES)
    supplemental = issues_from_pages(
        [TechnicalSEOPage(url="https://example.com/x", status_code=500)]
    )
    merged = merge_issues(primary, supplemental)
    assert len(merged) >= len(primary)


@pytest.mark.asyncio
async def test_fetch_ahrefs_issues_only_skips_page_inventory():
    with (
        patch("app.services.technical_seo_ahrefs.ahrefs.resolve_site_audit_project", new_callable=AsyncMock) as resolve,
        patch("app.services.technical_seo_ahrefs.ahrefs.site_audit_issues", new_callable=AsyncMock) as issues,
        patch("app.services.technical_seo_ahrefs.ahrefs.site_audit_page_explorer", new_callable=AsyncMock) as pages,
        patch("app.services.technical_seo_ahrefs.get_settings") as gs,
    ):
        settings = gs.return_value
        settings.ahrefs_api_key = "test-key"
        settings.use_mock_providers = False
        settings.ahrefs_site_audit_project_id = None
        settings.ahrefs_site_audit_page_limit = 0
        settings.ahrefs_site_audit_issue_sample_limit = 0

        resolve.return_value = (MOCK_PROJECT, [])
        issues.return_value = (MOCK_ISSUES, [])
        pages.return_value = ([], [])

        result = await fetch_ahrefs_technical_seo("https://example.com")

    assert result["available"] is True
    assert result["pages_fetched"] == 0
    pages.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_ahrefs_technical_seo_mocked_pipeline():
    with (
        patch("app.services.technical_seo_ahrefs.ahrefs.resolve_site_audit_project", new_callable=AsyncMock) as resolve,
        patch("app.services.technical_seo_ahrefs.ahrefs.site_audit_issues", new_callable=AsyncMock) as issues,
        patch("app.services.technical_seo_ahrefs.ahrefs.site_audit_page_explorer", new_callable=AsyncMock) as pages,
        patch("app.services.technical_seo_ahrefs.get_settings") as gs,
    ):
        settings = gs.return_value
        settings.ahrefs_api_key = "test-key"
        settings.use_mock_providers = False
        settings.ahrefs_site_audit_project_id = None
        settings.ahrefs_site_audit_page_limit = 2000
        settings.ahrefs_site_audit_issue_sample_limit = 1

        resolve.return_value = (MOCK_PROJECT, [])
        issues.return_value = (MOCK_ISSUES, [])
        pages.side_effect = [
            (MOCK_PAGES, []),
            ([{"url": "https://example.com/a"}], []),
        ]

        result = await fetch_ahrefs_technical_seo("https://example.com")

    assert result["available"] is True
    assert result["score"] == 82
    assert len(result["issues"]) >= 2
    assert result["pages_fetched"] == 2
    assert result["api_units_estimate"] >= 50


@pytest.mark.asyncio
async def test_fetch_ahrefs_unavailable_without_key():
    with patch("app.services.technical_seo_ahrefs.get_settings") as gs:
        settings = gs.return_value
        settings.ahrefs_api_key = ""
        settings.use_mock_providers = False
        result = await fetch_ahrefs_technical_seo("https://example.com")
    assert result["available"] is False
