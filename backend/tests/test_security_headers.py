"""AUDIT-019: baseline security headers. The app has no CSP (a wrong one
would silently break the SPA in a way this pass can't visually verify), but
the headers here are safe, no-risk defaults that were simply absent before."""

from __future__ import annotations


async def test_security_headers_present_on_api_response(api_client):
    resp = await api_client.get("/api/v1/auth/roles")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_no_hsts_over_plain_http(api_client):
    """HSTS only makes sense once a connection is already HTTPS — sending it
    over plain http (the test transport, and any un-proxied local run) would
    be a lie about the connection that was actually made."""
    resp = await api_client.get("/api/v1/auth/roles")
    assert "strict-transport-security" not in resp.headers
