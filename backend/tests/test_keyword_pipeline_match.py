"""Pipeline rules that keep DataForSEO metrics and Claude judgments apart."""

from app.integrations.dataforseo import should_retry_dataforseo
from app.services.keyword_cluster_naming import apply_cluster_names
from app.services.keyword_metrics import volumes_disagree
from app.services.keyword_validation_gate import _parse_verdicts


def test_volume_discrepancy_flags_a_ratio_above_two():
    assert volumes_disagree(100, 250, 2) is True
    assert volumes_disagree(100, 180, 2) is False
    assert volumes_disagree(None, 100, 2) is False
    assert volumes_disagree(0, 40, 2) is True


def test_relevance_json_cannot_inject_keywords():
    sent = {1: "drain cleaning", 2: "blocked drain"}
    parsed = _parse_verdicts(
        {
            "keywords": [
                {"keyword": "drain cleaning", "relevant": True, "reason": "buyer"},
                {"keyword": "invented term", "relevant": True, "reason": "no"},
                {"keyword": "blocked drain", "relevant": False, "reason": "jobs"},
            ]
        },
        sent,
    )
    assert set(parsed) == {"drain cleaning", "blocked drain"}
    assert parsed["drain cleaning"].verdict == "keep"
    assert parsed["blocked drain"].verdict == "drop"


def test_cluster_naming_ignores_a_primary_that_was_not_a_member():
    clusters = [
        {
            "name": "old",
            "primary_keyword": "drain cleaning",
            "keywords": [{"keyword": "drain cleaning"}, {"keyword": "blocked drain"}],
        }
    ]
    named = apply_cluster_names(
        clusters,
        {"clusters": [{"id": 1, "name": "Drain help", "primary_keyword": "not in cluster"}]},
    )
    assert named[0]["name"] == "Drain help"
    assert named[0]["primary_keyword"] == "drain cleaning"


def test_dataforseo_retries_transport_and_server_errors_only():
    assert should_retry_dataforseo(http_status=None, body_status=None, transport_error=True)
    assert should_retry_dataforseo(http_status=503, body_status=None, transport_error=False)
    assert should_retry_dataforseo(http_status=429, body_status=None, transport_error=False)
    assert not should_retry_dataforseo(http_status=400, body_status=None, transport_error=False)
    assert not should_retry_dataforseo(http_status=200, body_status=40501, transport_error=False)
    assert should_retry_dataforseo(http_status=200, body_status=50000, transport_error=False)
    assert not should_retry_dataforseo(http_status=200, body_status=20000, transport_error=False)
