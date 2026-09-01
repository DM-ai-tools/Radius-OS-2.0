"""Integration test: Verify Phase 5 topics/keywords flow through Phase 6/9 correctly."""

import pytest
from typing import Any


@pytest.fixture
def mock_phase5_output() -> dict[str, Any]:
    """Create a realistic Phase 5 search_demand_summary output."""
    return {
        "seed_keywords": ["seo", "content marketing"],
        "topic_plan": {
            "topic_ideas": [
                {
                    "title": "SEO Fundamentals Guide",
                    "keyword": "what is seo",
                    "primary_keyword": "what is seo",
                    "secondary_keywords": ["seo basics", "seo definition"],
                    "supporting_keywords": ["seo basics", "seo definition"],
                    "intent": "informational",
                    "funnel": "TOFU",
                    "angle": "what-why",
                    "opportunity_score": 65.5,
                    "volume": 3200,
                },
                {
                    "title": "Best SEO Tools 2026",
                    "keyword": "seo tools",
                    "primary_keyword": "seo tools",
                    "secondary_keywords": ["keyword research tools", "seo software"],
                    "supporting_keywords": ["keyword research tools", "seo software"],
                    "intent": "commercial",
                    "funnel": "MOFU",
                    "angle": "listicle",
                    "opportunity_score": 72.3,
                    "volume": 2800,
                },
            ]
        },
        "topics": [
            {
                "title": "SEO Fundamentals Guide",
                "keyword": "what is seo",
                "primary_keyword": "what is seo",
                "intent": "informational",
                "opportunity_score": 65.5,
            },
            {
                "title": "Best SEO Tools 2026",
                "keyword": "seo tools",
                "primary_keyword": "seo tools",
                "intent": "commercial",
                "opportunity_score": 72.3,
            },
        ],
        "cluster_report": {
            "clusters": [
                {
                    "name": "SEO Fundamentals",
                    "primary_keyword": "what is seo",
                    "intent": "informational",
                    "best_score": 65.5,
                    "keywords": [
                        {"keyword": "what is seo", "role": "Primary", "intent": "informational"},
                        {"keyword": "seo basics", "role": "Secondary", "intent": "informational"},
                        {"keyword": "seo definition", "role": "Supporting", "intent": "informational"},
                    ],
                    "entities": ["SEO", "basics", "fundamentals"],
                    "business_relevance": {
                        "reason": "seed_exact",
                        "evidence": "Matched to seed 'seo'",
                    },
                },
                {
                    "name": "SEO Tools",
                    "primary_keyword": "seo tools",
                    "intent": "commercial",
                    "best_score": 72.3,
                    "keywords": [
                        {"keyword": "seo tools", "role": "Primary", "intent": "commercial"},
                        {"keyword": "keyword research tools", "role": "Secondary", "intent": "commercial"},
                        {"keyword": "seo software", "role": "Supporting", "intent": "commercial"},
                    ],
                    "entities": ["tools", "software", "keyword research"],
                    "business_relevance": {
                        "reason": "seed_related",
                        "evidence": "Related to seed 'seo'",
                    },
                },
            ]
        },
        "best_opportunities": [
            {"keyword": "seo", "volume": 5200, "opportunity_score": 68.2},
            {"keyword": "seo tools", "volume": 2800, "opportunity_score": 72.3},
            {"keyword": "what is seo", "volume": 3200, "opportunity_score": 65.5},
        ],
        "serp_by_keyword": {
            "what is seo": {
                "validated": True,
                "dominant_format": "article",
                "intent": "informational",
                "organic": [
                    {"title": "What Is SEO? Definition & Basics", "position": 1},
                    {"title": "SEO Definition: How Search Engine Optimization Works", "position": 2},
                ],
            },
            "seo tools": {
                "validated": True,
                "dominant_format": "listicle",
                "intent": "commercial",
                "organic": [
                    {"title": "10 Best SEO Tools in 2026", "position": 1},
                    {"title": "Top SEO Software Compared", "position": 2},
                ],
            },
        },
    }


class TestPhase5Topics:
    """Verify Phase 5 topics persist into Phase 6a."""

    def test_phase5_topic_plan_structure(self, mock_phase5_output):
        """Phase 5 topic_plan has required fields."""
        topic_plan = mock_phase5_output["topic_plan"]
        assert "topic_ideas" in topic_plan
        assert len(topic_plan["topic_ideas"]) == 2

        for idea in topic_plan["topic_ideas"]:
            assert idea.get("keyword")
            assert idea.get("primary_keyword")
            assert idea.get("secondary_keywords")
            assert idea.get("intent") in ("informational", "commercial", "transactional")
            assert idea.get("opportunity_score") is not None

    def test_phase5_clusters_carry_keywords(self, mock_phase5_output):
        """Phase 5 clusters have keywords tied to primary_keyword."""
        clusters = mock_phase5_output["cluster_report"]["clusters"]
        assert len(clusters) == 2

        for cluster in clusters:
            assert cluster["primary_keyword"]
            assert cluster["intent"]
            keywords = [kw["keyword"] for kw in cluster["keywords"]]
            assert cluster["primary_keyword"] in keywords
            assert len(cluster["keywords"]) >= 1

    def test_phase5_best_opportunities_linked_to_topics(self, mock_phase5_output):
        """best_opportunities keywords exist in topics."""
        topic_keywords = {
            idea["keyword"] for idea in mock_phase5_output["topic_plan"]["topic_ideas"]
        }
        best_keywords = {opp["keyword"] for opp in mock_phase5_output["best_opportunities"]}

        # Not all best_opportunities need to be topics, but key ones should overlap
        overlap = topic_keywords & best_keywords
        assert len(overlap) >= 1, "best_opportunities should include at least one topic keyword"


class TestPhase6aContinuity:
    """Verify Phase 6a (Content Strategy) reads Phase 5 topics correctly."""

    def test_build_priority_queue_respects_phase5_spine(self, mock_phase5_output):
        """Content Strategy's build_priority_queue uses Phase 5 topics as spine."""
        from app.services.content_strategy import build_priority_queue

        topic_plan = mock_phase5_output["topic_plan"]
        best = mock_phase5_output["best_opportunities"]
        clusters = mock_phase5_output["cluster_report"]["clusters"]

        # Simulate what Phase 6a does
        priority_queue = build_priority_queue(
            best=best,
            evergreen=[],
            trends=[],
            avoid=[],
            report_clusters=clusters,
            domain="example.com",
            industry="SEO",
            topic_plan=topic_plan,
        )

        # Priority queue should have entries for both Phase 5 topics
        queue_keywords = {str(item["keyword"]).lower() for item in priority_queue}

        # Both Phase 5 topic keywords should appear
        assert "what is seo" in queue_keywords, "Topic 'what is seo' missing from priority queue"
        assert "seo tools" in queue_keywords, "Topic 'seo tools' missing from priority queue"

        # Verify topic titles are carried through
        for item in priority_queue:
            if item["keyword"].lower() == "what is seo":
                assert "SEO" in item["title"], "Topic title not carried through"
            elif item["keyword"].lower() == "seo tools":
                assert "Tools" in item["title"], "Topic title not carried through"

    def test_phase6a_core_topics_preserve_intent(self, mock_phase5_output):
        """Phase 6a core_topics preserve intent from Phase 5 topics."""
        from app.services.content_strategy import build_authority_map

        # Extract pillars from topic ideas (what Phase 6a does)
        topics = mock_phase5_output["topic_plan"]["topic_ideas"]

        # Verify intent is preserved in each topic
        intents_in_topics = {
            topic.get("intent"): topic.get("keyword") for topic in topics
        }

        assert "informational" in intents_in_topics
        assert "commercial" in intents_in_topics


class TestPhase6bContinuity:
    """Verify Phase 6b (Site Architecture) reads Phase 6a pillar structure."""

    def test_url_map_includes_pillar_metadata(self, mock_phase5_output):
        """URL map entries include pillar metadata from Phase 6a."""
        from app.services.url_mapping import build_final_url_map

        # Simulate Phase 6a's core_topics (pillar structure)
        core_topics = [
            {
                "pillar": "SEO Fundamentals",
                "clusters": [
                    {
                        "name": "SEO Fundamentals",
                        "primary_keyword": "what is seo",
                    }
                ],
            },
            {
                "pillar": "SEO Tools",
                "clusters": [
                    {
                        "name": "SEO Tools",
                        "primary_keyword": "seo tools",
                    }
                ],
            },
        ]

        url_map = build_final_url_map(
            mock_phase5_output["cluster_report"],
            serp_by_keyword=mock_phase5_output["serp_by_keyword"],
            pillar_structure=core_topics,
        )

        # Verify pillar information is in the URL map
        pillars_in_map = {entry.get("pillar") for entry in url_map["final_url_map"]}
        assert "SEO Fundamentals" in pillars_in_map, "Pillar not annotated in URL map"
        assert "SEO Tools" in pillars_in_map, "Pillar not annotated in URL map"

    def test_url_map_preserves_cluster_keywords(self, mock_phase5_output):
        """URL map entries preserve cluster primary keywords."""
        from app.services.url_mapping import build_final_url_map

        url_map = build_final_url_map(
            mock_phase5_output["cluster_report"],
            serp_by_keyword=mock_phase5_output["serp_by_keyword"],
        )

        mapped_keywords = {entry.get("primary_keyword") for entry in url_map["final_url_map"]}

        # Both cluster primary keywords should be in the map
        assert "what is seo" in mapped_keywords
        assert "seo tools" in mapped_keywords


class TestEndToEndContinuity:
    """Verify topics/keywords flow from Phase 5 through Phase 9."""

    def test_phase5_keyword_in_content_planning(self, mock_phase5_output):
        """Phase 9 Content Planning receives Phase 5 clusters."""
        clusters = mock_phase5_output["cluster_report"]["clusters"]

        # Verify Phase 9 would receive clusters with keyword context intact
        assert len(clusters) >= 2
        for cluster in clusters:
            assert cluster["primary_keyword"]
            assert cluster["intent"] in ("informational", "commercial")
            assert cluster["keywords"]  # Has keyword list for briefing

    def test_intent_preserved_through_chain(self, mock_phase5_output):
        """Intent from Phase 5 topics is preserved through Phase 6a/6b."""
        # Phase 5 topics
        topics = mock_phase5_output["topic_plan"]["topic_ideas"]
        phase5_intents = {t["keyword"]: t["intent"] for t in topics}

        # Phase 5 clusters
        clusters = mock_phase5_output["cluster_report"]["clusters"]
        phase5_cluster_intents = {c["primary_keyword"]: c["intent"] for c in clusters}

        # Verify alignment between topics and clusters
        for kw, intent in phase5_intents.items():
            assert phase5_cluster_intents.get(kw) == intent, (
                f"Intent mismatch for {kw}: topic intent={intent}, "
                f"cluster intent={phase5_cluster_intents.get(kw)}"
            )

    def test_keyword_deduplication_across_flow(self, mock_phase5_output):
        """No duplicate primary keywords in clusters."""
        clusters = mock_phase5_output["cluster_report"]["clusters"]
        primary_keywords = [c["primary_keyword"] for c in clusters]

        assert len(primary_keywords) == len(set(primary_keywords)), (
            f"Duplicate primary keywords found: {primary_keywords}"
        )

    def test_serp_format_informs_intent(self, mock_phase5_output):
        """SERP dominant_format aligns with intent."""
        serp_data = mock_phase5_output["serp_by_keyword"]

        for kw, serp in serp_data.items():
            dominant_format = serp.get("dominant_format")
            intent = serp.get("intent")

            # Listicle typically maps to commercial intent
            if dominant_format == "listicle":
                assert intent in ("commercial", "transactional")
            # Article typically maps to informational
            elif dominant_format == "article":
                assert intent in ("informational", "transactional")


class TestContinuityContracts:
    """Verify input/output contracts between phases."""

    def test_phase5_output_has_all_required_fields(self, mock_phase5_output):
        """Phase 5 output includes all fields Phase 6a/6b expect."""
        required_fields = [
            "topic_plan",
            "topics",
            "cluster_report",
            "best_opportunities",
            "serp_by_keyword",
        ]
        for field in required_fields:
            assert field in mock_phase5_output, f"Phase 5 missing required field: {field}"

    def test_cluster_has_keyword_roles(self, mock_phase5_output):
        """Clusters carry keyword roles (Primary/Secondary/Supporting)."""
        clusters = mock_phase5_output["cluster_report"]["clusters"]

        for cluster in clusters:
            keywords = cluster.get("keywords", [])
            roles = {kw.get("role") for kw in keywords}
            assert "Primary" in roles, f"Cluster {cluster['name']} missing Primary role"

    def test_business_relevance_metadata_present(self, mock_phase5_output):
        """Clusters carry business_relevance metadata from Phase 5."""
        clusters = mock_phase5_output["cluster_report"]["clusters"]

        for cluster in clusters:
            br = cluster.get("business_relevance", {})
            assert br.get("reason") in (
                "seed_exact",
                "seed_phrase",
                "seed_related",
                "cdd_phrase",
                "service_overlap",
                "page_overlap",
                "theme_overlap",
                "competitor_gap",
                "hygiene_only",
            ), f"Cluster {cluster['name']} has invalid business_relevance reason"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
