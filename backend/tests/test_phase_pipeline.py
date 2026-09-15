"""Phase pipeline — cross-phase stage validation."""

from app.services.phase_pipeline import (
    PHASE_5_STAGES,
    enrich_phase5_cluster_intent,
    enrich_phase89_pack,
    enrich_phase11_pack,
    validate_phase5_pack,
    validate_phase6_pack,
)


def test_phase5_stages_defined():
    from app.services.phase_pipeline import PHASE_6_STAGES

    assert "keyword_research" in PHASE_5_STAGES
    assert "sitemap_cluster_classification" in PHASE_5_STAGES
    assert PHASE_5_STAGES.index("keyword_clustering") < PHASE_5_STAGES.index(
        "sitemap_cluster_classification"
    )
    assert PHASE_5_STAGES.index("sitemap_cluster_classification") < PHASE_5_STAGES.index(
        "topic_creation"
    )
    assert PHASE_5_STAGES[-1] == "cluster_intent_understanding"
    assert "topic_creation" not in PHASE_6_STAGES
    assert PHASE_6_STAGES[0] == "cluster_url_mapping"


def test_validate_phase5_pack_minimal():
    pack = {
        "keyword_count": 10,
        "keyword_dataset": [{"keyword": "seo"}],
        "keyword_cleaning": {"input_count": 12, "kept_count": 10},
        "topic_plan": {"topic_ideas": [{"title": "SEO Guide", "keyword": "seo"}]},
        "sitemap_classification": {
            "existing_topic_count": 0,
            "existing_review_count": 0,
            "new_topic_count": 1,
        },
        "cluster_report": {
            "clusters_created": 1,
            "clusters": [
                {
                    "name": "SEO",
                    "primary_keyword": "seo",
                    "intent": "informational",
                    "funnel": "TOFU",
                    "topic_disposition": "new_topic",
                    "keywords": [{"keyword": "seo", "role": "Primary"}],
                }
            ],
            "cluster_validation": {"valid": True},
            "sitemap_classification": {
                "existing_topic_count": 0,
                "existing_review_count": 0,
                "new_topic_count": 1,
            },
        },
    }
    out = validate_phase5_pack(pack)
    assert out["phase"] == 5
    assert out["ready_for_phase_6"] is True
    assert len(out["stages"]) == 6


def test_phase5_fails_when_clusters_were_never_compared_to_the_site_map():
    """Clustering without the site-map comparison is an incomplete Phase 5 —
    URL mapping must not receive clusters that were never classified."""
    pack = {
        "keyword_count": 10,
        "keyword_dataset": [{"keyword": "seo"}],
        "keyword_cleaning": {"input_count": 12, "kept_count": 10},
        "cluster_report": {
            "clusters_created": 1,
            "clusters": [{"name": "SEO", "primary_keyword": "seo", "keywords": []}],
        },
    }
    out = validate_phase5_pack(pack)
    by_stage = {s["stage"]: s for s in out["stages"]}
    assert by_stage["keyword_clustering"]["ok"] is True
    assert by_stage["sitemap_cluster_classification"]["ok"] is False
    assert out["ready_for_phase_6"] is False


def test_phase5_classification_detail_reports_the_full_status_taxonomy():
    pack = {
        "keyword_count": 3,
        "keyword_dataset": [{"keyword": "seo"}],
        "keyword_cleaning": {"input_count": 4, "kept_count": 3},
        "sitemap_classification": {
            "existing_topic_count": 1,
            "existing_review_count": 1,
            "new_topic_count": 1,
            "supporting_topic_count": 2,
            "uncertain_count": 1,
            "out_of_scope_count": 0,
            "counts": {"NEW_TOPIC": 1, "SUPPORTING_TOPIC": 2},
        },
        "cluster_report": {"clusters": [{"name": "SEO", "primary_keyword": "seo"}]},
    }
    detail = next(
        s["detail"] for s in validate_phase5_pack(pack)["stages"]
        if s["stage"] == "sitemap_cluster_classification"
    )
    assert "2 supporting" in detail
    assert "1 uncertain" in detail


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
