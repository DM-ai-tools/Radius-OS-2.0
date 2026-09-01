"""Keyword search-intent stamping and carry-through."""

from app.services.keyword_clustering import build_service_seed_clusters
from app.services.keyword_opportunity import detect_intent, stamp_keyword_intent
from app.services.keyword_seeding import build_seed_clusters
from app.services.memory_packs import slim_search_demand_memory


def test_detect_intent_normalizes_ahrefs_labels():
    assert detect_intent("seo services", "informational") == "informational"
    assert detect_intent("seo agency", "commercial") == "commercial"
    assert detect_intent("seo pricing", "transactional") == "transactional"
    assert detect_intent("clicktrends login", "branded") == "navigational"
    assert detect_intent("seo melbourne", "local") == "commercial"


def test_detect_intent_heuristic_when_provider_blank():
    assert detect_intent("how to improve seo", "") == "informational"
    assert detect_intent("best seo services", None) == "commercial"
    assert detect_intent("seo pricing", "") == "transactional"


def test_stamp_keyword_intent_fills_missing():
    row = stamp_keyword_intent({"keyword": "seo services pricing", "intent": ""})
    assert row["intent"] == "transactional"


def test_seed_clusters_and_service_clusters_carry_intent():
    rows = [
        {
            "keyword": "seo services",
            "volume": 1000,
            "difficulty": 40,
            "seed": "seo services",
            "match_class": "exact",
            "intent": "commercial",
            "source": "ahrefs",
        },
        {
            "keyword": "how to choose seo services",
            "volume": 200,
            "difficulty": 20,
            "seed": "seo services",
            "match_class": "phrase",
            "intent": "",
            "source": "ahrefs",
        },
        {
            "keyword": "seo pricing",
            "volume": 150,
            "difficulty": 25,
            "seed": "seo services",
            "match_class": "broad",
            "source": "dataforseo",
        },
    ]
    clusters = build_seed_clusters(rows, ["seo services"])
    assert clusters[0]["exact"][0]["intent"] == "commercial"
    assert clusters[0]["phrase"][0]["intent"] == "informational"
    assert clusters[0]["broad"][0]["intent"] == "transactional"

    groups = build_service_seed_clusters(clusters, ["SEO Services"])
    kws = {row["keyword"]: row["intent"] for row in groups[0]["seeds"][0]["keywords"]}
    assert kws["seo services"] == "commercial"
    assert kws["how to choose seo services"] == "informational"
    assert kws["seo pricing"] == "transactional"


def test_slim_memory_keeps_seed_intent():
    slim = slim_search_demand_memory(
        {
            "services": ["SEO Services"],
            "best_opportunities": [
                {"keyword": "seo services", "volume": 1000, "intent": "commercial"}
            ],
            "clusters": [{"name": "SEO", "primary_keyword": "seo", "keywords": []}],
            "seed_clusters": [
                {
                    "seed": "seo services",
                    "target_type": "service",
                    "exact": [
                        {
                            "keyword": "seo services",
                            "volume": 1000,
                            "intent": "commercial",
                            "match_class": "exact",
                        }
                    ],
                    "phrase": [],
                    "related": [],
                    "broad": [],
                }
            ],
        }
    )
    exact = ((slim.get("seed_clusters") or [{}])[0].get("exact") or [{}])[0]
    assert exact.get("intent") == "commercial"
    assert (slim.get("best_opportunities") or [{}])[0].get("intent") == "commercial"
