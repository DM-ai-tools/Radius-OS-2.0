"""Host-variant fallback must never leave the client's registrable domain.

Regression cover for P0-2 (validation/SUMMARY.md). Auditing `example.com` — a
placeholder with no site — the crawler guessed `.com` -> `.com.au`, reached a
real unrelated Australian company that redirects to `weareexample.com`, and
crawled their pages as the client's own. 65 requests, one info-level log line.

The fix scopes variants to the same registrable domain (eTLD+1, via the Public
Suffix List) and makes a cross-domain resolution raise rather than continue.
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations import web_fetch
from app.integrations.web_fetch import (
    CrossRegistrableDomain,
    _url_host_variants,
    assert_same_registrable_domain,
    fetch_url,
    registrable_domain,
    same_registrable_domain,
)

# --- eTLD+1 resolution, via a real public suffix list -----------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("example.com", "example.com"),
        ("www.example.com", "example.com"),
        ("https://www.example.com/a/b?c=1", "example.com"),
        # Multi-label suffixes are where a naive rsplit(".", 2) breaks.
        ("example.com.au", "example.com.au"),
        ("www.example.co.uk", "example.co.uk"),
        ("shop.example.co.uk", "example.co.uk"),
        # Private suffixes: these are different owners, not one github.io.
        ("alice.github.io", "alice.github.io"),
        ("bob.github.io", "bob.github.io"),
        # No PSL opinion — fall back to the host so staging still compares equal.
        ("localhost", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("", ""),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_naive_dot_split_would_get_these_wrong():
    """The reason this uses a PSL rather than string surgery."""
    assert registrable_domain("example.com.au") == "example.com.au"   # not "com.au"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"  # not "b.example.co.uk"


# --- variants: allowed within the registrable domain -----------------------


def test_www_and_apex_are_both_tried():
    variants = _url_host_variants("https://example.com")
    assert "https://example.com" in variants
    assert "https://www.example.com" in variants


def test_www_input_also_yields_apex():
    variants = _url_host_variants("https://www.example.com")
    assert "https://www.example.com" in variants
    assert "https://example.com" in variants


def test_schemeless_input_is_promoted_to_https():
    assert all(v.startswith("https://") for v in _url_host_variants("example.com"))


def test_every_variant_stays_on_the_requested_registrable_domain():
    for url in ("https://example.com", "https://www.example.co.uk",
                "https://shop.example.com.au", "https://alice.github.io"):
        for variant in _url_host_variants(url):
            assert same_registrable_domain(url, variant), f"{url} -> {variant}"


def test_punycode_and_unicode_are_the_same_host():
    """An IDN typed in unicode must not be treated as a different domain."""
    unicode_url = "https://пример.com"   # пример.com
    ascii_url = "https://xn--e1afmkfd.com"
    assert registrable_domain(ascii_url) == "xn--e1afmkfd.com"
    assert same_registrable_domain(unicode_url, ascii_url)
    # And the variant list is emitted in ASCII form.
    assert all("xn--e1afmkfd.com" in v for v in _url_host_variants(unicode_url))


# --- variants: refused across the registrable domain -----------------------


def test_com_to_com_au_is_no_longer_guessed():
    """The exact P0-2 case: example.com must never try example.com.au."""
    variants = _url_host_variants("https://example.com")
    assert not any("example.com.au" in v for v in variants)
    assert not any(registrable_domain(v) != "example.com" for v in variants)


def test_co_uk_to_com_is_no_longer_guessed():
    variants = _url_host_variants("https://example.co.uk")
    assert not any(registrable_domain(v) == "example.com" for v in variants)


@pytest.mark.parametrize(
    ("requested", "resolved"),
    [
        ("https://example.com", "https://example.com.au"),      # different TLD
        ("https://example.co", "https://example.com"),           # .co -> .com
        ("https://example.com", "https://exampleshop.com"),      # different SLD
        ("https://example.com", "https://www.weareexample.com"), # the live incident
        ("https://alice.github.io", "https://bob.github.io"),    # private suffix
    ],
)
def test_cross_domain_resolution_raises(requested, resolved):
    assert not same_registrable_domain(requested, resolved)
    with pytest.raises(CrossRegistrableDomain) as exc:
        assert_same_registrable_domain(requested, resolved, context="client site resolution")
    # The message must name both domains — this is what an operator acts on.
    assert registrable_domain(resolved) in str(exc.value)
    assert registrable_domain(requested) in str(exc.value)


def test_same_domain_resolution_does_not_raise():
    assert_same_registrable_domain("https://example.com", "https://www.example.com/")
    assert_same_registrable_domain("http://example.com", "https://example.com")


# --- fetch_url: the redirect path ------------------------------------------


def _mock_transport(monkeypatch, handler):
    real = httpx.AsyncClient

    class _Factory:
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            self._c = real(transport=httpx.MockTransport(handler), **kwargs)

        async def __aenter__(self):
            return await self._c.__aenter__()

        async def __aexit__(self, *args):
            return await self._c.__aexit__(*args)

    monkeypatch.setattr(web_fetch.httpx, "AsyncClient", _Factory)
    monkeypatch.setattr(web_fetch, "_is_public_host", lambda h: True)


def _redirect_to(final: str):
    """301 everything that is not `final`, then serve a real page at `final`."""
    def handler(request):
        if str(request.url).rstrip("/") == final.rstrip("/"):
            return httpx.Response(200, text=PAGE)
        return httpx.Response(301, headers={"Location": final})
    return handler


PAGE = "<html><head><title>A real page</title></head><body><a href='/x'>x</a>" + ("content " * 80) + "</body></html>"


@pytest.mark.asyncio
async def test_client_resolution_refuses_a_cross_domain_redirect(monkeypatch):
    """The live incident, end to end: a redirect onto a stranger's site."""
    _mock_transport(monkeypatch, _redirect_to("https://www.weareexample.com/"))
    with pytest.raises(CrossRegistrableDomain):
        await fetch_url("https://example.com", enforce_registrable_domain=True)


@pytest.mark.asyncio
async def test_competitor_path_still_follows_cross_domain_redirects(monkeypatch):
    """Competitor crawling legitimately lands on other companies' sites."""
    _mock_transport(monkeypatch, _redirect_to("https://www.weareexample.com/"))
    result = await fetch_url("https://example.com")  # enforcement off by default
    assert result.get("status_code") == 200
    assert "weareexample.com" in str(result.get("url"))


@pytest.mark.asyncio
async def test_same_domain_redirect_is_allowed_under_enforcement(monkeypatch):
    _mock_transport(monkeypatch, _redirect_to("https://www.example.com/"))
    result = await fetch_url("https://example.com", enforce_registrable_domain=True)
    assert result.get("status_code") == 200


@pytest.mark.asyncio
async def test_cross_domain_is_not_downgraded_to_a_failed_fetch(monkeypatch):
    """It must raise, not return an error dict the caller can ignore."""
    _mock_transport(monkeypatch, _redirect_to("https://other-company.com/"))
    with pytest.raises(CrossRegistrableDomain):
        await fetch_url("https://example.com", enforce_registrable_domain=True)
