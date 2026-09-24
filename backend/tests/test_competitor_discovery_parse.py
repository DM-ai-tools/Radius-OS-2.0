"""Competitor discovery must accept the replies models actually send."""

from app.agents.competitor import _discovery_competitor_inputs
from app.integrations.providers import extract_competitor_rows


def test_extracts_json_object_wrapped_in_prose():
    text = (
        "Here is the set.\n"
        '{"competitors":[{"name":"King Kong","url":"https://kingkong.co","rationale":"peer"}]}\n'
        "Note: scores use {similarity}."
    )
    rows = extract_competitor_rows(text)
    assert rows[0]["name"] == "King Kong"
    assert rows[0]["url"] == "https://kingkong.co"


def test_extracts_website_key_and_markdown_links():
    rows = extract_competitor_rows(
        '{"items":[{"company":"WebFX","website":"webfx.com"}]}'
    )
    assert rows == [{"name": "WebFX", "url": "webfx.com"}]

    linked = extract_competitor_rows(
        "Peers include [Mindshare](https://www.mindshareworld.com) and others."
    )
    assert linked[0]["url"] == "https://www.mindshareworld.com"


def test_discovery_field_splits_urls_and_names():
    seeds, hints = _discovery_competitor_inputs(
        "King Kong https://kingkong.co\nWebFX\nhttps://www.webfx.com"
    )
    assert [s["url"] for s in seeds] == ["https://kingkong.co", "https://www.webfx.com"]
    assert hints == ["WebFX"]
