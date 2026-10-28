"""Service-page keywords stay on the pillar. Questions become guides. The rest stay unused."""

from app.services.pillar_pages import (
    assign_sheet_urls,
    build_pillar_and_guide_clusters,
    url_map_rows_from_clusters,
)


SYNONYMS = [
    {
        "service": "SEO",
        "suggested": ["search engine optimisation", "organic search", "google ranking"],
        "skipped": [],
    },
    {
        "service": "PPC Management",
        "suggested": ["ppc advertising", "pay per click management"],
        "skipped": [],
    },
]


def _rows() -> list[dict]:
    return [
        {"keyword": "seo agency melbourne", "volume": 1600, "cpc": 12.0},
        {"keyword": "melbourne seo agency", "volume": 800, "cpc": 11.0},
        {"keyword": "seo agency sydney", "volume": 900, "cpc": 10.0},
        {"keyword": "seo agency australia", "volume": 500, "cpc": 9.0},
        {"keyword": "what is seo", "volume": 2400, "cpc": 2.0},
        {"keyword": "seo meaning", "volume": 700, "cpc": 1.5},
        {"keyword": "seo tips for small business", "volume": 80, "cpc": 3.0},
        {"keyword": "how to do seo", "volume": 20, "cpc": 1.0},
        {"keyword": "google lens", "volume": 5000, "cpc": 0.2},
        {"keyword": "ppc management melbourne", "volume": 400, "cpc": 18.0},
        {"keyword": "pay per click management", "volume": 200, "cpc": 16.0},
        {"keyword": "ppc vs seo", "volume": 300, "cpc": 8.0},
        {"keyword": "what is email marketing", "volume": 10, "cpc": 1.0},
    ]


def test_one_pillar_cluster_and_guides_leave_the_rest_unused():
    result = build_pillar_and_guide_clusters(
        _rows(),
        offer_synonyms=SYNONYMS,
        services=["SEO", "PPC Management", "Email Marketing"],
        industry="Digital Marketing Agency",
    )
    pillars = {row["service"]: row for row in result["pillar_pages"]}
    seo = pillars["SEO"]
    assert seo["primary_keyword"] == "seo agency melbourne"
    assert "melbourne seo agency" in seo["secondary_keywords"]
    assert "seo agency sydney" not in seo["secondary_keywords"]
    assert "seo agency australia" not in seo["secondary_keywords"]
    assert "what is seo" not in seo["secondary_keywords"]

    guide_primaries = {row["primary_keyword"] for row in result["guides"] if row["service"] == "SEO"}
    assert "what is seo" in guide_primaries
    assert "how to do seo" not in guide_primaries
    email_guides = {row["primary_keyword"] for row in result["guides"] if row["service"] == "Email Marketing"}
    assert "what is email marketing" in email_guides
    assert "google lens" not in guide_primaries
    assert result["unused_keywords"] > 0

    mapped = url_map_rows_from_clusters(result)
    assert mapped[0]["l1_category"] in {"SEO", "PPC Management"}
    assert all(row["l1_category"] != "Melbourne / Local" for row in mapped)
    assert all("/ Local" not in str(row["l1_category"]) for row in mapped)


def test_live_service_page_is_current_and_a_missing_page_is_proposed():
    result = build_pillar_and_guide_clusters(
        _rows(),
        offer_synonyms=SYNONYMS,
        services=["SEO", "PPC Management", "Email Marketing"],
        industry="Digital Marketing Agency",
    )
    mapped = assign_sheet_urls(
        url_map_rows_from_clusters(result),
        pages=[{"path": "/seo", "url": "https://example.com/seo"}],
        service_paths={
            "SEO": "/seo/",
            "PPC Management": "/ppc-management/",
            "Email Marketing": "/email-marketing/",
        },
    )
    seo = next(row for row in mapped if row["l1_category"] == "SEO" and row["page_job"] == "optimize_service_page")
    assert seo["current_url"] == "/seo"
    assert seo["proposed_url"] is None
    ppc = next(row for row in mapped if row["l1_category"] == "PPC Management" and row["page_job"] == "optimize_service_page")
    assert ppc["current_url"] is None
    assert ppc["proposed_url"] == "/ppc-management"
    guide = next(row for row in mapped if row["primary_keyword"] == "what is seo")
    assert guide["current_url"] is None
    assert guide["proposed_url"] == "/what-is-seo"


def test_live_page_fills_current_url_when_the_slug_is_the_sites_own():
    rows = [
        {
            "l1_category": "Social Media Marketing",
            "primary_keyword": "social media marketing",
            "page_job": "optimize_service_page",
        },
        {
            "l1_category": "Conversion Rate Optimization",
            "primary_keyword": "conversion rate optimisation service",
            "page_job": "optimize_service_page",
        },
        {
            "l1_category": "SEO",
            "primary_keyword": "search engine optimisation",
            "page_job": "optimize_service_page",
        },
        {"l1_category": "SEO", "primary_keyword": "what is seo", "page_job": "draft_guide"},
        {"l1_category": "SEO", "primary_keyword": "seo tips", "page_job": "draft_guide"},
    ]
    mapped = assign_sheet_urls(
        rows,
        pages=[
            {"path": "/social-media-management"},
            {"path": "/conversion-rate-optimisation-services"},
            {"path": "/pxl-template/social-media-management-streamline-your-strategies"},
            {"path": "/seo-services-melbourne"},
            {"path": "/what-is-seo"},
        ],
        service_paths={
            "Social Media Marketing": "/social-media-marketing/",
            "Conversion Rate Optimization": "/conversion-rate-optimization/",
            "SEO": "/seo/",
        },
    )
    social, cro, seo, guide, missing = mapped
    assert social["current_url"] == "/social-media-management"
    assert social["proposed_url"] is None
    assert cro["current_url"] == "/conversion-rate-optimisation-services"
    assert cro["proposed_url"] is None
    assert seo["current_url"] is None
    assert seo["proposed_url"] == "/seo"
    assert guide["current_url"] == "/what-is-seo"
    assert guide["proposed_url"] is None
    assert missing["current_url"] is None
    assert missing["proposed_url"] == "/seo-tips"


def test_switched_off_tiktok_does_not_become_a_page():
    result = build_pillar_and_guide_clusters(
        [
            {"keyword": "social media marketing", "volume": 2400},
            {"keyword": "tiktok advertising agency", "volume": 110},
        ],
        services=["Social Media Marketing"],
        prioritization={
            "service_scope": {
                "social media marketing": {
                    "name": "Social Media Marketing",
                    "offered": ["Meta Ads"],
                    "excluded": ["TikTok Ads"],
                }
            }
        },
    )
    primaries = [row["primary_keyword"] for row in [*result["pillar_pages"], *result["guides"]]]
    assert "tiktok advertising agency" not in primaries
    assert "social media marketing" in primaries


def test_a_top_list_is_not_a_secondary_and_the_same_question_is_one_guide():
    result = build_pillar_and_guide_clusters(
        [
            *_rows(),
            {"keyword": "seo top companies", "volume": 300},
            {"keyword": "how to improve site seo", "volume": 80},
            {"keyword": "how to increase seo of website", "volume": 50},
            {"keyword": "cost of seo marketing", "volume": 390},
            {"keyword": "how much does search engine optimisation cost", "volume": 50},
            {"keyword": "google seo beginners guide", "volume": 320},
            {"keyword": "google seo starter guide", "volume": 320},
            {"keyword": "social media marketing", "volume": 2400},
            {"keyword": "social media marketing vs digital marketing", "volume": 10},
            {"keyword": "difference between digital marketing and social media management", "volume": 10},
            {"keyword": "what is smm in digital marketing", "volume": 10},
            {"keyword": "conversion rate optimisation", "volume": 4400},
            {"keyword": "conversion rate optimisation expert", "volume": 90},
            {"keyword": "conversion optimization experts", "volume": 80},
            {"keyword": "what is a good conversion rate for digital marketing", "volume": 10},
            {"keyword": "what is conversion rate in digital marketing", "volume": 10},
        ],
        offer_synonyms=SYNONYMS,
        services=["SEO", "PPC Management", "Email Marketing", "Social Media Marketing", "Conversion Rate Optimization"],
        industry="Digital Marketing Agency",
    )
    seo = next(row for row in result["pillar_pages"] if row["service"] == "SEO")
    assert "seo top companies" not in seo["secondary_keywords"]

    cro = next(row for row in result["pillar_pages"] if row["service"] == "Conversion Rate Optimization")
    assert cro["primary_keyword"] == "conversion rate optimisation"
    assert "conversion rate optimisation expert" in cro["secondary_keywords"]
    assert "conversion optimization experts" not in cro["secondary_keywords"]

    seo_guides = [row for row in result["guides"] if row["service"] == "SEO"]
    by_primary = {row["primary_keyword"]: row["secondary_keywords"] for row in seo_guides}
    assert "how to increase seo of website" in by_primary["how to improve site seo"]
    assert "how much does search engine optimisation cost" in by_primary["cost of seo marketing"]
    assert "google seo starter guide" in by_primary["google seo beginners guide"]
    assert "how to increase seo of website" not in by_primary

    social = [row["primary_keyword"] for row in result["guides"] if row["service"] == "Social Media Marketing"]
    assert len(social) == 2
    cro_guides = {row["primary_keyword"] for row in result["guides"] if row["service"] == "Conversion Rate Optimization"}
    assert "what is a good conversion rate for digital marketing" in cro_guides
    assert "what is conversion rate in digital marketing" in cro_guides
