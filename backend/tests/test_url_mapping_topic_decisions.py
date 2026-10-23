"""URL mapping executes the Phase 5 topic decision — it does not re-decide it.

Also covers the scoring-attribution and collision rules that let URL mapping
claim an existing page it never actually matched.
"""

from __future__ import annotations

from app.services.topic_classification import classify_clusters_against_site_map
from app.services.url_mapping import (
    build_final_url_map,
    map_cluster_to_url,
    real_match_candidates,
    resolve_sheet_url_columns,
)

PAGES = [
    {"url": "https://acme.io/about", "path": "/about", "title": "About"},
    {"url": "https://acme.io/blog", "path": "/blog", "title": "Blog"},
    {"url": "https://acme.io/contact", "path": "/contact", "title": "Contact"},
    {"url": "https://acme.io/team", "path": "/team", "title": "Team"},
    {"url": "https://acme.io/careers", "path": "/careers", "title": "Careers"},
    {
        "url": "https://acme.io/crm/small-business",
        "path": "/crm/small-business",
        "title": "CRM for Small Business",
        "metrics": {"clicks": 140, "impressions": 3000, "position": 6},
    },
]


def _kw(keyword, role="Secondary", *, intent="commercial", volume=200):
    return {"keyword": keyword, "role": role, "intent": intent, "volume": volume}


def _crm_cluster():
    return {
        "name": "Small Business CRM",
        "primary_keyword": "small business crm",
        "intent": "commercial",
        "funnel": "MOFU",
        "keywords": [
            _kw("small business crm", "Primary", volume=2400),
            _kw("best crm for small business", volume=1800),
            _kw("crm software for small businesses", volume=900),
        ],
    }


# --- classification governs the URL action ---------------------------------


def test_existing_topic_maps_to_existing_url_not_a_new_one():
    cluster = _crm_cluster()
    classify_clusters_against_site_map([cluster], pages=PAGES)
    entry = map_cluster_to_url(cluster, crawled_pages=PAGES)

    assert entry["action"] == "OPTIMIZE_EXISTING"
    assert entry["current_url"] == "/crm/small-business"
    assert entry["proposed_url"] is None
    assert entry["url_status"] == "existing_url"
    assert entry["target_type"] == "existing_page"
    assert entry["dedicated_url"] is True
    assert entry["mapping_reason"]


def test_new_topic_gets_a_proposed_url():
    cluster = {
        "name": "Payroll Software Pricing",
        "primary_keyword": "payroll software pricing",
        "intent": "commercial",
        "keywords": [_kw("payroll software pricing", "Primary", volume=800), _kw("payroll software cost")],
    }
    classify_clusters_against_site_map([cluster], pages=PAGES)
    entry = map_cluster_to_url(cluster, crawled_pages=PAGES)

    assert entry["action"] == "CREATE"
    assert entry["current_url"] is None
    # The prefix follows the resolved page type rather than one hardcoded
    # default. A plain commercial keyword is a service page; the routing for
    # the other intents is pinned in test_proposed_url_prefix_follows_page_type.
    # A "pricing" keyword resolves to a landing page, so the prefix is /pricing/.
    assert entry["proposed_url"].startswith("/pricing/")
    assert entry["proposed_url"].strip("/").endswith("payroll-software-pricing")
    assert entry["url_status"] == "new_proposed_url"


def test_proposed_url_prefix_follows_page_type_not_a_single_default():
    """URL mapping used to send every new topic to one hardcoded prefix —
    first /blog/, then /services/. The prefix now follows the page type the
    cluster's keyword and intent resolve to, so a how-to and a pricing page
    stop landing in the same folder."""
    cases = [
        ("how to choose payroll software", "informational", "/guides/"),
        ("best payroll software", "commercial", "/blog/"),
        ("buy payroll software", "transactional", "/pricing/"),
        ("payroll software vs excel", "commercial", "/compare/"),
        ("payroll software pricing", "commercial", "/pricing/"),
        ("payroll software services", "commercial", "/services/"),
    ]
    for primary, intent, expected_prefix in cases:
        cluster = {
            "name": primary.title(),
            "primary_keyword": primary,
            "intent": intent,
            "keywords": [_kw(primary, "Primary", intent=intent, volume=800)],
        }
        classify_clusters_against_site_map([cluster], pages=PAGES)
        entry = map_cluster_to_url(cluster, crawled_pages=PAGES)
        assert entry["action"] == "CREATE", (primary, entry["action"])
        assert entry["proposed_url"].startswith(expected_prefix), (
            primary,
            intent,
            entry["proposed_url"],
        )


def test_a_service_or_guide_query_is_not_filed_under_pricing():
    """Company, city, and meaning phrasings are one category, not one page each.

    The workbook titles the page with the offer ("Search Engine Optimisation")
    and keeps "near me", "company", and "sydney" in the secondary column.
    """
    cases = [
        "seo company near me",
        "search engine optimisation business",
        "seo meaning in business",
    ]
    for primary in cases:
        cluster = {
            "name": primary.title(),
            "primary_keyword": primary,
            "intent": "commercial",
            "content_type": "landing",
            "recommended_url": f"/pricing/{primary.replace(' ', '-')}/",
            "keywords": [_kw(primary, "Primary", intent="commercial", volume=90)],
        }
        entry = map_cluster_to_url(
            cluster,
            crawled_pages=PAGES,
            offer_index={
                "seo": "search engine optimisation",
                "search engine optimisation": "search engine optimisation",
            },
        )
        assert entry["proposed_url"] == "/services/search-engine-optimisation/", entry["proposed_url"]
        assert entry["l1_category"] == "Search Engine Optimisation"
        assert entry["l2_subcategory"] is None
        assert entry["page_type"] == "Hub Page"


def test_same_service_line_publishes_one_page():
    keywords = [
        ("search engine optimisation business", 90),
        ("search engine optimisation company australia", 1300),
        ("seo company near me", 260),
        ("seo marketing sydney", 2900),
        ("seo meaning in business", 90),
        ("seo", 500),
        ("search engine optimization", 700),
        ("search engine optimisation", 400),
        ("technical seo", 800),
    ]
    clusters = [
        {
            "name": keyword.title(),
            "primary_keyword": keyword,
            "intent": "commercial",
            "keywords": [_kw(keyword, "Primary", volume=volume)],
        }
        for keyword, volume in keywords
    ]
    report = build_final_url_map(
        {"clusters": clusters},
        website={"pages": PAGES},
        offer_synonyms=[
            {
                "service": "Search Engine Optimisation",
                "suggested": ["seo", "search engine optimization"],
                "added": ["seo"],
            }
        ],
    )
    rows = report["final_url_map"]
    by_url = {r.get("proposed_url"): r for r in rows}
    hub = by_url["/services/search-engine-optimisation/"]
    assert hub["l1_category"] == "Search Engine Optimisation"
    assert hub["primary_keyword"] == "seo marketing sydney"
    secondaries = " ".join(hub["secondary_keywords_sheet"]).lower()
    assert "seo company near me" in secondaries
    assert "seo meaning in business" in secondaries
    assert "search engine optimisation business" in secondaries
    assert "seo" in secondaries.split(", ") or " seo " in f" {secondaries} "
    assert "search engine optimization" in secondaries
    assert "search engine optimisation" in secondaries
    assert all(r.get("primary_keyword") not in {"seo", "search engine optimization", "search engine optimisation"} for r in rows)
    assert all(r.get("l1_category") != "Seo Company Near Me" for r in rows)
    technical = [r for r in rows if r.get("primary_keyword") == "technical seo"]
    assert len(technical) == 1
    assert technical[0]["proposed_url"] != hub["proposed_url"]


def test_the_same_rule_applies_to_every_service():
    clusters = [
        {
            "name": "Email Marketing",
            "primary_keyword": "email marketing",
            "intent": "commercial",
            "keywords": [_kw("email marketing", "Primary", volume=1000)],
        },
        {
            "name": "Email Marketing Agency Sydney",
            "primary_keyword": "email marketing agency sydney",
            "intent": "commercial",
            "keywords": [_kw("email marketing agency sydney", "Primary", volume=200)],
        },
        {
            "name": "Pay Per Click",
            "primary_keyword": "pay per click",
            "intent": "commercial",
            "keywords": [_kw("pay per click", "Primary", volume=800)],
        },
        {
            "name": "PPC",
            "primary_keyword": "ppc",
            "intent": "commercial",
            "keywords": [_kw("ppc", "Primary", volume=300)],
        },
    ]
    report = build_final_url_map(
        {"clusters": clusters},
        website={"pages": PAGES},
        offer_synonyms=[
            {"service": "PPC Management", "suggested": ["ppc", "pay per click"], "added": ["pay per click"]}
        ],
    )
    rows = report["final_url_map"]
    email = [r for r in rows if "email" in str(r.get("primary_keyword") or "")]
    ppc = [r for r in rows if r.get("offer_line") == "ppc management"]
    assert len(email) == 1
    assert email[0]["primary_keyword"] == "email marketing"
    assert "email marketing agency sydney" in " ".join(email[0]["secondary_keywords_sheet"]).lower()
    assert len(ppc) == 1
    assert ppc[0]["primary_keyword"] in {"pay per click", "ppc"}


def test_supporting_cluster_gets_no_dedicated_url_and_folds_into_its_parent():
    parent = {
        "name": "CRM Pricing",
        "primary_keyword": "crm pricing",
        "intent": "commercial",
        "keywords": [
            _kw("crm pricing", "Primary", volume=4000),
            _kw("crm cost", volume=900),
            _kw("crm plans", volume=500),
        ],
    }
    child = {
        "name": "CRM Pricing For Nonprofits",
        "primary_keyword": "crm pricing for nonprofits",
        "intent": "commercial",
        "keywords": [_kw("crm pricing for nonprofits", "Primary", volume=90), _kw("nonprofit crm pricing", volume=60)],
    }
    classify_clusters_against_site_map([parent, child], pages=PAGES)
    report = build_final_url_map({"clusters": [parent, child]}, website={"pages": PAGES})

    rows = {r["cluster"]: r for r in report["final_url_map"]}
    child_row = rows["CRM Pricing For Nonprofits"]
    parent_row = rows["CRM Pricing"]

    assert child_row["dedicated_url"] is False
    assert child_row["proposed_url"] is None
    # Repointed at whatever URL the parent actually owns.
    assert child_row["selected_url"] == (parent_row["selected_url"] or parent_row["proposed_url"])
    # The parent's page does not exist yet, so the child folds into that
    # yet-to-be-created page (not "review/merge/redirect": nothing to merge).
    assert parent_row["action"] == "CREATE"
    assert child_row["action"] == "FOLD_INTO_NEW_PARENT"
    assert child_row["url_status"] == "fold_into_new_parent"
    assert child_row["parent_cluster_id"] == parent_row["cluster_id"]
    assert report["summary"]["fold_into_new_parent"] == 1


def test_uncertain_cluster_is_surfaced_for_review_but_still_mapped():
    cluster = {
        "name": "CRM Software",
        "primary_keyword": "crm software",
        "intent": "commercial",
        "keywords": [
            _kw("crm software", "Primary", intent="informational", volume=900),
            _kw("buy crm software", intent="transactional", volume=400),
        ],
    }
    classify_clusters_against_site_map([cluster], pages=PAGES)
    entry = map_cluster_to_url(cluster, crawled_pages=PAGES)

    assert entry["topic_status"] == "UNCERTAIN"
    assert entry["needs_human_review"] is True
    assert entry["mapping_reason"]
    # Acts on the provisional call rather than silently guessing a new answer.
    assert entry["action"] in ("CREATE", "REVIEW_MERGE_REDIRECT")


# --- scoring attribution ----------------------------------------------------


def test_score_band_describes_the_page_actually_selected():
    """A proposed URL that does not exist must not set the score band."""
    cluster = {
        "name": "Marketing Tips",
        "primary_keyword": "marketing tips",
        "intent": "informational",
        "recommended_url": "/blog/marketing-tips",  # does not exist on the site
        "keywords": [_kw("marketing tips", "Primary", intent="informational", volume=150)],
    }
    entry = map_cluster_to_url(cluster, crawled_pages=PAGES)

    assert entry["action"] == "CREATE"
    # No real candidate matched, so no score may be claimed for one.
    assert entry["url_score"] == 0
    assert entry["score_band"] == "LOW"
    # The pages looked at are still reported for auditability, but every one of
    # them is labelled as a fallback rather than a match.
    assert real_match_candidates(entry["url_candidates"]) == []


def test_competing_urls_only_lists_real_matches():
    cluster = _crm_cluster()
    entry = map_cluster_to_url(cluster, crawled_pages=PAGES)
    assert "/about" not in entry["competing_urls"]
    assert "/careers" not in entry["competing_urls"]


def test_real_match_candidates_drops_fallback_and_stub_rows():
    scored = [
        {"path": "/a", "candidate_reason": "crawl_pool_fallback", "url_score": 90},
        {"path": "/b", "candidate_reason": "suggested_url_only", "url_score": 80},
        {"path": "/c", "candidate_reason": "keyword_topic_overlap", "url_score": 40},
    ]
    assert [c["path"] for c in real_match_candidates(scored)] == ["/c"]


# --- collisions -------------------------------------------------------------


def test_two_clusters_proposing_the_same_new_url_are_flagged():
    clusters = [
        {
            "name": "Best CRM Software",
            "primary_keyword": "best crm software",
            "intent": "commercial",
            "recommended_url": "/blog/best-crm-software",
            "keywords": [_kw("best crm software", "Primary", volume=5000)],
        },
        {
            "name": "Top CRM Software",
            "primary_keyword": "best crm software",
            "intent": "commercial",
            "recommended_url": "/blog/best-crm-software",
            "keywords": [_kw("best crm software", "Primary", volume=900)],
        },
    ]
    report = build_final_url_map({"clusters": clusters}, website={"pages": PAGES})

    collisions = report["duplicate_proposed_urls"]
    assert len(collisions) == 1
    assert collisions[0]["proposed_url"] == "/blog/best-crm-software"
    assert report["summary"]["duplicate_proposed_urls"] == 1
    flagged = [r for r in report["final_url_map"] if r["cannibalization_risk"]]
    assert flagged and all(r["needs_human_review"] for r in flagged)


# --- regression: sheet column resolution ------------------------------------


def test_resolve_sheet_url_columns_always_returns_a_pair():
    """Previously fell off the end returning None, crashing the tuple unpack."""
    assert resolve_sheet_url_columns(
        current_url=None, proposed_url=None, action="OPTIMIZE_EXISTING", selected_url=None, create_url=None
    ) == (None, None)
    assert resolve_sheet_url_columns(current_url="/a/", proposed_url=None) == ("/a", None)
    assert resolve_sheet_url_columns(current_url=None, proposed_url="/b/") == (None, "/b")
    assert resolve_sheet_url_columns(
        current_url=None, proposed_url=None, action="CREATE", create_url="/c"
    ) == (None, "/c")


# --- every mapping carries a reason ----------------------------------------


def test_every_mapped_row_has_a_reason_and_a_confidence():
    clusters = [_crm_cluster(), {
        "name": "Payroll Pricing",
        "primary_keyword": "payroll software pricing",
        "intent": "commercial",
        "keywords": [_kw("payroll software pricing", "Primary", volume=800)],
    }]
    classify_clusters_against_site_map(clusters, pages=PAGES)
    report = build_final_url_map({"clusters": clusters}, website={"pages": PAGES})

    for row in report["final_url_map"]:
        assert isinstance(row["mapping_reason"], str) and row["mapping_reason"].strip()
        assert 0.0 <= row["confidence"] <= 1.0
        assert row["cluster_id"]
        assert row["url_status"]
