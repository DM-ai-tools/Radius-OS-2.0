"""Lightweight LLM keyword clustering report builder."""

from app.services.keyword_llm_clustering import build_cluster_report_from_llm


def test_build_cluster_report_assigns_intent_and_funnel():
    cleaned = [
        {"keyword": "seo audit", "volume": 500, "difficulty": 30, "intent": "commercial"},
        {"keyword": "what is seo", "volume": 800, "difficulty": 20, "intent": "informational"},
    ]
    llm_payload = {
        "dropped": [],
        "clusters": [
            {
                "name": "SEO Audits",
                "intent": "commercial",
                "funnel": "MOFU",
                "content_type": "service page",
                "primary_keyword": "seo audit",
                "keywords": [
                    {
                        "keyword": "seo audit",
                        "role": "Primary",
                        "intent": "commercial",
                        "funnel": "MOFU",
                    },
                    {
                        "keyword": "what is seo",
                        "role": "Supporting",
                        "intent": "informational",
                        "funnel": "TOFU",
                    },
                ],
            }
        ],
        "orphans": [],
    }
    report = build_cluster_report_from_llm(llm_payload, cleaned)
    assert len(report["clusters"]) == 2
    by_intent = {cluster["intent"]: cluster for cluster in report["clusters"]}
    assert by_intent["commercial"]["funnel"] == "MOFU"
    assert by_intent["commercial"]["keywords"][0]["keyword"] == "seo audit"
    info = by_intent["informational"]["keywords"][0]
    assert info["keyword"] == "what is seo"
    assert info["funnel"] == "TOFU"
    assert by_intent["commercial"]["est_traffic"] == 500
    assert by_intent["informational"]["avg_difficulty"] == 20.0
