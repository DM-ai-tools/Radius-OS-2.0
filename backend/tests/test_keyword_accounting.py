"""Every keyword is either clustered or explicitly excluded — with a reason.

Guards the audit trail: keywords must not disappear between the cleaned set and
the cluster report, and orphans must still carry intent/funnel.
"""

from __future__ import annotations

from app.services.keyword_clustering import (
    build_clusters_deterministic,
    build_keyword_accounting,
)


def _rows(pairs):
    return [{"keyword": kw, "volume": vol} for kw, vol in pairs]


def test_every_clustered_keyword_is_accounted_for():
    rows = _rows(
        [
            ("crm pricing", 4000),
            ("crm pricing plans", 900),
            ("crm pricing comparison", 600),
            ("crm pricing tiers", 300),
            ("email marketing software", 2500),
            ("email marketing software free", 800),
            ("email marketing software comparison", 500),
            ("obscure one off term", 10),
        ]
    )
    report = build_clusters_deterministic(rows, brand_name="Acme", domain="acme.io")
    accounting = report["keyword_accounting"]

    assert accounting["input_count"] == len(rows)
    assert accounting["accounted_count"] == len(rows)
    assert accounting["unaccounted_count"] == 0
    assert accounting["balanced"] is True


def test_orphans_keep_their_classification_and_an_exclusion_reason():
    rows = _rows([("totally unrelated singleton phrase", 5)])
    report = build_clusters_deterministic(rows)

    assert report["orphan_count"] == 1
    orphan = report["orphans"][0]
    assert orphan["intent"]
    assert orphan["funnel"] in ("TOFU", "MOFU", "BOFU")
    assert orphan["exclusion_reason"] == "single_low_value_keyword"
    assert report["keyword_accounting"]["orphan_reasons"]["single_low_value_keyword"] == 1


def test_cluster_keyword_count_is_true_membership_not_the_display_cap():
    """Reporting the capped row count made keywords vanish from the audit."""
    rows = _rows([(f"crm pricing option {i}", 100 + i) for i in range(60)])
    report = build_clusters_deterministic(rows)

    biggest = max(report["clusters"], key=lambda c: c["keyword_count"])
    assert biggest["keyword_count"] > biggest["keywords_shown"]
    assert biggest["keywords_truncated"] == biggest["keyword_count"] - biggest["keywords_shown"]
    assert report["keyword_accounting"]["balanced"] is True


def test_every_cluster_keyword_row_has_intent_and_funnel():
    rows = _rows(
        [
            ("what is crm software", 900),
            ("crm software pricing", 700),
            ("best crm software", 1200),
            ("crm software reviews", 400),
        ]
    )
    report = build_clusters_deterministic(rows)
    for cluster in report["clusters"]:
        assert cluster["intent"]
        assert cluster["funnel"] in ("TOFU", "MOFU", "BOFU")
        for row in cluster["keywords"]:
            assert row["intent"]
            assert row["funnel"] in ("TOFU", "MOFU", "BOFU")


def test_accounting_reports_an_imbalance_rather_than_hiding_it():
    unbalanced = build_keyword_accounting(
        [{"keyword_count": 2, "keywords": []}], [], input_count=5
    )
    assert unbalanced["balanced"] is False
    assert unbalanced["unaccounted_count"] == 3
