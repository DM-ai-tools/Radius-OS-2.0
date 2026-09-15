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
from app.services.publish_preview import (
    build_content_html,
    build_draft_site_preview,
    build_page_preview,
    draft_body_markdown,
    markdown_to_content_html,
)
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

# A written Phase 10 draft for the same URL. Long enough to clear the content-length
# gate and free of placeholder markers, so it represents a publishable article.
DRAFT_BODY = (
    "Local SEO pricing in Australia usually lands between a few hundred and several "
    "thousand dollars a month, and the spread comes down to how much of the work is "
    "done for you. This guide breaks the pricing down by scope so you can tell which "
    "band you actually need before you start calling agencies for quotes.\n\n"
    "## What local SEO costs\n\n"
    "Most agencies price on retainer. A single-location business with a tidy site "
    "usually sits at the lower end, while multi-location businesses pay more because "
    "each location needs its own landing page, citations and review pipeline. Ask what "
    "is included before comparing two numbers that look similar.\n\n"
    "## What affects price\n\n"
    "- Number of locations you need to rank\n"
    "- How competitive your category is locally\n"
    "- Whether content production is included\n\n"
    "See our [pricing page](/pricing) for the current bands."
)
PRODUCTION_WITH_DRAFT = {
    "drafts": [
        {
            "url": "https://example.com/blog/local-seo-pricing",
            "title": "Local SEO Pricing | Acme",
            "meta_description": "What local SEO costs.",
            "keyword": "local seo pricing",
            "markdown": DRAFT_BODY,
            "images": [],
        }
    ],
    "briefs": [],
}
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
        return {
            "ok": True,
            "action": "created",
            "post_id": 1,
            "post_type": kwargs.get("post_type", "posts"),
            "status": "draft",
            "slug": kwargs["slug"],
        }

    async def _fetch(conn, object_id, *, post_type="posts"):
        seen_conns.append(conn)
        return {
            "id": object_id,
            "status": "draft",
            "slug": "local-seo-pricing",
            "title": {"raw": "Local SEO Pricing | Acme"},
            "content": {"raw": "<p>body</p>"},
        }

    async def _find_anywhere(conn, slug):
        seen_conns.append(conn)
        return None, None

    async def _caps(conn):
        seen_conns.append(conn)
        return {"elementor_active": False, "seo_plugin": None}

    monkeypatch.setattr(wordpress, "verify_connection", _verify)
    monkeypatch.setattr(wordpress, "upsert_object", _upsert)
    monkeypatch.setattr(wordpress, "fetch_object", _fetch)
    monkeypatch.setattr(wordpress, "find_object_anywhere", _find_anywhere)
    monkeypatch.setattr(wordpress, "detect_site_capabilities", _caps)

    other_conn = WordPressConnection(
        base_url="https://a-different-client.example", username="x", app_password="y"
    )
    asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="complete",
            on_page_seo=ON_PAGE,
            content_production=PRODUCTION_WITH_DRAFT,
            mode=MODE_DRAFT,
            wordpress_connection=other_conn,
        )
    )
    # The write path itself must have run, not just the connection check.
    assert any(c is other_conn for c in seen_conns)
    assert seen_conns and all(c is other_conn for c in seen_conns)
    assert len(seen_conns) >= 4  # verify + capabilities + slug lookup + write + re-read


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


def test_draft_body_markdown_strips_editorial_sections():
    md = """# Title

## Review queue — resolve before publishing
- [ ] Named reviewer

## Draft metadata
- **URL**: /foo

---

# Title

## Real section
Body copy here.
"""
    body = draft_body_markdown(md)
    assert "Review queue" not in body
    assert "Draft metadata" not in body
    assert "Real section" in body
    assert "Body copy here" in body


def test_markdown_to_content_html_escapes_and_structures():
    html_out = markdown_to_content_html("## Hello\n\nPara **bold**.\n\n- one\n- two")
    assert "<h2>Hello</h2>" in html_out
    assert "<p>Para **bold**.</p>" in html_out
    assert "<ul>" in html_out
    assert "<li>one</li>" in html_out


def test_markdown_to_content_html_renders_figure_placeholders_and_images():
    md = (
        "# Title\n\n"
        "[FIGURE hero] Hero prompt for loans\n\n"
        "## Section\n\n"
        "[FIGURE supporting] Supporting diagram\n"
    )
    html_out = markdown_to_content_html(
        md,
        images=[
            {"role": "hero", "status": "failed", "prompt": "Hero prompt for loans"},
            {
                "role": "supporting",
                "src": "/media/drafts/demo.png",
                "status": "ready",
                "prompt": "Supporting diagram",
            },
        ],
        media_base="http://localhost:5173",
    )
    assert "Image generation failed" in html_out
    assert 'src="http://localhost:5173/media/drafts/demo.png"' in html_out
    assert "draft-figure-placeholder" in html_out


def test_display_meta_strips_stringified_audience_dict():
    from app.services.publish_preview import _display_meta

    ugly = (
        "Website Conversion Optimisation for {'primary': {'gender': 'M & F', "
        "'age_range': '32–50', 'job_titles': ['Founder']}}"
    )
    assert _display_meta(ugly) == "Website Conversion Optimisation"
    assert "Founder" in _display_meta({"primary": {"job_titles": ["Founder"], "age_range": "32-50"}})
    assert "32" in _display_meta({"primary": {"job_titles": ["Founder"], "age_range": "32-50"}})


@pytest.mark.asyncio
async def test_build_draft_site_preview_uses_client_brand(monkeypatch):
    draft = {
        "title": "SEO Services",
        "url": "/seo-services/",
        "meta_description": "Professional SEO help.",
        "markdown": "---\n\n# SEO Services\n\n## Why it matters\n\nWe help you grow.",
    }

    async def fake_brand(_url):
        return BRAND_OK

    async def fake_scrape(_url):
        return {"available": True, "url": "https://acme.example/", "markdown": "# Home\n\n## Services"}

    monkeypatch.setattr("app.integrations.brandfetch.fetch_brand", fake_brand)
    monkeypatch.setattr("app.integrations.firecrawl.scrape_page", fake_scrape)

    out = await build_draft_site_preview(
        client_name="Acme",
        primary_url="https://acme.example",
        draft=draft,
        site_architecture={},
    )
    assert out["preview_html"].startswith("<!doctype html>")
    assert "SITE PREVIEW" in out["preview_html"]
    assert "Why it matters" in out["preview_html"]
    assert out["brand_applied"] is True
