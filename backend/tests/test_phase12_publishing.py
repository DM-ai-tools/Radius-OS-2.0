"""Phase 12 — design fetch, dry-run preview, and guarded WordPress publishing.

The dangerous failure mode here is a write nobody asked for, or a claim of success that
the CMS did not actually store. These pin both.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.publishing import resolve_mode
from app.config import clear_settings_cache, get_settings
from app.integrations import wordpress
from app.integrations.wordpress import WordPressConnection
from app.services.publish_preview import build_content_html, build_page_preview
from app.services.publishing import (
    MODE_DRAFT,
    MODE_PREVIEW,
    MODE_PUBLISH,
    platform_capability_check,
    run_publishing_plan,
)

CONN = WordPressConnection(
    base_url="https://client-site.example", username="editor", app_password="app-pw-1234"
)

PAGE = {
    "url": "https://example.com/blog/local-seo-pricing",
    "keyword": "local seo pricing",
    "title": {"before": "Old", "after": "Local SEO Pricing | Acme", "length_after": 24},
    "meta_description": {"before": "", "after": "What local SEO costs.", "length_after": 21},
    "headings": {"h1": "Local SEO Pricing", "h2s": ["What it costs", "What affects price"]},
    "schema_json_ld": {"@type": "Article", "headline": "Local SEO Pricing"},
}
ON_PAGE = {"pages": [PAGE]}
BRAND_OK = {
    "available": True,
    "name": "Acme",
    "primary_color": "#0f766e",
    "logo": {"url": "https://cdn.example.com/logo.svg"},
    "palette": [{"hex": "#0f766e"}],
    "font": {"name": "Inter"},
}
BRAND_MISSING = {"available": False, "error": "brandfetch_not_configured"}


@pytest.fixture(autouse=True)
def _no_live_credentials(monkeypatch):
    """Every test runs with the integrations unconfigured unless it opts in."""
    for key in (
        "BRANDFETCH_API_KEY",
        "FIRECRAWL_API_KEY",
        "WORDPRESS_BASE_URL",
        "WORDPRESS_USERNAME",
        "WORDPRESS_APP_PASSWORD",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("WORDPRESS_ALLOW_LIVE_PUBLISH", "false")
    monkeypatch.setenv("SECRET_KEY", "test-suite-secret-key-not-a-real-default-value")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-suite-encryption-key-not-a-real-default")
    clear_settings_cache()
    yield
    clear_settings_cache()


# --- mode routing: a write must be asked for -----------------------------------------

@pytest.mark.parametrize(
    "message,expected",
    [
        ("run phase 12", MODE_PREVIEW),
        ("publishing checklist", MODE_PREVIEW),
        ("show me the preview", MODE_PREVIEW),
        ("save to wordpress as draft", MODE_DRAFT),
        ("create draft", MODE_DRAFT),
        ("publish it live", MODE_PUBLISH),
        ("go live", MODE_PUBLISH),
    ],
)
def test_mode_is_only_a_write_when_explicitly_requested(message, expected):
    assert resolve_mode(message) == expected


def test_ambiguous_request_never_writes():
    """A vague instruction must not be read as consent to touch the live site."""
    for vague in ("do phase 12", "finish publishing", "wrap up the pipeline", ""):
        assert resolve_mode(vague) == MODE_PREVIEW


# --- unconfigured / dry run: no writes, no network ------------------------------------

def test_preview_makes_no_cms_write():
    out = asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="complete",
            on_page_seo=ON_PAGE,
            mode=MODE_PREVIEW,
        )
    )
    assert out["mode_effective"] == MODE_PREVIEW
    assert [q["status"] for q in out["publish_queue"]] == ["preview_only"]
    assert out["verification"] == []
    assert "no CMS write" in out["note"]


def test_preview_still_produced_without_brand_or_firecrawl_keys():
    out = asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="complete",
            on_page_seo=ON_PAGE,
            mode=MODE_PREVIEW,
        )
    )
    assert out["design"]["brand_available"] is False
    assert out["previews"][0]["preview_html"].startswith("<!doctype html>")


def test_write_request_downgrades_to_preview_when_no_wordpress_connected():
    """A capability blocker must stop the batch up front, not mid-write."""
    out = asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="complete",
            on_page_seo=ON_PAGE,
            mode=MODE_DRAFT,
            wordpress_connection=None,
        )
    )
    assert out["mode_effective"] == MODE_PREVIEW
    assert out["capability_blockers"][0]["blocker"] == "wordpress_not_connected"
    assert "fell back to preview" in out["note"]


def test_write_request_downgrades_to_preview_when_connected_site_unreachable(monkeypatch):
    """Distinct from 'not connected': a stored connection whose site no longer answers."""

    async def _fails(_conn):
        return {"ok": False, "error": "request_failed: connection refused"}

    monkeypatch.setattr(wordpress, "verify_connection", _fails)
    out = asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="complete",
            on_page_seo=ON_PAGE,
            mode=MODE_DRAFT,
            wordpress_connection=CONN,
        )
    )
    assert out["mode_effective"] == MODE_PREVIEW
    assert out["capability_blockers"][0]["blocker"] == "cms_unreachable"


def test_gate_blocks_without_on_page_package():
    out = asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="not_started",
            on_page_seo={},
            mode=MODE_PREVIEW,
        )
    )
    assert out["blocked"] is True


# --- live-publish guard ----------------------------------------------------------------

def test_live_publish_is_downgraded_unless_deployment_opts_in(monkeypatch):
    status, reason = wordpress.resolve_status("publish")
    assert status == "draft"
    assert "WORDPRESS_ALLOW_LIVE_PUBLISH" in reason


def test_live_publish_allowed_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("WORDPRESS_ALLOW_LIVE_PUBLISH", "true")
    clear_settings_cache()
    status, reason = wordpress.resolve_status("publish")
    assert status == "publish"
    assert reason is None


def test_default_status_is_draft():
    assert get_settings().wordpress_default_status == "draft"
    assert wordpress.resolve_status(None)[0] == "draft"


def test_unknown_status_falls_back_to_draft():
    status, reason = wordpress.resolve_status("nonsense")
    assert status == "draft"
    assert "unknown status" in reason


def test_capability_check_flags_missing_publish_rights():
    blockers = platform_capability_check(
        {"ok": True, "user": "editor", "capabilities_publish": False},
        mode=MODE_PUBLISH,
        has_connection=True,
    )
    assert blockers[0]["blocker"] == "insufficient_cms_rights"
    # ...but a draft write does not need publish rights
    assert (
        platform_capability_check(
            {"ok": True, "user": "editor", "capabilities_publish": False},
            mode=MODE_DRAFT,
            has_connection=True,
        )
        == []
    )


def test_capability_check_distinguishes_not_connected_from_unreachable():
    not_connected = platform_capability_check({}, mode=MODE_DRAFT, has_connection=False)
    assert not_connected[0]["blocker"] == "wordpress_not_connected"

    unreachable = platform_capability_check(
        {"ok": False, "error": "timeout"}, mode=MODE_DRAFT, has_connection=True
    )
    assert unreachable[0]["blocker"] == "cms_unreachable"


# --- per-client connection: storage, not a deployment setting -------------------------

def test_connection_pack_unpack_round_trips():
    packed = wordpress.pack_connection(CONN)
    restored = wordpress.unpack_connection(packed)
    assert restored == CONN
    # Never accidentally stored as plaintext-recognisable JSON keys leaking beyond the
    # blob that encrypt_token wraps — this only checks the round trip, encryption is
    # exercised at the API layer below.
    assert "app_password" in packed


def test_write_path_uses_the_passed_in_connection_not_global_settings(monkeypatch):
    """Two different clients must never be able to write to each other's site."""
    seen_conns: list[WordPressConnection] = []

    async def _verify(conn):
        seen_conns.append(conn)
        return {"ok": True, "user": "editor", "capabilities_publish": True, "site": conn.base_url}

    async def _upsert(conn, **kwargs):
        seen_conns.append(conn)
        return {"ok": True, "action": "created", "post_id": 1, "status": "draft", "slug": kwargs["slug"]}

    async def _fetch(conn, post_id):
        seen_conns.append(conn)
        return {"id": post_id, "status": "draft", "title": {"raw": "Local SEO Pricing | Acme"}}

    monkeypatch.setattr(wordpress, "verify_connection", _verify)
    monkeypatch.setattr(wordpress, "upsert_post", _upsert)
    monkeypatch.setattr(wordpress, "fetch_post", _fetch)

    other_conn = WordPressConnection(
        base_url="https://a-different-client.example", username="x", app_password="y"
    )
    asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="complete",
            on_page_seo=ON_PAGE,
            mode=MODE_DRAFT,
            wordpress_connection=other_conn,
        )
    )
    assert seen_conns and all(c is other_conn for c in seen_conns)


# --- slugs / idempotency ----------------------------------------------------------------

def test_slug_derives_from_url_so_reruns_update_not_duplicate():
    assert wordpress.slug_from_url("https://x.com/blog/local-seo-pricing/") == "local-seo-pricing"
    assert wordpress.slugify("Local SEO Pricing!") == "local-seo-pricing"


# --- preview integrity --------------------------------------------------------------------

def test_preview_payload_is_what_the_publisher_sends():
    """Reviewer and publisher must never diverge — same cms_payload object."""
    preview = build_page_preview(
        client_name="Acme",
        page=PAGE,
        brief=None,
        brand=BRAND_OK,
        layout={"available": False},
        target_status="draft",
        slug="local-seo-pricing",
    )
    payload = preview["cms_payload"]
    assert payload["title"] == "Local SEO Pricing | Acme"
    assert payload["slug"] == "local-seo-pricing"
    assert payload["status"] == "draft"
    assert payload["content"] == build_content_html(PAGE, None)


def test_unwritten_sections_are_flagged_not_passed_off_as_copy():
    html = build_content_html(PAGE, None)
    assert 'data-placeholder="true"' in html
    preview = build_page_preview(
        client_name="Acme", page=PAGE, brief=None, brand=BRAND_OK,
        layout=None, target_status="draft", slug="s",
    )
    assert preview["has_written_copy"] is False


def test_missing_brand_is_disclosed_on_the_preview():
    """A generic-looking preview must not be mistaken for the client's real design."""
    preview = build_page_preview(
        client_name="Acme", page=PAGE, brief=None, brand=BRAND_MISSING,
        layout=None, target_status="draft", slug="s",
    )
    assert preview["brand_applied"] is False
    assert "Brand assets unavailable" in preview["preview_html"]


def test_preview_escapes_page_content():
    hostile = {
        **PAGE,
        "title": {"after": "<script>alert(1)</script>", "before": ""},
        "headings": {"h1": "<img src=x onerror=alert(1)>", "h2s": []},
    }
    preview = build_page_preview(
        client_name="Acme", page=hostile, brief=None, brand=BRAND_OK,
        layout=None, target_status="draft", slug="s",
    )
    html = preview["preview_html"]
    # The payload must survive only as inert text — no executable tag may be formed.
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_preview_marks_itself_as_a_dry_run():
    preview = build_page_preview(
        client_name="Acme", page=PAGE, brief=None, brand=BRAND_OK,
        layout=None, target_status="draft", slug="s",
    )
    assert "DRY-RUN PREVIEW" in preview["preview_html"]
