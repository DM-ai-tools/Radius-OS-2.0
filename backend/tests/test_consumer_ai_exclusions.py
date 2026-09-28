"""Consumer AI-tool queries are not B2B marketing-service buyers.

Every keyword below came out of the real Click Trends Phase 5 pool — a B2B
email-marketing and CRO agency in Melbourne — riding on single-token "ai"
overlap with the seed "AI-Powered Email Marketing":

    ai girlfriend (12,100)   ai clothing remover (8,100)   spicy ai (9,900)
    faceswap ai              undetected ai                 ai checkers
    ai music generator       ai photo generator            google gemini ai
    free email services      secure email services

The searcher behind these is a consumer playing with an AI toy, a student
evading a plagiarism checker, or someone looking for a free mailbox. None of
them is buying email-marketing services, at any price, in any market — so the
deterministic gate should reject them without spending an LLM call.

Deliberately NOT blocked here: the client's own territory. "ai email marketing",
"ai marketing automation", "ai for marketers" are the business. Anything
genuinely ambiguous is left to the per-keyword LLM gate.
"""

from __future__ import annotations

import pytest

from app.services.keyword_exclusions import hard_reject_term, reroute_term

ADULT = ["ai girlfriend", "ai boyfriend", "spicy ai", "ai clothing remover", "nsfw ai"]
DETECTION = [
    "ai detection",
    "ai detector",
    "ai checker",
    "ai checkers",
    "undetected ai",
    "originality ai",
    "humanise ai",
    "humanize ai",
    "ai detection tool",
]
GENERATIVE_MEDIA = [
    "ai music generator",
    "ai photo generator",
    "ai picture generator",
    "ai image generator",
    "ai video generator",
    "ai generated images",
    "faceswap ai",
    "ai art generator",
]
CONSUMER_MAILBOX = [
    "free email services",
    "secure email services",
    "free email account",
    "webmail login",
    "email provider",
]

CLIENT_TERRITORY = [
    "ai email marketing",
    "ai marketing automation",
    "email marketing automation",
    "ai powered email marketing",
    "email marketing agency melbourne",
    "conversion rate optimisation audit",
    "ai audience segmentation",
    "automated email journeys",
    "email marketing services for ecommerce",
]


@pytest.mark.parametrize("keyword", ADULT)
def test_adult_ai_toys_are_hard_rejected(keyword):
    assert hard_reject_term(keyword) is not None, keyword


@pytest.mark.parametrize("keyword", DETECTION)
def test_ai_detection_tools_are_hard_rejected(keyword):
    """Students evading detectors, not marketing buyers."""
    assert hard_reject_term(keyword) is not None, keyword


@pytest.mark.parametrize("keyword", GENERATIVE_MEDIA)
def test_consumer_generative_media_is_hard_rejected(keyword):
    assert hard_reject_term(keyword) is not None, keyword


@pytest.mark.parametrize("keyword", CONSUMER_MAILBOX)
def test_consumer_mailbox_queries_are_hard_rejected(keyword):
    """Someone wanting a free inbox is not buying email-marketing services."""
    assert hard_reject_term(keyword) is not None, keyword


@pytest.mark.parametrize("keyword", CLIENT_TERRITORY)
def test_the_clients_own_territory_survives(keyword):
    """The whole point of tightening is to keep this list intact."""
    assert hard_reject_term(keyword) is None, keyword
    assert reroute_term(keyword) is None, keyword


BRAND_AI = [
    "google ai",
    "google gemini ai",
    "meta ai",
    "microsoft ai",
    "deepseek ai",
    "character ai",
    "poly ai",
    "blackbox ai",
    "viggle ai",
    "chatgpt",
]


@pytest.mark.parametrize("keyword", BRAND_AI)
def test_brand_ai_assistants_never_become_commercial_targets(keyword):
    """Third-party AI product names are someone else's brand. Informational at
    best — never a service page for this client."""
    assert (
        hard_reject_term(keyword) is not None or reroute_term(keyword) is not None
    ), keyword


def test_a_bare_ai_token_alone_is_not_enough_to_reject_a_real_service():
    """The rule must key on the consumer-product pattern, not the word "ai"."""
    assert hard_reject_term("ai consulting for b2b") is None
    assert hard_reject_term("ai lead scoring") is None


def test_generator_pattern_does_not_block_legitimate_marketing_tools():
    """A marketing agency does write about these."""
    for keyword in ("email subject line generator", "utm link generator"):
        assert hard_reject_term(keyword) is None, keyword
