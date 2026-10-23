"""A platform word alone ("google") must not file a seed under a paid-ads service.

Found in the Click Trends service clusters: "Google Business Profile optimization",
a Tag Manager post and a "google search results" post were all grouped under
"Google Ads Management" because the bare word "google" counted as evidence for it,
and alias expansion (google -> adwords, gads, ...) multiplied that one shared word
into seven matching tokens, outscoring a seed that really contained "seo".
"""

import pytest

from app.services.keyword_clustering import build_service_seed_clusters

from tests.test_service_keyword_clustering import _seed_cluster

SERVICES = ["Google Ads Management", "SEO", "Meta Ads"]


def _where(seed: str, *, target_type: str = "keyword", services=SERVICES) -> str:
    groups = build_service_seed_clusters(
        [_seed_cluster(seed, target_type=target_type)], services
    )
    for group in groups:
        if any(s["seed"] == seed for s in group["seeds"]):
            return group["service"]
    raise AssertionError(f"{seed!r} was not assigned anywhere")


@pytest.mark.parametrize(
    "seed",
    [
        "Google Business Profile optimization",
        "Google Business Profile optimization Melbourne",
        "twitter-can-get-you-to-the-top-of-google-search-results",
        "creating-facebook-group-account-successful-marketing-engagement",
    ],
)
def test_platform_word_alone_does_not_pick_a_paid_ads_service(seed):
    assert _where(seed) == "Other website topics"


def test_tag_manager_seo_post_goes_to_seo_not_google_ads():
    assert _where("how-to-use-google-tag-manager-for-better-seo") == "SEO"


@pytest.mark.parametrize(
    ("seed", "service"),
    [
        ("google ads cost melbourne", "Google Ads Management"),
        ("adwords agency", "Google Ads Management"),
        ("google ppc consultant", "Google Ads Management"),
        ("facebook ads melbourne", "Meta Ads"),
        ("instagram advertising agency", "Meta Ads"),
    ],
)
def test_paid_intent_still_reaches_the_paid_ads_service(seed, service):
    assert _where(seed) == service


def test_explicit_service_target_is_unaffected():
    assert _where("Google Ads Management", target_type="service") == "Google Ads Management"


def test_one_shared_word_is_not_counted_once_per_alias():
    # Both tokens match once each; the SEO service must not lose to an alias count.
    assert _where("seo for google rankings") == "SEO"


AEO = "AEO & GEO Services (AI/answer engine optimization)"


@pytest.mark.parametrize(
    "seed",
    [
        "Google Business Profile optimization",
        "image optimization tips",
        "page speed optimisation",
    ],
)
def test_the_word_optimization_alone_is_not_evidence_for_an_optimization_service(seed):
    services = ["Google Ads Management", "SEO", AEO]
    assert _where(seed, services=services) == "Other website topics"


def test_a_real_aeo_seed_still_reaches_the_aeo_service():
    services = ["Google Ads Management", "SEO", AEO]
    assert _where("answer engine optimization agency", services=services) == AEO
    assert _where("AEO", services=services) == AEO
