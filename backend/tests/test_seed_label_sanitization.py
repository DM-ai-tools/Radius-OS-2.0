"""A seed must be a search term, not a UI label.

From the real Click Trends Phase 5 run, these were sent to DataForSEO as
keywords:

    "how to choose Focus service: AI-Powered Email Marketing & Automation"
    "PRIMARY: AI-Powered Email Marketing - email strategy near me"
    "how to choose automated journeys (welcome"

The form/label prefixes ("Focus service:", "PRIMARY:") were concatenated into
the seed text, and a parenthetical list was cut mid-item. `clean_provider_seed`
stripped the punctuation but kept the label words, so the provider was asked
about "Focus service AI-Powered Email Marketing Automation" — a phrase nobody
searches. Every expansion from such a seed is noise, which is how a B2B
email-marketing agency ended up with "ai girlfriend" in its keyword pool.
"""

from __future__ import annotations

from app.services.keyword_seeding import clean_provider_seed


def test_focus_service_label_is_stripped():
    assert (
        clean_provider_seed("Focus service: AI-Powered Email Marketing & Automation")
        == "AI-Powered Email Marketing Automation"
    )


def test_priority_rank_labels_are_stripped():
    for label in ("PRIMARY", "SECONDARY", "TERTIARY", "Primary", "primary"):
        got = clean_provider_seed(f"{label}: Email Marketing")
        assert got == "Email Marketing", (label, got)


def test_a_label_followed_by_a_dash_is_stripped():
    assert clean_provider_seed("Focus service - Email Marketing") == "Email Marketing"


def test_an_em_dash_qualifier_is_dropped_keeping_the_service():
    """"AI-Powered Email Marketing - email strategy" is two concepts glued
    together; the seed is the service, not the trailing note."""
    assert (
        clean_provider_seed("AI-Powered Email Marketing – email strategy")
        == "AI-Powered Email Marketing"
    )
    assert (
        clean_provider_seed("SEO — technical audit") == "SEO"
    )


def test_a_truncated_parenthetical_is_cut_not_absorbed():
    """An unbalanced "(" means a list was cut mid-item. Dropping the fragment
    is right; absorbing it invents a term the client never named."""
    assert clean_provider_seed("automated journeys (welcome") == "automated journeys"


def test_a_balanced_parenthetical_still_drops_as_before():
    assert (
        clean_provider_seed("Website & Conversion Optimisation (CRO)")
        == "Website Conversion Optimisation"
    )


def test_labels_and_qualifiers_combined():
    assert (
        clean_provider_seed("PRIMARY: AI-Powered Email Marketing – email strategy")
        == "AI-Powered Email Marketing"
    )


def test_a_genuine_seed_is_untouched():
    for seed in ("email marketing automation", "CRO audit", "b2b lead generation"):
        assert clean_provider_seed(seed) == seed


def test_a_label_only_seed_does_not_become_empty_garbage():
    """Nothing usable left — return empty so the caller skips it rather than
    querying the provider for the word "primary"."""
    assert clean_provider_seed("PRIMARY:") in ("", "PRIMARY:")
    assert clean_provider_seed("Focus service:") in ("", "Focus service:")


def test_a_service_word_inside_a_real_seed_is_not_stripped():
    """Only a leading label is a label. "email marketing services" keeps it."""
    assert clean_provider_seed("email marketing services") == "email marketing services"
    assert clean_provider_seed("service design consultancy") == "service design consultancy"
