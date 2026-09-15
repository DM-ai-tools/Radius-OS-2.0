"""Topic classification — clusters compared against the existing site map.

Covers the decision paths the workflow depends on: existing topic, existing
topic needing optimization, new topic, supporting keywords, cannibalization,
materially different intent, and the ambiguous case.
"""

from __future__ import annotations

import pytest

from app.services.topic_classification import (
    CANNIBALIZATION_RISK,
    EXISTING_TOPIC,
    EXISTING_TOPIC_NEEDS_CONSOLIDATION,
    EXISTING_TOPIC_NEEDS_OPTIMIZATION,
    NEW_TOPIC,
    SUPPORTING_TOPIC,
    TOPIC_STATUSES,
    UNCERTAIN,
    classify_clusters_against_site_map,
    detect_supporting_topics,
    mixed_intent_keywords,
)

# A small but realistic inventory so "nothing matched" is a real finding rather
# than an artefact of an empty site map.
BASE_PAGES = [
    {"url": "https://acme.io/about", "path": "/about", "title": "About Acme"},
    {"url": "https://acme.io/contact", "path": "/contact", "title": "Contact"},
    {"url": "https://acme.io/blog", "path": "/blog", "title": "Blog"},
    {"url": "https://acme.io/careers", "path": "/careers", "title": "Careers"},
    {"url": "https://acme.io/team", "path": "/team", "title": "Our Team"},
]


def _cluster(name, primary, *, intent="commercial", funnel="MOFU", keywords=None, **over):
    base = {
        "name": name,
        "primary_keyword": primary,
        "intent": intent,
        "funnel": funnel,
        "keywords": keywords
        or [{"keyword": primary, "role": "Primary", "intent": intent, "volume": 500}],
    }
    base.update(over)
    return base


def _kw(keyword, role="Secondary", *, intent="commercial", volume=200):
    return {"keyword": keyword, "role": role, "intent": intent, "volume": volume}


# --- 1. Existing topic ------------------------------------------------------


def test_existing_topic_maps_to_the_existing_url():
    pages = BASE_PAGES + [
        {
            "url": "https://acme.io/crm/small-business",
            "path": "/crm/small-business",
            "title": "CRM for Small Business",
            "page_type": "service",
            "metrics": {"clicks": 140, "impressions": 3000, "position": 6},
        }
    ]
    cluster = _cluster(
        "Small Business CRM",
        "small business crm",
        keywords=[
            _kw("small business crm", "Primary", volume=2400),
            _kw("best crm for small business", volume=1800),
            _kw("crm software for small businesses", volume=900),
        ],
    )
    classify_clusters_against_site_map([cluster], pages=pages)

    assert cluster["topic_status"] == EXISTING_TOPIC
    assert cluster["url_status"] == "existing_url"
    assert cluster["existing_page_match"]["matched_url"] == "/crm/small-business"
    assert cluster["topic_disposition"] == "existing_topic"
    assert cluster["topic_confidence"] >= 0.65
    assert "/crm/small-business" in cluster["topic_reason"]


# --- 2. Existing topic needing optimization ---------------------------------


def test_existing_page_with_wrong_page_type_needs_optimization_not_a_new_url():
    """The blog post ranks, but a transactional cluster wants a service page."""
    pages = BASE_PAGES + [
        {
            "url": "https://acme.io/blog/seo-audit-services",
            "path": "/blog/seo-audit-services",
            "title": "SEO Audit Services",
            "metrics": {"clicks": 300, "impressions": 5000, "position": 3},
        }
    ]
    cluster = _cluster(
        "SEO Audit Services",
        "seo audit services",
        intent="transactional",
        funnel="BOFU",
        keywords=[
            _kw("seo audit services", "Primary", intent="transactional", volume=900),
            _kw("seo audit company", intent="transactional", volume=300),
            _kw("professional seo audit", intent="transactional", volume=200),
        ],
    )
    classify_clusters_against_site_map([cluster], pages=pages)

    assert cluster["topic_status"] == EXISTING_TOPIC_NEEDS_OPTIMIZATION
    assert cluster["url_status"] == "existing_url_needs_optimization"
    # Still mapped to the existing page — never a new URL.
    assert cluster["existing_page_match"]["matched_url"] == "/blog/seo-audit-services"
    assert cluster["topic_disposition"] == "existing_topic"
    assert "intent" in cluster["topic_reason"]


def test_thin_existing_page_needs_optimization_not_replacement():
    """A strong match on a page the site map rates thin still maps to that URL."""
    pages = BASE_PAGES + [
        {
            "url": "https://acme.io/crm/small-business",
            "path": "/crm/small-business",
            "title": "CRM for Small Business",
            "page_type": "service",
            "word_count": 140,
            "content_quality": "thin",
            "metrics": {"clicks": 140, "impressions": 3000, "position": 6},
        }
    ]
    cluster = _cluster(
        "Small Business CRM",
        "small business crm",
        keywords=[
            _kw("small business crm", "Primary", volume=2400),
            _kw("best crm for small business", volume=1800),
            _kw("crm software for small businesses", volume=900),
        ],
    )
    classify_clusters_against_site_map([cluster], pages=pages)

    assert cluster["topic_status"] == EXISTING_TOPIC_NEEDS_OPTIMIZATION
    assert cluster["existing_page_match"]["matched_url"] == "/crm/small-business"
    assert "thin" in cluster["topic_reason"]
    assert "140 words" in cluster["topic_reason"]


def test_existing_page_flagged_as_overlapping_routes_to_consolidation():
    pages = BASE_PAGES + [
        {
            "url": "https://acme.io/crm/small-business",
            "path": "/crm/small-business",
            "title": "CRM for Small Business",
            "page_type": "service",
            "potential_cannibalization": ["/small-business-crm"],
            "metrics": {"clicks": 140, "impressions": 3000, "position": 6},
        }
    ]
    cluster = _cluster(
        "Small Business CRM",
        "small business crm",
        keywords=[
            _kw("small business crm", "Primary", volume=2400),
            _kw("best crm for small business", volume=1800),
        ],
    )
    classify_clusters_against_site_map([cluster], pages=pages)

    assert cluster["topic_status"] == EXISTING_TOPIC_NEEDS_CONSOLIDATION
    assert "/small-business-crm" in cluster["topic_reason"]


# --- 3. New topic -----------------------------------------------------------


def test_new_topic_when_nothing_on_the_site_covers_the_cluster():
    cluster = _cluster(
        "Payroll Software Pricing",
        "payroll software pricing",
        keywords=[
            _kw("payroll software pricing", "Primary", volume=800),
            _kw("payroll software cost", volume=400),
        ],
    )
    classify_clusters_against_site_map([cluster], pages=BASE_PAGES)

    assert cluster["topic_status"] == NEW_TOPIC
    assert cluster["url_status"] == "new_proposed_url"
    assert cluster["existing_page_match"]["matched_url"] is None
    assert "No existing page covers this topic" in cluster["topic_reason"]


def test_generic_single_token_page_is_not_an_existing_match():
    """'/pricing' must not claim 'payroll software pricing'."""
    pages = BASE_PAGES + [{"url": "https://acme.io/pricing", "path": "/pricing", "title": "Pricing"}]
    cluster = _cluster("Payroll Pricing", "payroll software pricing")
    classify_clusters_against_site_map([cluster], pages=pages)

    assert cluster["existing_page_match"]["matched_url"] is None
    assert cluster["topic_status"] == NEW_TOPIC


# --- 4. Supporting keywords -------------------------------------------------


def test_small_longtail_cluster_supports_its_parent_instead_of_getting_a_url():
    parent = _cluster(
        "CRM Pricing",
        "crm pricing",
        keywords=[
            _kw("crm pricing", "Primary", volume=4000),
            _kw("crm cost", volume=900),
            _kw("crm price comparison", volume=500),
            _kw("crm plans", volume=300),
        ],
    )
    child = _cluster(
        "CRM Pricing For Nonprofits",
        "crm pricing for nonprofits",
        keywords=[
            _kw("crm pricing for nonprofits", "Primary", volume=90),
            _kw("nonprofit crm pricing", volume=60),
        ],
    )
    classify_clusters_against_site_map([parent, child], pages=BASE_PAGES)

    assert child["topic_status"] == SUPPORTING_TOPIC
    assert child["url_status"] == "no_dedicated_url"
    assert child["supporting_parent"]["parent_cluster"] == "CRM Pricing"
    assert child["topic_disposition"] == "supporting_topic"
    assert parent["topic_status"] != SUPPORTING_TOPIC


def test_supporting_detection_requires_the_same_intent():
    parent = _cluster(
        "CRM Pricing",
        "crm pricing",
        intent="transactional",
        keywords=[_kw("crm pricing", "Primary", volume=4000), _kw("crm cost", volume=900), _kw("crm plans", volume=300)],
    )
    child = _cluster(
        "What Affects CRM Pricing",
        "crm pricing factors explained",
        intent="informational",
        keywords=[_kw("crm pricing factors explained", "Primary", volume=90)],
    )
    assert detect_supporting_topics([parent, child]) == {}


def test_semantically_adjacent_but_different_needs_are_not_collapsed():
    """Different audiences for one product are separate needs, not supporting."""
    clusters = [
        _cluster(
            "Running Shoes Beginners",
            "running shoes for beginners",
            keywords=[_kw("running shoes for beginners", "Primary", volume=900)],
        ),
        _cluster(
            "Running Shoes Flat Feet",
            "running shoes for flat feet",
            keywords=[_kw("running shoes for flat feet", "Primary", volume=800)],
        ),
    ]
    assert detect_supporting_topics(clusters) == {}


# --- 5. Cannibalization -----------------------------------------------------


def test_two_clusters_on_one_existing_url_flag_consolidation():
    pages = BASE_PAGES + [
        {
            "url": "https://acme.io/services/seo-audit",
            "path": "/services/seo-audit",
            "title": "SEO Audit Services",
            "metrics": {"clicks": 200, "impressions": 4000, "position": 5},
        }
    ]
    strong = _cluster(
        "SEO Audit Services",
        "seo audit services",
        keywords=[
            _kw("seo audit services", "Primary", volume=900),
            _kw("seo audit agency", volume=300),
            _kw("seo audit firm", volume=200),
        ],
    )
    weak = _cluster(
        "SEO Audit Company",
        "seo audit company",
        keywords=[
            _kw("seo audit company", "Primary", volume=400),
            _kw("seo audit providers", volume=120),
            _kw("seo audit consultants", volume=90),
        ],
    )
    report = classify_clusters_against_site_map([strong, weak], pages=pages)

    assert weak["topic_status"] == EXISTING_TOPIC_NEEDS_CONSOLIDATION
    assert weak["cannibalization"]["competes_with_cluster"] == "SEO Audit Services"
    assert weak["cannibalization"]["recommended_action"] == "consolidate_into_owner"
    assert weak["cannibalization"]["trigger"] == "shared_existing_url"
    # The higher-value cluster keeps ownership of the page.
    assert strong["topic_status"] == EXISTING_TOPIC
    assert report["existing_review_count"] >= 1


def test_two_existing_pages_competing_for_one_cluster_flag_consolidation():
    pages = BASE_PAGES + [
        {"url": "https://acme.io/services/seo-audit", "path": "/services/seo-audit", "title": "SEO Audit Services"},
        {"url": "https://acme.io/seo-audit-services", "path": "/seo-audit-services", "title": "SEO Audit Services"},
    ]
    cluster = _cluster(
        "SEO Audit",
        "seo audit services",
        keywords=[_kw("seo audit services", "Primary", volume=900), _kw("seo audit agency", volume=200)],
    )
    classify_clusters_against_site_map([cluster], pages=pages)

    assert cluster["topic_status"] == EXISTING_TOPIC_NEEDS_CONSOLIDATION
    assert "Two existing pages" in cluster["topic_reason"]


# --- 6. Materially different intent ----------------------------------------


def test_similar_keywords_with_different_intent_are_flagged_not_merged():
    pages = BASE_PAGES + [
        {
            "url": "https://acme.io/running-shoes",
            "path": "/running-shoes",
            "title": "Running Shoes",
            "metrics": {"clicks": 500, "impressions": 9000, "position": 4},
        }
    ]
    informational = _cluster(
        "Choosing Running Shoes",
        "how to choose running shoes",
        intent="informational",
        keywords=[
            _kw("how to choose running shoes", "Primary", intent="informational", volume=900),
            _kw("running shoes guide", intent="informational", volume=300),
            _kw("choosing running shoes", intent="informational", volume=200),
        ],
    )
    transactional = _cluster(
        "Buy Running Shoes",
        "buy running shoes online",
        intent="transactional",
        keywords=[
            _kw("buy running shoes online", "Primary", intent="transactional", volume=1500),
            _kw("running shoes for sale", intent="transactional", volume=600),
            _kw("order running shoes", intent="transactional", volume=200),
        ],
    )
    classify_clusters_against_site_map([informational, transactional], pages=pages)

    assert informational["topic_status"] == CANNIBALIZATION_RISK
    assert informational["cannibalization"]["different_intent"] is True
    assert informational["cannibalization"]["recommended_action"] == "keep_separate_review_intent"
    # Explicitly NOT merged into the transactional cluster.
    assert informational["url_status"] != "no_dedicated_url" or informational.get("supporting_parent") is None
    assert transactional["topic_status"] != SUPPORTING_TOPIC


# --- 7. Ambiguous case ------------------------------------------------------


def test_mixed_intent_cluster_is_flagged_uncertain():
    cluster = _cluster(
        "CRM Software",
        "crm software",
        keywords=[
            _kw("crm software", "Primary", intent="informational", volume=900),
            _kw("buy crm software", intent="transactional", volume=400),
            _kw("what is crm software", intent="informational", volume=300),
        ],
    )
    assert mixed_intent_keywords(cluster) == ["informational", "transactional"]

    classify_clusters_against_site_map([cluster], pages=BASE_PAGES)
    assert cluster["topic_status"] == UNCERTAIN
    assert cluster["topic_needs_review"] is True
    assert cluster["provisional_topic_status"] == NEW_TOPIC
    assert "split or confirm before mapping" in cluster["topic_reason"]


def test_no_site_inventory_makes_every_call_uncertain():
    cluster = _cluster("Payroll", "payroll software pricing")
    report = classify_clusters_against_site_map([cluster], pages=[])

    assert cluster["topic_status"] == UNCERTAIN
    assert cluster["topic_confidence"] < 0.4
    # The pipeline still flows on the provisional call rather than stalling.
    assert cluster["topic_disposition"] == "new_topic"
    assert report["uncertain_count"] == 1


# --- Cross-cutting invariants ----------------------------------------------


def test_every_cluster_gets_a_status_a_reason_and_a_confidence():
    clusters = [
        _cluster("A", "small business crm"),
        _cluster("B", "payroll software pricing"),
        _cluster("C", "how to choose a crm", intent="informational"),
    ]
    report = classify_clusters_against_site_map(clusters, pages=BASE_PAGES)

    for cluster in clusters:
        assert cluster["topic_status"] in TOPIC_STATUSES
        assert isinstance(cluster["topic_reason"], str) and cluster["topic_reason"].strip()
        assert 0.0 <= cluster["topic_confidence"] <= 1.0
        assert cluster["url_status"] in {
            "existing_url",
            "existing_url_needs_optimization",
            "existing_url_needs_consolidation",
            "new_proposed_url",
            "no_dedicated_url",
        }
        # Traceable back to the evidence that produced it.
        assert cluster["topic_evidence"]["primary_keyword"]
        assert cluster["topic_evidence"]["site_map_pages_compared"] == len(BASE_PAGES)

    assert report["clusters_classified"] == 3
    assert sum(report["counts"].values()) == 3


@pytest.mark.parametrize("status_key", sorted(TOPIC_STATUSES))
def test_report_exposes_every_status_bucket(status_key):
    report = classify_clusters_against_site_map([_cluster("A", "small business crm")], pages=BASE_PAGES)
    assert status_key in report["by_status"]
    assert status_key in report["counts"]
