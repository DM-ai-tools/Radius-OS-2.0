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
    assert len(report["clusters"]) == 1
    cluster = report["clusters"][0]
    assert cluster["intent"] == "commercial"
    assert cluster["funnel"] == "MOFU"
    kws = {k["keyword"]: k for k in cluster["keywords"]}
    assert kws["what is seo"]["funnel"] == "TOFU"
    assert kws["what is seo"]["intent"] == "informational"
