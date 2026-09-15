from app.integrations.llm import mock_pre_research
from app.services.discovery_fields import DISCOVERY_FIELDS, RESEARCH_FIELDS


def test_phase_one_does_not_discover_competitors():
    assert "competitors" not in RESEARCH_FIELDS
    assert "competitors" not in DISCOVERY_FIELDS
    assert "competitors" not in mock_pre_research(
        "Example Co",
        "https://example.com",
        industry="Professional Services",
    )
