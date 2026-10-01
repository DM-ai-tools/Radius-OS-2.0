"""Relevance flags must survive the seed-cluster round trip.

`build_seed_clusters` rebuilds every keyword into a fixed-shape `entry` dict, and
`flatten_dataset` rebuilds the flat dataset from those entries. Any flag not in
that whitelist is silently dropped — which meant `commercial_target_eligible`
and `relevance_unverified` never reached
`select_topics_from_service_clusters`, the step that is supposed to honour them.
These tests pin the whole round trip, not just the units.
"""

from __future__ import annotations

from app.services.keyword_clustering import build_service_seed_clusters
from app.services.keyword_opportunity import select_topics_from_service_clusters
from app.services.keyword_seeding import build_seed_clusters, flatten_dataset

SEED = "teeth whitening"


def _rows():
    return [
        {
            "keyword": "diy teeth whitening at home",
            "seed": SEED,
            "match_class": "phrase",
            "volume": 5000,
            "intent": "informational",
            "commercial_target_eligible": False,
            "relevance_unverified": True,
            "relevance_reason": "relevance_unverified",
            "gap_flag": True,
            "provider_fallback": True,
        },
        {
            "keyword": "teeth whitening cost melbourne",
            "seed": SEED,
            "match_class": "phrase",
            "volume": 300,
            "intent": "commercial",
        },
    ]


def test_build_seed_clusters_preserves_relevance_flags():
    clusters = build_seed_clusters(_rows(), [SEED])
    entry = next(
        e for e in clusters[0]["phrase"] if e["keyword"] == "diy teeth whitening at home"
    )
    assert entry["commercial_target_eligible"] is False
    assert entry["relevance_unverified"] is True
    assert entry["relevance_reason"] == "relevance_unverified"


def test_clean_rows_are_not_marked_unverified():
    clusters = build_seed_clusters(_rows(), [SEED])
    entry = next(
        e for e in clusters[0]["phrase"] if e["keyword"] == "teeth whitening cost melbourne"
    )
    assert entry["relevance_unverified"] is False
    assert entry["commercial_target_eligible"] is not False


def test_flags_survive_flatten_dataset():
    flat = flatten_dataset(build_seed_clusters(_rows(), [SEED]))
    row = next(r for r in flat if r["keyword"] == "diy teeth whitening at home")
    assert row["commercial_target_eligible"] is False
    assert row["relevance_unverified"] is True


def test_other_carried_flags_survive():
    clusters = build_seed_clusters(_rows(), [SEED])
    entry = next(
        e for e in clusters[0]["phrase"] if e["keyword"] == "diy teeth whitening at home"
    )
    assert entry["gap_flag"] is True
    assert entry["provider_fallback"] is True


def test_non_targetable_keyword_is_excluded_from_topic_selection_end_to_end():
    """The real path: seeding -> clusters -> service groups -> topic selection.

    Previously this passed only when rows were handed straight to the selector;
    through the real path the flag had already been stripped.
    """
    seed_clusters = build_seed_clusters(_rows(), [SEED])
    service_clusters = build_service_seed_clusters(seed_clusters, ["Teeth Whitening"])
    picked = select_topics_from_service_clusters(
        service_clusters, products=["Teeth Whitening"], limit=5, max_per_service=5
    )
    keywords = {row["keyword"] for row in picked}
    assert "diy teeth whitening at home" not in keywords
    assert "teeth whitening cost melbourne" in keywords


def test_gate_flags_are_stamped_back_onto_cluster_rows():
    """The gate judges the flat dataset, but flatten_dataset rebuilds it from
    the cluster rows — so verdicts have to be written back to the clusters."""
    from app.services.keyword_validation_gate import stamp_relevance_flags

    clusters = build_seed_clusters(
        [
            {"keyword": "teeth whitening cost", "seed": SEED, "match_class": "phrase", "volume": 300},
            {"keyword": "whitening strips", "seed": SEED, "match_class": "broad", "volume": 50},
        ],
        [SEED],
    )
    judged = [
        {"keyword": "teeth whitening cost", "seed": SEED},
        {
            "keyword": "whitening strips",
            "seed": SEED,
            "relevance_unverified": True,
            "commercial_target_eligible": False,
            "relevance_reason": "relevance_unverified",
            "relevance_unjudged_cause": "review_band",
        },
    ]
    stamped = stamp_relevance_flags(clusters, judged)
    flat = {r["keyword"]: r for r in flatten_dataset(stamped)}
    assert flat["whitening strips"]["relevance_unverified"] is True
    assert flat["whitening strips"]["commercial_target_eligible"] is False
    assert flat["teeth whitening cost"]["relevance_unverified"] is False


def test_stamping_matches_on_seed_and_keyword_together():
    """The same keyword under a different service must not inherit a verdict."""
    from app.services.keyword_validation_gate import stamp_relevance_flags

    clusters = build_seed_clusters(
        [
            {"keyword": "shared term", "seed": "service a", "match_class": "broad", "volume": 10},
            {"keyword": "shared term", "seed": "service b", "match_class": "broad", "volume": 10},
        ],
        ["service a", "service b"],
    )
    stamped = stamp_relevance_flags(
        clusters,
        [{"keyword": "shared term", "seed": "service a",
          "relevance_unverified": True, "commercial_target_eligible": False}],
    )
    by_seed = {c["seed"]: c for c in stamped}
    assert by_seed["service a"]["broad"][0]["relevance_unverified"] is True
    assert by_seed["service b"]["broad"][0]["relevance_unverified"] is False


def test_verified_keywords_only_excludes_quarantined_rows():
    """The single filter every downstream consumer in Phase 5 goes through."""
    from app.agents.search_demand import verified_keywords_only

    rows = [
        {"keyword": "verified term"},
        {"keyword": "quarantined term", "relevance_unverified": True},
        {"keyword": "reason-only term", "relevance_reason": "relevance_unverified"},
    ]
    assert [r["keyword"] for r in verified_keywords_only(rows)] == ["verified term"]


def test_verified_keywords_only_ignores_non_dicts():
    from app.agents.search_demand import verified_keywords_only

    assert verified_keywords_only([{"keyword": "ok"}, "junk", None]) == [{"keyword": "ok"}]
