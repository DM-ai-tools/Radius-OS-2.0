"""Measured design capture: local CSS walk + Context.dev merge. Never invents a color."""

import pytest

from app.services.design_capture import (
    build_design_md,
    design_source_url,
    merge_site_design,
)
from app.services.design_tokens import (
    custom_properties,
    find_logo,
    normalize_color,
    resolve_vars,
    tokens_from_css,
)


def test_design_source_url_preference_order():
    assert design_source_url(
        existing_page_url="https://client.example/services/seo",
        parent_pillar_page_url="https://client.example/services",
        client_website_url="https://client.example",
    ) == "https://client.example/services/seo"
    assert design_source_url(client_website_url="https://client.example") == "https://client.example"
    assert design_source_url(profile_website_url="not-a-url") == ""


def test_css_vars_resolve_and_normalize():
    css = """
    :root { --brand-primary: #0F766E; --btn-bg: var(--brand-primary); }
    .btn { background-color: var(--btn-bg); color: rgb(255, 255, 255); border-radius: 8px; padding: 12px 20px; }
    body { color: hsl(210, 40%, 12%); background-color: #ffffff; }
    """
    props = custom_properties(css)
    assert resolve_vars("var(--btn-bg)", props) == "#0F766E"
    assert normalize_color("#abc") == "#aabbcc"
    tokens = tokens_from_css(css)
    assert tokens["custom_properties"]["--brand-primary"] == "#0f766e"
    assert tokens["accent"] == "#0f766e"
    assert tokens["buttons"][0]["background"] == "#0f766e"
    assert tokens["buttons"][0]["radius"] == "8px"


def test_logo_hint_beats_social_icon():
    chosen = find_logo(
        [
            {"src": "/icons/facebook.svg", "alt": "", "class": "social", "id": "", "context": "footer"},
            {"src": "/img/wordmark.svg", "alt": "Acme logo", "class": "site-title", "id": "", "context": "header a"},
        ]
    )
    assert chosen["src"] == "/img/wordmark.svg"


def test_design_md_quotes_colors_and_puts_logo_url_first():
    md = build_design_md(
        {
            "url": "https://client.example",
            "colors": {"accent": "#a4d36b", "background": "", "text": "#111111"},
            "local": {
                "logo": {"url": "https://client.example/logo.svg", "data_uri": "data:image/svg+xml;base64,abc"},
                "custom_properties": {"--brand-primary": "#a4d36b"},
                "structure": {"bands": ["header", "main", "footer"], "nav_labels": ["Work"]},
            },
            "styleguide": {},
            "fonts": [],
            "overview": "Measured.",
        }
    )
    assert 'accent: "#a4d36b"' in md
    assert md.index("url: https://client.example/logo.svg") < md.index("data_uri:")
    assert "header → main → footer" in md


def test_missing_color_is_not_available():
    md = build_design_md({"url": "https://client.example", "colors": {}, "local": {}, "overview": ""})
    assert "NOT AVAILABLE" in md
    assert "#0f766e" not in md


def test_measured_tokens_override_wordpress_kit():
    merged = merge_site_design(
        {"available": True, "source": "wordpress", "primary_color": "#111111", "font": "Old"},
        {
            "available": True,
            "version": 3,
            "url": "https://client.example",
            "font": "Measured Sans",
            "colors": {"accent": "#0f766e", "background": "#ffffff", "text": ""},
            "local": {},
            "design_md": "---\n",
            "screenshots": [{"url": "https://cdn.example/hero.png", "model_safe": True, "height": 900}],
        },
    )
    assert merged["source"] == "measured"
    assert merged["primary_color"] == "#0f766e"
    assert merged["font"] == "Measured Sans"
    assert merged["design_capture"]["screenshots"][0]["url"].endswith("hero.png")


@pytest.mark.asyncio
async def test_context_dev_sends_key_to_sdk_not_query(monkeypatch):
    from types import SimpleNamespace

    from app.config import get_settings
    from app.integrations import context_dev

    settings = get_settings()
    monkeypatch.setattr(settings, "context_dev_api_key", "ctxt_secret_test", raising=False)
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(context_dev, "assert_safe_url", lambda url: (True, url))

    seen: dict = {}

    class _Guide:
        def model_dump(self, **_kwargs):
            return {"colors": {"accent": "#112233"}, "typography": {"p": {"fontFamily": "Inter"}}}

    class _Web:
        async def extract_styleguide(self, **kwargs):
            seen["kwargs"] = kwargs
            return SimpleNamespace(styleguide=_Guide())

    class _Client:
        def __init__(self, **kwargs):
            seen["init"] = kwargs
            self.web = _Web()

        async def close(self):
            return None

    monkeypatch.setattr(context_dev, "AsyncContextDev", _Client)
    guide, err = await context_dev.extract_styleguide("https://client.example")
    assert err is None
    assert guide["colors"]["accent"] == "#112233"
    assert seen["init"]["api_key"] == "ctxt_secret_test"
    assert seen["kwargs"]["direct_url"] == "https://client.example"
    assert "key" not in seen["kwargs"]
    assert "ctxt_secret_test" not in str(seen["kwargs"])
