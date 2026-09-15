"""Ahrefs domain-rating coercion and its failure logging.

Regression cover for two defects in ``pull_backlinks``:

1. The rating was coerced with
   ``A if isinstance(x, dict) else B or C or 0``. A conditional expression
   binds looser than ``or``, so that parses as ``A if isinstance(...) else
   (B or C or 0)`` — the ``or … or 0`` fallback never protected the dict
   branch. A nested dict missing its inner key produced ``float(None)``,
   which raised and was swallowed into ``authority = 0.0``.

2. One ``except Exception`` wrapped both the HTTP call and the parse, so a
   network failure, an Ahrefs 5xx and a coercion bug were indistinguishable
   — all three silently yielded 0.0 with no log line.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import clear_settings_cache
from app.integrations import providers
from app.integrations.providers import coerce_domain_rating, pull_backlinks


# --- coercion, one case per response shape ---------------------------------


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("nested dict with the key", {"domain_rating": {"domain_rating": 72}}, 72.0),
        # The case that was silently failing before.
        ("nested dict without the key", {"domain_rating": {}}, None),
        ("nested dict falls through to metrics", {"domain_rating": {}, "metrics": {"domain_rating": 55}}, 55.0),
        ("bare scalar", {"domain_rating": 41}, 41.0),
        # 0 is a real rating, not "missing" — it must not become None.
        ("scalar zero", {"domain_rating": 0}, 0.0),
        ("under metrics only", {"metrics": {"domain_rating": 33}}, 33.0),
        ("missing entirely", {}, None),
        ("numeric string", {"domain_rating": "67.5"}, 67.5),
        ("unparseable value", {"domain_rating": "n/a"}, None),
        ("bool is not a rating", {"domain_rating": True}, None),
        ("payload is not a dict", "nope", None),
    ],
)
def test_coerce_domain_rating(name, payload, expected):
    assert coerce_domain_rating(payload) == expected, name


def test_missing_and_zero_are_distinguishable():
    """0.0 and None must not collapse — only one of them is a failure."""
    assert coerce_domain_rating({"domain_rating": 0}) == 0.0
    assert coerce_domain_rating({}) is None


# --- failure paths must log ------------------------------------------------


@pytest.fixture
def ahrefs_live(monkeypatch):
    """Live (non-mock) Ahrefs path with a key set."""
    monkeypatch.setenv("USE_MOCK_PROVIDERS", "false")
    monkeypatch.setenv("AHREFS_API_KEY", "test-key-not-real")
    monkeypatch.setenv("MOZ_API_KEY", "")
    monkeypatch.setenv("SECRET_KEY", "test-suite-secret-key-not-a-real-default-value")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-suite-encryption-key-not-a-real-default")
    clear_settings_cache()
    yield
    clear_settings_cache()


def _route(dr_response):
    """Backlink stats always succeed; the domain-rating call is the variable."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "backlinks-stats" in str(request.url):
            return httpx.Response(200, json={"metrics": {"live_refdomains": 120}})
        if callable(dr_response):
            return dr_response(request)
        return dr_response
    return handler


@pytest.fixture
def capture_warnings(monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        providers.log, "warning", lambda event, **kw: seen.append((event, kw))
    )
    return seen


def _mock_client(monkeypatch, handler):
    """Route httpx through a MockTransport.

    `pull_backlinks` does `import httpx` inside its closure, so the name
    resolves to the real module at call time — patching the module attribute
    is what the code under test actually sees.
    """
    real = httpx.AsyncClient

    class _Factory:
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            self._c = real(transport=httpx.MockTransport(handler), **kwargs)

        async def __aenter__(self):
            return await self._c.__aenter__()

        async def __aexit__(self, *args):
            return await self._c.__aexit__(*args)

    monkeypatch.setattr(httpx, "AsyncClient", _Factory)


@pytest.mark.asyncio
async def test_rating_is_used_when_present(ahrefs_live, capture_warnings, monkeypatch):
    _mock_client(monkeypatch, _route(httpx.Response(200, json={"domain_rating": {"domain_rating": 64}})))
    data, provider = await pull_backlinks("acme.example")
    assert provider == "ahrefs"
    assert data["authority_score"] == 64.0
    assert capture_warnings == []


@pytest.mark.asyncio
async def test_dict_without_the_key_logs_instead_of_silently_zeroing(
    ahrefs_live, capture_warnings, monkeypatch
):
    """The regression: this used to raise TypeError and be swallowed to 0.0."""
    _mock_client(monkeypatch, _route(httpx.Response(200, json={"domain_rating": {}})))
    data, _ = await pull_backlinks("acme.example")

    assert data["authority_score"] == 0.0
    events = [e for e, _ in capture_warnings]
    assert "ahrefs_domain_rating_unavailable" in events
    kw = dict(capture_warnings[0][1])
    assert kw["reason"] == "no_rating_in_response"
    assert "domain_rating" in kw["shape"]


@pytest.mark.asyncio
async def test_http_error_logs_its_status(ahrefs_live, capture_warnings, monkeypatch):
    _mock_client(monkeypatch, _route(httpx.Response(503, json={"error": "busy"})))
    data, _ = await pull_backlinks("acme.example")

    assert data["authority_score"] == 0.0
    kw = dict(capture_warnings[0][1])
    assert kw["reason"] == "http_error"
    assert kw["status"] == 503


@pytest.mark.asyncio
async def test_request_failure_logs_the_transport_error(
    ahrefs_live, capture_warnings, monkeypatch
):
    def boom(request):
        if "backlinks-stats" in str(request.url):
            return httpx.Response(200, json={"metrics": {"live_refdomains": 120}})
        raise httpx.ConnectError("no route to host")

    _mock_client(monkeypatch, boom)
    data, _ = await pull_backlinks("acme.example")

    assert data["authority_score"] == 0.0
    kw = dict(capture_warnings[0][1])
    assert kw["reason"] == "request_failed"
    assert kw["error"] == "ConnectError"


@pytest.mark.asyncio
async def test_invalid_json_logs_rather_than_raising(ahrefs_live, capture_warnings, monkeypatch):
    _mock_client(monkeypatch, _route(httpx.Response(200, text="<html>not json</html>")))
    data, _ = await pull_backlinks("acme.example")

    assert data["authority_score"] == 0.0
    assert dict(capture_warnings[0][1])["reason"] == "invalid_json"


@pytest.mark.asyncio
async def test_zero_rating_is_reported_without_a_warning(
    ahrefs_live, capture_warnings, monkeypatch
):
    """A genuine DR of 0 is data, not a failure — it must not log."""
    _mock_client(monkeypatch, _route(httpx.Response(200, json={"domain_rating": 0})))
    data, _ = await pull_backlinks("acme.example")

    assert data["authority_score"] == 0.0
    assert capture_warnings == []


@pytest.mark.asyncio
async def test_authority_zero_is_never_reachable_without_a_log(
    ahrefs_live, capture_warnings, monkeypatch
):
    """The contract from the audit: every 0.0 either is a real rating or is logged."""
    failure_modes = [
        httpx.Response(200, json={"domain_rating": {}}),
        httpx.Response(500, json={}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"domain_rating": "n/a"}),
    ]
    for response in failure_modes:
        capture_warnings.clear()
        _mock_client(monkeypatch, _route(response))
        data, _ = await pull_backlinks("acme.example")
        assert data["authority_score"] == 0.0
        assert capture_warnings, f"0.0 returned with no log for {response!r}"
