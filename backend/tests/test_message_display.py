"""Chat history sent to the page drops duplicate keyword dumps."""

from app.services.message_display import display_message_payload


def test_checkpoint_does_not_repeat_the_report():
    shown = display_message_payload(
        {
            "event_type": "checkpoint",
            "card_type": "search_demand_report",
            "title": "Search Demand",
            "agent_key": "search_demand",
            "keyword_dataset": [{"keyword": "seo"}] * 50,
        }
    )
    assert shown == {
        "event_type": "checkpoint",
        "card_type": "search_demand_report",
        "title": "Search Demand",
        "agent_key": "search_demand",
    }


def test_service_clusters_replace_the_duplicate_seed_dump():
    stored = {
        "card_type": "search_demand_report",
        "seed_clusters": [{"seed": "seo", "keywords": [{"keyword": "seo"}]}],
        "clusters": [{"name": "SEO"}],
        "cluster_intent": {"seo": "commercial"},
        "cluster_report": {
            "service_clusters": [{"service": "SEO", "seeds": []}],
            "clusters": [{"name": "SEO", "primary_keyword": "seo"}],
            "cluster_intent": {"seo": "commercial"},
        },
        "keyword_count": 1,
    }
    shown = display_message_payload(stored)
    assert "seed_clusters" not in shown
    assert "clusters" not in shown
    assert "cluster_intent" not in shown
    assert shown["cluster_report"]["clusters"][0]["primary_keyword"] == "seo"
    assert shown["keyword_count"] == 1
    assert "seed_clusters" in stored


def test_a_report_without_service_groups_keeps_its_seeds():
    stored = {
        "card_type": "search_demand_report",
        "seed_clusters": [{"seed": "seo"}],
        "cluster_report": {"clusters": []},
    }
    assert display_message_payload(stored) is stored
