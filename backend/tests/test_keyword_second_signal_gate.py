"""A single shared token is no longer enough to call a keyword relevant."""

from __future__ import annotations

from app.services.keyword_opportunity import select_topics_from_service_clusters
from app.services.keyword_relevance import (
    build_relevance_context,
    evaluate_keyword,
    filter_relevant_keywords,
)


def ctx():
    return build_relevance_context(
        services=["Dental Implants", "Teeth Whitening", "Emergency Dental Care"],
        cdd_keywords=["dentist melbourne"],
        brand_name="Northside Dental",
        domain="northsidedental.com.au",
        industry="Dental",
    )


def test_single_token_overlap_alone_is_rejected():
    """'dental' alone is not evidence that a searcher wants dentistry bought."""
    keep, reason, _ = evaluate_keyword(
        "dental museum exhibition", ctx(), match_class="broad", seed=None
    )
    assert keep is False
    assert reason == "weak_single_token"


def test_single_token_overlap_passes_with_a_commercial_signal():
    keep, _reason, _ = evaluate_keyword(
        "dentist open saturday", ctx(), match_class="broad", seed=None
    )
    assert keep is True


def test_single_token_overlap_passes_with_service_seed_lineage():
    keep, _reason, _ = evaluate_keyword(
        "dental anxiety children", ctx(), match_class="broad", seed="Dental Implants"
    )
    assert keep is True


def test_two_distinctive_tokens_pass_without_other_signals():
    keep, _reason, _ = evaluate_keyword(
        "teeth whitening aftercare", ctx(), match_class="broad", seed=None
    )
    assert keep is True


def test_lemma_matching_links_dentist_dental_dentistry():
    keep, _reason, _ = evaluate_keyword(
        "dentistry prices", ctx(), match_class="broad", seed=None
    )
    assert keep is True


def test_lemma_matching_links_bake_baker_bakery():
    bakery = build_relevance_context(
        services=["Sourdough Bread", "Custom Cakes"],
        cdd_keywords=["artisan bakery"],
        brand_name="Crust and Co",
        domain="crustandco.com",
        industry="Bakery",
    )
    keep, _reason, _ = evaluate_keyword(
        "baker near me", bakery, match_class="broad", seed=None
    )
    assert keep is True


def test_hard_reject_beats_every_other_signal():
    """Two distinctive tokens AND a commercial signal must not rescue a job ad."""
    keep, reason, _ = evaluate_keyword(
        "emergency dental care jobs near me",
        ctx(),
        match_class="phrase",
        seed="Emergency Dental Care",
    )
    assert keep is False
    assert reason == "excluded_jobs"


def test_filter_stamps_rerouted_rows_as_informational():
    rows = [{"keyword": "diy teeth whitening", "match_class": "broad", "seed": "Teeth Whitening"}]
    kept, _excluded, _audit = filter_relevant_keywords(rows, ctx())
    assert len(kept) == 1
    assert kept[0]["intent"] == "informational"
    assert kept[0]["commercial_target_eligible"] is False


def test_filter_leaves_buyer_rows_commercially_eligible():
    rows = [{"keyword": "teeth whitening cost", "match_class": "phrase", "seed": "Teeth Whitening"}]
    kept, _excluded, _audit = filter_relevant_keywords(rows, ctx())
    assert kept[0]["commercial_target_eligible"] is True


def test_service_topic_selection_skips_non_commercial_rows():
    """The reroute flag has to actually stop a service page targeting the keyword."""
    groups = [
        {
            "service": "Teeth Whitening",
            "seeds": [
                {
                    "seed": "teeth whitening",
                    "target": "Teeth Whitening",
                    "keywords": [
                        {
                            "keyword": "diy teeth whitening at home",
                            "match_class": "phrase",
                            "volume": 5000,
                            "commercial_target_eligible": False,
                        },
                        {
                            "keyword": "teeth whitening cost melbourne",
                            "match_class": "phrase",
                            "volume": 300,
                        },
                    ],
                }
            ],
        }
    ]
    picked = select_topics_from_service_clusters(
        groups, products=["Teeth Whitening"], limit=5, max_per_service=2
    )
    keywords = {row["keyword"] for row in picked}
    assert "diy teeth whitening at home" not in keywords
    assert "teeth whitening cost melbourne" in keywords
