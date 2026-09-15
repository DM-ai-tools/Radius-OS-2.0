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
    assert entry["proposed_url"] == "/blog/payroll-software-pricing"
    assert entry["url_status"] == "new_proposed_url"


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
    assert child_row["url_status"] == "no_dedicated_url"
    assert child_row["proposed_url"] is None
    # Repointed at whatever URL the parent actually owns.
    assert child_row["selected_url"] == (parent_row["selected_url"] or parent_row["proposed_url"])
    assert report["summary"]["no_dedicated_url"] == 1


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
