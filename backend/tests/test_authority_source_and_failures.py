"""Authority provenance, and telling the truth about why a provider failed.

Two defects this covers:

A) The homepage heuristic wrote the same ``authority_score`` key as Ahrefs/Moz,
   so an estimate built from counting links on one page was indistinguishable
   from a real backlink metric — and was fed into opportunity ranking as one.

B) The fallback's note was hardcoded to "SEMrush/Ahrefs/Moz keys not set", which
   it printed even when the keys WERE set and the call had failed with a 401, a
   429, a 5xx or a timeout. An operator would go hunting a config problem that
   did not exist.

Also asserted here: no credential ever reaches a log line or a response.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.integrations import providers as providers_mod
from app.integrations.provider_status import (
    BAD_RESPONSE,
    NETWORK_ERROR,
    NOT_CONFIGURED,
    OK,
    PROVIDER_ERROR,
    RATE_LIMITED,
    TIMEOUT,
    UNAUTHORIZED,
    AttemptLog,
    classify_exception,
    classify_status,
    scrub,
)

FAKE_KEY = "sk-verysecret-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    """Providers must be in live mode or the chain short-circuits to mock data."""
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "semrush_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    monkeypatch.setattr(settings, "moz_api_key", "")
    return settings


def _stub_live_signals(monkeypatch, *, hrefs: int = 10, json_ld: int = 2):
    """Make the heuristic deterministic without touching the network."""

    class _Parser:
        def __init__(self):
            self.hrefs = [f"https://other{i}.example/" for i in range(hrefs)]
            self.json_ld = [{} for _ in range(json_ld)]

    async def fake_fetch(url, **kw):
        return {"text": "<html></html>", "url": url, "status_code": 200}

    monkeypatch.setattr("app.integrations.web_fetch.fetch_url", fake_fetch)
    monkeypatch.setattr("app.integrations.web_fetch.parse_html", lambda html: _Parser())
    monkeypatch.setattr(
        "app.integrations.web_fetch.absolute_links", lambda base, hrefs: list(hrefs)
    )


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [(200, OK), (401, UNAUTHORIZED), (403, UNAUTHORIZED), (429, RATE_LIMITED),
     (500, PROVIDER_ERROR), (502, PROVIDER_ERROR), (503, PROVIDER_ERROR),
     (400, PROVIDER_ERROR), (404, PROVIDER_ERROR)],
)
def test_http_status_classification(code, expected):
    assert classify_status(code) == expected


@pytest.mark.parametrize(
    "exc,expected",
    [
        (httpx.ReadTimeout("slow"), TIMEOUT),
        (httpx.ConnectTimeout("slow"), TIMEOUT),
        (httpx.ConnectError("no route"), NETWORK_ERROR),
        (ValueError("not json"), BAD_RESPONSE),
        (RuntimeError("Ahrefs HTTP 401: nope"), UNAUTHORIZED),
        (RuntimeError("Moz HTTP 429: slow down"), RATE_LIMITED),
        (RuntimeError("Ahrefs HTTP 503: down"), PROVIDER_ERROR),
        (RuntimeError("Ahrefs API key not configured"), NOT_CONFIGURED),
    ],
)
def test_exception_classification(exc, expected):
    assert classify_exception(exc) == expected


# --- no credential leakage ------------------------------------------------


def test_scrub_removes_bearer_tokens():
    assert FAKE_KEY not in scrub(f"Authorization: Bearer {FAKE_KEY}")
    assert "[redacted]" in scrub(f"Authorization: Bearer {FAKE_KEY}")


def test_scrub_removes_key_value_pairs():
    assert FAKE_KEY not in scrub(f"api_key={FAKE_KEY}")
    assert FAKE_KEY not in scrub(f"token: {FAKE_KEY}")


def test_scrub_removes_long_opaque_strings():
    assert FAKE_KEY not in scrub(f"request failed for {FAKE_KEY}")


def test_attempt_detail_is_scrubbed_on_the_way_in():
    log = AttemptLog()
    attempt = log.record("ahrefs", UNAUTHORIZED, detail=f"Bearer {FAKE_KEY} rejected")
    assert FAKE_KEY not in attempt.detail
    assert FAKE_KEY not in str(attempt.to_dict())


# --- the fallback reason tells the truth ----------------------------------


def test_all_keys_absent_says_keys_are_not_configured():
    log = AttemptLog()
    for name in ("semrush", "ahrefs", "moz"):
        log.record(name, NOT_CONFIGURED)
    reason = log.fallback_reason().lower()
    assert "no backlink provider api key is configured" in reason


@pytest.mark.parametrize(
    "status,must_contain,must_not_contain",
    [
        (UNAUTHORIZED, "unauthorized", "not configured"),
        (RATE_LIMITED, "rate-limited", "not configured"),
        (PROVIDER_ERROR, "server error", "not configured"),
        (TIMEOUT, "did not respond", "not configured"),
        (NETWORK_ERROR, "could not reach", "not configured"),
        (BAD_RESPONSE, "could not be read", "not configured"),
    ],
)
def test_a_configured_key_that_fails_is_never_reported_as_missing(
    status, must_contain, must_not_contain
):
    """The core of defect B."""
    log = AttemptLog()
    log.record("ahrefs", status, detail="whatever")
    reason = log.fallback_reason().lower()
    assert must_contain in reason
    assert must_not_contain not in reason


def test_a_mix_reports_the_real_failure_not_the_missing_keys():
    log = AttemptLog()
    log.record("semrush", NOT_CONFIGURED)
    log.record("ahrefs", RATE_LIMITED)
    reason = log.fallback_reason().lower()
    assert "rate-limited" in reason
    assert "no backlink provider api key is configured" not in reason


# --- provider success -----------------------------------------------------


async def test_provider_success_is_labelled_as_a_provider_metric(monkeypatch, _live_mode):
    monkeypatch.setattr(_live_mode, "ahrefs_api_key", FAKE_KEY)

    async def fake_ahrefs():
        return {
            "referring_domains": 412,
            "authority_score": 58.0,
            "top_anchor_text": {},
            "sample_links": [],
        }

    async def fake_retry(fn, **kw):
        return await fake_ahrefs()

    monkeypatch.setattr(providers_mod, "with_retry", fake_retry)
    data, provider = await providers_mod.pull_backlinks("acme.example")

    assert provider == "ahrefs"
    assert data["source"] == "provider"
    assert data["provider"] == "ahrefs"
    assert data["authority_score"] == 58.0
    assert data.get("estimated_authority_score") is None
    statuses = {a["provider"]: a["status"] for a in data["provider_status"]}
    assert statuses["ahrefs"] == OK
    assert statuses["semrush"] == NOT_CONFIGURED


# --- heuristic fallback ---------------------------------------------------


async def test_heuristic_is_labelled_and_namespaced(monkeypatch, _live_mode):
    _stub_live_signals(monkeypatch)
    data, provider = await providers_mod.pull_backlinks("acme.example")

    assert provider == "live_site_signals"
    assert data["source"] == "heuristic"
    assert data["provider"] is None
    assert data["estimated_authority_score"] is not None
    assert data["heuristic"]["is_estimate"] is True
    assert data["heuristic"]["method"] == "homepage_links_and_structured_data"
    # Back-compat: the old key still carries the same number.
    assert data["authority_score"] == data["estimated_authority_score"]
    assert "ESTIMATE" in data["note"]


async def test_heuristic_note_says_no_keys_when_that_is_true(monkeypatch, _live_mode):
    _stub_live_signals(monkeypatch)
    data, _ = await providers_mod.pull_backlinks("acme.example")
    assert "no backlink provider api key is configured" in data["fallback_reason"].lower()


@pytest.mark.parametrize(
    "exc,expected_status,phrase",
    [
        (RuntimeError("Ahrefs HTTP 401: bad key"), UNAUTHORIZED, "unauthorized"),
        (RuntimeError("Ahrefs HTTP 429: slow"), RATE_LIMITED, "rate-limited"),
        (RuntimeError("Ahrefs HTTP 503: down"), PROVIDER_ERROR, "server error"),
        (httpx.ReadTimeout("slow"), TIMEOUT, "did not respond"),
        (httpx.ConnectError("no route"), NETWORK_ERROR, "could not reach"),
        (ValueError("not json"), BAD_RESPONSE, "could not be read"),
    ],
)
async def test_each_failure_type_reaches_the_response_with_its_real_reason(
    monkeypatch, _live_mode, exc, expected_status, phrase
):
    """Defect B end to end: a configured key that fails must not read as missing."""
    monkeypatch.setattr(_live_mode, "ahrefs_api_key", FAKE_KEY)
    _stub_live_signals(monkeypatch)

    async def boom(fn, **kw):
        raise exc

    monkeypatch.setattr(providers_mod, "with_retry", boom)
    data, provider = await providers_mod.pull_backlinks("acme.example")

    assert provider == "live_site_signals"
    assert data["source"] == "heuristic"
    by_provider = {a["provider"]: a for a in data["provider_status"]}
    assert by_provider["ahrefs"]["status"] == expected_status
    reason = data["fallback_reason"].lower()
    assert phrase in reason
    assert "api key is not configured" not in reason
    # And it says BOTH that the heuristic ran and why the provider didn't.
    assert "ESTIMATE" in data["note"]
    assert phrase in data["note"].lower()


async def test_no_key_value_appears_anywhere_in_the_response(
    monkeypatch, _live_mode
):
    monkeypatch.setattr(_live_mode, "ahrefs_api_key", FAKE_KEY)
    _stub_live_signals(monkeypatch)

    async def boom(fn, **kw):
        raise RuntimeError(f"Ahrefs HTTP 401: Bearer {FAKE_KEY} rejected")

    monkeypatch.setattr(providers_mod, "with_retry", boom)
    data, _ = await providers_mod.pull_backlinks("acme.example")
    assert FAKE_KEY not in str(data)


async def test_no_key_value_appears_in_the_logs(monkeypatch, _live_mode, caplog):
    import logging

    monkeypatch.setattr(_live_mode, "ahrefs_api_key", FAKE_KEY)
    _stub_live_signals(monkeypatch)

    async def boom(fn, **kw):
        raise RuntimeError(f"Ahrefs HTTP 401: Bearer {FAKE_KEY} rejected")

    monkeypatch.setattr(providers_mod, "with_retry", boom)
    with caplog.at_level(logging.DEBUG):
        await providers_mod.pull_backlinks("acme.example")
    assert FAKE_KEY not in caplog.text


async def test_the_first_working_provider_wins_and_later_ones_are_not_tried(
    monkeypatch, _live_mode
):
    monkeypatch.setattr(_live_mode, "semrush_api_key", FAKE_KEY)
    monkeypatch.setattr(_live_mode, "ahrefs_api_key", FAKE_KEY)
    called: list[str] = []

    async def fake_retry(fn, **kw):
        called.append(kw.get("label") or "?")
        return {
            "referring_domains": 1,
            "authority_score": 1.0,
            "top_anchor_text": {},
            "sample_links": [],
        }

    monkeypatch.setattr(providers_mod, "with_retry", fake_retry)
    data, provider = await providers_mod.pull_backlinks("acme.example")
    assert provider == "semrush"
    assert called == ["semrush"], "ahrefs must not be called once semrush succeeded"
    assert data["source"] == "provider"


# --- ranking provenance ---------------------------------------------------


def test_rank_opportunities_records_whether_authority_was_an_estimate():
    from app.services.keyword_opportunity import rank_opportunities

    rows = [{"keyword": "seo services", "volume": 100, "difficulty": 20}]
    estimated = rank_opportunities(
        rows, seeds=["seo"], products=[], client_authority=40.0,
        client_authority_source="heuristic",
    )
    measured = rank_opportunities(
        rows, seeds=["seo"], products=[], client_authority=40.0,
        client_authority_source="provider",
    )
    assert estimated["client_authority_is_estimate"] is True
    assert measured["client_authority_is_estimate"] is False
    assert measured["client_authority_source"] == "provider"
