"""Create-topic fallback — pain-point-led, varied, framework-shaped topic plans."""

from app.services.create_topic import (
    _ANGLE_CYCLE,
    _ANGLE_TEMPLATE,
    _fallback_topic_plan,
    _ground_primary_keyword,
    _pool_by_keyword,
    format_audience_label,
)


def _plan(count=8, pain_points=None):
    best = [
        {"keyword": "email deliverability", "volume": 900, "difficulty": 20},
        {"keyword": "cold email outreach", "volume": 700, "difficulty": 25},
        {"keyword": "email list segmentation", "volume": 400, "difficulty": 15},
    ]
    return _fallback_topic_plan(
        seed="email marketing",
        count=count,
        audience="B2B founders",
        funnel="all",
        best=best,
        evergreen=[],
        competitor_domains=["competitor.com"],
        products=["Email Marketing"],
        pain_points=pain_points,
    )


def test_pain_point_angle_leads_the_cycle():
    assert _ANGLE_CYCLE[0][0] == "pain-point"
    assert {"pain-point", "why-failing", "never-again"} <= set(_ANGLE_TEMPLATE)


def test_fallback_produces_requested_count_and_pain_first():
    plan = _plan(count=8)
    ideas = plan["topic_ideas"]
    assert len(ideas) == 8
    assert ideas[0]["angle_category"] == "pain-point"
    assert ideas[0]["title"].lower().startswith("struggling with")
    # Varied titles — no two adjacent ideas share the same title
    titles = [i["title"] for i in ideas]
    assert len(set(titles)) == len(titles)


def test_supplied_pain_points_surface_in_title_and_why():
    plan = _plan(count=4, pain_points=["campaigns landing in spam"])
    first = plan["topic_ideas"][0]
    assert first["angle_category"] == "pain-point"
    assert "campaigns landing in spam" in first["title"].lower()
    assert "campaigns landing in spam" in first["why"].lower()


def test_format_audience_label_flattens_demographic_blob():
    label = format_audience_label(
        {
            "primary": {
                "gender": "M & F",
                "age_range": "32–50",
                "job_titles": [
                    "Founder",
                    "Director",
                    "Agency Owner",
                    "Managing Director",
                ],
            },
            "secondary": {"role": "Senior Account Manager"},
        }
    )
    assert label is not None
    assert "Founder" in label
    assert "Director" in label
    assert "ages 32–50" in label
    assert "primary" not in label


def test_ground_primary_keyword_requires_exact_seeded_match():
    pool = [
        {"keyword": "meta ads library", "volume": 18100},
        {"keyword": "seo for agencies", "volume": 4400},
    ]
    by_kw = _pool_by_keyword(pool)
    kw, hit = _ground_primary_keyword(
        "meta ads library", pool_by_kw=by_kw, used=set()
    )
    assert kw == "meta ads library"
    assert hit is not None
    # Invented / paraphrased keywords are rejected (no soft remap)
    kw2, hit2 = _ground_primary_keyword("zoho crm", pool_by_kw=by_kw, used=set())
    assert kw2 == ""
    assert hit2 is None
    kw3, hit3 = _ground_primary_keyword(
        "Meta Ads Library Guide", pool_by_kw=by_kw, used=set()
    )
    assert kw3 == ""
    assert hit3 is None


def test_secondary_keywords_stay_in_same_seed_family():
    pool = [
        {
            "keyword": "meta business suite",
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "intent": "informational",
            "match_class": "exact",
        },
        {
            "keyword": "meta ads manager",
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "intent": "informational",
            "match_class": "phrase",
        },
        {
            "keyword": "facebook ads campaign",
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "intent": "commercial",
            "match_class": "related",
        },
        {
            "keyword": "google ads ppc management",
            "seed": "Google Ads/PPC Management",
            "target": "Google Ads/PPC Management",
            "intent": "informational",
            "match_class": "exact",
        },
        {
            "keyword": "google ads",
            "seed": "Google Ads/PPC Management",
            "target": "Google Ads/PPC Management",
            "intent": "informational",
            "match_class": "exact",
        },
    ]
    from app.services.create_topic import _secondary_keywords_for

    seconds = _secondary_keywords_for("meta business suite", pool=pool, limit=4)
    assert seconds
    assert all("google" not in s.lower() for s in seconds)
    assert any("meta" in s.lower() or "facebook" in s.lower() for s in seconds)


def test_shape_from_keyword_uses_intent_and_match_class():
    from app.services.create_topic import _shape_from_keyword

    info_exact = _shape_from_keyword(
        {
            "keyword": "meta business suite",
            "intent": "informational",
            "match_class": "exact",
            "seed": "Meta Ads",
            "service": "Meta Ads",
        },
        audience="Agency Owner",
        pain_point="inconsistent delivery quality",
    )
    assert info_exact["funnel"] == "TOFU"
    assert info_exact["intent"] == "informational"
    assert info_exact["traffic"] == "cold"
    assert info_exact["core_topic"] == "Meta Ads"

    commercial = _shape_from_keyword(
        {
            "keyword": "best seo tools",
            "intent": "commercial",
            "match_class": "phrase",
            "seed": "AI-Powered SEO",
        }
    )
    assert commercial["funnel"] == "MOFU"
    assert commercial["intent"] == "commercial"
    assert commercial["traffic"] == "warm"

    transactional = _shape_from_keyword(
        {
            "keyword": "google ads management services",
            "intent": "transactional",
            "match_class": "related",
            "seed": "Google Ads/PPC Management",
        }
    )
    assert transactional["funnel"] == "BOFU"
    assert transactional["traffic"] == "hot"


def test_select_seeded_topic_keywords_diversifies_and_skips_broad_heads():
    from app.services.keyword_opportunity import select_seeded_topic_keywords

    rows = [
        {
            "keyword": "instagram",
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "match_class": "broad",
            "intent": "informational",
            "volume": 1020000,
        },
        {
            "keyword": "google ads",
            "seed": "Google Ads/PPC Management",
            "target": "Google Ads/PPC Management",
            "match_class": "exact",
            "intent": "informational",
            "volume": 81000,
        },
        {
            "keyword": "meta business suite",
            "seed": "Meta Ads",
            "target": "Meta Ads",
            "match_class": "exact",
            "intent": "informational",
            "volume": 19000,
        },
        {
            "keyword": "ai marketing strategy for agencies",
            "seed": "AI Marketing Strategy",
            "target": "AI Marketing Strategy",
            "match_class": "phrase",
            "intent": "commercial",
            "volume": 720,
        },
        {
            "keyword": "email deliverability checklist",
            "seed": "Email Marketing",
            "target": "Email Marketing",
            "match_class": "related",
            "intent": "informational",
            "volume": 880,
        },
        {
            "keyword": "best seo tools",
            "seed": "AI-Powered SEO",
            "target": "AI-Powered SEO",
            "match_class": "phrase",
            "intent": "commercial",
            "volume": 201000,
        },
    ]
    picked = select_seeded_topic_keywords(
        rows,
        products=["Meta Ads", "Google Ads/PPC Management", "AI Marketing Strategy"],
        limit=5,
        max_per_seed=1,
    )
    kws = [str(r["keyword"]).lower() for r in picked]
    assert "instagram" not in kws
    assert "google ads" not in kws  # 2-word broad head blocked
    assert "meta business suite" in kws
    seeds = {str(r.get("target") or r.get("seed")) for r in picked}
    assert len(seeds) >= 3


def test_select_topics_from_service_clusters_one_per_service():
    from app.services.keyword_opportunity import select_topics_from_service_clusters

    clusters = [
        {
            "service": "Meta Ads",
            "seeds": [
                {
                    "seed": "Meta Ads",
                    "target": "Meta Ads",
                    "keywords": [
                        {
                            "keyword": "instagram",
                            "match_class": "broad",
                            "intent": "informational",
                            "volume": 1000000,
                        },
                        {
                            "keyword": "meta business suite",
                            "match_class": "exact",
                            "intent": "informational",
                            "volume": 19000,
                        },
                    ],
                }
            ],
        },
        {
            "service": "Email Marketing",
            "seeds": [
                {
                    "seed": "Email Marketing",
                    "target": "Email Marketing",
                    "keywords": [
                        {
                            "keyword": "email deliverability checklist",
                            "match_class": "phrase",
                            "intent": "informational",
                            "volume": 900,
                        }
                    ],
                }
            ],
        },
        {
            "service": "AI-Powered SEO",
            "seeds": [
                {
                    "seed": "AI-Powered SEO",
                    "target": "AI-Powered SEO",
                    "keywords": [
                        {
                            "keyword": "best seo tool",
                            "match_class": "phrase",
                            "intent": "commercial",
                            "volume": 201000,
                        },
                        {
                            "keyword": "best seo tools",
                            "match_class": "phrase",
                            "intent": "commercial",
                            "volume": 201000,
                        },
                        {
                            "keyword": "ai seo content brief",
                            "match_class": "related",
                            "intent": "commercial",
                            "volume": 120,
                        },
                    ],
                }
            ],
        },
        {
            "service": "AEO & GEO Services (AI/answer engine optimization)",
            "seeds": [
                {
                    "seed": "AEO",
                    "target": "AEO & GEO Services (AI/answer engine optimization)",
                    "keywords": [
                        {
                            "keyword": "aeo stock price",
                            "match_class": "related",
                            "intent": "transactional",
                            "volume": 720,
                        },
                        {
                            "keyword": "answer engine optimization agency",
                            "match_class": "related",
                            "intent": "commercial",
                            "volume": 90,
                        },
                    ],
                }
            ],
        },
    ]
    picked = select_topics_from_service_clusters(
        clusters,
        products=["Meta Ads", "Email Marketing", "AI-Powered SEO", "AEO & GEO Services"],
        limit=10,
    )
    kws = [str(r["keyword"]).lower() for r in picked]
    services = [str(r.get("service")) for r in picked]
    assert "instagram" not in kws
    assert "aeo stock price" not in kws
    assert "meta business suite" in kws
    assert "email deliverability checklist" in kws
    assert "answer engine optimization agency" in kws
    assert len(services) == len(set(services))
    # Near-duplicate best seo tool/tools should not both appear
    assert sum(1 for k in kws if "best seo tool" in k) <= 1


def test_filter_seed_clusters_keeps_only_allowed_keywords():
    from app.services.keyword_seeding import filter_seed_clusters, flatten_dataset

    clusters = [
        {
            "seed": "seo",
            "exact": [{"keyword": "seo agency", "volume": 100}],
            "phrase": [{"keyword": "zoho crm", "volume": 50}],
            "related": [{"keyword": "seo for agencies", "volume": 40}],
            "broad": [],
        }
    ]
    cleaned = filter_seed_clusters(clusters, {"seo agency", "seo for agencies"})
    assert len(cleaned) == 1
    flat = flatten_dataset(cleaned)
    kws = {str(r["keyword"]).lower() for r in flat}
    assert kws == {"seo agency", "seo for agencies"}
    assert "zoho crm" not in kws


def test_attach_url_map_to_topic_plan_stamps_actions():
    from app.services.create_topic import attach_url_map_to_topic_plan

    plan = {
        "topic_ideas": [
            {"primary_keyword": "seo services", "title": "SEO Services Guide"},
            {"primary_keyword": "local seo", "title": "Local SEO"},
        ]
    }
    report = {
        "final_url_map": [
            {
                "primary_keyword": "seo services",
                "action": "OPTIMIZE_EXISTING",
                "final_url": "/services/seo",
            },
            {
                "primary_keyword": "local seo",
                "action": "CREATE",
                "final_url": "/blog/local-seo",
            },
        ],
        "summary": {"optimize_existing": 1, "create": 1},
    }
    out = attach_url_map_to_topic_plan(plan, report)
    by_kw = {i["primary_keyword"]: i for i in out["topic_ideas"]}
    assert by_kw["seo services"]["url_map_action"] == "OPTIMIZE_EXISTING"
    assert by_kw["seo services"]["selected_url"] == "/services/seo"
    assert by_kw["local seo"]["url_map_action"] == "CREATE"
    assert out["created_after_url_map"] is True
    assert out["deferred"] is False
