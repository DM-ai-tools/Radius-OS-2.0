"""Phase pipeline — cross-phase stage validation."""

from app.services.phase_pipeline import (
    PHASE_5_STAGES,
    PHASE_6_STAGES,
    PHASE_8_9_STAGES,
    PHASE_11_STAGES,
    enrich_phase5_cluster_intent,
    enrich_phase5_pack,
    enrich_phase6_pack,
    enrich_phase89_pack,
    enrich_phase11_pack,
    validate_phase5_pack,
    validate_phase6_pack,
    validate_phase89_pack,
    validate_phase11_pack,
)


def test_phase5_stages_defined():
    assert "keyword_research" in PHASE_5_STAGES
    assert PHASE_5_STAGES.index("topic_creation") < PHASE_5_STAGES.index("keyword_clustering")
    assert PHASE_5_STAGES[-1] == "cluster_intent_understanding"


def test_validate_phase5_pack_minimal():
    pack = {
        "keyword_count": 10,
        "keyword_dataset": [{"keyword": "seo"}],
        "keyword_cleaning": {"input_count": 12, "kept_count": 10},
        "topic_plan": {"topic_ideas": [{"title": "SEO Guide", "keyword": "seo"}]},
        "cluster_report": {
            "clusters_created": 1,
            "clusters": [{"name": "SEO", "primary_keyword": "seo", "intent": "informational", "keywords": [{"keyword": "seo", "role": "Primary"}]}],
            "cluster_validation": {"valid": True},
        },
    }
    out = validate_phase5_pack(pack)
    assert out["phase"] == 5
    assert out["ready_for_phase_6"] is True
    assert len(out["stages"]) == 5


def test_enrich_phase5_cluster_intent():
    report = {
        "clusters": [
            {
                "name": "Local SEO",
                "primary_keyword": "local seo melbourne",
                "intent": "commercial",
                "content_type": "service",
                "keywords": [
                    {"keyword": "local seo melbourne", "role": "Primary"},
                    {"keyword": "seo agency melbourne", "role": "Secondary"},
                ],
            }
        ]
    }
    intent = enrich_phase5_cluster_intent(report)
    assert intent["intents"][0]["primary_keyword"] == "local seo melbourne"
    assert intent["page_count"] >= 1


def test_validate_phase6_pack():
    pack = {
        "final_url_map": [{"cluster": "SEO", "action": "OPTIMIZE_EXISTING"}],
        "target_url_tree": [{"url": "/services/seo"}],
        "cluster_ownership": [{"cluster": "SEO", "canonical_owner_url": "/services/seo"}],
        "url_map_summary": {"optimize_existing": 1, "create": 1, "review_merge_redirect": 0},
    }
    out = validate_phase6_pack(pack)
    assert out["ready_for_phase_8_9"] is True


def test_validate_phase89_split():
    report = {
        "locked": True,
        "pages": [
            {"action": "refresh", "url": "/blog/old", "primary_keyword": "seo"},
            {"action": "create", "url": "/blog/new", "primary_keyword": "content marketing"},
        ],
    }
    out = enrich_phase89_pack(report)
    assert out["planning_split"]["existing_content"]
    assert out["planning_split"]["new_content"]
    assert out["phase_pipeline"]["existing_count"] == 1
    assert out["phase_pipeline"]["new_count"] == 1


def test_validate_phase11_pack():
    pack = {
        "pages": [
            {
                "title": {"after": "SEO Guide | Client"},
                "meta_description": {"after": "Learn SEO from Client."},
                "headings": {"h1": "SEO Guide", "h2s": ["Intro"]},
                "content": "Body copy",
            }
        ],
        "internal_links": [{"from": "/", "to": "/blog/seo"}],
    }
    out = enrich_phase11_pack(pack)
    assert out["phase_pipeline"]["ready_for_publishing"] is True
