import pytest

from app.integrations.providers import pull_backlinks
from app.ml.scoring import cluster_competitors, spam_risk_score


@pytest.mark.asyncio
async def test_backlink_mock_provider():
    data, provider = await pull_backlinks("acme-retail.example")
    assert provider in ("ahrefs", "moz")
    assert data["referring_domains"] > 0


def test_spam_risk_bounds():
    score = spam_risk_score([{"spammy": True, "anchor": "exact"}, {"spammy": False}])
    assert 0 <= score <= 1


def test_cluster_labels():
    mapping = cluster_competitors(
        [
            {"name": "Value Players Ltd", "url": "https://value.example"},
            {"name": "Premium DTC Brand", "url": "https://premium.example"},
        ]
    )
    assert mapping["https://value.example"] == "Value players"
    assert mapping["https://premium.example"] == "Premium/DTC"
