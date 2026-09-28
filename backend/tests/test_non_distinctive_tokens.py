"""One ultra-generic token is not evidence of relevance.

Click Trends' service catalog contains "AI-Powered SEO" and "AI-Powered Email
Marketing & Automation", which made **"ai"** a distinctive service token. Every
keyword sharing that one token then satisfied the second-signal rule, so the
deterministic gate kept 159 of 199 rows including:

    google ai (201,000)   ai mode (135,000)   ai assist (27,100)
    ask ai                ai overview         ai bubble
    our dream ai          use ai              ai news

"ai" is three characters shared with an entire consumer product category. It
cannot be the only thing linking a keyword to this business — a real match needs
a second signal (the service head noun, a commercial modifier, or service-seed
lineage), exactly as for any other weak token.

The same applies to "app", "tool", "online", "digital", "platform": common
service-name filler, useless as a lone relevance signal.
"""

from __future__ import annotations

import pytest

from app.services.keyword_relevance import build_relevance_context

SERVICES = [
    "AI-Powered SEO",
    "Google Ads/PPC Management",
    "Website & Conversion Optimisation (CRO)",
    "AI-Powered Email Marketing & Automation",
]


def _ctx():
    return build_relevance_context(
        services=SERVICES,
        cdd_keywords=["email marketing automation", "conversion rate optimisation"],
        page_terms=[],
        themes=[],
        seeds=SERVICES,
        competitor_names=["WebFX", "King Kong"],
        competitor_domains=["webfx.com", "kingkong.co"],
        brand_name="Click Trends",
        domain="clicktrends.com.au",
    )


def test_ai_is_not_a_distinctive_token():
    assert "ai" not in _ctx().distinctive_tokens


@pytest.mark.parametrize(
    "token", ["ai", "app", "apps", "tool", "tools", "online", "digital", "platform", "software"]
)
def test_ultra_generic_service_filler_is_never_distinctive(token):
    ctx = build_relevance_context(
        services=[f"{token} services", "Email Marketing"],
        cdd_keywords=[],
        page_terms=[],
        themes=[],
        seeds=[f"{token} services"],
        competitor_names=[],
        competitor_domains=[],
        brand_name="Acme",
        domain="acme.com",
    )
    assert token not in ctx.distinctive_tokens


def test_real_service_words_stay_distinctive():
    """The fix must not blunt genuine signals."""
    distinctive = _ctx().distinctive_tokens
    for token in ("email", "marketing", "conversion", "seo", "automation"):
        assert token in distinctive, token


@pytest.mark.parametrize(
    "keyword",
    [
        "google ai",
        "ai mode",
        "ai assist",
        "ask ai",
        "ai overview",
        "ai bubble",
        "our dream ai",
        "use ai",
        "ai news",
        "ai agent",
        "free ai",
        "best ai",
        "ai tools",
    ],
)
def test_a_bare_ai_query_no_longer_counts_as_service_overlap(keyword):
    """These are the real survivors from the Click Trends pool."""
    assert not _ctx()._token_hit(keyword), keyword


@pytest.mark.parametrize(
    "keyword",
    [
        "ai email marketing",
        "ai marketing automation",
        "email marketing automation",
        "conversion rate optimisation",
        "ai powered seo agency",
    ],
)
def test_genuine_service_keywords_still_match(keyword):
    assert _ctx()._token_hit(keyword), keyword


def test_a_label_polluted_service_name_does_not_add_label_tokens():
    """"Focus service: X" reached the catalog verbatim on the real run. The
    label words must not become relevance evidence."""
    ctx = build_relevance_context(
        services=["Focus service: AI-Powered Email Marketing & Automation"],
        cdd_keywords=[],
        page_terms=[],
        themes=[],
        seeds=["Focus service: AI-Powered Email Marketing & Automation"],
        competitor_names=[],
        competitor_domains=[],
        brand_name="Click Trends",
        domain="clicktrends.com.au",
    )
    assert "focus" not in ctx.distinctive_tokens
    assert "service" not in ctx.distinctive_tokens
    assert "email" in ctx.distinctive_tokens


# --- business-entity nouns are not service identity -----------------------
#
# Found in a real Click Trends run: "fujifilm company" was the #1 head term at
# 11,105/mo for a Melbourne digital-marketing agency. It passed because the CDD
# keywords include "email marketing company" and "email marketing agency", which
# made "company" and "agency" distinctive tokens — so any "<brand> company"
# query matched on one word. Same failure shape as "ai".

CLICKTRENDS_SERVICES = [
    "Website Conversion Optimisation",
    "AI-Powered Email Marketing Automation",
    "Meta Ads",
    "AEO",
    "GEO",
]
CLICKTRENDS_CDD = [
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


def _clicktrends_ctx():
    return build_relevance_context(
        services=CLICKTRENDS_SERVICES,
        cdd_keywords=CLICKTRENDS_CDD,
        page_terms=[],
        themes=[],
        seeds=CLICKTRENDS_SERVICES,
        competitor_names=["WebFX", "King Kong"],
        competitor_domains=["webfx.com", "kingkong.co"],
        brand_name="Click Trends",
        domain="clicktrends.com.au",
    )


@pytest.mark.parametrize(
    "token",
    ["company", "companies", "agency", "agencies", "consultant", "consultants",
     "consultancy", "firm", "specialist", "specialists", "expert", "experts",
     "provider", "providers", "freelancer"],
)
def test_business_entity_nouns_are_never_distinctive(token):
    ctx = build_relevance_context(
        services=[f"Email Marketing {token}"],
        cdd_keywords=[f"email marketing {token}"],
        page_terms=[], themes=[], seeds=[f"Email Marketing {token}"],
        competitor_names=[], competitor_domains=[],
        brand_name="Acme", domain="acme.com",
    )
    assert token not in ctx.distinctive_tokens


def test_the_real_fujifilm_company_case_no_longer_matches():
    """The exact keyword that topped a real client's head terms."""
    assert not _clicktrends_ctx()._token_hit("fujifilm company")


@pytest.mark.parametrize(
    "keyword",
    ["fujifilm company", "toyota company", "apple agency", "bunnings company"],
)
def test_an_unrelated_brand_plus_a_business_noun_does_not_match(keyword):
    assert not _clicktrends_ctx()._token_hit(keyword)


@pytest.mark.parametrize(
    "keyword",
    [
        "email marketing agency",
        "email marketing company",
        "email marketing agency melbourne",
        "conversion optimisation services",
        "meta ads agency",
        "aeo australia",
        "email deliverability services",
    ],
)
def test_the_clients_own_keywords_still_match(keyword):
    """The business nouns become harmless, not harmful — the real service words
    in these keywords still carry them."""
    assert _clicktrends_ctx()._token_hit(keyword), keyword


# --- the two regressions the business-noun change exposed -----------------
#
# Making "agency"/"company" non-distinctive correctly killed "fujifilm company",
# but it also dropped two keywords the client genuinely wants, because each was
# left with only ONE distinctive token:
#
#   "facebook ads agency"            facebook=platform alias, ads=distinctive
#   "conversion optimization agency" American spelling of the CDD's "optimisation"
#
# Fixes: a platform alias of the CLIENT'S OWN family counts as distinctive, and
# -ise/-ize spellings normalise to one root.


def test_american_and_british_spellings_match():
    ctx = _clicktrends_ctx()
    assert ctx._token_hit("conversion optimization services"), "American spelling"
    assert ctx._token_hit("conversion optimisation services"), "British spelling"


def test_an_ize_spelling_variant_counts_as_the_same_distinctive_token():
    ctx = _clicktrends_ctx()
    # "optimisation" is in the CDD; "optimization" must resolve to the same root.
    assert "optimisation" in ctx.distinctive_tokens or "optimis" in " ".join(
        ctx.distinctive_tokens
    )
    assert ctx._token_hit("optimization agency")


def test_a_platform_alias_of_the_clients_own_family_is_distinctive():
    """The client sells "Meta Ads"; Facebook Ads is the same product."""
    ctx = _clicktrends_ctx()
    assert ctx._token_hit("facebook ads agency")
    assert ctx._token_hit("instagram ads agency")


def test_a_platform_alias_of_a_family_the_client_does_not_sell_stays_generic():
    """Client sells Meta Ads, not Google Ads — "google" must not become a signal."""
    ctx = _clicktrends_ctx()
    assert not ctx._token_hit("google company")
    assert not ctx._token_hit("linkedin company")


def test_the_two_regressed_keywords_are_kept_again():
    from app.services.keyword_relevance import filter_relevant_keywords

    ctx = _clicktrends_ctx()
    rows = [
        {"keyword": k, "volume": 100, "intent": "commercial"}
        for k in (
            "facebook ads agency",
            "conversion optimization agency",
            "meta ads agency",
            "fujifilm company",
            "seo company",
        )
    ]
    kept, _dropped, _audit = filter_relevant_keywords(rows, ctx, source_label="t")
    kept_kw = {r["keyword"] for r in kept}
    assert "facebook ads agency" in kept_kw
    assert "conversion optimization agency" in kept_kw
    assert "meta ads agency" in kept_kw
    # ...and the junk still dies.
    assert "fujifilm company" not in kept_kw
    assert "seo company" not in kept_kw
