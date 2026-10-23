"""Competitor tier labels are classification output, not market language.

ARG Finance's service clusters contained a "Strong Direct Competitors" seed with
10 keywords: the tier model's label, harvested as a keyword seed.
"""

from app.agents.search_demand import _competitor_theme_seeds


def _none_are_brands(_term: str) -> bool:
    return False


def test_tier_labels_are_never_harvested_as_seeds():
    competitive = {
        "tier_overview": [
            {"tier_name": "Strong Direct Competitors"},
            {"tier_name": "Emerging Challengers"},
            {"tier_name": "Local/Similar Competitors"},
        ],
        "scorecards": [{"tier_name": "Strong Direct Competitors", "name": "Aussie"}],
    }
    assert _competitor_theme_seeds(competitive, seen=set(), is_brand=_none_are_brands) == []


def test_positioning_and_notes_are_still_harvested():
    competitive = {
        "scorecards": [
            {"positioning": "specialist SMSF lending broker", "notes": "strong refinance content"},
        ]
    }
    assert _competitor_theme_seeds(competitive, seen=set(), is_brand=_none_are_brands) == [
        "specialist SMSF lending broker",
        "strong refinance content",
    ]


def test_brand_stale_year_and_already_seen_phrases_are_skipped():
    competitive = {
        "competitors": [
            {"positioning": "Aussie Home Loans"},
            {"positioning": "best loans 2019"},
            {"positioning": "Specialist broker"},
            {"positioning": "ab"},
        ]
    }
    out = _competitor_theme_seeds(
        competitive,
        seen={"specialist broker"},
        is_brand=lambda t: "aussie" in t.lower(),
    )
    assert out == []


def test_phrases_are_capped_at_six_words_and_deduped_across_rows():
    competitive = {
        "scorecards": [
            {"positioning": "one two three four five six seven eight"},
            {"positioning": "one two three four five six"},
        ]
    }
    assert _competitor_theme_seeds(competitive, seen=set(), is_brand=_none_are_brands) == [
        "one two three four five six"
    ]
