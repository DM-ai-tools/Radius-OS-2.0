"""Headline framework — curiosity + benefit titles without invented claims."""

from app.services.headline_framework import (
    asset_class,
    build_headline_options,
    pick_primary_headline,
    traffic_temperature,
)
from app.services.topic_naming import specific_page_title


def test_asset_class_from_page_type():
    assert asset_class(page_type="service") == "landing"
    assert asset_class(content_type="comparison") == "comparison"
    assert asset_class(intent="informational") == "blog"


def test_traffic_temperature_from_intent():
    assert traffic_temperature("informational") == "cold"
    assert traffic_temperature("commercial") == "warm"
    assert traffic_temperature("transactional") == "hot"


def test_blog_headlines_are_varied_and_grounded():
    opts = build_headline_options(
        "local seo melbourne",
        page_type="article",
        intent="informational",
        audience="small business owners",
        location="Melbourne",
        outline_count=5,
    )
    titles = " | ".join(o["title"].lower() for o in opts)
    assert opts
    # Audience / geo grounding still surfaces
    assert "melbourne" in titles or "small business" in titles
    # A noun-phrase keyword must NOT lead with an ungrammatical "How To <noun>"
    assert not opts[0]["title"].lower().startswith("how to local seo melbourne")


def test_noun_phrase_howto_is_grammatical_and_optional():
    # Requesting the how-to angle yields a grammatical reframing, not "How To <noun>"
    title = pick_primary_headline(
        "content marketing",
        content_type="guide",
        intent="informational",
        prefer_template="howto",
    )
    low = title.lower()
    assert low.startswith("how to ")
    assert "how to content marketing" not in low


def test_angle_hint_varies_titles_for_same_keyword():
    kw = "content marketing"
    seen = {
        pick_primary_headline(kw, content_type="guide", intent="informational", prefer_template=t)
        for t in ("what_is", "best_strategies", "list_mistakes", "beginner", "templates")
    }
    # Distinct angles must produce distinct titles (no more "everything is How To")
    assert len(seen) >= 4


def test_actionable_keyword_keeps_direct_howto():
    title = pick_primary_headline(
        "improve conversion rate",
        content_type="guide",
        intent="informational",
        prefer_template="howto",
    )
    assert title.lower().startswith("how to improve conversion rate")


def test_service_headlines_are_commercial_not_blog_explainer():
    title = pick_primary_headline(
        "seo services melbourne",
        page_type="service",
        intent="commercial",
        audience="Melbourne SMBs",
        location="Melbourne",
        client_name="Click Trends",
    )
    low = title.lower()
    assert "seo services" in low
    assert "complete guide" not in low


def test_no_invented_social_proof_or_fake_stats():
    blob = " ".join(
        o["title"]
        for o in build_headline_options(
            "crm software",
            page_type="landing",
            intent="transactional",
            audience="sales managers",
        )
    ).lower()
    assert "harvard" not in blob
    assert "50,000" not in blob
    assert "guaranteed" not in blob


def test_pain_point_templates_express_the_problem():
    opts = build_headline_options(
        "email deliverability",
        content_type="blog",
        intent="informational",
        outline_count=5,
        limit=20,
    )
    templates = {o["template"] for o in opts}
    assert {"pain_struggle", "why_not_working"} <= templates
    by_template = {o["template"]: o["title"] for o in opts}
    assert by_template["pain_struggle"].lower().startswith("struggling with")
    assert "isn't working" in by_template["why_not_working"].lower()
    # never_again is reachable when an angle requests it
    title = pick_primary_headline(
        "email deliverability",
        content_type="blog",
        intent="informational",
        prefer_template="never_again",
    )
    assert title.lower().startswith("never struggle with")


def test_supplied_pain_point_is_used_and_boosted():
    title = pick_primary_headline(
        "marketing automation",
        content_type="blog",
        intent="informational",
        prefer_template="pain_struggle",
        pain_point="leads going cold before sales follows up",
    )
    assert "leads going cold" in title.lower()
    assert title.lower().startswith("struggling with")


def test_mapped_page_titles_follow_the_framework():
    from app.services.headline_framework import mapped_page_title, title_for_map_row

    question = mapped_page_title("what is seo", page_type="guide", intent="informational")
    assert question == "What Is SEO? A Clear Guide"
    howto = mapped_page_title("how to improve site seo", page_type="guide", intent="informational")
    assert howto == "How to Improve Site SEO"
    service = mapped_page_title(
        "search engine optimisation", page_type="service", intent="commercial"
    )
    assert service == "A Clear Path To Search Engine Optimisation"
    guide = mapped_page_title("cost of seo marketing", page_type="guide", intent="informational")
    assert guide == "The Practical Guide to Cost of SEO Marketing"
    blog = title_for_map_row({"primary_keyword": "seo tips", "page_type": "Blog", "page_job": "draft_guide"})
    assert blog == "The Practical Guide to SEO Tips"
    hub = title_for_map_row(
        {"primary_keyword": "email marketing", "page_type": "Hub Page", "page_job": "optimize_service_page"}
    )
    assert hub == "A Clear Path To Email Marketing"
    for title in (question, howto, service, guide, blog, hub):
        assert len(title) <= 60
        low = title.lower()
        assert "guaranteed" not in low
        assert "harvard" not in low
        assert "50,000" not in low


def test_specific_page_title_uses_framework():
    title = specific_page_title(
        "share of search",
        content_type="guide",
        intent="informational",
        audience="brand marketers",
    )
    assert title
    assert "share of search" in title.lower() or "Share of Search" in title
