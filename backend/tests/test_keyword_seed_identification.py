"""Multiple seeds per service, with keywords grouped under those seeds."""

import json

import pytest

from app.services.keyword_clustering import build_service_seed_clusters
from app.services.keyword_seed_identification import (
    assignments_from_model_payload,
    choose_primary_keyword,
    identify_multiple_seed_clusters,
    names_another_service,
    seed_fits_service,
    theme_seed_groups,
)


def _cluster(seed: str, service: str, rows: list[tuple[str, int]], **extra) -> dict:
    phrase = [
        {
            "keyword": keyword,
            "volume": volume,
            "match_class": "phrase",
            "intent": "commercial",
            "relevance_reason": "service_match",
            "relevance_unverified": False,
            **extra,
        }
        for keyword, volume in rows
    ]
    return {
        "seed": seed,
        "target": service,
        "target_type": "service",
        "exact": [],
        "phrase": phrase,
        "related": [],
        "broad": [],
        "keyword_count": len(phrase),
        "volume_sum": sum(volume for _, volume in rows),
    }


SEO_ROWS = [
    ("seo services", 2400),
    ("seo agency", 1900),
    ("seo company", 1600),
    ("seo consultant", 850),
    ("local seo", 1400),
    ("local seo services", 900),
    ("local seo melbourne", 700),
    ("technical seo", 1100),
    ("technical seo audit", 800),
    ("technical seo services", 500),
    ("ecommerce seo", 1000),
    ("ecommerce seo agency", 600),
    ("on page seo", 750),
    ("on page seo checklist", 300),
    ("shopify seo", 400),
]


def test_primary_keyword_beats_a_long_tail_variant():
    volumes = {
        "local seo": 1400,
        "local seo melbourne": 700,
        "local seo melbourne checklist": 80,
        "how to do local seo": 50,
    }
    classes = {
        "local seo": "phrase",
        "local seo melbourne": "related",
        "local seo melbourne checklist": "broad",
        "how to do local seo": "broad",
    }
    assert (
        choose_primary_keyword(list(volumes), volumes, classes) == "local seo"
    )


@pytest.mark.asyncio
async def test_identified_seeds_are_the_primary_keywords():
    rows = [
        ("local seo", 1400),
        ("local seo services", 900),
        ("local seo melbourne", 700),
        ("local seo melbourne checklist", 80),
        ("technical seo", 1100),
        ("technical seo audit", 800),
        ("technical seo services", 500),
        ("technical seo audit checklist", 90),
        ("ecommerce seo", 1000),
        ("ecommerce seo agency", 600),
    ]
    clusters, _audit = await identify_multiple_seed_clusters(
        [_cluster("SEO", "SEO", rows)],
        services=["SEO"],
    )
    seeds = {cluster["seed"] for cluster in clusters}
    assert seeds == {"SEO"}
    assert "local seo" not in seeds
    assert "technical seo" not in seeds
    assert "ecommerce seo" not in seeds


def test_theme_split_finds_several_seeds_and_keeps_every_keyword():
    groups = theme_seed_groups(SEO_ROWS, service="SEO")
    assert len(groups) >= 4
    assigned = [keyword for members in groups.values() for keyword in members]
    assert sorted(assigned) == sorted(keyword for keyword, _ in SEO_ROWS)
    assert any("local" in seed for seed in groups)
    assert any("technical" in seed for seed in groups)
    assert any("ecommerce" in seed for seed in groups)


def test_sibling_service_names_are_not_seeds_of_this_service():
    services = ["Content Marketing", "SEO", "Google Ads", "Social Media Marketing"]
    assert names_another_service("seo agency", "Content Marketing", services)
    assert names_another_service("brisbane seo agency", "Content Marketing", services)
    assert names_another_service("google ads agency", "Content Marketing", services)
    assert not names_another_service("content marketing strategy", "Content Marketing", services)
    assert names_another_service("content marketing", "Social Media Marketing", services)
    assert names_another_service("seo agency", "Social Media Marketing", services)
    assert not names_another_service("social media agency sydney", "Social Media Marketing", services)
    assert not names_another_service("marketing strategy social media", "Social Media Marketing", services)
    assert seed_fits_service("content marketing strategy", "Content Marketing", services)
    assert seed_fits_service("content strategy", "Content Marketing", services)
    assert not seed_fits_service("seo agency", "Content Marketing", services)
    assert not seed_fits_service("blog writing", "Content Marketing", services)
    assert seed_fits_service("local seo", "SEO", services)
    assert seed_fits_service("instagram marketing", "Social Media Marketing", services)
    assert not seed_fits_service("instagram marketing", "Content Marketing", services)


@pytest.mark.asyncio
async def test_content_and_social_seeds_do_not_repeat_other_services():
    content_rows = [
        ("content marketing", 5000),
        ("content marketing strategy", 1200),
        ("content marketing agency", 800),
        ("content strategy", 600),
        ("blog writing", 400),
        ("blog writing services", 350),
        ("content marketing plan", 280),
        ("content marketing services", 260),
        ("content calendar", 220),
        ("blog content writing", 180),
        ("seo agency", 9000),
        ("seo services", 2200),
        ("brisbane seo agency", 300),
        ("google ads agency", 1500),
        ("google ads management", 700),
    ]
    social_rows = [
        ("social media marketing", 4000),
        ("social media agency sydney", 800),
        ("marketing strategy social media", 500),
        ("marketing tools social media", 300),
        ("social media management", 900),
        ("instagram marketing", 700),
        ("facebook marketing", 650),
        ("social media strategy", 420),
        ("tiktok marketing", 380),
        ("content marketing", 2000),
        ("seo agency", 1500),
        ("brisbane seo agency", 200),
    ]
    services = ["Content Marketing", "SEO", "Google Ads", "Social Media Marketing"]
    clusters, _audit = await identify_multiple_seed_clusters(
        [
            _cluster("Content Marketing", "Content Marketing", content_rows),
            _cluster("Social Media Marketing", "Social Media Marketing", social_rows),
        ],
        services=services,
    )
    content = [cluster for cluster in clusters if cluster["target"] == "Content Marketing"]
    social = [cluster for cluster in clusters if cluster["target"] == "Social Media Marketing"]

    def keywords(group: list[dict]) -> set[str]:
        return {
            row["keyword"]
            for cluster in group
            for row in cluster["phrase"]
        }

    content_seeds = {cluster["seed"] for cluster in content}
    social_seeds = {cluster["seed"] for cluster in social}
    assert content_seeds == {"Content Marketing"}
    assert social_seeds == {"Social Media Marketing"}
    assert "seo agency" not in keywords(content)
    assert "google ads agency" not in keywords(content)
    assert "content marketing" not in keywords(social)
    assert "seo agency" not in keywords(social)


def test_one_theme_stays_one_seed():
    rows = [
        ("seo services", 100),
        ("seo agency", 90),
        ("seo company", 80),
        ("seo consultant", 70),
        ("seo firm", 60),
        ("top seo company", 50),
        ("hire seo agency", 40),
        ("seo providers", 30),
        ("best seo agency", 20),
        ("seo experts", 10),
    ]
    assert theme_seed_groups(rows, service="SEO") == {}


@pytest.mark.asyncio
async def test_identify_groups_keywords_under_multiple_seeds():
    source = [_cluster("SEO", "SEO", SEO_ROWS)]
    clusters, audit = await identify_multiple_seed_clusters(
        source,
        services=["SEO", "PPC Management"],
    )
    assert audit["source"] == "unchanged"
    assert audit["seed_count_after"] == 1
    assert [cluster["seed"] for cluster in clusters] == ["SEO"]
    flat = [
        (row["keyword"], row["volume"], row["relevance_reason"])
        for cluster in clusters
        for row in cluster["phrase"]
    ]
    assert len(flat) == len(SEO_ROWS)
    assert all(volume > 0 and reason == "service_match" for _, volume, reason in flat)

    grouped = build_service_seed_clusters(clusters, ["SEO", "PPC Management"])
    seo = next(group for group in grouped if group["service"] == "SEO")
    assert seo["seed_count"] == 1
    assert seo["keyword_count"] == len(SEO_ROWS)
    assert {row["seed_label"] for row in seo["seeds"]} == {"Seed 1"}


def test_model_payload_drops_invented_keywords_and_tiny_seeds():
    keywords = [keyword for keyword, _ in SEO_ROWS]
    payload = {
        "seeds": [
            {
                "seed": "local seo",
                "keywords": ["local seo", "local seo services", "local seo melbourne", "invented local term"],
            },
            {
                "seed": "technical seo",
                "keywords": ["technical seo", "technical seo audit", "technical seo services"],
            },
            {
                "seed": "ecommerce seo",
                "keywords": ["ecommerce seo", "ecommerce seo agency", "shopify seo"],
            },
            {"seed": "on page seo", "keywords": ["on page seo"]},
            {
                "seed": "not a real theme",
                "keywords": ["seo services", "seo agency"],
            },
        ]
    }
    groups = assignments_from_model_payload(payload, keywords)
    assert groups is not None
    assert "not a real theme" not in groups
    assigned = [keyword for members in groups.values() for keyword in members]
    assert "invented local term" not in assigned
    assert sorted(assigned) == sorted(keywords)
    assert all(len(members) >= 2 for members in groups.values())
    assert "on page seo" not in groups or len(groups["on page seo"]) >= 2


@pytest.mark.asyncio
async def test_model_seeds_are_used_when_the_model_returns_several(monkeypatch):
    class _Settings:
        use_mock_llm = False
        anthropic_api_key = "test-key"
        skill_model = "claude-sonnet-5"

    monkeypatch.setattr(
        "app.services.keyword_seed_identification.get_settings",
        lambda: _Settings(),
    )

    async def _fake_chat(**kwargs):
        assert "SEO" in kwargs["user"]
        payload = {
            "seeds": [
                {
                    "seed": "local seo",
                    "keywords": ["local seo", "local seo services", "local seo melbourne"],
                },
                {
                    "seed": "technical seo",
                    "keywords": [
                        "technical seo",
                        "technical seo audit",
                        "technical seo services",
                    ],
                },
                {
                    "seed": "ecommerce seo",
                    "keywords": ["ecommerce seo", "ecommerce seo agency", "shopify seo"],
                },
                {
                    "seed": "on page seo",
                    "keywords": ["on page seo", "on page seo checklist"],
                },
                {
                    "seed": "seo services",
                    "keywords": ["seo services", "seo agency", "seo company", "seo consultant"],
                },
            ]
        }
        return json.dumps(payload)

    monkeypatch.setattr(
        "app.services.keyword_seed_identification.keyword_analysis_chat",
        _fake_chat,
    )
    clusters, audit = await identify_multiple_seed_clusters(
        [_cluster("SEO", "SEO", SEO_ROWS)],
        services=["SEO"],
        brand_name="ClickTrends",
        geography="Melbourne, Australia",
    )
    assert audit["source"] == "unchanged"
    assert [cluster["seed"] for cluster in clusters] == ["SEO"]


@pytest.mark.asyncio
async def test_single_model_seed_falls_back_to_themes(monkeypatch):
    class _Settings:
        use_mock_llm = False
        anthropic_api_key = "test-key"
        skill_model = "claude-sonnet-5"

    monkeypatch.setattr(
        "app.services.keyword_seed_identification.get_settings",
        lambda: _Settings(),
    )

    async def _fake_chat(**_kwargs):
        return json.dumps(
            {"seeds": [{"seed": "seo", "keywords": [keyword for keyword, _ in SEO_ROWS]}]}
        )

    monkeypatch.setattr(
        "app.services.keyword_seed_identification.keyword_analysis_chat",
        _fake_chat,
    )
    _clusters, audit = await identify_multiple_seed_clusters(
        [_cluster("SEO", "SEO", SEO_ROWS)],
        services=["SEO"],
    )
    assert audit["source"] == "unchanged"
    assert audit["seed_count_after"] == 1
