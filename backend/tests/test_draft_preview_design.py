"""The Phase 12 dry-run preview wears the live page's measured fonts and colors."""

from app.integrations.firecrawl import branding_tokens
from app.services.publish_preview import render_preview_html

BRANDING = {
    "colors": {
        "primary": "#0B3D2E",
        "background": "#F7F3EA",
        "textPrimary": "#1C1917",
        "textSecondary": "#57534E",
        "link": "#0B3D2E",
        "nope": "not-a-color",
    },
    "typography": {
        "fontFamilies": {"primary": "Source Sans 3", "heading": "Fraunces"},
        "fontSizes": {"h1": "52px", "h2": "32px", "h3": "22px", "body": "18px"},
        "fontWeights": {"bold": 700},
    },
    "spacing": {"borderRadius": "10px"},
    "components": {
        "buttonPrimary": {"background": "#0B3D2E", "textColor": "#F7F3EA"},
    },
}


def test_branding_tokens_keep_measured_values_only():
    tokens = branding_tokens({"branding": BRANDING})
    assert tokens["primary"] == "#0B3D2E"
    assert tokens["background"] == "#F7F3EA"
    assert tokens["heading_font"] == "Fraunces"
    assert tokens["body_font"] == "Source Sans 3"
    assert tokens["h1"] == "52px"
    assert tokens["body"] == "18px"
    assert "nope" not in tokens.values()
    assert branding_tokens({"branding": {"colors": {"primary": "blue"}}}) == {}


def test_preview_css_uses_the_live_page_type_and_color():
    html = render_preview_html(
        client_name="Click Trends",
        page={"title": "Local SEO", "url": "https://clicktrends.com.au/local-seo/"},
        content_html="<h1>Local SEO</h1><p>Melbourne agencies.</p>",
        brand={"available": False},
        site_design={
            "available": True,
            "source": "firecrawl",
            "primary_color": "#0B3D2E",
            "font": "Fraunces",
            "preview_tokens": branding_tokens({"branding": BRANDING}),
        },
    )
    assert "has-page-design" in html
    assert "Fraunces" in html
    assert "Source Sans 3" in html
    assert "#F7F3EA" in html
    assert "#1C1917" in html
    assert "52px" in html
    assert "fonts.googleapis.com" in html
    assert "measured from the live page with Firecrawl" in html
