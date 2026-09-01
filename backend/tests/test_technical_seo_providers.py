"""Phase 7 technical SEO family — CWV measurement (new) and the live-audit
honesty fix (no fabricated scores for unmeasured categories)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.integrations.providers import (
    find_duplicate_field,
    parse_pagespeed_response,
    run_cwv_measurement,
    run_seo_audit,
    run_technical_seo_audit,
)
from app.services import technical_seo


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.url = text  # unused by CWV path; harmless default

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Drop-in for httpx.AsyncClient — responses queued in order per test."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *args, **kwargs):
        raise NotImplementedError


FULL_PSI_RESPONSE = {
    "loadingExperience": {
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2350, "category": "AVERAGE"},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 180, "category": "FAST"},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 8, "category": "FAST"},
        },
        "overall_category": "AVERAGE",
    },
    "lighthouseResult": {
        "categories": {"performance": {"score": 0.78}},
        "audits": {
            "largest-contentful-paint": {"numericValue": 2600},
            "cumulative-layout-shift": {"numericValue": 0.09},
            "total-blocking-time": {"numericValue": 210},
        },
    },
}

LAB_ONLY_PSI_RESPONSE = {
    "lighthouseResult": {
        "categories": {"performance": {"score": 0.55}},
        "audits": {
            "largest-contentful-paint": {"numericValue": 4100},
            "cumulative-layout-shift": {"numericValue": 0.2},
            "total-blocking-time": {"numericValue": 450},
        },
    }
}


# --- parse_pagespeed_response: pure parser, no network -----------------------------

def test_parse_pagespeed_response_extracts_field_and_lab_separately():
    parsed = parse_pagespeed_response(FULL_PSI_RESPONSE)
    assert parsed["field"]["lcp_ms"] == 2350
    assert parsed["field"]["lcp_category"] == "AVERAGE"
    assert parsed["field"]["inp_ms"] == 180
    assert parsed["field"]["cls"] == 0.08  # 8 / 100
    assert parsed["lab"]["performance_score"] == 78
    assert parsed["passes_core_web_vitals"] is False  # AVERAGE lcp fails "all FAST"


def test_parse_pagespeed_response_all_fast_passes_cwv():
    resp = {
        "loadingExperience": {
            "metrics": {
                "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 1800, "category": "FAST"},
                "INTERACTION_TO_NEXT_PAINT": {"percentile": 120, "category": "FAST"},
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5, "category": "FAST"},
            }
        }
    }
    parsed = parse_pagespeed_response(resp)
    assert parsed["passes_core_web_vitals"] is True


def test_parse_pagespeed_response_falls_back_to_first_input_delay():
    resp = {
        "loadingExperience": {
            "metrics": {
                "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2000, "category": "FAST"},
                "FIRST_INPUT_DELAY_MS": {"percentile": 50, "category": "FAST"},
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 3, "category": "FAST"},
            }
        }
    }
    parsed = parse_pagespeed_response(resp)
    assert parsed["field"]["inp_ms"] == 50


def test_parse_pagespeed_response_no_crux_data_lab_only():
    """No 'loadingExperience' key at all — normal for low-traffic pages. Field
    must be None (never fabricated from lab data), lab still parses."""
    parsed = parse_pagespeed_response(LAB_ONLY_PSI_RESPONSE)
    assert parsed["field"] is None
    assert parsed["lab"]["performance_score"] == 55
    assert parsed["passes_core_web_vitals"] is None


def test_parse_pagespeed_response_empty_dict():
    parsed = parse_pagespeed_response({})
    assert parsed == {"field": None, "lab": None, "passes_core_web_vitals": None}


# --- run_cwv_measurement: mock mode --------------------------------------------

@pytest.mark.asyncio
async def test_run_cwv_measurement_mock_mode():
    result = await run_cwv_measurement("https://acme.example", display_name="Acme")
    assert result["source"] == "mock"
    assert result["field_data_available"] is True
    assert result["field"]["lcp_ms"] == 2350


# --- run_cwv_measurement: live mode, mocked network ----------------------------

@pytest.mark.asyncio
async def test_run_cwv_measurement_live_parses_real_response(monkeypatch):
    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    class Client(_FakeAsyncClient):
        async def get(self, url, *args, **kwargs):
            assert "pagespeedonline" in url
            return _FakeResponse(200, FULL_PSI_RESPONSE)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await run_cwv_measurement("https://acme.example", display_name="Acme")
    assert result["source"] == "pagespeed_insights"
    assert result["field_data_available"] is True
    assert result["field"]["lcp_ms"] == 2350
    assert result["note"] is None


@pytest.mark.asyncio
async def test_run_cwv_measurement_live_no_field_data_says_so_explicitly(monkeypatch):
    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    class Client(_FakeAsyncClient):
        async def get(self, url, *args, **kwargs):
            return _FakeResponse(200, LAB_ONLY_PSI_RESPONSE)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await run_cwv_measurement("https://acme.example", display_name="Acme")
    assert result["field_data_available"] is False
    assert result["field"] is None
    assert result["lab"] is not None
    assert "no crux field data" in (result["note"] or "").lower()


@pytest.mark.asyncio
async def test_run_cwv_measurement_live_network_failure_degrades_gracefully(monkeypatch):
    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    class Client(_FakeAsyncClient):
        async def get(self, url, *args, **kwargs):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await run_cwv_measurement("https://acme.example", display_name="Acme")
    assert result["field_data_available"] is False
    assert result["field"] is None
    assert "unreachable" in result["error"].lower()


@pytest.mark.asyncio
async def test_run_cwv_measurement_live_non_200_degrades_gracefully(monkeypatch):
    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    class Client(_FakeAsyncClient):
        async def get(self, url, *args, **kwargs):
            return _FakeResponse(429, {})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await run_cwv_measurement("https://acme.example", display_name="Acme")
    assert result["field_data_available"] is False
    assert "429" in result["error"]


# --- run_technical_seo_audit live path: no fabricated scores -------------------

HOMEPAGE_HTML_FULL_SIGNAL = """
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://acme.example/">
<script type="application/ld+json">{"@type": "Organization"}</script>
</head><body>Hello</body></html>
"""

HOMEPAGE_HTML_NO_SIGNAL = "<html><head></head><body>Hello</body></html>"


@pytest.mark.asyncio
async def test_live_technical_audit_never_fabricates_scores_when_signal_present(monkeypatch):
    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    class Client(_FakeAsyncClient):
        async def get(self, url, *args, **kwargs):
            if "robots.txt" in url:
                return _FakeResponse(200, text="User-agent: *\nSitemap: https://acme.example/sitemap.xml")
            if "sitemap.xml" in url:
                return _FakeResponse(200, text="")
            return _FakeResponse(200, text=HOMEPAGE_HTML_FULL_SIGNAL)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await run_technical_seo_audit("https://acme.example", display_name="Acme")
    sections = result["sections"]
    # Performance is never measurable from an HTML fetch alone — must stay unscored.
    assert sections["performance"]["score"] is None
    # Mobile/structured-data ARE measurable here (real presence signal) — must
    # reflect what was actually found, not the old flat 50.
    assert sections["mobile"]["score"] == 75
    assert sections["structured_data"]["score"] == 75
    assert any("viewport meta tag present" in f.lower() for f in sections["mobile"]["findings"])
    assert any("json-ld" in f.lower() for f in sections["structured_data"]["findings"])


@pytest.mark.asyncio
async def test_live_technical_audit_no_signal_scores_low_not_fifty(monkeypatch):
    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    class Client(_FakeAsyncClient):
        async def get(self, url, *args, **kwargs):
            if "robots.txt" in url or "sitemap.xml" in url:
                return _FakeResponse(200, text="")
            return _FakeResponse(200, text=HOMEPAGE_HTML_NO_SIGNAL)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await run_technical_seo_audit("https://acme.example", display_name="Acme")
    sections = result["sections"]
    assert sections["mobile"]["score"] == 35
    assert sections["structured_data"]["score"] == 35
    assert sections["performance"]["score"] is None
    # None of the old fabricated flat-50 placeholders should be present anywhere.
    scores = [s.get("score") for s in sections.values()]
    assert 50 not in scores


@pytest.mark.asyncio
async def test_live_technical_audit_homepage_fetch_failure_leaves_mobile_schema_unscored(monkeypatch):
    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    class Client(_FakeAsyncClient):
        async def get(self, url, *args, **kwargs):
            if "robots.txt" in url or "sitemap.xml" in url:
                return _FakeResponse(200, text="")
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await run_technical_seo_audit("https://acme.example", display_name="Acme")
    sections = result["sections"]
    assert sections["mobile"]["score"] is None
    assert sections["structured_data"]["score"] is None
    assert result["score"] is not None  # crawlability/indexation/security still scored


# --- Phase 7 composite wiring: CWV must replace the permanent handoff stub ---------
# when real data comes back, and stay an honest handoff when it doesn't.

@pytest.mark.asyncio
async def test_phase7_composite_uses_real_cwv_when_available():
    """Mock mode always returns CWV field data — the composite must fold it into
    the performance section and stop listing CWV under not_measured."""
    result = await technical_seo.run_technical_seo_plan(
        client_name="Acme", primary_url="https://acme.example"
    )
    assert result["core_web_vitals"]["field_data_available"] is True
    assert result["sections"]["performance"]["score"] is not None
    assert "LCP" in result["sections"]["performance"]["findings"][0]
    not_measured_items = [n["item"] for n in result["not_measured"]]
    assert not any("core web vitals" in i.lower() for i in not_measured_items)
    cwv_routing = next(
        r for r in result["specialist_routing"] if r["route_to"] == "cwv-measurement"
    )
    assert cwv_routing["status"] == "measured_in_this_report"
    assert "CWV (field)" in result["executive_summary"]


@pytest.mark.asyncio
async def test_phase7_composite_falls_back_to_handoff_when_cwv_unavailable(monkeypatch):
    async def _no_cwv(*args, **kwargs):
        return {
            "field_data_available": False,
            "field": None,
            "lab": None,
            "note": "No CrUX field data for this URL.",
        }

    monkeypatch.setattr(technical_seo, "run_cwv_measurement", _no_cwv)
    result = await technical_seo.run_technical_seo_plan(
        client_name="Acme", primary_url="https://acme.example"
    )
    not_measured_items = {n["item"]: n for n in result["not_measured"]}
    cwv_item = next(v for k, v in not_measured_items.items() if "core web vitals" in k.lower())
    assert cwv_item["note"] == "No CrUX field data for this URL."
    cwv_routing = next(
        r for r in result["specialist_routing"] if r["route_to"] == "cwv-measurement"
    )
    assert cwv_routing["status"] == "handoff"
    assert "not measured this pass" in result["executive_summary"].lower()


@pytest.mark.asyncio
async def test_phase7_composite_survives_cwv_provider_exception(monkeypatch):
    """If run_cwv_measurement throws (e.g. PSI down), the composite must not
    crash and must not fabricate a performance score to cover the gap — it
    falls back to whatever the base technical audit reported (honestly None
    in live mode, since that path was fixed not to guess)."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("PSI down")

    async def _live_shaped_tech_audit(*args, **kwargs):
        return {
            "score": 60,
            "sections": {
                "crawlability": {"score": 60, "findings": ["ok"]},
                "performance": {"score": None, "findings": ["Not measured in this pass"]},
            },
            "priority_fixes": [],
        }

    monkeypatch.setattr(technical_seo, "run_cwv_measurement", _boom)
    monkeypatch.setattr(technical_seo, "run_technical_seo_audit", _live_shaped_tech_audit)
    result = await technical_seo.run_technical_seo_plan(
        client_name="Acme", primary_url="https://acme.example"
    )
    assert result["core_web_vitals"] is None
    assert result["sections"]["performance"]["score"] is None


# --- Screaming-Frog-style duplicate title / meta description detection ------------
# seo-audit already audits every crawled page individually (title/meta/H1/alt/
# canonical/viewport/OG/schema) but never compared pages against each other —
# the SKILL.md said titles/descriptions must be "unique" but nothing checked it.

def test_find_duplicate_field_groups_identical_values():
    pages = [
        {"url": "/a", "title": "SEO Services Melbourne"},
        {"url": "/b", "title": "SEO Services Melbourne"},
        {"url": "/c", "title": "Web Design Melbourne"},
    ]
    dups = find_duplicate_field(pages, "title")
    assert len(dups) == 1
    assert dups[0]["count"] == 2
    assert set(dups[0]["urls"]) == {"/a", "/b"}


def test_find_duplicate_field_normalizes_case_and_whitespace():
    pages = [
        {"url": "/a", "title": "SEO   Services"},
        {"url": "/b", "title": "seo services"},
    ]
    dups = find_duplicate_field(pages, "title")
    assert len(dups) == 1
    assert dups[0]["count"] == 2


def test_find_duplicate_field_ignores_empty_and_unique_values():
    pages = [
        {"url": "/a", "title": ""},
        {"url": "/b", "title": None},
        {"url": "/c", "title": "Unique Title"},
    ]
    assert find_duplicate_field(pages, "title") == []


def test_find_duplicate_field_deduplicates_repeated_url_for_same_page():
    pages = [
        {"url": "/a", "title": "Same"},
        {"url": "/a", "title": "Same"},
        {"url": "/b", "title": "Same"},
    ]
    dups = find_duplicate_field(pages, "title")
    assert dups[0]["urls"] == ["/a", "/b"]


@pytest.mark.asyncio
async def test_run_seo_audit_mock_mode_reports_duplicate_keys():
    result = await run_seo_audit("https://acme.example", display_name="Acme")
    assert "duplicate_titles" in result
    assert "duplicate_meta_descriptions" in result
    assert isinstance(result["duplicate_titles"], list)


@pytest.mark.asyncio
async def test_phase7_composite_surfaces_duplicate_titles_in_backlog(monkeypatch):
    async def _seo_with_duplicates(*args, **kwargs):
        return {
            "score": 70,
            "pages_analyzed": 3,
            "critical": [],
            "warnings": [],
            "duplicate_titles": [
                {"value": "SEO Services", "urls": ["/a", "/b"], "count": 2}
            ],
            "duplicate_meta_descriptions": [
                {"value": "Learn about our services.", "urls": ["/a", "/c"], "count": 2}
            ],
        }

    monkeypatch.setattr(technical_seo, "run_seo_audit", _seo_with_duplicates)
    result = await technical_seo.run_technical_seo_plan(
        client_name="Acme", primary_url="https://acme.example"
    )
    assert result["seo_audit"]["duplicate_titles"][0]["count"] == 2
    assert result["seo_audit"]["duplicate_meta_descriptions"][0]["count"] == 2
    backlog_issues = [b["issue"] for b in result["priority_backlog"]]
    assert any("Duplicate title" in i for i in backlog_issues)
    assert any("Duplicate meta description" in i for i in backlog_issues)
