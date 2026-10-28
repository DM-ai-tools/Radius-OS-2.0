"""Per-service scope: which sub-services the client actually offers."""

from app.services.service_scope import (
    CLASSIFIER_VERSION,
    REJECT_REASON,
    build_default_scope,
    excluded_hits,
    finalize_scope,
    normalize_scope,
    partition_scope_rejects,
    resolve_scope,
    scope_signal,
    scope_violations,
    sub_allowed,
    suggest_exclusions,
)

SMM = {
    "name": "Social Media Marketing",
    "offered": [{"id": "meta ads", "name": "Meta Ads"}, "Instagram Ads"],
    "excluded": ["TikTok Ads", "LinkedIn Ads", "Pinterest Ads", "Twitter/X Ads"],
}


def _prio(**extra):
    return {
        "confirmed_at": "x",
        "selected_service_ids": ["social media marketing"],
        "selected_subservice_ids": [],
        "service_scope": {"social media marketing": SMM},
        **extra,
    }


def test_normalize_accepts_plain_strings_and_dicts_and_dedupes():
    scope = normalize_scope(
        {"Social Media Marketing": {**SMM, "offered": SMM["offered"] + ["meta ads"]}}
    )
    entry = scope["social media marketing"]
    assert [o["name"] for o in entry["offered"]] == ["Meta Ads", "Instagram Ads"]
    assert entry["offered"][1]["id"] == "instagram ads"
    assert [e["name"] for e in entry["excluded"]][0] == "TikTok Ads"


def test_offered_wins_when_a_name_is_in_both_lists():
    scope = normalize_scope(
        {"smm": {"name": "SMM", "offered": ["Meta Ads"], "excluded": ["meta ads", "TikTok Ads"]}}
    )
    assert [e["name"] for e in scope["smm"]["excluded"]] == ["TikTok Ads"]


def test_normalize_tolerates_garbage():
    assert normalize_scope(None) == {}
    assert normalize_scope({"a": "nope"}) == {}
    assert normalize_scope({"a": {"offered": [None, 3, {}]}})["a"]["offered"] == []


def test_resolve_scope_by_service_slug_name_or_member_sub_service():
    prio = _prio()
    assert resolve_scope(prio, "social-media-marketing")[0] == "social media marketing"
    assert resolve_scope(prio, "Social Media Marketing")[0] == "social media marketing"
    assert resolve_scope(prio, "tiktok ads")[0] == "social media marketing"
    assert resolve_scope(prio, "meta ads")[0] == "social media marketing"
    assert resolve_scope(prio, "seo") is None


def test_sub_allowed_scoped_service_is_strict():
    prio = _prio()
    assert sub_allowed(prio, "social media marketing", "meta ads", "Meta Ads")
    assert sub_allowed(prio, "social media marketing", "instagram ads", "Instagram Ads")
    assert not sub_allowed(prio, "social media marketing", "tiktok-ads", "TikTok Ads")
    # a sub-service nobody mentioned is not offered either
    assert not sub_allowed(prio, "social media marketing", "snapchat ads", "Snapchat Ads")


def test_sub_allowed_scoped_with_empty_offered_means_none():
    prio = {"service_scope": {"seo": {"name": "SEO", "offered": [], "excluded": []}}}
    assert not sub_allowed(prio, "seo", "local seo", "Local SEO")


def test_sub_allowed_unscoped_keeps_legacy_flat_list_behaviour():
    legacy_empty = {"selected_subservice_ids": []}
    assert sub_allowed(legacy_empty, "seo", "local seo", "Local SEO")
    legacy_set = {"selected_subservice_ids": ["local seo"]}
    assert sub_allowed(legacy_set, "seo", "local seo", "Local SEO")
    assert not sub_allowed(legacy_set, "seo", "link building", "Link Building")


def test_excluded_hits_catches_the_unoffered_platform():
    entry = normalize_scope({"s": SMM})["s"]
    assert excluded_hits("tiktok ads agency melbourne", entry) == ["TikTok Ads"]
    assert excluded_hits("How to run LinkedIn campaigns", entry) == ["LinkedIn Ads"]
    assert excluded_hits("twitter ad costs", entry) == ["Twitter/X Ads"]


def test_excluded_hits_ignores_offered_and_unrelated_text():
    entry = normalize_scope({"s": SMM})["s"]
    assert excluded_hits("meta ads management melbourne", entry) == []
    assert excluded_hits("instagram ads for restaurants", entry) == []
    assert excluded_hits("social media marketing agency", entry) == []


def test_alias_family_covers_facebook_when_meta_is_offered():
    entry = normalize_scope(
        {"s": {"name": "SMM", "offered": ["Meta Ads"], "excluded": ["Facebook Ads", "TikTok Ads"]}}
    )["s"]
    assert excluded_hits("facebook ads melbourne", entry) == []
    assert excluded_hits("tiktok ads", entry) == ["TikTok Ads"]


def test_multi_token_exclusion_needs_every_token():
    entry = {"name": "Email", "offered": ["Email Marketing"], "excluded": ["SMS Automation"]}
    assert excluded_hits("sms automation tools", entry) == ["SMS Automation"]
    assert excluded_hits("marketing automation tools", entry) == []


def test_generic_only_exclusion_matches_as_a_phrase():
    entry = {"name": "X", "offered": ["Meta Ads"], "excluded": ["Social Ads"]}
    assert excluded_hits("social ads agency", entry) == ["Social Ads"]
    assert excluded_hits("social media agency", entry) == []


def test_partition_scope_rejects_drops_only_scoped_hits():
    rows = [
        {"keyword": "tiktok ads melbourne", "target": "meta ads"},
        {"keyword": "meta ads melbourne", "target": "meta ads"},
        {"keyword": "tiktok marketing", "target": "seo"},  # names TikTok even though SEO has no scope
        {"keyword": "linkedin ads cost", "target": "Social Media Marketing"},
    ]
    kept, dropped = partition_scope_rejects(rows, _prio())
    assert [r["keyword"] for r in kept] == ["meta ads melbourne"]
    assert [r["keyword"] for r in dropped] == [
        "tiktok ads melbourne",
        "tiktok marketing",
        "linkedin ads cost",
    ]
    assert dropped[0]["relevance_drop_reason"] == REJECT_REASON
    assert dropped[0]["classifier_version"] == CLASSIFIER_VERSION
    assert dropped[0]["excluded_sub_service"] == "TikTok Ads"
    assert dropped[0]["originating_service"] == "meta ads"


def test_partition_without_scope_is_a_noop():
    rows = [{"keyword": "tiktok ads", "target": "meta ads"}]
    kept, dropped = partition_scope_rejects(rows, {"selected_service_ids": ["meta ads"]})
    assert kept == rows and dropped == []


def test_scope_signal_scoped_unscoped_and_unknown():
    prio = _prio(
        service_catalog=[
            {"id": "seo", "name": "SEO", "subservices": [{"id": "local seo", "name": "Local SEO"}]},
            {"id": "pr", "name": "PR", "subservices": []},
        ]
    )
    scoped = scope_signal(prio, "social-media-marketing")
    assert scoped["scoped"] is True
    assert scoped["offered"] == ["Meta Ads", "Instagram Ads"]
    assert "TikTok Ads" in scoped["excluded"]
    unscoped = scope_signal(prio, "seo")
    assert unscoped["scoped"] is False and unscoped["offered"] == []
    assert scope_signal(prio, "pr") is None  # no sub-services → nothing to say
    assert scope_signal(prio, "unknown thing") is None


def test_scope_violations_reports_text_and_sub_service():
    sig = scope_signal(_prio(), "social media marketing")
    found = scope_violations(["meta ads tips", "TikTok Ads for retailers"], sig)
    assert found == [{"text": "TikTok Ads for retailers", "sub_service": "TikTok Ads"}]
    assert scope_violations(["tiktok ads"], {"scoped": False, "excluded": [], "offered": []}) == []
    assert scope_violations(["tiktok ads"], None) == []


def test_suggest_exclusions_social_family_skips_covered_and_existing():
    names = [s["name"] for s in suggest_exclusions("Social Media Marketing", ["Meta Ads"], ["LinkedIn Ads"])]
    assert "TikTok Ads" in names and "Pinterest Ads" in names
    assert "Facebook Ads" not in names and "Instagram Ads" not in names  # covered by Meta
    assert "LinkedIn Ads" not in names  # already excluded


def test_suggest_exclusions_unknown_family_is_empty():
    assert suggest_exclusions("Plumbing Repairs", ["Drain Cleaning"]) == []


def test_suggest_exclusions_paid_search_never_excludes_the_parent_itself():
    names = [s["name"] for s in suggest_exclusions("Google Ads/PPC Management", ["Shopping Ads"])]
    assert "Google Ads" not in names
    assert "Bing Ads" in names


def test_build_default_scope_offers_site_and_manual_but_not_competitor_subs():
    catalog = [
        {
            "id": "social media marketing",
            "name": "Social Media Marketing",
            "subservices": [
                {"id": "meta ads", "name": "Meta Ads", "source": "website"},
                {"id": "tiktok-ads", "name": "TikTok Ads", "source": "competitor"},
                {"id": "reels", "name": "Reels", "source": "manual"},
            ],
        },
        {"id": "seo", "name": "SEO", "subservices": []},
    ]
    scope = build_default_scope(catalog, ["social media marketing"])
    entry = scope["social media marketing"]
    assert [o["name"] for o in entry["offered"]] == ["Meta Ads", "Reels"]
    excluded = [e["name"] for e in entry["excluded"]]
    assert excluded[0] == "TikTok Ads" and "LinkedIn Ads" in excluded
    assert "seo" not in scope  # not selected → no entry


def test_finalize_scope_stamps_filters_and_derives_flat_ids():
    payload = {
        "selected_service_ids": ["social media marketing", "seo"],
        "selected_subservice_ids": ["meta ads", "tiktok-ads", "local seo"],
        "service_catalog": [
            {
                "id": "social media marketing",
                "name": "Social Media Marketing",
                "subservices": [
                    {"id": "meta ads", "name": "Meta Ads"},
                    {"id": "tiktok-ads", "name": "TikTok Ads"},
                ],
            },
            {"id": "seo", "name": "SEO", "subservices": [{"id": "local seo", "name": "Local SEO"}]},
        ],
        "service_scope": {
            "social media marketing": {
                "name": "Social Media Marketing",
                "offered": ["Meta Ads"],
                "excluded": [{"id": "tiktok-ads", "name": "TikTok Ads"}],
            },
            "not selected": {"name": "Nope", "offered": ["X"], "excluded": []},
        },
    }
    finalize_scope(payload)
    assert set(payload["service_scope"]) == {"social media marketing"}
    assert payload["service_scope"]["social media marketing"]["decided_at"]
    # tiktok is governed by the scope and dropped; unscoped seo's legacy id survives
    assert payload["selected_subservice_ids"] == ["local seo", "meta ads"]


def test_finalize_scope_with_no_scope_leaves_legacy_ids_alone():
    payload = {"selected_service_ids": ["seo"], "selected_subservice_ids": ["local seo"]}
    finalize_scope(payload)
    assert payload["service_scope"] == {}
    assert payload["selected_subservice_ids"] == ["local seo"]


def test_suggestions_skip_siblings_a_listed_subservice_already_covers():
    listed = ["Google Ads Management", "Bing Ads Management", "Shopping Ads", "Display Advertising"]
    names = [
        s["name"]
        for s in suggest_exclusions("PPC Management", ["Google Ads Management"], [], existing=listed)
    ]
    assert "Bing Ads" not in names  # "Bing Ads Management" is listed
    assert "Display Ads" not in names  # "Display Advertising" is listed
    assert "Shopping Ads" not in names
    assert "YouTube Ads" in names


def test_default_scope_does_not_block_what_a_subservice_already_names():
    catalog = [
        {
            "id": "ppc management",
            "name": "PPC Management",
            "source": "website",
            "subservices": [
                {"id": "google ads management", "name": "Google Ads Management", "source": "website"},
                {"id": "bing ads management", "name": "Bing Ads Management", "source": "industry"},
            ],
        }
    ]
    entry = build_default_scope(catalog, ["ppc management"])["ppc management"]
    blocked = [e["name"] for e in entry["excluded"]]
    assert "Bing Ads Management" in blocked  # listed but not offered
    assert "Bing Ads" not in blocked  # not blocked a second time under another name
