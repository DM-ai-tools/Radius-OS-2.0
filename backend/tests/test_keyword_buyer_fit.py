"""Keyword cleansing on business potential (2026-10-22).

Real keywords from the stored Click Trends run (Australian digital marketing
agency: SEO, PPC, website development, email marketing).
"""

from __future__ import annotations

import pytest

from app.services.keyword_exclusions import BUYER_FIT_REASONS, buyer_fit_reject
from app.services.keyword_opportunity import detect_funnel, detect_intent, funnel_with_evidence
from app.services.keyword_relevance import build_relevance_context, evaluate_keyword
from app.services.keyword_service_contract import judge_keyword
from app.services.keyword_variants import collapse_close_variants

AGENCY = {
    "industry": "digital marketing agency",
    "services": ["SEO", "PPC Management", "Website Development", "Email Marketing"],
    "geography": "Australia-wide (HQ Melbourne, VIC); priority metros: Melbourne, Sydney, Brisbane, Perth",
}


@pytest.mark.parametrize(
    "keyword,reason",
    [
        ("cek seo website", "foreign_language"),
        ("check seo website", "tool_seeker"),
        ("check site seo", "tool_seeker"),
        ("ai search visibility checker", "tool_seeker"),
        ("best email marketing software", "tool_seeker"),
        ("google search console seo tool", "tool_seeker"),
        ("free website builder", "tool_seeker"),
        ("best email marketing free", "tool_seeker"),
        ("email marketing templates for outlook", "learning_resource"),
        ("best email marketing examples", "learning_resource"),
        ("google ads search certification", "learning_resource"),
        ("seo meaning", "learning_resource"),
        ("advanced search on google", "consumer_search"),
        ("private search engine", "consumer_search"),
        ("ai-powered search engine", "consumer_search"),
        ("google search in website", "consumer_search"),
        ("google ads", "platform_navigation"),
        ("google keyword planner", "platform_navigation"),
        ("google ads manager", "platform_navigation"),
        ("wordpress website", "platform_navigation"),
        ("google lens", "consumer_platform"),
        ("easter egg google", "consumer_platform"),
        ("minesweeper google", "consumer_platform"),
        ("google doc", "consumer_platform"),
        ("google color picker", "consumer_platform"),
        ("google in russian", "consumer_platform"),
        ("google marketing platform", "consumer_platform"),
    ],
)
def test_searchers_who_cannot_become_clients_are_dropped(keyword, reason):
    assert buyer_fit_reject(keyword, **AGENCY)[0] == reason


@pytest.mark.parametrize(
    "keyword",
    [
        "seo agency melbourne",
        "google ads management",
        "adwords management melbourne",
        "search engine optimisation perth",
        "best search engine marketing agency australia",
        "ai search engine marketing",
        "software development company",
        "wordpress plugin development",
        "shopify developer",
        "web app development",
        "melbourne website builders",
        "web development price calculator",
        "free seo audit",
        "free quote seo",
        "how much does seo cost",
        "how to improve google ranking",
        "what is seo",
        "google business profile optimisation",
        "a/b testing in email marketing",
        "email automation",
    ],
)
def test_buyer_and_pitchable_queries_are_kept(keyword):
    assert buyer_fit_reject(keyword, **AGENCY) is None


def test_a_software_business_keeps_tool_searches_and_a_school_keeps_courses():
    assert buyer_fit_reject("best email marketing software", industry="SaaS email platform", services=["Email Marketing Software"]) is None
    assert buyer_fit_reject("seo course", industry="digital marketing academy", services=["SEO Training"]) is None


def test_foreign_words_are_only_rejected_in_english_markets():
    assert buyer_fit_reject("jasa seo", geography="Australia")[0] == "foreign_language"
    assert buyer_fit_reject("jasa seo", geography="Jakarta, Indonesia") is None


def test_celebrity_names_tool_brands_and_site_tests_are_not_buyers():
    ctx = build_relevance_context(
        services=AGENCY["services"],
        seeds=["SEO"],
        industry=AGENCY["industry"],
        geography=AGENCY["geography"],
    )
    for keyword in (
        "tv shows with seo kang joon",
        "tv shows with park seo joon",
        "tv shows with seo in guk",
        "what i s seo",
        "screaming frog seo spider",
        "screaming spider seo",
        "seo web test",
        "seo website testing",
        "test website for seo",
        "google seo words",
    ):
        keep, _reason, _detail = evaluate_keyword(keyword, ctx, match_class="phrase", seed="SEO")
        assert keep is False, keyword
    for keyword in ("ai seo", "google seo", "seo sunshine coast", "what is seo", "seo for dentists"):
        keep, reason, _detail = evaluate_keyword(keyword, ctx, match_class="phrase", seed="SEO")
        assert keep is True, (keyword, reason)
    assert detect_intent("seo sunshine coast") == "commercial"
    assert detect_funnel("seo sunshine coast") == "BOFU"
    assert detect_intent("what is seo") == "informational"
    assert detect_funnel("what is seo") == "TOFU"
    assert buyer_fit_reject("seo testing services", **AGENCY) is None


def test_a_digital_marketing_agency_keeps_its_own_category_and_drops_google_products():
    ctx = build_relevance_context(
        services=["SEO", "PPC Management", "Email Marketing", "Conversion Rate Optimization", "Social Media Marketing"],
        industry="Digital Marketing Agency",
        geography=AGENCY["geography"],
    )
    keep, reason, _detail = evaluate_keyword("digital marketing melbourne", ctx)
    assert keep is True and reason == "service_overlap"
    keep, reason, _detail = evaluate_keyword("search engine optimisation geelong", ctx, seed="SEO")
    assert keep is True, reason
    for keyword in ("google lens", "easter egg google", "minesweeper google", "google doc"):
        keep, reason, _detail = evaluate_keyword(keyword, ctx, match_class="related", seed="google ranking")
        assert keep is False and reason == "consumer_platform", (keyword, reason)
    ctx = build_relevance_context(services=["SEO"], seeds=["SEO"], industry="digital marketing agency")
    keep, reason, detail = evaluate_keyword("check seo website", ctx, match_class="phrase", seed="SEO")
    assert keep is False and reason == "tool_seeker" and detail == "check"
    assert reason in BUYER_FIT_REASONS


def test_search_engine_alone_is_not_the_seo_service():
    geo = AGENCY["geography"]
    assert judge_keyword("private search engine", service="SEO", geography=geo).decision == "DROP"
    assert judge_keyword("search engine optimisation perth", service="SEO", geography=geo).decision == "KEEP"
    assert judge_keyword("search engine ranking factors", service="SEO", geography=geo).decision == "KEEP"


@pytest.mark.parametrize(
    "keyword",
    ["pay per click management companies in usa", "web development company canada", "seo agency uk", "seo for nz businesses"],
)
def test_other_markets_are_dropped_for_an_australian_business(keyword):
    assert judge_keyword(keyword, service="SEO", geography=AGENCY["geography"]).reason == "geo_mismatch"


def test_google_grouped_volume_folds_only_when_one_wording_contains_the_other():
    rows = [
        {"keyword": "perth seo", "volume": 2900, "cpc": 39.3, "difficulty": 3, "seed": "SEO"},
        {"keyword": "seo marketing perth", "volume": 2900, "cpc": 39.3, "difficulty": 3, "seed": "SEO"},
        {"keyword": "search engine optimisation perth wa", "volume": 2900, "cpc": 39.3, "difficulty": 3, "seed": "SEO"},
        {"keyword": "googlebot seo", "volume": 880, "cpc": 33.59, "difficulty": 73, "seed": "SEO"},
        {"keyword": "google seo marketing", "volume": 880, "cpc": 33.59, "difficulty": 73, "seed": "SEO"},
        {"keyword": "seo audit", "volume": 100, "cpc": 5.0, "difficulty": 10, "seed": "SEO"},
        {"keyword": "seo audit tool", "volume": 100, "cpc": 5.0, "difficulty": 11, "seed": "SEO"},
    ]
    kept, folded = collapse_close_variants(rows)
    by_kw = {r["keyword"]: r for r in kept}
    assert by_kw["perth seo"]["close_variants"] == ["seo marketing perth", "search engine optimisation perth wa"]
    assert len(folded) == 2
    assert by_kw["googlebot seo"]["volume_shared_with"] == ["google seo marketing"]
    assert by_kw["google seo marketing"]["volume_shared_with"] == ["googlebot seo"]
    assert "seo audit tool" in by_kw and not by_kw["seo audit"].get("close_variants")
    assert all("volume_shared_with" not in r for r in rows)


def test_unmeasured_or_estimated_volumes_never_count_as_a_google_bucket():
    rows = [
        {"keyword": "perth seo", "volume": 2900, "cpc": 39.3, "difficulty": 3, "volume_estimated": True},
        {"keyword": "seo marketing perth", "volume": 2900, "cpc": 39.3, "difficulty": 3},
        {"keyword": "seo perth wa", "volume": 2900, "cpc": None, "difficulty": 3},
    ]
    kept, folded = collapse_close_variants(rows)
    assert len(kept) == 3 and not folded


@pytest.mark.parametrize(
    "keyword,intent,stage",
    [
        ("search engine optimisation perth", "commercial", "BOFU"),
        ("pay per click management australia", "commercial", "BOFU"),
        ("ppc management", "informational", "BOFU"),
        ("google ads management", "commercial", "BOFU"),
        ("ppc budget management", "commercial", "MOFU"),
        ("what is seo", "informational", "TOFU"),
        ("time management", "informational", "TOFU"),
    ],
)
def test_funnel_reads_full_service_names_and_managed_services(keyword, intent, stage):
    assert funnel_with_evidence(keyword, intent)[0] == stage


def test_judge_prompt_version_changed_so_old_verdicts_are_not_reused():
    from app.services import keyword_validation_gate as gate

    assert gate.PROMPT_VERSION == "kwjudge-v4"
    assert "business potential" in gate._SYSTEM
    assert "checker" in gate._SCREEN_SYSTEM
