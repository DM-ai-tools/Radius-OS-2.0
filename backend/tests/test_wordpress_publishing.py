"""WordPress publishing — content rendering, Elementor, strategy, verification.

External WordPress calls are served by an httpx MockTransport, so nothing here
touches a real site.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.integrations import wordpress
from app.integrations.wordpress import WordPressConnection
from app.services import wp_elementor
from app.services.wp_content import (
    markdown_to_publish_html,
    safe_href,
    strip_unresolved_images,
    validate_publish_payload,
)
from app.services.wp_publish_strategy import (
    STRATEGY_BLOCKED,
    STRATEGY_ELEMENTOR,
    STRATEGY_STANDARD,
    PublishTarget,
    build_seo_meta,
    publish_to_wordpress,
    resolve_strategy,
)

CONN = WordPressConnection(
    base_url="https://example.com", username="editor", app_password="app-pw-1234"
)

ARTICLE = (
    "Choosing a payroll platform comes down to how much of the compliance burden you "
    "want to carry yourself. This guide walks through the trade-offs so you can pick "
    "the option that matches your team size and your appetite for administration, "
    "rather than the one with the loudest marketing budget behind it.\n\n"
    "## What it costs\n\n"
    "Pricing is usually per employee per month, with a platform fee on top. Read the "
    "fee schedule carefully because the headline number rarely includes year-end "
    "filing, and that is where most of the surprise cost sits for smaller teams.\n\n"
    "## What to check first\n\n"
    "1. Award interpretation for your industry\n"
    "2. Single Touch Payroll reporting\n"
    "3. How leave accruals are handled\n\n"
    "See the [pricing guide](/pricing) before you commit."
)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from app.config import clear_settings_cache

    monkeypatch.setenv("SECRET_KEY", "test-suite-secret-key-not-a-real-default-value")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-suite-encryption-key-not-a-real-default")
    monkeypatch.setenv("WORDPRESS_ALLOW_LIVE_PUBLISH", "false")
    clear_settings_cache()
    yield
    clear_settings_cache()


def mock_wp(monkeypatch, handler):
    """Route every httpx.AsyncClient in the wordpress module through a mock transport."""
    real = httpx.AsyncClient

    class _Factory:
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            self._client = real(transport=httpx.MockTransport(handler), **kwargs)

        async def __aenter__(self):
            return await self._client.__aenter__()

        async def __aexit__(self, *args):
            return await self._client.__aexit__(*args)

    monkeypatch.setattr(wordpress.httpx, "AsyncClient", _Factory)


# --- content rendering ------------------------------------------------------


def test_inline_markdown_becomes_real_html():
    """Links and emphasis must survive — a link rendered as literal text is not a link."""
    out = markdown_to_publish_html(ARTICLE)
    assert '<a href="/pricing"' in out
    assert ">pricing guide</a>" in out
    assert "<h2>What it costs</h2>" in out
    assert "<ol>" in out and "<li>Award interpretation for your industry</li>" in out


def test_tables_blockquotes_and_emphasis_render():
    md = (
        "## Compare\n\n"
        "| Plan | Price |\n| --- | --- |\n| Basic | $10 |\n| Pro | $30 |\n\n"
        "> Pricing changed in March.\n\n"
        "Some **bold** and *italic* and `code`.\n"
    )
    out = markdown_to_publish_html(md)
    assert "<table>" in out and "<th>Plan</th>" in out and "<td>$30</td>" in out
    assert "<blockquote><p>Pricing changed in March.</p></blockquote>" in out
    assert "<strong>bold</strong>" in out and "<em>italic</em>" in out and "<code>code</code>" in out


def test_html_in_source_text_is_escaped_not_executed():
    out = markdown_to_publish_html("Watch out for <script>alert(1)</script> in copy.\n")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com/a", "https://example.com/a"),
        ("/internal/page", "/internal/page"),
        ("mailto:a@b.com", "mailto:a@b.com"),
        ("javascript:alert(1)", None),
        ("vbscript:x", None),
        ("", None),
    ],
)
def test_safe_href(raw, expected):
    assert safe_href(raw) == expected


def test_unsafe_link_keeps_the_words_but_drops_the_link():
    out = markdown_to_publish_html("Click [here](javascript:alert(1)) now.\n")
    assert "javascript:" not in out
    assert "here" in out


def test_local_draft_images_survive_rendering_so_they_can_be_uploaded():
    """They are stripped later only if the upload fails — see strip_unresolved_images."""
    out = markdown_to_publish_html(
        "# T\n\n[FIGURE hero] A diagram\n",
        images=[{"role": "hero", "src": "/media/drafts/local.png"}],
        media_base=None,
    )
    assert 'src="/media/drafts/local.png"' in out

    stripped, removed = strip_unresolved_images(out)
    assert removed == 1
    assert "<img" not in stripped

    rewritten = out.replace("/media/drafts/local.png", "https://cdn.example.com/a.png")
    kept, removed2 = strip_unresolved_images(rewritten)
    assert removed2 == 0
    assert 'src="https://cdn.example.com/a.png"' in kept


def test_unresolvable_image_is_omitted_not_published_broken():
    out = markdown_to_publish_html(
        "# T\n\n[FIGURE hero] A diagram\n",
        images=[{"role": "hero", "src": "data:image/png;base64,AAAA"}],
        media_base=None,
    )
    assert "<img" not in out

    resolved = markdown_to_publish_html(
        "# T\n\n[FIGURE hero] A diagram\n",
        images=[{"role": "hero", "src": "/media/local.png", "alt": "Diagram"}],
        media_base="https://cdn.example.com",
    )
    assert 'src="https://cdn.example.com/media/local.png"' in resolved
    assert 'alt="Diagram"' in resolved


# --- validation -------------------------------------------------------------


def _payload(**over):
    base = {
        "title": "Payroll Software Pricing",
        "slug": "payroll-software-pricing",
        "content": markdown_to_publish_html(ARTICLE),
        "excerpt": "What payroll software costs in Australia.",
    }
    base.update(over)
    return base


def test_valid_payload_passes():
    assert validate_publish_payload(_payload())["ok"] is True


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("title", "", "title is empty"),
        ("content", "", "content is empty"),
        ("slug", "", "slug is empty"),
        ("slug", "Not A Slug", "not a valid WordPress slug"),
        ("content", "<p>too short</p>", "below the 400 minimum"),
    ],
)
def test_validation_blocks_bad_payloads(field, value, fragment):
    result = validate_publish_payload(_payload(**{field: value}))
    assert result["ok"] is False
    assert any(fragment in e for e in result["errors"]), result["errors"]


def test_placeholder_content_is_blocked():
    skeleton = (
        "<h1>Payroll</h1>"
        + '<p data-placeholder="true">Section outlined in the approved brief — '
        "copy to be written before go-live.</p>" * 6
    )
    result = validate_publish_payload(_payload(content=skeleton))
    assert result["ok"] is False
    assert any("placeholder" in e for e in result["errors"])


def test_pipeline_variable_leak_is_blocked():
    leaky = "<p>" + ("Hello {{client_name}} welcome to the guide. " * 20) + "</p>"
    result = validate_publish_payload(_payload(content=leaky))
    assert result["ok"] is False


def test_malformed_markup_is_detected():
    broken = "<p>" + ("Real sentence here for length. " * 20) + "<div><span></p>"
    result = validate_publish_payload(_payload(content=broken))
    assert result["ok"] is False
    assert any("unclosed" in e or "mismatched" in e for e in result["errors"])


def test_heading_and_image_problems_warn_but_do_not_block():
    content = (
        "<h1>One</h1><h1>Two</h1>"
        + "<p>" + ("Body sentence for length. " * 25) + "</p>"
        + '<img src="https://x.com/a.png" alt="">'
    )
    result = validate_publish_payload(_payload(content=content))
    assert result["ok"] is True
    assert any("H1" in w for w in result["warnings"])
    assert any("alt" in w for w in result["warnings"])


# --- Elementor data model ---------------------------------------------------


def _elementor_tree():
    """A page a human built: a hero section and a two-widget content section."""
    return [
        {
            "id": "hero001",
            "elType": "section",
            "settings": {"background_color": "#123456"},
            "elements": [
                {
                    "id": "heroc01",
                    "elType": "column",
                    "settings": {"_column_size": 100},
                    "elements": [
                        {
                            "id": "herow01",
                            "elType": "widget",
                            "widgetType": "heading",
                            "settings": {"title": "Payroll", "header_size": "h1"},
                            "elements": [],
                        }
                    ],
                }
            ],
        },
        {
            "id": "body001",
            "elType": "section",
            "settings": {},
            "elements": [
                {
                    "id": "bodyc01",
                    "elType": "column",
                    "settings": {"_column_size": 50},
                    "elements": [
                        {
                            "id": "bodyw01",
                            "elType": "widget",
                            "widgetType": "image",
                            "settings": {"image": {"url": "https://x.com/a.png"}},
                            "elements": [],
                        }
                    ],
                }
            ],
        },
    ]


def test_detects_elementor_from_meta_and_from_markup():
    assert wp_elementor.is_elementor_post({"meta": {"_elementor_edit_mode": "builder"}}) is True
    assert wp_elementor.is_elementor_post(
        {"meta": [{"key": "_elementor_edit_mode", "value": "builder"}]}
    ) is True
    assert wp_elementor.is_elementor_post(
        {"meta": {}, "content": {"rendered": '<div class="elementor elementor-42">x</div>'}}
    ) is True
    assert wp_elementor.is_elementor_post({"meta": {}, "content": {"rendered": "<p>plain</p>"}}) is False


def test_parse_is_lenient_and_never_raises():
    assert wp_elementor.parse_elementor_data(None) == []
    assert wp_elementor.parse_elementor_data("not json{{{") == []
    assert wp_elementor.parse_elementor_data('{"a":1}') == []
    tree = _elementor_tree()
    assert wp_elementor.parse_elementor_data(json.dumps(tree)) == tree


def test_merge_preserves_every_existing_widget():
    """The whole point: updating content must not destroy the client's layout."""
    original = _elementor_tree()
    merged, action = wp_elementor.merge_managed_content(
        original, "<p>New copy</p>", slot_key="pages:payroll"
    )
    assert action == "appended_section"

    # Original object untouched — a caller that aborts loses nothing.
    assert original == _elementor_tree()

    ids_before = {e["id"] for e in wp_elementor.iter_elements(original)}
    ids_after = {e["id"] for e in wp_elementor.iter_elements(merged)}
    assert ids_before.issubset(ids_after)

    hero = wp_elementor.find_element(merged, "hero001")
    assert hero["settings"]["background_color"] == "#123456"
    image = wp_elementor.find_element(merged, "bodyw01")
    assert image["settings"]["image"]["url"] == "https://x.com/a.png"
    assert wp_elementor.extract_managed_html(merged, slot_key="pages:payroll") == "<p>New copy</p>"


def test_second_publish_updates_in_place_instead_of_appending_again():
    tree = _elementor_tree()
    first, _ = wp_elementor.merge_managed_content(tree, "<p>v1</p>", slot_key="pages:payroll")
    second, action = wp_elementor.merge_managed_content(
        first, "<p>v2</p>", slot_key="pages:payroll"
    )
    assert action == "updated_in_place"
    assert len(second) == len(first)
    assert wp_elementor.count_widgets(second) == wp_elementor.count_widgets(first)
    assert wp_elementor.extract_managed_html(second, slot_key="pages:payroll") == "<p>v2</p>"


def test_merge_respects_a_container_based_document():
    containers = [{"id": "cont001", "elType": "container", "settings": {}, "elements": []}]
    merged, _ = wp_elementor.merge_managed_content(containers, "<p>x</p>", slot_key="k")
    assert merged[-1]["elType"] == "container"


def test_human_styling_on_the_managed_widget_survives_a_rerun():
    tree = _elementor_tree()
    first, _ = wp_elementor.merge_managed_content(tree, "<p>v1</p>", slot_key="k")
    widget = wp_elementor.find_element(first, wp_elementor.managed_id("k"))
    widget["settings"]["text_color"] = "#ff0000"  # a human tweaked it in the builder

    second, _ = wp_elementor.merge_managed_content(first, "<p>v2</p>", slot_key="k")
    updated = wp_elementor.find_element(second, wp_elementor.managed_id("k"))
    assert updated["settings"]["text_color"] == "#ff0000"
    assert updated["settings"]["editor"] == "<p>v2</p>"


def test_build_document_is_structurally_valid():
    doc = wp_elementor.build_document("<p>Body</p>", slot_key="new-page")
    assert wp_elementor.validate_document(doc) == []
    assert wp_elementor.count_widgets(doc) == 1
    assert wp_elementor.extract_managed_html(doc, slot_key="new-page") == "<p>Body</p>"


def test_validate_document_catches_broken_structures():
    assert wp_elementor.validate_document([]) == ["elementor data is empty"]
    assert wp_elementor.validate_document("nope") == ["elementor data is not a list of elements"]
    bad = [{"id": "a", "elType": "widget", "settings": {}, "elements": []}]
    assert any("widgetType" in i for i in wp_elementor.validate_document(bad))
    dupes = [
        {"id": "same", "elType": "section", "settings": {}, "elements": []},
        {"id": "same", "elType": "section", "settings": {}, "elements": []},
    ]
    assert any("duplicate" in i for i in wp_elementor.validate_document(dupes))


def test_meta_payload_never_writes_the_generated_css_cache():
    payload = wp_elementor.build_meta_payload(wp_elementor.build_document("<p>x</p>", slot_key="k"))
    assert wp_elementor.CSS_KEY not in payload
    assert payload[wp_elementor.EDIT_MODE_KEY] == "builder"
    assert json.loads(payload[wp_elementor.DATA_KEY])


# --- post type inference ----------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/blog/post-name", "posts"),
        ("https://x.com/news/item", "posts"),
        ("https://x.com/services/payroll", "pages"),
        ("https://x.com/about", "pages"),
        ("https://x.com/locations/sydney", "pages"),
    ],
)
def test_post_type_inference(url, expected):
    assert wordpress.infer_post_type(url) == expected


def test_explicit_page_type_overrides_the_url_guess():
    assert wordpress.infer_post_type("https://x.com/blog/x", page_type="service") == "pages"


# --- strategy resolution ----------------------------------------------------


def _target(**over):
    base = dict(
        url="https://example.com/services/payroll",
        slug="payroll",
        title="Payroll Services",
        content_html=markdown_to_publish_html(ARTICLE),
        excerpt="Payroll services.",
    )
    base.update(over)
    return PublishTarget(**base)


def _json(data, status=200):
    return httpx.Response(status, json=data)


def test_strategy_is_standard_when_nothing_exists(monkeypatch):
    mock_wp(monkeypatch, lambda request: _json([]))
    decision = asyncio.run(resolve_strategy(CONN, _target()))
    assert decision.strategy == STRATEGY_STANDARD
    assert decision.post_type == "pages"
    assert decision.existing is None


def test_strategy_is_standard_for_a_plain_existing_page(monkeypatch):
    def handler(request):
        if "/pages" in str(request.url):
            return _json([{"id": 7, "slug": "payroll", "meta": {}, "content": {"rendered": "<p>x</p>"}}])
        return _json([])

    mock_wp(monkeypatch, handler)
    decision = asyncio.run(resolve_strategy(CONN, _target()))
    assert decision.strategy == STRATEGY_STANDARD
    assert decision.existing["id"] == 7


def test_strategy_is_elementor_when_the_page_is_builder_managed(monkeypatch):
    tree = _elementor_tree()

    def handler(request):
        if "/pages" in str(request.url):
            return _json([{
                "id": 7, "slug": "payroll",
                "meta": {
                    "_elementor_edit_mode": "builder",
                    "_elementor_data": json.dumps(tree),
                },
            }])
        return _json([])

    mock_wp(monkeypatch, handler)
    decision = asyncio.run(resolve_strategy(CONN, _target()))
    assert decision.strategy == STRATEGY_ELEMENTOR
    assert decision.elementor["widget_count"] == 2


def test_elementor_page_with_unwritable_meta_is_blocked_not_silently_published(monkeypatch):
    """The core trap: post_content writes return 200 and change nothing visible."""
    def handler(request):
        if "/pages" in str(request.url):
            # Builder mode detected from rendered markup; meta is not exposed.
            return _json([{
                "id": 7, "slug": "payroll", "meta": {},
                "content": {"rendered": '<div class="elementor elementor-7">built</div>'},
            }])
        return _json([])

    mock_wp(monkeypatch, handler)
    decision = asyncio.run(resolve_strategy(CONN, _target()))
    assert decision.strategy == STRATEGY_BLOCKED
    assert decision.blockers[0]["blocker"] == "elementor_meta_not_writable"
    assert "resolution" in decision.blockers[0]


def test_unparseable_elementor_data_refuses_rather_than_overwriting(monkeypatch):
    def handler(request):
        if "/pages" in str(request.url):
            return _json([{
                "id": 7, "slug": "payroll",
                "meta": {"_elementor_edit_mode": "builder", "_elementor_data": "{{{broken"},
            }])
        return _json([])

    mock_wp(monkeypatch, handler)
    decision = asyncio.run(resolve_strategy(CONN, _target()))
    assert decision.strategy == STRATEGY_BLOCKED
    assert decision.blockers[0]["blocker"] == "elementor_data_unparseable"


# --- SEO plugin mapping -----------------------------------------------------


def test_seo_meta_is_plugin_specific_and_absent_when_unknown():
    assert build_seo_meta("rank_math", seo_title="T", seo_description="D") == {
        "rank_math_title": "T",
        "rank_math_description": "D",
    }
    assert "_yoast_wpseo_title" in build_seo_meta("yoast_seo", seo_title="T", seo_description=None)
    assert build_seo_meta(None, seo_title="T", seo_description="D") == {}
    assert build_seo_meta("something_else", seo_title="T", seo_description="D") == {}


# --- end-to-end publish -----------------------------------------------------


def test_publish_stops_before_any_network_call_when_content_is_invalid(monkeypatch):
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return _json([])

    mock_wp(monkeypatch, handler)
    trace = asyncio.run(publish_to_wordpress(CONN, _target(content_html="<p>tiny</p>")))
    assert trace["ok"] is False
    assert trace["stage"] == "validation"
    assert trace["error"] == "content_validation_failed"
    assert calls == []


def test_standard_publish_writes_verifies_and_reports_the_strategy(monkeypatch):
    written: dict = {}

    def handler(request):
        url, method = str(request.url), request.method
        if method == "GET" and "/pages?" in url:
            return _json([])
        if method == "GET" and "/posts?" in url:
            return _json([])
        if method == "POST" and url.endswith("/pages"):
            written.update(json.loads(request.content))
            return _json({"id": 42, "slug": "payroll", "status": "draft", "link": "https://example.com/payroll"})
        if method == "GET" and "/pages/42" in url:
            return _json({
                "id": 42, "slug": "payroll", "status": "draft",
                "title": {"raw": "Payroll Services"},
                "content": {"raw": written.get("content", "")},
                "meta": {}, "link": "https://example.com/payroll",
            })
        return _json({}, 404)

    mock_wp(monkeypatch, handler)
    trace = asyncio.run(publish_to_wordpress(CONN, _target()))

    assert trace["ok"] is True
    assert trace["strategy"] == STRATEGY_STANDARD
    assert trace["post_type"] == "pages"
    assert trace["stage"] == "completed"
    checks = trace["verification"]["checks"]
    assert checks["object_exists"] and checks["title_matches"] and checks["content_stored"]
    assert "pricing guide" in written["content"]


def test_elementor_publish_preserves_layout_and_verifies_the_tree(monkeypatch):
    tree = _elementor_tree()
    stored_meta: dict = {}

    def handler(request):
        url, method = str(request.url), request.method
        if method == "GET" and "/pages?" in url:
            return _json([{
                "id": 9, "slug": "payroll",
                "meta": {"_elementor_edit_mode": "builder", "_elementor_data": json.dumps(tree)},
            }])
        if method == "POST" and "/pages/9" in url:
            stored_meta.update(json.loads(request.content).get("meta", {}))
            return _json({"id": 9, "slug": "payroll", "status": "draft", "link": "https://example.com/payroll"})
        if method == "GET" and "/pages/9" in url:
            return _json({
                "id": 9, "slug": "payroll", "status": "draft",
                "title": {"raw": "Payroll Services"},
                "content": {"raw": ""}, "meta": stored_meta,
                "link": "https://example.com/payroll",
            })
        return _json({}, 404)

    mock_wp(monkeypatch, handler)
    trace = asyncio.run(publish_to_wordpress(CONN, _target()))

    assert trace["ok"] is True
    assert trace["strategy"] == STRATEGY_ELEMENTOR
    assert trace["elementor"]["widgets_before"] == 2
    assert trace["elementor"]["widgets_after"] == 3  # ours added, theirs kept

    saved = wp_elementor.parse_elementor_data(stored_meta["_elementor_data"])
    ids = {e["id"] for e in wp_elementor.iter_elements(saved)}
    assert {"hero001", "herow01", "body001", "bodyw01"}.issubset(ids)
    assert stored_meta["_elementor_edit_mode"] == "builder"
    assert trace["verification"]["checks"]["elementor_content_stored"] is True


def test_generated_image_is_uploaded_from_disk_and_rewritten_into_the_content(monkeypatch, tmp_path):
    """create_content persists images to a local path WordPress cannot fetch."""
    img = tmp_path / "hero.png"
    img.write_bytes(b"PNGBYTES")
    monkeypatch.setattr("app.integrations.llm.draft_images_dir", lambda: tmp_path)

    body = markdown_to_publish_html(
        ARTICLE + "\n\n[FIGURE hero] A cost chart\n",
        images=[{"role": "hero", "src": "/media/drafts/hero.png", "alt": "Cost chart"}],
    )
    assert 'src="/media/drafts/hero.png"' in body

    written: dict = {}

    def handler(request):
        url, method = str(request.url), request.method
        if method == "POST" and url.endswith("/media"):
            return _json({"id": 88, "source_url": "https://example.com/wp-content/hero.png"})
        if method == "POST" and "/media/88" in url:
            return _json({"id": 88})
        if method == "POST" and url.endswith("/pages"):
            written.update(json.loads(request.content))
            return _json({"id": 12, "slug": "payroll", "status": "draft"})
        if method == "GET" and "/pages/12" in url:
            return _json({
                "id": 12, "slug": "payroll", "status": "draft",
                "title": {"raw": "Payroll Services"},
                "content": {"raw": written.get("content", "")},
                "meta": {}, "featured_media": written.get("featured_media"),
            })
        if method == "GET" and "?" in url:
            return _json([])
        return _json({}, 404)

    mock_wp(monkeypatch, handler)
    trace = asyncio.run(publish_to_wordpress(
        CONN,
        _target(content_html=body, images=[
            {"role": "hero", "src": "/media/drafts/hero.png", "alt": "Cost chart"}
        ]),
    ))

    assert trace["media"]["uploaded"] == 1
    assert trace["media"]["featured_media"] == 88
    assert "/media/drafts/hero.png" not in written["content"]
    assert 'src="https://example.com/wp-content/hero.png"' in written["content"]
    assert written["featured_media"] == 88
    assert trace["ok"] is True


def test_a_figure_that_could_not_be_uploaded_is_dropped_not_published_broken(monkeypatch):
    body = markdown_to_publish_html(
        ARTICLE + "\n\n[FIGURE hero] A cost chart\n",
        images=[{"role": "hero", "src": "/media/drafts/missing.png", "alt": "Chart"}],
    )
    written: dict = {}

    def handler(request):
        url, method = str(request.url), request.method
        if method == "POST" and url.endswith("/pages"):
            written.update(json.loads(request.content))
            return _json({"id": 13, "slug": "payroll", "status": "draft"})
        if method == "GET" and "/pages/13" in url:
            return _json({
                "id": 13, "slug": "payroll", "status": "draft",
                "title": {"raw": "Payroll Services"},
                "content": {"raw": written.get("content", "")}, "meta": {},
            })
        if method == "GET" and "?" in url:
            return _json([])
        return _json({}, 404)

    mock_wp(monkeypatch, handler)
    trace = asyncio.run(publish_to_wordpress(
        CONN,
        _target(content_html=body, images=[
            {"role": "hero", "src": "/media/drafts/missing.png", "alt": "Chart"}
        ]),
    ))
    assert trace["media"]["failed"][0]["reason"] == "no_binary_to_upload"
    assert trace["media"]["figures_dropped_unresolved"] == 1
    assert "<img" not in written["content"]
    assert trace["ok"] is True  # the article still publishes, just without the figure


def test_slot_key_does_not_depend_on_post_type():
    """The managed element id must be stable across runs.

    post_type is unknown when the target is built and only resolved later, so if it
    fed the slot key the second publish would append a second managed widget rather
    than updating the first — duplicating the article on the page.
    """
    unresolved = _target(post_type=None)
    resolved_page = _target(post_type="pages")
    resolved_post = _target(post_type="posts")
    assert unresolved.slot_key() == resolved_page.slot_key() == resolved_post.slot_key()
    assert wp_elementor.managed_id(unresolved.slot_key()) == wp_elementor.managed_id(
        resolved_page.slot_key()
    )


def test_republishing_an_elementor_page_does_not_duplicate_content(monkeypatch):
    state = {"meta": {
        "_elementor_edit_mode": "builder",
        "_elementor_data": json.dumps(_elementor_tree()),
    }}

    def handler(request):
        url, method = str(request.url), request.method
        if method == "POST" and "/pages/9" in url:
            state["meta"].update(json.loads(request.content).get("meta") or {})
            return _json({"id": 9, "slug": "payroll", "status": "draft"})
        if method == "GET" and "/pages/9" in url:
            return _json({
                "id": 9, "slug": "payroll", "status": "draft",
                "title": {"raw": "Payroll Services"}, "content": {"raw": ""},
                "meta": state["meta"],
            })
        if method == "GET" and "/pages?" in url:
            return _json([{"id": 9, "slug": "payroll", "meta": state["meta"]}])
        if method == "GET" and "/posts?" in url:
            return _json([])
        return _json({}, 404)

    mock_wp(monkeypatch, handler)
    first = asyncio.run(publish_to_wordpress(CONN, _target()))
    widgets_after_first = first["elementor"]["widgets_after"]
    second = asyncio.run(publish_to_wordpress(CONN, _target()))

    assert first["elementor"]["merge_action"] == "appended_section"
    assert second["elementor"]["merge_action"] == "updated_in_place"
    assert second["elementor"]["widgets_after"] == widgets_after_first
    saved = wp_elementor.parse_elementor_data(state["meta"]["_elementor_data"])
    assert wp_elementor.count_widgets(saved) == widgets_after_first


def test_verification_fails_when_the_cms_did_not_store_the_content(monkeypatch):
    def handler(request):
        url, method = str(request.url), request.method
        if method == "POST" and url.endswith("/pages"):
            return _json({"id": 5, "slug": "payroll", "status": "draft"})
        if method == "GET" and "/pages/5" in url:  # must precede the collection rule
            return _json({
                "id": 5, "slug": "payroll", "status": "draft",
                "title": {"raw": "Payroll Services"},
                "content": {"raw": ""}, "meta": {},
            })
        if method == "GET" and ("?" in url):
            return _json([])  # nothing exists yet
        return _json({}, 404)

    # The write returns 200 but a plugin stripped the body — nothing was stored.
    mock_wp(monkeypatch, handler)
    trace = asyncio.run(publish_to_wordpress(CONN, _target()))
    assert trace["ok"] is False
    assert "content_stored" in trace["verification"]["failed_checks"]
    assert trace["verification"]["action_required"]


def test_slug_collision_updates_the_existing_object_instead_of_duplicating(monkeypatch):
    seen: list[str] = []

    def handler(request):
        url, method = str(request.url), request.method
        if method == "GET" and "/pages?" in url:
            return _json([{"id": 3, "slug": "payroll", "meta": {}, "content": {"rendered": "<p>old</p>"}}])
        if method == "GET" and "/posts?" in url:
            return _json([])
        if method == "POST":
            seen.append(url)
            return _json({"id": 3, "slug": "payroll", "status": "draft"})
        if method == "GET" and "/pages/3" in url:
            return _json({
                "id": 3, "slug": "payroll", "status": "draft",
                "title": {"raw": "Payroll Services"},
                "content": {"raw": markdown_to_publish_html(ARTICLE)}, "meta": {},
            })
        return _json({}, 404)

    mock_wp(monkeypatch, handler)
    trace = asyncio.run(publish_to_wordpress(CONN, _target(post_type="posts")))
    # Found as a page, so it updates the page — never creates a competing post.
    assert all("/pages/3" in u for u in seen), seen
    assert trace["write"]["action"] == "updated"


# --- transport-level error handling -----------------------------------------


def test_transient_errors_are_retried(monkeypatch):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, json={"message": "busy"})
        return _json({"id": 1, "status": "draft", "slug": "s"})

    mock_wp(monkeypatch, handler)
    monkeypatch.setattr(wordpress, "RETRY_BASE_DELAY", 0.0)
    result = asyncio.run(wordpress.fetch_object(CONN, 1))
    assert attempts["n"] == 3
    assert result["id"] == 1


def test_auth_failures_are_not_retried(monkeypatch):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(401, json={"message": "bad creds"})

    mock_wp(monkeypatch, handler)
    result = asyncio.run(
        wordpress.upsert_object(CONN, title="T", content_html="<p>x</p>", slug="s")
    )
    assert attempts["n"] == 1
    assert result["ok"] is False
    assert "401" in result["error"]


def test_credentials_never_appear_in_the_result(monkeypatch):
    mock_wp(monkeypatch, lambda request: httpx.Response(500, json={"message": "boom"}))
    monkeypatch.setattr(wordpress, "RETRY_BASE_DELAY", 0.0)
    result = asyncio.run(
        wordpress.upsert_object(CONN, title="T", content_html="<p>x</p>", slug="s")
    )
    blob = json.dumps(result)
    assert "app-pw-1234" not in blob
    assert "Basic " not in blob


def test_media_upload_returns_the_public_source_url(monkeypatch):
    def handler(request):
        if request.method == "POST" and str(request.url).endswith("/media"):
            assert b"PNGDATA" in request.content
            return _json({"id": 88, "source_url": "https://example.com/wp-content/a.png"})
        return _json({"id": 88})

    mock_wp(monkeypatch, handler)
    result = asyncio.run(
        wordpress.upload_media(CONN, filename="a.png", data=b"PNGDATA", alt_text="A chart")
    )
    assert result["ok"] is True
    assert result["source_url"].endswith("/wp-content/a.png")
