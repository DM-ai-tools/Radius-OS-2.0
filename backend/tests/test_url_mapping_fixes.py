"""URL mapping fixes (2026-10-22 brief): every row consistent, scored on the
signals it actually had, and checked against the other proposals and the site."""

from __future__ import annotations

from app.services.url_map_report import DECISION_HEADERS, decision_row, score_text
from app.services.url_mapping import (
    COMPONENT_MAX,
    FOLD_INTO_NEW_PARENT,
    _consistency_errors,
    _prior_score_on_this_scale,
    _priority_with_reason,
    _sheet_page_type,
    _site_url_conventions,
    _taxonomy_levels,
    build_final_url_map,
    check_url_map_consistency,
    apply_workbook_taxonomy,
    map_cluster_to_url,
    score_ranking_evidence,
    score_semantic_match,
    score_url_candidate,
    site_traffic_stats,
    url_hygiene,
)


def _kw(keyword, role="Secondary", volume=100, intent="commercial"):
    return {"keyword": keyword, "role": role, "volume": volume, "intent": intent}


PAGES = [
    {"url": "https://acme.com.au/services/seo-melbourne/", "title": "SEO Melbourne | Acme", "h1": "SEO Melbourne",
     "page_type": "service", "word_count": 1400, "content_quality": "good", "last_modified": "2026-08-01"},
    {"url": "https://acme.com.au/services/google-ads/", "title": "Google Ads Management",
     "h1": "Google Ads Management", "page_type": "service", "word_count": 220, "content_quality": "thin"},
    {"url": "https://acme.com.au/services/web-design/", "title": "Web Design", "h1": "Web design",
     "page_type": "service", "canonical_url": "https://acme.com.au/web-design/"},
    {"url": "https://acme.com.au/blog/", "title": "Blog"},
    {"url": "https://acme.com.au/blog/what-is-seo/", "title": "What Is SEO? A Guide", "h1": "What is SEO",
     "page_type": "blog"},
    {"url": "https://acme.com.au/about/", "title": "About Acme"},
]


def _map(clusters, pages=PAGES, **kwargs):
    return build_final_url_map({"clusters": clusters}, website={"pages": pages}, client_domain="acme.com.au", **kwargs)


# --------------------------------------------------------------------------- consistency guard


def test_guard_rejects_each_contradictory_combination():
    assert _consistency_errors({"action": "CREATE", "current_url": "/a", "proposed_url": None, "status": "LIVE"})
    assert _consistency_errors({"action": "OPTIMIZE_EXISTING", "current_url": None, "status": "NEW"})
    assert _consistency_errors({"action": "OPTIMIZE_EXISTING", "current_url": "/a", "proposed_url": "/b", "status": "LIVE"})
    assert _consistency_errors({"action": "CREATE", "proposed_url": "/b", "status": "LIVE"})
    assert _consistency_errors({"action": FOLD_INTO_NEW_PARENT, "status": "NEW"})  # no parent id
    assert _consistency_errors({"action": "OPTIMIZE_EXISTING", "current_url": "/a", "status": "LIVE",
                                "url_status": "new_proposed_url"})
    assert not _consistency_errors({"action": "CREATE", "proposed_url": "/b", "status": "NEW",
                                    "url_status": "new_proposed_url"})


def test_new_topic_with_a_strong_live_match_becomes_optimize_not_create_with_a_url():
    cluster = {
        "name": "Google Ads Management", "intent": "commercial", "topic_status": "NEW_TOPIC",
        "keywords": [_kw("google ads management", "Primary", 880), _kw("google ads agency", volume=590)],
    }
    row = _map([cluster])["final_url_map"][0]
    assert row["action"] == "OPTIMIZE_EXISTING"
    assert row["current_url"] == "/services/google-ads" and not row["proposed_url"]
    assert row["topic_status_original"] == "NEW_TOPIC"
    assert row["topic_status"] == "EXISTING_TOPIC_NEEDS_OPTIMIZATION"
    assert not _consistency_errors(row)


def test_new_topic_with_a_weak_match_creates_and_keeps_the_weak_page_as_competing_only():
    cluster = {
        "name": "SEO Audit Checklist", "intent": "informational", "topic_status": "NEW_TOPIC",
        "keywords": [_kw("seo audit checklist", "Primary", 300, "informational")],
    }
    row = _map([cluster])["final_url_map"][0]
    assert row["action"] == "CREATE"
    assert row["current_url"] is None and row["proposed_url"]
    assert row["status"] == "NEW"
    assert not _consistency_errors(row)


def test_every_row_of_a_realistic_map_is_consistent_and_states_its_score_basis():
    clusters = [
        {"name": "SEO Melbourne", "intent": "commercial", "best_score": 72, "est_traffic": 1900,
         "keywords": [_kw("seo melbourne", "Primary", 1900), _kw("melbourne seo", volume=700)]},
        {"name": "Google Ads Management", "intent": "commercial",
         "keywords": [_kw("google ads management", "Primary", 880)]},
        {"name": "Local SEO Checklist", "intent": "informational",
         "keywords": [_kw("local seo checklist", "Primary", 320, "informational")]},
        {"name": "What Is SEO", "intent": "informational",
         "keywords": [_kw("what is seo", "Primary", 9900, "informational")]},
    ]
    report = _map(clusters)
    assert report["inconsistent_rows"] == []
    assert check_url_map_consistency(report["final_url_map"]) == []
    for row in report["final_url_map"]:
        assert row["band_source"] in ("live_candidates", "prior_classification", "no_match")
        assert "available" in row["score_basis"] and "unavailable" in row["score_basis"]
        text = score_text(row)
        assert "/100" in text
        if row["band_source"] == "live_candidates":
            assert "based on" in text


# --------------------------------------------------------------------------- live-page folds


def test_same_offer_rows_with_their_own_live_pages_are_never_silently_dropped():
    clusters = [
        {"name": "SEO Melbourne", "intent": "commercial",
         "keywords": [_kw("seo melbourne", "Primary", 1900)]},
        {"name": "What Is SEO", "intent": "informational",
         "keywords": [_kw("what is seo", "Primary", 9900, "informational")]},
    ]
    rows = _map(clusters)["final_url_map"]
    by_url = {r["current_url"]: r for r in rows}
    assert {"/services/seo-melbourne", "/blog/what-is-seo"} <= set(by_url)
    for url in ("/services/seo-melbourne", "/blog/what-is-seo"):
        assert by_url[url]["needs_human_review"]
        assert any("two pages compete" in note for note in by_url[url]["collisions"])


# --------------------------------------------------------------------------- id-keyed links


def test_pillar_and_supporting_parent_link_on_cluster_id_after_a_rename():
    from app.services.topic_classification import classify_clusters_against_site_map

    parent = {"name": "Local SEO Checklist", "intent": "informational",
              "keywords": [_kw("local seo checklist", "Primary", 320, "informational"),
                           _kw("local seo audit checklist", volume=90, intent="informational")]}
    child = {"name": "Local SEO Checklist Template", "intent": "informational",
             "keywords": [_kw("local seo checklist template", "Primary", 40, "informational")]}
    clusters = [parent, child]
    classify_clusters_against_site_map(clusters, pages=PAGES)
    assert child.get("topic_status") == "SUPPORTING_TOPIC"
    parent["name"] = "Local SEO Checklist (renamed)"
    rows = {r["cluster"]: r for r in _map(clusters)["final_url_map"]}
    folded = rows["Local SEO Checklist Template"]
    assert folded["action"] == FOLD_INTO_NEW_PARENT
    assert folded["url_status"] == "fold_into_new_parent"
    assert folded["parent_cluster_id"] == rows["Local SEO Checklist (renamed)"]["cluster_id"]
    assert folded["current_url"] is None and folded["proposed_url"] is None
    assert folded["target_url"] == rows["Local SEO Checklist (renamed)"]["proposed_url"]
    assert not folded.get("link_warnings")
    assert not _consistency_errors(folded)


def test_unresolved_supporting_parent_warns_and_keeps_its_own_decision():
    child = {"name": "CRM Pricing For Nonprofits", "intent": "commercial",
             "topic_status": "SUPPORTING_TOPIC",
             "supporting_parent": {"parent_cluster": "Gone Cluster", "parent_cluster_id": "c_doesnotexist"},
             "keywords": [_kw("crm pricing for nonprofits", "Primary", 90)]}
    row = _map([child])["final_url_map"][0]
    assert row["needs_human_review"]
    assert any("could not be resolved" in w for w in row["link_warnings"])
    assert row["action"] != FOLD_INTO_NEW_PARENT


def test_pillar_named_by_a_stale_name_is_warned_not_silently_dropped():
    cluster = {"name": "Google Ads Management", "intent": "commercial",
               "keywords": [_kw("google ads management", "Primary", 880)]}
    report = _map([cluster], pillar_structure=[{"pillar": "Paid Search", "clusters": [{"name": "Old Name"}]}])
    assert report["link_warnings"]
    assert report["summary"]["link_warnings"] == len(report["link_warnings"])


# --------------------------------------------------------------------------- scoring


def test_score_is_out_of_obtainable_points_and_lists_missing_components():
    page = {"path": "/services/google-ads", "title": "Google Ads Management", "h1": "Google Ads Management"}
    out = score_url_candidate(
        page, cluster={"name": "x"}, primary_keyword="google ads management",
        secondary_keywords=[], cluster_intent="commercial",
    )
    assert "ranking_evidence" in out["components_unavailable"]
    assert "traffic_business" in out["components_unavailable"]
    assert out["score_obtainable"] == sum(COMPONENT_MAX[k] for k in out["components_available"])
    assert 0 <= out["url_score"] <= 100
    assert abs(out["url_score"] - round(100 * out["score_raw"] / out["score_obtainable"], 2)) < 0.02
    assert out["signal_coverage"] == round(out["score_obtainable"] / 100, 2)


def test_exact_suggested_url_earns_no_extra_points_and_score_never_exceeds_100():
    page = {"path": "/services/google-ads", "title": "Google Ads Management", "h1": "Google Ads Management",
            "metrics": {"clicks": 900, "impressions": 20000, "position": 1.2}}
    kwargs = {
        "cluster": {"name": "x", "best_score": 100, "est_traffic": 9000},
        "primary_keyword": "google ads management",
        "secondary_keywords": ["google ads management"],
        "cluster_intent": "commercial",
        "site_stats": site_traffic_stats([page]),
    }
    plain = score_url_candidate(page, **kwargs)
    boosted = score_url_candidate(page, suggested_url="/services/google-ads", **kwargs)
    assert plain["url_score"] == boosted["url_score"]
    assert boosted["url_score"] <= 100


def test_semantic_match_rewards_agreement_over_a_lone_slug():
    agreeing = {"path": "/services/google-ads-management", "title": "Google Ads Management", "h1": "Google Ads Management"}
    slug_only = {"path": "/services/google-ads-management", "title": "Welcome to Acme", "h1": "Our story"}
    assert score_semantic_match("google ads management", agreeing) > score_semantic_match("google ads management", slug_only) + 8
    assert score_semantic_match("google ads management", agreeing) <= COMPONENT_MAX["semantic_match"]


def test_ranking_thresholds_are_relative_to_the_site():
    small_site = [{"path": f"/p{i}", "metrics": {"clicks": c, "impressions": c * 20}} for i, c in enumerate([1, 2, 3, 40])]
    stats = site_traffic_stats(small_site)
    top = score_ranking_evidence(small_site[-1], site_stats=stats)
    bottom = score_ranking_evidence(small_site[0], site_stats=stats)
    assert top is not None and bottom is not None and top > bottom
    assert top >= 10  # 40 clicks is this site's best page, so it earns the click and impression marks
    assert score_ranking_evidence({"path": "/x"}, site_stats=site_traffic_stats([{"path": "/x"}])) is None


def test_match_confidence_follows_the_normalized_score_and_topic_confidence_stays_separate():
    cluster = {"name": "Google Ads Management", "intent": "commercial", "topic_confidence": 0.33,
               "keywords": [_kw("google ads management", "Primary", 880)]}
    row = map_cluster_to_url(cluster, crawled_pages=[{**p, "path": p["url"]} for p in PAGES])
    assert row["match_confidence"] == round(row["url_score"] / 100, 2)
    assert row["confidence"] == row["match_confidence"]
    assert row["signal_coverage"] == row["score_basis"]["coverage"]
    assert row["topic_confidence"] != row["match_confidence"]


def test_unmatched_fallback_pages_are_not_listed_as_candidates():
    cluster = {"name": "Kubernetes Consulting", "intent": "commercial",
               "keywords": [_kw("kubernetes consulting", "Primary", 200)]}
    row = map_cluster_to_url(cluster, crawled_pages=[{**p, "path": p["url"]} for p in PAGES])
    assert row["band_source"] == "no_match"
    assert row["url_candidates"] == []
    assert row["match_confidence"] == 0.0


# --------------------------------------------------------------------------- prior classification


def test_prior_score_is_read_on_its_own_scale_and_zero_is_a_real_score():
    assert _prior_score_on_this_scale({"match_score": 0}) == 0.0
    assert _prior_score_on_this_scale({"match_score": None}) is None
    assert _prior_score_on_this_scale({"match_score": 40, "score_obtainable": 50}) == 80.0
    assert _prior_score_on_this_scale({"match_score": 72, "score_scale": "normalized_obtainable", "score_obtainable": 50}) == 72.0
    assert _prior_score_on_this_scale({"match_score": 140}) == 100.0


def test_band_from_prior_classification_is_labelled_as_such():
    cluster = {"name": "Brand Story", "intent": "informational",
               "existing_page_match": {"matched_url": "/about", "match_score": 0,
                                       "score_scale": "normalized_obtainable"},
               "keywords": [_kw("acme history", "Primary", 20, "informational")]}
    row = map_cluster_to_url(cluster, crawled_pages=[])
    assert row["band_source"] == "prior_classification"
    assert row["url_score"] == 0.0 and row["score_band"] == "LOW"
    assert "(from prior classification)" in score_text(row)


# --------------------------------------------------------------------------- taxonomy


def test_deep_url_keeps_every_level_and_page_type_matches_the_filled_levels():
    tax = _taxonomy_levels("/product-category/shoes/running/trail/waterproof/womens")
    assert [tax["l1_category"], tax["l2_subcategory"], tax["l3_subsubcategory"]] == ["Shoes", "Running", "Trail"]
    assert tax["l4_attribution"] == "Waterproof / Womens"
    assert tax["level"] == 4
    assert _sheet_page_type(tax) == "Attribution Collection"
    two = _taxonomy_levels("/product-category/shoes/running")
    assert two["level"] == 2 and _sheet_page_type(two) == "Collection"


def test_l4_takes_the_cluster_location_when_the_url_is_shallow():
    tax = _taxonomy_levels("/services/seo", attribution="Melbourne")
    assert tax["l4_attribution"] == "Melbourne" and tax["level"] == 2
    already = _taxonomy_levels("/services/seo-melbourne", attribution="Melbourne")
    assert already["l4_attribution"] is None


# --------------------------------------------------------------------------- collisions


def test_two_proposals_for_one_url_flag_both_sides_and_record_the_owner_rule():
    clusters = [
        {"name": "Kubernetes Consulting", "intent": "commercial", "recommended_url": "/services/kubernetes",
         "keywords": [_kw("kubernetes consulting", "Primary", 900)]},
        {"name": "Kubernetes Support", "intent": "commercial", "recommended_url": "/services/kubernetes",
         "keywords": [_kw("kubernetes support", "Primary", 50)]},
    ]
    report = _map(clusters)
    collision = next(c for c in report["url_collisions"] if c["type"] == "proposed_vs_proposed")
    assert collision["owner_cluster"] == "Kubernetes Consulting"
    assert collision["ownership_rule"]
    rows = {r["cluster"]: r for r in report["final_url_map"]}
    assert rows["Kubernetes Consulting"]["collisions"] and rows["Kubernetes Support"]["collisions"]
    assert rows["Kubernetes Consulting"]["needs_human_review"] and rows["Kubernetes Support"]["needs_human_review"]


def test_proposal_for_a_url_that_already_exists_on_the_site_becomes_optimize():
    from app.services.url_mapping import _convert_live_proposals

    conventions = _site_url_conventions([{"url": p["url"]} for p in PAGES])
    row = {"cluster": "Company History", "cluster_id": "c_h", "action": "CREATE", "url_status": "new_proposed_url",
           "current_url": None, "proposed_url": "/about/", "status": "NEW"}
    collisions = _convert_live_proposals([row], conventions)
    assert row["action"] == "OPTIMIZE_EXISTING"
    assert row["current_url"] == "/about" and row["proposed_url"] is None
    assert row["status"] == "LIVE"
    assert collisions[0]["type"] == "proposed_exists_on_site" and collisions[0]["ownership_rule"]
    assert not _consistency_errors(row)


def test_proposal_that_is_another_rows_live_page_flags_both_rows():
    from app.services.url_mapping import _resolve_url_collisions

    owner = {"cluster": "Google Ads", "cluster_id": "c_a", "action": "OPTIMIZE_EXISTING",
             "current_url": "/services/google-ads", "proposed_url": None}
    other = {"cluster": "Adwords", "cluster_id": "c_b", "action": "CREATE",
             "current_url": None, "proposed_url": "/services/google-ads"}
    collisions = _resolve_url_collisions([owner, other], {"paths": set()})
    assert collisions[0]["type"] == "proposed_vs_current"
    assert collisions[0]["ownership_rule"]
    assert owner["collisions"] and other["collisions"]


# --------------------------------------------------------------------------- hygiene


def test_hygiene_flags_problems_against_site_conventions_and_never_rewrites():
    conventions = _site_url_conventions([{"url": p["url"]} for p in PAGES])
    proposed = "/blog/seo/the-best-of-the-seo-guide-for-seo"
    result = url_hygiene(proposed, conventions)
    assert result["verdict"] == "review"
    joined = " ".join(result["problems"])
    assert "stop words" in joined
    assert "repeats its parent folder" in joined
    assert "trailing slash" in joined
    assert url_hygiene("/blog/", conventions)["verdict"] == "review"
    assert url_hygiene("/services/google-ads-audit/", conventions)["verdict"] == "ok"


def test_hygiene_problems_land_on_the_row_without_changing_the_url():
    clusters = [{"name": "Kubernetes Consulting", "intent": "commercial",
                 "recommended_url": "/services/the-kubernetes-consulting",
                 "keywords": [_kw("kubernetes consulting", "Primary", 200)]}]
    row = _map(clusters)["final_url_map"][0]
    assert row["action"] == "CREATE"
    assert row["proposed_url"] == "/services/the-kubernetes-consulting"
    assert row["url_hygiene"]["verdict"] == "review"
    assert any("stop words" in p for p in row["url_hygiene"]["problems"])


# --------------------------------------------------------------------------- priority and new fields


def test_priority_reason_shows_each_contribution():
    priority, reason = _priority_with_reason("Hub Page", live=False, band="LOW", volume=5900, difficulty=20)
    assert priority == "P0"
    assert reason == "Hub Page +3, 5,900/mo +3, KD 20 +1 = 7 -> P0"
    low, why = _priority_with_reason("Attribution Collection", live=False, band="LOW", volume=10, difficulty=75)
    assert low == "P3" and "KD 75 -1" in why


def test_rows_carry_the_new_evidence_fields():
    clusters = [
        {"name": "Web Design Agency", "intent": "commercial",
         "keywords": [_kw("web design agency", "Primary", 1300), _kw("web design company", volume=600)]},
        {"name": "Google Ads Management", "intent": "commercial",
         "keywords": [_kw("google ads management", "Primary", 880)]},
    ]
    rows = {r["cluster"]: r for r in _map(clusters)["final_url_map"]}
    web = rows["Web Design Agency"]
    assert web["canonical_conflict"] and web["canonical_conflicts"]
    ads = rows["Google Ads Management"]
    assert ads["existing_page_quality"]["content_quality"] == "thin"
    assert ads["effort"] in ("Medium", "High") and ads["effort_basis"]
    for row in rows.values():
        for key in ("redirect_sources", "internal_link_parents", "decision_trail", "row_no", "market", "priority_reason"):
            assert key in row, key
        assert row["decision_trail"] and row["decision_trail"][0]


# --------------------------------------------------------------------------- report tables


def test_decision_table_row_matches_its_headers_and_numbers():
    clusters = [{"name": "Google Ads Management", "intent": "commercial",
                 "keywords": [_kw("google ads management", "Primary", 880)]}]
    row = _map(clusters)["final_url_map"][0]
    cells = decision_row(row)
    assert len(cells) == len(DECISION_HEADERS)
    assert cells[0] == row["row_no"] == 1
    assert any(line.startswith("based on") for line in cells[2])


def test_docx_blueprint_has_the_three_numbered_url_map_tables():
    from app.services.report_pdf_document import _site_architecture_sections

    titles = [s.title for s in _site_architecture_sections()]
    assert any("Decision & Evidence" in t for t in titles)
    numbered = [s for s in _site_architecture_sections() if s.table_headers and s.table_headers[0] == "#"]
    assert len(numbered) >= 3


def test_notes_do_not_keep_a_weak_match_warning_after_the_row_was_folded():
    from app.services.topic_classification import classify_clusters_against_site_map

    parent = {"name": "Local SEO Checklist", "intent": "informational",
              "keywords": [_kw("local seo checklist", "Primary", 320, "informational"),
                           _kw("local seo audit checklist", volume=90, intent="informational")]}
    child = {"name": "Local SEO Checklist Template", "intent": "informational",
             "keywords": [_kw("local seo checklist template", "Primary", 40, "informational")]}
    classify_clusters_against_site_map([parent, child], pages=PAGES)
    rows = {r["cluster"]: r for r in _map([parent, child])["final_url_map"]}
    folded = rows["Local SEO Checklist Template"]
    assert folded["action"] == FOLD_INTO_NEW_PARENT
    assert "weak match" not in str(folded.get("notes"))


def test_category_sheet_puts_the_parent_in_l1_and_the_specific_page_in_l3_or_l4():
    entries = [
        {"primary_keyword": "adjustable beds", "proposed_url": "/adjustable-beds/"},
        {"primary_keyword": "king adjustable bed", "proposed_url": "/adjustable-beds/king-adjustable-bed/"},
        {"primary_keyword": "king electric adjustable bed", "proposed_url": "/adjustable-beds/king-electric-adjustable-bed/"},
        {"primary_keyword": "firm king electric adjustable bed", "proposed_url": "/adjustable-beds/firm-king-electric-adjustable-bed/"},
        {"primary_keyword": "adjustable beds brisbane", "proposed_url": "/locations/adjustable-beds-brisbane/"},
        {"primary_keyword": "ergoadjust", "proposed_url": "/brands/ergoadjust/"},
    ]
    apply_workbook_taxonomy(
        entries,
        offer_synonyms=[{"service": "Adjustable Beds", "suggested": ["adjustable bed"], "added": []}],
    )
    by_keyword = {row["primary_keyword"]: row for row in entries}
    hub = by_keyword["adjustable beds"]
    assert hub["l1_category"] == "Adjustable Beds"
    assert hub["l2_subcategory"] is None and hub["page_type"] == "Hub Page"
    king = by_keyword["king adjustable bed"]
    assert [king["l1_category"], king["l2_subcategory"], king["l3_subsubcategory"], king["l4_attribution"]] == [
        "Adjustable Beds",
        "King Adjustable Bed",
        None,
        None,
    ]
    assert king["page_type"] == "Collection"
    electric = by_keyword["king electric adjustable bed"]
    assert electric["l2_subcategory"] == "King Adjustable Bed"
    assert electric["l3_subsubcategory"] == "King Electric Adjustable Bed"
    assert electric["l4_attribution"] is None
    assert electric["page_type"] == "Attribution Collection"
    firm = by_keyword["firm king electric adjustable bed"]
    assert firm["l2_subcategory"] == "King Adjustable Bed"
    assert firm["l3_subsubcategory"] == "King Electric Adjustable Bed"
    assert firm["l4_attribution"] == "Firm King Electric Adjustable Bed"
    local = by_keyword["adjustable beds brisbane"]
    assert local["l1_category"] == "Adjustable Beds"
    assert local["l2_subcategory"] == "Adjustable Beds Brisbane"
    assert local["page_type"] == "Landing Page"
    brand = by_keyword["ergoadjust"]
    assert brand["l1_category"] == "Shop by Brand"
    assert brand["l2_subcategory"] == "Ergoadjust"
    assert brand["page_type"] == "Brand Collection"
