"""Priority must rank issues, not saturate.

`priority = severity_weight × impact × (1 + log10(1 + urls))` overshoots its own
0-100 cap for 56% of severity×category×url-count combinations. Once two issues
both clamp to 100, severity stops discriminating and the sort falls through to
raw affected-URL count — so a Medium "duplicate title" on 10 URLs outranks a
High "4xx page" on 5, which is backwards for an audit whose whole job is to say
what to fix first. Real site audits report hundreds of URLs per issue, so
saturation is the normal case, not an edge case.

The contract: severity decides the band, and impact/URL count only order issues
*within* a band.
"""

from __future__ import annotations

import math

from app.services.technical_seo_prioritization import compute_priority
from app.services.technical_seo_rules import issues_from_ahrefs
from app.services.technical_seo_schemas import CATEGORY_IMPACT, SEVERITY_WEIGHTS

SEVERITIES = ["Critical", "High", "Medium", "Low", "Info"]


def test_a_higher_severity_always_outranks_a_lower_one():
    """Even when the lower-severity issue has vastly more URLs and more impact."""
    for better, worse in zip(SEVERITIES, SEVERITIES[1:]):
        strong = compute_priority(severity=better, category="Content", affected_url_count=1)
        weak = compute_priority(severity=worse, category="Indexability", affected_url_count=100000)
        assert strong > weak, f"{better}(1 url) should outrank {worse}(100000 urls)"


def test_more_affected_urls_ranks_higher_within_a_severity():
    few = compute_priority(severity="High", category="Metadata", affected_url_count=2)
    many = compute_priority(severity="High", category="Metadata", affected_url_count=900)
    assert many > few


def test_higher_category_impact_ranks_higher_within_a_severity():
    low = compute_priority(severity="High", category="Content", affected_url_count=10)
    high = compute_priority(severity="High", category="Indexability", affected_url_count=10)
    assert high > low


def test_priority_stays_inside_the_documented_range():
    for sev in SEVERITY_WEIGHTS:
        for cat in CATEGORY_IMPACT:
            for n in (0, 1, 10, 100, 1000, 100000):
                p = compute_priority(severity=sev, category=cat, affected_url_count=n)
                assert 1 <= p <= 100, (sev, cat, n, p)


def test_priorities_do_not_all_collapse_onto_the_cap():
    """The old formula pinned 56% of combinations to exactly 100."""
    values = [
        compute_priority(severity=sev, category=cat, affected_url_count=n)
        for sev in SEVERITY_WEIGHTS
        for cat in CATEGORY_IMPACT
        for n in (1, 5, 10, 50, 100, 500, 1000)
    ]
    at_cap = sum(1 for v in values if v == 100)
    assert at_cap / len(values) < 0.05, f"{at_cap}/{len(values)} pinned at the cap"


def test_an_unknown_severity_does_not_outrank_a_known_one():
    unknown = compute_priority(severity="Bogus", category="Content", affected_url_count=10)
    critical = compute_priority(severity="Critical", category="Content", affected_url_count=10)
    assert unknown < critical


def test_zero_affected_urls_is_still_ranked():
    assert compute_priority(severity="High", category="Content", affected_url_count=0) >= 1


def test_ahrefs_error_issue_is_ordered_above_a_warning_with_more_urls():
    """The reported case: a 4xx Error on 5 URLs must lead a Warning on 10."""
    issues = issues_from_ahrefs(
        [
            {
                "issue_id": "broken_internal",
                "name": "4xx page",
                "category": "Internal pages",
                "importance": "Error",
                "crawled": 5,
            },
            {
                "issue_id": "dup_title",
                "name": "Duplicate title",
                "category": "Duplicates",
                "importance": "Warning",
                "crawled": 10,
            },
        ]
    )
    assert [i.rule_id for i in issues] == ["AHREFS_broken_internal", "AHREFS_dup_title"]
    assert issues[0].affected_url_count == 5


def test_url_count_still_breaks_ties_between_equal_issues():
    issues = issues_from_ahrefs(
        [
            {"issue_id": "a", "name": "Duplicate title", "category": "Duplicates",
             "importance": "Warning", "crawled": 3},
            {"issue_id": "b", "name": "Duplicate title", "category": "Duplicates",
             "importance": "Warning", "crawled": 300},
        ]
    )
    assert [i.rule_id for i in issues] == ["AHREFS_b", "AHREFS_a"]


def test_the_formula_is_monotonic_in_url_count():
    prev = -1
    for n in (0, 1, 2, 5, 10, 100, 1000, 10000):
        p = compute_priority(severity="Medium", category="Performance", affected_url_count=n)
        assert p >= prev, f"priority dropped at {n} urls"
        prev = p


def test_negative_url_counts_are_treated_as_zero():
    assert compute_priority(
        severity="High", category="Content", affected_url_count=-5
    ) == compute_priority(severity="High", category="Content", affected_url_count=0)


def _unused_math_reference() -> float:  # pragma: no cover - documents the old formula
    return 100 * 1.3 * (1 + math.log10(1 + 1000))
