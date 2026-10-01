from unittest.mock import AsyncMock, patch

from app.integrations.llm import live_pre_research, mock_pre_research
from app.services.discovery_fields import DISCOVERY_FIELDS, RESEARCH_FIELDS


def test_phase_one_does_not_discover_competitors():
    assert "competitors" not in RESEARCH_FIELDS
    assert "competitors" not in DISCOVERY_FIELDS
    assert "competitors" not in mock_pre_research(
        "Example Co",
        "https://example.com",
        industry="Professional Services",
    )


def test_failed_discovery_does_not_invent_reviews_or_positioning():
    result = mock_pre_research(
        "Example Co",
        "https://example.com",
        industry="Professional Services",
    )
    assert result["status"] == "unavailable"
    assert result["needs_review"] is True
    assert result["positioning"]["value"] is None
    assert result["public_reviews_summary"]["value"] is None
    assert result["products"]["value"] is None
    blob = str(result).lower()
    assert "price-sensitive" not in blob
    assert "quality-focused" not in blob


async def test_unparsed_discovery_json_stays_unavailable():
    fetched = {"url": "https://example.com", "text": "<html><title>Example</title></html>", "error": None}
    with (
        patch("app.integrations.llm._skill_provider_ready", return_value=True),
        patch("app.integrations.web_fetch.fetch_url", new_callable=AsyncMock, return_value=fetched),
        patch("app.integrations.llm.synthesize_json", new_callable=AsyncMock, return_value=None),
    ):
        result = await live_pre_research("Example Co", "https://example.com", industry="Trades")
    assert result["needs_review"] is True
    assert result["public_reviews_summary"]["value"] is None
    assert result["positioning"]["value"] is None
    assert "could not be parsed" in result["unavailable_reason"]
