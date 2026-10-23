"""Relevance stays with the model for long-tail queries, and clusters stay distinct."""

import pytest

from app.services.keyword_clustering import build_clusters_deterministic
from app.services.keyword_llm_clustering import build_cluster_report_from_llm
from app.services.keyword_service_contract import judge_keyword
from app.services.keyword_validation_gate import PROMPT_VERSION, validate_keywords


def test_long_tail_without_the_service_name_is_left_for_the_model():
    verdict = judge_keyword(
        "klaviyo abandoned cart flows",
        service="Email Marketing",
        cdd_keywords=["abandoned cart", "klaviyo flows"],
    )
    assert verdict.decision == "SKIP"
    assert verdict.reason == "long_tail_for_llm"


def test_a_different_service_phrase_is_still_rejected():
    verdict = judge_keyword("google ads agency", service="Email Marketing")
    assert verdict.decision == "DROP"


def test_spelling_and_spacing_variants_share_one_cluster():
    """One topic is one page. '4ps' and '4 ps' are the same query."""
    rows = [
        {"keyword": "4 ps of marketing", "volume": 720, "intent": "informational"},
        {"keyword": "4ps of marketing", "volume": 480, "intent": "informational"},
        {"keyword": "7 ps of marketing", "volume": 680, "intent": "informational"},
        {"keyword": "7ps of marketing mix", "volume": 620, "intent": "informational"},
    ]
    report = build_clusters_deterministic(rows)
    by_kw = {
        row["keyword"]: cluster["primary_keyword"]
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    }
    assert by_kw["4 ps of marketing"] == by_kw["4ps of marketing"]
    assert by_kw["7 ps of marketing"] != by_kw["7ps of marketing mix"]
    assert by_kw["4 ps of marketing"] != by_kw["7 ps of marketing"]
    assert len(report["clusters"]) == 3


def test_parent_topic_spelling_variants_share_one_cluster():
    """A stored parent phrase does not keep spacing variants on separate pages."""
    rows = [
        {
            "keyword": "4 ps of marketing",
            "volume": 720,
            "intent": "informational",
            "parent_topic": "4 ps of marketing",
        },
        {
            "keyword": "4ps of marketing",
            "volume": 480,
            "intent": "informational",
            "parent_topic": "4ps of marketing",
        },
        {
            "keyword": "7 ps of marketing",
            "volume": 680,
            "intent": "informational",
            "parent_topic": "7 ps of marketing",
        },
        {
            "keyword": "7ps of marketing mix",
            "volume": 620,
            "intent": "informational",
            "parent_topic": "7ps of marketing mix",
        },
    ]
    report = build_clusters_deterministic(rows)
    by_kw = {
        row["keyword"]: cluster["primary_keyword"]
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    }
    assert by_kw["4 ps of marketing"] == by_kw["4ps of marketing"]
    assert by_kw["7 ps of marketing"] != by_kw["7ps of marketing mix"]
    assert by_kw["4 ps of marketing"] != by_kw["7 ps of marketing"]
    assert len(report["clusters"]) == 3


def test_shared_parent_topic_does_not_merge_different_keywords():
    """A broad parent topic is not a cluster. The keyword is."""
    rows = [
        {
            "keyword": "easter egg google",
            "volume": 9900,
            "intent": "informational",
            "parent_topic": "search engine optimisation",
        },
        {
            "keyword": "google doc",
            "volume": 246000,
            "intent": "informational",
            "parent_topic": "search engine optimisation",
        },
        {
            "keyword": "4 ps of marketing",
            "volume": 720,
            "intent": "informational",
            "parent_topic": "4 ps of marketing",
        },
        {
            "keyword": "4ps of marketing",
            "volume": 480,
            "intent": "informational",
            "parent_topic": "4ps of marketing",
        },
    ]
    report = build_clusters_deterministic(rows)
    by_kw = {
        row["keyword"]: cluster["primary_keyword"]
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    }
    assert by_kw["easter egg google"] != by_kw["google doc"]
    assert by_kw["4 ps of marketing"] == by_kw["4ps of marketing"]


def test_one_shared_platform_word_does_not_merge_keywords():
    """Mentioning Google is not a shared topic."""
    rows = [
        {"keyword": "google seo starter guide", "volume": 1000, "intent": "informational"},
        {"keyword": "google seo beginners guide", "volume": 800, "intent": "informational"},
        {"keyword": "google lens", "volume": 90000, "intent": "informational"},
        {"keyword": "minesweeper google", "volume": 9000, "intent": "informational"},
    ]
    report = build_clusters_deterministic(rows)
    by_kw = {
        row["keyword"]: cluster["primary_keyword"]
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    }
    assert by_kw["google seo starter guide"] != by_kw["google seo beginners guide"]
    assert by_kw["google lens"] != by_kw["google seo starter guide"]
    assert by_kw["minesweeper google"] != by_kw["google lens"]
    assert by_kw["minesweeper google"] != by_kw["google seo starter guide"]


def test_cluster_is_the_primary_and_its_secondaries():
    rows = [
        {"keyword": "seo agency melbourne", "volume": 500, "intent": "commercial", "funnel": "MOFU"},
        {"keyword": "seo company melbourne", "volume": 300, "intent": "commercial", "funnel": "MOFU"},
        {"keyword": "seo services melbourne", "volume": 250, "intent": "commercial", "funnel": "MOFU"},
        {"keyword": "seo agency melbourne for dentists", "volume": 20, "intent": "commercial", "funnel": "MOFU"},
    ]
    report = build_clusters_deterministic(rows)
    by_kw = {
        row["keyword"]: cluster
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    }
    page = by_kw["seo agency melbourne"]
    members = {row["keyword"] for row in page["keywords"]}
    assert page["primary_keyword"] == "seo agency melbourne"
    assert members == {"seo agency melbourne", "seo company melbourne", "seo services melbourne"}
    dentists = by_kw["seo agency melbourne for dentists"]
    assert dentists["primary_keyword"] == "seo agency melbourne for dentists"
    assert {row["role"] for row in dentists["keywords"]} == {"Primary"}
    for cluster in report["clusters"]:
        assert {row["role"] for row in cluster["keywords"]} <= {"Primary", "Secondary"}


def test_clusters_separate_funnel_as_well_as_intent():
    """A best-of query and a hire query share a topic and can share an intent
    label, but they are different pages: MOFU versus BOFU."""
    rows = [
        {"keyword": "best crm software", "volume": 1200, "parent_topic": "crm software", "intent": "commercial"},
        {"keyword": "hire crm agency", "volume": 800, "parent_topic": "crm software", "intent": "commercial"},
        {"keyword": "what is crm software", "volume": 400, "parent_topic": "crm software", "intent": "informational"},
    ]
    report = build_clusters_deterministic(rows)
    by_kw = {
        row["keyword"]: cluster
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    }
    assert by_kw["best crm software"]["funnel"] == "MOFU"
    assert by_kw["hire crm agency"]["funnel"] == "BOFU"
    assert by_kw["what is crm software"]["funnel"] == "TOFU"
    assert by_kw["best crm software"] is not by_kw["hire crm agency"]
    assert by_kw["hire crm agency"] is not by_kw["what is crm software"]
    for cluster in report["clusters"]:
        funnels = {row["funnel"] for row in cluster["keywords"]}
        intents = {row["intent"] for row in cluster["keywords"]}
        assert len(funnels) == 1
        assert len(intents) == 1


def test_clusters_separate_search_intent():
    rows = [
        {"keyword": "crm pricing", "volume": 900, "parent_topic": "crm software", "intent": "transactional"},
        {"keyword": "what is crm software", "volume": 400, "parent_topic": "crm software", "intent": "informational"},
    ]
    report = build_clusters_deterministic(rows)
    intents = {cluster["intent"] for cluster in report["clusters"]}
    assert intents == {"transactional", "informational"}
    keywords = [
        row["keyword"]
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    ]
    assert keywords.count("crm pricing") == 1
    assert keywords.count("what is crm software") == 1


def test_a_measured_standalone_topic_stays_a_cluster():
    report = build_clusters_deterministic(
        [{"keyword": "warehouse slotting guide", "volume": 40, "intent": "informational"}]
    )
    assert report["clusters"]
    assert report["clusters"][0]["primary_keyword"] == "warehouse slotting guide"
    assert report["clusters"][0]["keywords"][0]["volume"] == 40
    assert report["orphan_count"] == 0


def test_llm_clusters_do_not_repeat_a_keyword_or_invent_volume():
    cleaned = [
        {"keyword": "local seo", "volume": 100, "difficulty": None, "cpc": None},
    ]
    payload = {
        "clusters": [
            {
                "name": "Local",
                "est_traffic": 99999,
                "keywords": [
                    {"keyword": "local seo", "role": "Primary", "intent": "commercial"},
                    {"keyword": "local seo", "role": "Supporting", "intent": "commercial"},
                    {"keyword": "invented term", "role": "Supporting", "intent": "commercial"},
                ],
            }
        ]
    }
    report = build_cluster_report_from_llm(payload, cleaned)
    cluster = report["clusters"][0]
    assert [row["keyword"] for row in cluster["keywords"]] == ["local seo", "invented term"]
    assert cluster["est_traffic"] == 100
    assert cluster["keywords"][0]["volume"] == 100
    assert cluster["keywords"][0]["difficulty"] is None
    assert cluster["keywords"][0]["cpc"] is None
    assert cluster["avg_difficulty"] is None


@pytest.mark.asyncio
async def test_validation_reuses_a_judgement_inside_one_run(monkeypatch):
    calls = {"n": 0}

    async def judge(system, user, batch_size=0):
        calls["n"] += 1
        return {
            "keywords": [
                {
                    "keyword": "email marketing strategy",
                    "relevant": True,
                    "intent": "commercial",
                    "topic": "abandoned carts",
                    "reason": "buyer task for the service",
                    "score": 0.9,
                }
            ]
        }

    store: dict[str, dict] = {}

    async def cache_get_many(keys):
        return {key: store[key] for key in keys if key in store}

    async def cache_set_many(payload, ttl):
        store.update(payload)

    monkeypatch.setattr(
        "app.services.keyword_validation_gate.cache_get_many", cache_get_many
    )
    monkeypatch.setattr(
        "app.services.keyword_validation_gate.cache_set_many", cache_set_many
    )
    monkeypatch.setattr(
        "app.services.keyword_validation_gate._provider_ready", lambda: True
    )

    rows = [
        {"keyword": "email marketing strategy", "service": "Email Marketing", "volume": None},
        {"keyword": "email marketing strategy", "service": "Email Marketing", "volume": None},
    ]
    common = dict(
        client_id="client-1",
        industry="Marketing",
        services=["Email Marketing"],
        cdd_keywords=["email nurture"],
        caller=judge,
        enabled=True,
    )
    first_kept, first_dropped, first_audit = await validate_keywords(rows, **common)
    second_kept, second_dropped, second_audit = await validate_keywords(rows, **common)

    assert calls["n"] == 1
    assert first_audit["cached"] >= 1
    assert second_audit["api_calls"] == 0
    assert second_audit["cached"] == 2
    assert len(first_kept) == len(second_kept) == 2
    assert first_dropped == second_dropped == []
    assert all(row["volume"] is None for row in second_kept)
    assert all(row["intent"] == "commercial" for row in second_kept)
    assert PROMPT_VERSION in next(iter(store))
