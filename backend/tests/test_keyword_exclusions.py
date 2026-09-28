"""Two-tier keyword exclusions: hard reject vs informational reroute."""

from __future__ import annotations

import pytest

from app.services.keyword_exclusions import (
    hard_reject_term,
    has_commercial_signal,
    industry_slug,
    is_commercially_targetable,
    reroute_term,
)


# --- tier 1: hard reject (not a customer at all) ---------------------------


@pytest.mark.parametrize(
    "keyword",
    [
        "free dental jobs",
        "dental assistant salary",
        "dentist salaries australia",
        "dental hygienist careers",
        "dental career path",
        "bakery hiring now",
        "pastry chef internship",
        "dental nurse vacancy",
        "dental assistant resume",
        "patient portal login",
        "dentist sign in",
        "dentistry wikipedia",
        "dental wiki",
    ],
)
def test_non_customer_terms_are_hard_rejected(keyword):
    assert hard_reject_term(keyword) is not None


@pytest.mark.parametrize(
    "keyword",
    [
        "emergency dentist near me",
        "teeth whitening cost",
        "sourdough bread delivery",
        "custom birthday cake",
    ],
)
def test_buyer_keywords_are_not_hard_rejected(keyword):
    assert hard_reject_term(keyword) is None


def test_hard_reject_matches_whole_tokens_only():
    # "jobs" must not fire inside an unrelated word
    assert hard_reject_term("jobsite safety flooring") is None
    assert hard_reject_term("careerbuilder alternatives") is None
    assert hard_reject_term("resumes writing for dentists") is not None  # "resumes"


def test_hard_reject_matches_multi_word_phrases():
    assert hard_reject_term("dentist sign in portal") == "sign in"


def test_hard_reject_returns_the_matched_term():
    assert hard_reject_term("free dental jobs") == "jobs"


# --- tier 2: informational reroute (real person, wrong intent) -------------


@pytest.mark.parametrize(
    "keyword",
    [
        "diy teeth whitening",
        "how to whiten teeth",
        "bread recipe",
        "sourdough recipes",
        "homemade pastry",
        "how to bake sourdough at home",
        "dental nursing course",
        "barista training courses",
        "food safety certification",
        "what is a dental implant",
        "veneer definition",
        "occlusion meaning",
    ],
)
def test_informational_terms_are_rerouted(keyword):
    assert reroute_term(keyword) is not None
    assert is_commercially_targetable(keyword) is False


def test_diy_does_not_match_inside_another_word():
    assert reroute_term("diyanet catering") is None


def test_reviews_reroute_only_when_paired_with_a_product_noun():
    assert reroute_term("bread maker machine reviews") is not None
    assert reroute_term("stand mixer reviews") is not None
    # A review of the client's own service is a legitimate commercial query.
    assert reroute_term("teeth whitening reviews") is None
    assert reroute_term("best dentist reviews") is None


def test_client_service_terms_are_never_rerouted_by_reviews():
    assert (
        reroute_term("bread maker reviews", service_terms=["bread maker"]) is None
    )


@pytest.mark.parametrize(
    "keyword",
    [
        "emergency dentist near me",
        "teeth whitening cost",
        "order pastries online",
        "dental implants price",
    ],
)
def test_buyer_keywords_stay_commercially_targetable(keyword):
    assert is_commercially_targetable(keyword) is True


# --- "free" is per-industry, never global ---------------------------------


def test_free_is_a_reroute_signal_for_dental_and_bakery():
    assert reroute_term("free teeth whitening", industry="Dental") is not None
    assert reroute_term("free bread samples", industry="Bakery") is not None


def test_free_is_allowed_for_saas():
    assert reroute_term("free trial project management", industry="SaaS") is None


def test_free_is_not_rerouted_for_an_unknown_industry():
    """Unknown industry falls back to universal lists only."""
    assert reroute_term("free samples", industry="Llama Grooming") is None
    assert reroute_term("free samples") is None


@pytest.mark.parametrize("industry", ["Dental", "Bakery", "SaaS", None, "Unknown Co"])
def test_free_consultation_is_allowed_everywhere(industry):
    assert reroute_term("free consultation", industry=industry) is None
    assert is_commercially_targetable("free consultation", industry=industry) is True


def test_industry_slug_derivation():
    assert industry_slug("Dental") == "dental"
    assert industry_slug("Dentistry & Orthodontics") == "dental"
    assert industry_slug("Artisan Bakery") == "bakery"
    assert industry_slug("B2B SaaS") == "saas"
    assert industry_slug("Llama Grooming") is None
    assert industry_slug(None) is None


# --- commercial signal (second gate signal) -------------------------------


@pytest.mark.parametrize(
    "keyword",
    [
        "dentist open saturday",
        "teeth whitening cost",
        "dental implants price",
        "emergency dentist near me",
        "book a dentist appointment",
        "order pastries online",
        "sourdough bread delivery",
        "buy custom cake",
    ],
)
def test_commercial_signals_are_detected(keyword):
    assert has_commercial_signal(keyword) is True


@pytest.mark.parametrize(
    "keyword",
    ["bread recipe", "dental school admission", "history of bread"],
)
def test_non_commercial_keywords_have_no_signal(keyword):
    assert has_commercial_signal(keyword) is False
