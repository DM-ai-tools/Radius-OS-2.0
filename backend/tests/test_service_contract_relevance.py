"""Click Trends failure case: a shared word is not service relevance."""

from app.services.bw_workbook import _excel_priority
from app.services.keyword_relevance import build_relevance_context, evaluate_keyword
from app.services.keyword_service_contract import CLASSIFIER_VERSION, judge_keyword

EMAIL = "AI-Powered Email Marketing & Automation"
SEO = "AI-Powered SEO"
PPC = "Google Ads/PPC Management"
CRO = "Website & Conversion Optimisation"
LOCAL = "Local SEO Melbourne"

EMAIL_CDD = [
    "email marketing agency",
    "email marketing agency Melbourne",
    "email marketing services Australia",
    "email marketing company",
    "email automation agency",
    "AI email marketing",
    "ecommerce email marketing",
    "email deliverability services",
    "email nurture sequences",
]

REJECT = [
    "character ai",
    "google ai",
    "ai mode",
    "humanize ai",
    "ai image generator",
    "google ai studio",
    "ai humanizer",
    "google gemini ai",
    "disposable temporary email",
    "gmail email",
    "poly ai",
    "temp email",
    "our dream ai",
    "rmit email",
    "ai image editor",
    "hotmail email",
    "telstra email",
    "ai generator",
    "ai photo generator",
    "ask ai",
    "free ai image generator",
    "humanise ai",
    "microsoft ai",
]

KEEP = [
    "email marketing agency",
    "email marketing agency Melbourne",
    "email marketing services Australia",
    "email marketing company",
    "email automation agency",
    "AI email marketing",
    "ecommerce email marketing",
    "email deliverability services",
    "email nurture sequences",
    "email marketing strategy",
    "automated email marketing",
    "email campaign management",
    "email personalization services",
    "email marketing consultant",
]

REVIEW = ["email services", "email platform", "AI assistant", "marketing automation"]


def test_email_false_positives_are_rejected():
    for keyword in REJECT:
        verdict = judge_keyword(keyword, service=EMAIL, cdd_keywords=EMAIL_CDD, geography="Australia-wide")
        assert verdict.decision == "DROP", keyword
        assert verdict.classifier_version == CLASSIFIER_VERSION


def test_email_service_queries_are_kept():
    for keyword in KEEP:
        verdict = judge_keyword(keyword, service=EMAIL, cdd_keywords=EMAIL_CDD, geography="Australia-wide")
        assert verdict.decision == "KEEP", keyword


def test_ambiguous_email_terms_are_review_not_keep():
    for keyword in REVIEW:
        verdict = judge_keyword(keyword, service=EMAIL, cdd_keywords=EMAIL_CDD)
        assert verdict.decision == "REVIEW", keyword


def test_australia_wide_allows_brisbane_and_rejects_london():
    brisbane = judge_keyword(
        "email marketing agency Brisbane",
        service=EMAIL,
        geography="Australia-wide",
    )
    london = judge_keyword(
        "email marketing agency London",
        service=EMAIL,
        geography="Australia-wide",
    )
    assert brisbane.decision == "KEEP"
    assert london.decision == "DROP"
    assert london.reason == "geo_mismatch"


def test_melbourne_only_rejects_another_australian_city():
    verdict = judge_keyword(
        "email marketing agency Brisbane",
        service=EMAIL,
        geography="Melbourne",
    )
    assert verdict.decision == "DROP"
    assert verdict.reason == "geo_mismatch"


def test_commercial_and_informational_email_queries_both_match_the_service():
    commercial = judge_keyword("email marketing pricing", service=EMAIL)
    informational = judge_keyword("email marketing strategy", service=EMAIL)
    assert commercial.decision == "KEEP"
    assert informational.decision == "KEEP"
    assert commercial.intent_match > informational.intent_match


def test_email_seed_cannot_keep_a_generic_ai_product():
    ctx = build_relevance_context(
        services=[EMAIL, SEO, PPC],
        cdd_keywords=EMAIL_CDD,
        seeds=[EMAIL],
        geography="Australia-wide",
    )
    keep, reason, _note = evaluate_keyword(
        "character ai",
        ctx,
        match_class="related",
        seed=EMAIL,
    )
    assert keep is False
    assert reason in ("negative_concept", "entity_mismatch", "service_mismatch")


def test_cross_service_google_ads_term_does_not_survive_an_email_seed():
    verdict = judge_keyword("google ads agency", service=EMAIL, cdd_keywords=EMAIL_CDD)
    assert verdict.decision == "DROP"
    kept = judge_keyword("google ads agency", service=PPC)
    assert kept.decision == "KEEP"


def test_other_click_trends_services_reject_generic_ai_products():
    for service in (SEO, PPC, CRO, LOCAL):
        assert judge_keyword("character ai", service=service).decision == "DROP"
        assert judge_keyword("ai image generator", service=service).decision == "DROP"


def test_other_services_keep_their_own_queries():
    assert judge_keyword("technical seo audit", service=SEO).decision == "KEEP"
    assert judge_keyword("google ads management", service=PPC).decision == "KEEP"
    assert judge_keyword("conversion rate optimisation", service=CRO).decision == "KEEP"
    assert judge_keyword("local seo melbourne", service=LOCAL).decision == "KEEP"
    assert judge_keyword("website design", service=CRO).decision == "REVIEW"


def test_local_seo_city_does_not_keep_tourist_attractions():
    """The client's city is where they sell, not what the searcher wants."""
    for keyword in (
        "melbourne zoo",
        "melbourne museum",
        "luna park melbourne",
        "royal botanic gardens melbourne",
        "sydney olympic park",
    ):
        assert judge_keyword(keyword, service=LOCAL).decision == "DROP", keyword

    ctx = build_relevance_context(
        services=[LOCAL, SEO, PPC, CRO],
        seeds=[LOCAL],
        geography="Melbourne, Victoria, Australia",
        industry="Digital Marketing Agency",
    )
    keep, reason, _note = evaluate_keyword(
        "melbourne zoo",
        ctx,
        match_class="broad",
        seed=LOCAL,
    )
    assert keep is False
    assert reason == "service_mismatch"


def test_volume_cannot_make_a_rejected_keyword_p0():
    assert _excel_priority(volume=50000, relevance_reason="negative_concept") == "P3"
    assert _excel_priority(volume=50000, relevance_reason="service_review") == "P2"
    assert _excel_priority(volume=50000) == "P0"


def test_unknown_service_is_not_forced_through_the_email_contract():
    verdict = judge_keyword("teeth whitening cost", service="Teeth Whitening")
    assert verdict.decision == "SKIP"
