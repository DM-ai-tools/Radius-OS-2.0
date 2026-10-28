"""Canonical service → sub-service taxonomy (app.services.service_taxonomy).

Covers the hierarchy rules (one canonical parent, wrong parents moved), naming rules
(keyword variants are one concept, modifiers are not services, distinct offerings stay
distinct), attributes kept as metadata rather than services, determinism, and the
places the taxonomy feeds: the selection card and per-service keyword seeding.
"""

import itertools
import random

import pytest

from app.services.industry_services import build_catalog_from_taxonomy
from app.services.service_prioritization import build_prioritization_pack
from app.services.service_seeding import generate_service_seeds
from app.services.service_taxonomy import (
    analyze_name,
    canonical_name,
    refine_catalog,
    refine_taxonomy,
    remap_saved_selection,
    same_concept,
)


def svc(name, *subs, covers=()):
    return {
        "name": name,
        "covers": list(covers),
        "subservices": [s if isinstance(s, dict) else {"name": s, "covers": []} for s in subs],
    }


def tree(taxonomy):
    """{service: [sub-services]} of a refined taxonomy."""
    return {s["name"]: [sub["name"] for sub in s["subservices"]] for s in taxonomy}


def parent_of(taxonomy, sub_name):
    return [s["name"] for s in taxonomy for sub in s["subservices"] if sub["name"] == sub_name]


# ------------------------------------------------------------------ Test 1 & 2


def test_roof_repair_under_roofing_is_valid_and_untouched():
    out, report = refine_taxonomy(
        [svc("Roofing", "Roof Repair", "Roof Replacement"), svc("Plumbing", "Blocked Drains")]
    )
    assert tree(out) == {
        "Roofing": ["Roof Repair", "Roof Replacement"],
        "Plumbing": ["Blocked Drains"],
    }
    assert "moved" not in report and "promoted" not in report
    assert out[0]["subservices"][0]["taxonomy"]["parent_confidence"] == 1.0


def test_roof_repair_under_plumbing_is_reassigned_to_roofing():
    out, report = refine_taxonomy(
        [svc("Roofing", "Roof Replacement"), svc("Plumbing", "Blocked Drains", "Roof Repair")]
    )
    assert parent_of(out, "Roof Repair") == ["Roofing"]
    assert "Roof Repair" not in tree(out)["Plumbing"]
    moved = next(s for s in out[0]["subservices"] if s["name"] == "Roof Repair")
    assert moved["taxonomy"]["moved_from"] == "Plumbing"
    assert report["moved"] == [{"name": "Roof Repair", "from": "Plumbing", "to": "Roofing"}]


def test_roof_repair_under_plumbing_without_a_roofing_service_leaves_plumbing():
    out, report = refine_taxonomy([svc("Plumbing", "Blocked Drains", "Hot Water Systems", "Roof Repair")])
    assert "Roof Repair" not in tree(out)["Plumbing"]
    # never dropped: it becomes a service of its own, flagged as promoted
    assert "Roof Repair" in tree(out)
    assert report["promoted"] == [{"name": "Roof Repair", "from": "Plumbing"}]


def test_paid_social_moves_from_ppc_to_social_media_marketing():
    out, _ = refine_taxonomy(
        [
            svc("PPC Management", "Google Ads Management", "Meta Ads", "Social Media Advertising"),
            svc("Social Media Marketing", "Facebook Ads", "Instagram Ads"),
        ]
    )
    assert tree(out)["PPC Management"] == ["Google Ads Management"]
    assert {"Meta Ads", "Social Media Advertising"} <= set(tree(out)["Social Media Marketing"])


# ---------------------------------------------------------------------- Test 3


@pytest.mark.parametrize(
    "variant", ["Roof Repair", "Roof Repairs", "Roof Repair Services", "roof repair", "ROOF REPAIRS"]
)
def test_spellings_of_roof_repair_are_one_concept_with_one_name(variant):
    assert canonical_name(variant) == "Roof Repair"
    assert same_concept(variant, "Roof Repair")


def test_duplicate_spellings_merge_into_one_sub_service_and_keep_their_evidence():
    out, report = refine_taxonomy(
        [
            svc(
                "Roofing",
                {"name": "Roof Repair", "covers": ["Roof Repairs Melbourne"]},
                {"name": "Roof Repairs", "covers": []},
                {"name": "Roof Repair Services", "covers": ["Roof Repair Services"]},
            )
        ]
    )
    subs = out[0]["subservices"]
    assert [s["name"] for s in subs] == ["Roof Repair"]
    assert set(subs[0]["covers"]) == {"Roof Repairs Melbourne", "Roof Repair Services"}
    assert report["merged"]


def test_spelling_variants_of_a_service_merge():
    out, _ = refine_taxonomy(
        [svc("SEO", "Local SEO"), svc("Search Engine Optimisation", "Technical SEO"), svc("SEO Services")]
    )
    assert tree(out) == {"SEO": ["Local SEO", "Technical SEO"]}
    assert set(out[0]["taxonomy"]["aliases"]) >= {"Search Engine Optimisation"}


# ---------------------------------------------------------------------- Test 4


@pytest.mark.parametrize(
    "raw",
    ["Best Roof Repair", "Affordable Roof Repair", "Roof Repair Near Me", "Roof Repair Melbourne",
     "Cheap Roof Repairs", "Top Rated Roof Repair Services"],
)
def test_modifier_and_location_variants_are_the_service_not_new_services(raw):
    assert canonical_name(raw) == "Roof Repair"


@pytest.mark.parametrize(
    "raw,kind",
    [("Best", "modifier"), ("Affordable", "modifier"), ("Near Me", "modifier"),
     ("Melbourne", "location"), ("First Home Buyers", "segment"), ("Small Business", "segment"),
     ("Hindi Brokers", "language"), ("7 Steps For Obtaining A Better Mortgage", "content"),
     ("How To Choose A Roofer", "content"), ("Cashing In On Home Equity", "content")],
)
def test_attributes_and_articles_are_not_services(raw, kind):
    found = analyze_name(raw)
    assert found.kind == kind
    assert canonical_name(raw) is None


def test_modifier_variants_never_become_services_in_a_taxonomy():
    out, _ = refine_taxonomy(
        [
            svc("Roofing", "Roof Repair", "Best Roof Repair", "Affordable Roof Repair", "Roof Repair Near Me"),
            svc("Best Roofing"),
            svc("Affordable"),
        ]
    )
    assert tree(out) == {"Roofing": ["Roof Repair"]}


# ---------------------------------------------------------------------- Test 5


def test_distinct_roof_offerings_stay_distinct():
    subs = ["Roof Repair", "Roof Replacement", "Roof Installation", "Roof Inspection"]
    out, report = refine_taxonomy([svc("Roofing", *subs)])
    assert tree(out) == {"Roofing": subs}
    assert "merged" not in report


def test_distinct_top_level_roof_services_are_not_merged_but_nest_under_roofing():
    out, _ = refine_taxonomy(
        [svc("Roof Repair"), svc("Roof Replacement"), svc("Roof Installation"), svc("Roofing")]
    )
    assert tree(out) == {"Roofing": ["Roof Repair", "Roof Replacement", "Roof Installation"]}


def test_web_design_and_web_development_are_different_offerings():
    out, _ = refine_taxonomy([svc("Web Design"), svc("Web Development"), svc("Website Development")])
    assert tree(out) == {"Web Design": [], "Web Development": []}


# ---------------------------------------------------------------------- Test 6


def test_no_cross_service_contamination():
    out, _ = refine_taxonomy(
        [
            svc("Roofing", "Roof Repair", "Blocked Drains", "Switchboard Upgrade"),
            svc("Plumbing", "Hot Water Systems", "Gutter Cleaning", "Roof Repair"),
            svc("Electrical", "Power Point Installation", "Leaking Tap Repair"),
        ]
    )
    t = tree(out)
    assert set(t["Roofing"]) == {"Roof Repair", "Gutter Cleaning"}
    assert set(t["Plumbing"]) == {"Hot Water Systems", "Blocked Drains", "Leaking Tap Repair"}
    assert set(t["Electrical"]) == {"Power Point Installation", "Switchboard Upgrade"}
    # every sub-service has exactly one parent
    all_subs = [s for subs in t.values() for s in subs]
    assert len(all_subs) == len(set(all_subs))


def test_a_sub_service_moves_to_the_service_it_clearly_fits_better():
    # Same domain (lending), so no family conflict: decided by the words they share.
    out, report = refine_taxonomy(
        [
            svc("Asset Finance", "Equipment Finance", "Secured Business Loan", "Truck Finance"),
            svc("Business Loans", "Unsecured Business Loan", covers=["Business loans"]),
        ]
    )
    assert parent_of(out, "Secured Business Loan") == ["Business Loans"]
    # a tie stays where it was: Truck Finance fits both equally well
    assert parent_of(out, "Truck Finance") == ["Asset Finance"]
    assert report["moved"] == [{"name": "Secured Business Loan", "from": "Asset Finance", "to": "Business Loans"}]


def test_a_sub_service_listed_under_two_services_keeps_one_parent():
    out, _ = refine_taxonomy(
        [svc("SEO", "Local SEO", "Google Business Profile"), svc("Local Marketing", "Google Business Profile")]
    )
    assert len(parent_of(out, "Google Business Profile")) == 1


def test_a_sub_service_that_is_another_service_merges_into_that_service():
    out, _ = refine_taxonomy([svc("SEO", "Local SEO"), svc("Digital Marketing", "SEO", "Email Marketing")])
    assert "SEO" not in tree(out)["Digital Marketing"]
    assert tree(out)["SEO"] == ["Local SEO"]


def test_narrow_service_nests_under_its_broader_service():
    out, report = refine_taxonomy([svc("SEO", "Technical SEO"), svc("Local SEO", "Google Business Profile")])
    assert tree(out) == {"SEO": ["Technical SEO", "Local SEO", "Google Business Profile"]}
    assert report["demoted"] == [{"name": "Local SEO", "under": "SEO"}]


# ---------------------------------------------------------------------- Test 7


def test_naming_is_stable_regardless_of_spelling_and_order():
    variants = ["Roof Repairs", "roof repair services", "Best Roof Repair", "Roof Repair"]
    names = set()
    for perm in itertools.permutations(variants):
        out, _ = refine_taxonomy([svc("Roofing", *perm)])
        names.add(tuple(s["name"] for s in out[0]["subservices"]))
    assert names == {("Roof Repair",)}


def test_shuffled_equivalent_input_gives_the_same_hierarchy():
    base = [
        svc("Roofing", "Roof Repairs", "Roof Replacement", "Gutter Cleaning"),
        svc("Plumbing", "Blocked Drains", "Roof Repair", "Hot Water Systems"),
        svc("Roof Repair Services"),
    ]
    expected = {(p, s) for p, subs in tree(refine_taxonomy(base)[0]).items() for s in subs}
    rng = random.Random(7)
    for _ in range(10):
        shuffled = [dict(s, subservices=rng.sample(s["subservices"], len(s["subservices"]))) for s in base]
        rng.shuffle(shuffled)
        got = {(p, s) for p, subs in tree(refine_taxonomy(shuffled)[0]).items() for s in subs}
        assert got == expected


def test_refining_twice_changes_nothing():
    raw = [
        svc("Home Loan Broking", {"name": "First Home Buyer", "covers": ["First Home Buyer"]},
            covers=["Mortgage broking"]),
        svc("Multilingual Broker Services", {"name": "Hindi Brokers", "covers": ["Hindi Brokers"]},
            covers=["Multilingual Mortgage Brokers In"]),
        svc("Asset Finance", {"name": "Hire Purchases", "covers": ["Hire Purchases"]}),
    ]
    once, _ = refine_taxonomy(raw)
    twice, report = refine_taxonomy(once)
    assert twice == once
    assert report == {}


# ------------------------------------------------------- attributes and evidence


def test_customer_segments_and_languages_become_attributes_of_the_service_they_qualify():
    out, report = refine_taxonomy(
        [
            svc("Home Loan Broking", "Refinancing",
                {"name": "First Home Buyer", "covers": ["First Home Buyer"]},
                covers=["Mortgage broking", "Home loans"]),
            svc("Multilingual Broker Services",
                {"name": "Hindi Brokers", "covers": ["Hindi Brokers"]},
                {"name": "Tamil Brokers", "covers": ["Tamil Brokers"]},
                covers=["Multilingual Mortgage Brokers In"]),
        ]
    )
    assert tree(out) == {"Home Loan Broking": ["Refinancing"]}
    meta = out[0]["taxonomy"]
    assert meta["attributes"]["segments"] == ["First Home Buyer"]
    assert {"Hindi", "Tamil", "Multilingual"} <= set(meta["attributes"]["languages"])
    # the client's own offerings are still searched for, and still count as placed
    assert {"First Home Buyer", "Hindi Brokers", "Tamil Brokers"} <= set(meta["keyword_qualifiers"])
    assert {"First Home Buyer", "Hindi Brokers", "Multilingual Mortgage Brokers In"} <= set(out[0]["covers"])
    assert {a["kind"] for a in report["attributes"]} == {"segment", "language"}


def test_language_is_kept_when_it_is_what_is_sold():
    assert canonical_name("Chinese Translation") == "Chinese Translation"
    assert canonical_name("Mandarin Lessons") == "Mandarin Lessons"


def test_segment_qualified_product_stays_a_service():
    assert canonical_name("Residential Roofing") == "Residential Roofing"
    assert canonical_name("Small Business Loans") == "Small Business Loans"
    found = analyze_name("SEO for Dentists")
    assert found.name == "SEO" and found.attributes["segments"] == ("Dentists",)


def test_real_names_with_modifier_words_survive():
    assert canonical_name("Professional Indemnity Insurance") == "Professional Indemnity Insurance"
    assert canonical_name("Emergency Plumbing") == "Emergency Plumbing"
    assert canonical_name("Local SEO") == "Local SEO"
    assert canonical_name("WooCommerce Development") == "WooCommerce Development"


def test_product_plurals_are_kept_and_acronyms_cased():
    assert canonical_name("facebook ads") == "Facebook Ads"
    assert canonical_name("smsf property loans") == "SMSF Property Loans"
    assert canonical_name("Hire Purchases") == "Hire Purchase"
    assert canonical_name("ppc management services") == "PPC Management"


# --------------------------------------------------------------- catalog rows


def _row(rid, name, source="website", subs=(), selected=True, candidate=False, path=None):
    row = {
        "id": rid, "name": name, "source": source, "selected": selected,
        "priority_tier": None if candidate else "secondary", "candidate_new_service": candidate,
        "subservices": [dict(s) for s in subs],
    }
    if path:
        row["path"] = path
    return row


def test_fallback_catalog_drops_articles_brand_pages_and_combined_duplicates():
    catalog = [
        _row("business loans", "Business Loans", path="/business-loans-melbourne"),
        _row("commercial loans", "Commercial Loans"),
        _row("car loans", "Car Loans", "website_page", selected=False, candidate=True),
        _row("anz broker", "Anz Broker", "website_page", selected=False, candidate=True),
        _row("budget planner", "Budget Planner", "website_page", selected=False, candidate=True),
        _row("7 steps for obtaining a better mortgage", "7 Steps For Obtaining A Better Mortgage",
             "website_page", selected=False, candidate=True),
        _row("commercial and business loan", "Commercial And Business Loan", "website_page",
             selected=False, candidate=True),
        _row("multilingual mortgage brokers in", "Multilingual Mortgage Brokers In",
             subs=[{"id": "hindi brokers", "name": "Hindi Brokers", "source": "website", "selected": True}]),
    ]
    rows, report = refine_catalog(catalog, vocabulary=["Mortgage broking", "Business loans"])
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"business loans", "commercial loans", "car loans", "multilingual mortgage brokers in"}
    # ids survive renames, so saved selections keep matching
    assert by_id["multilingual mortgage brokers in"]["name"] == "Mortgage Brokers"
    assert by_id["multilingual mortgage brokers in"]["subservices"] == []
    assert "Hindi Brokers" in by_id["multilingual mortgage brokers in"]["taxonomy"]["keyword_qualifiers"]
    assert "Commercial and Business Loan" in by_id["business loans"]["taxonomy"]["aliases"]
    reasons = {e["name"]: e["reason"] for e in report["excluded"]}
    assert reasons == {
        "Anz Broker": "unrelated",
        "Budget Planner": "unrelated",
        "7 Steps For Obtaining A Better Mortgage": "content",
    }


def test_manual_services_pass_through_untouched():
    manual = _row("best seo agency melbourne", "Best SEO Agency Melbourne", "manual")
    rows, _ = refine_catalog([_row("seo", "SEO"), manual])
    assert manual in rows


def test_competitor_suggestions_are_not_called_unrelated():
    rows, _ = refine_catalog(
        [_row("seo", "SEO"), _row("cro", "CRO", "competitor", selected=False, candidate=True)]
    )
    assert [r["name"] for r in rows] == ["SEO", "CRO"]


# ------------------------------------------------------------ pipeline wiring


def test_llm_catalog_is_canonical_and_reports_changes():
    report: dict = {}
    catalog, unplaced = build_catalog_from_taxonomy(
        [
            svc("Roofing", {"name": "Roof Repairs", "covers": ["Roof Repairs"]}, "Roof Replacement",
                covers=["Roofing"]),
            svc("Plumbing", "Blocked Drains", {"name": "Roof Repair", "covers": []}),
        ],
        [],
        ["Roofing", "Roof Repairs"],
        report=report,
    )
    t = {r["name"]: [s["name"] for s in r["subservices"]] for r in catalog}
    assert t == {"Roofing": ["Roof Repair", "Roof Replacement"], "Plumbing": ["Blocked Drains"]}
    assert catalog[0]["subservices"][0]["selected"] is True  # still the client's
    assert unplaced == []
    assert report["merged"] and report["moved"]


def test_fallback_pack_lists_what_was_left_out():
    website = {
        "page_hierarchy": [
            {"path": p, "url": f"https://x.com{p}", "page_type": "page", "cluster": "other", "title": p}
            for p in ("/car-loans", "/anz-broker", "/7-steps-for-obtaining-a-better-mortgage")
        ]
    }
    pack = build_prioritization_pack(
        client_name="Broker",
        services=["Business loans"],
        website=website,
        page_seeds=[{"term": "Business Loans", "page_type": "service", "path": "/business-loans"}],
    )
    names = [r["name"] for r in pack["service_catalog"]]
    assert names == ["Business Loans", "Car Loans"]
    assert {(e["name"], e["reason"]) for e in pack["excluded_offerings"]} == {
        ("Anz Broker", "unrelated"),
        ("7 Steps For Obtaining A Better Mortgage", "content"),
    }


def test_saved_selection_ids_follow_canonical_names():
    catalog = [
        {
            "id": "asset finance", "name": "Asset Finance",
            "subservices": [{"id": "hire purchase", "name": "Hire Purchase",
                             "taxonomy": {"aliases": ["Hire Purchases"]}}],
        }
    ]
    saved = {
        "selected_service_ids": ["asset finance"],
        "selected_subservice_ids": ["hire purchases", "unknown thing"],
        "service_scope": {"asset finance": {"name": "Asset Finance", "offered": [], "excluded": []}},
    }
    out = remap_saved_selection(saved, catalog)
    assert out["selected_subservice_ids"] == ["hire purchase", "unknown thing"]
    assert out["selected_service_ids"] == ["asset finance"]
    assert saved["selected_subservice_ids"] == ["hire purchases", "unknown thing"]  # not mutated


def test_attribute_offerings_are_not_seeded_but_sub_services_are():
    catalog = [
        {
            "id": "home loan broking", "name": "Home Loan Broking", "source": "cdd",
            "taxonomy": {"keyword_qualifiers": ["First Home Buyer", "Hindi Brokers"]},
            "subservices": [{"id": "refinancing", "name": "Refinancing", "source": "website"}],
        },
        {
            "id": "asset finance", "name": "Asset Finance", "source": "website",
            "taxonomy": {"keyword_qualifiers": ["Truck Finance For Tradies"]}, "subservices": [],
        },
    ]
    prio = {
        "confirmed_at": "2026-10-01T00:00:00+00:00",
        "selected_service_ids": ["home loan broking", "asset finance"],
        "service_catalog": catalog,
    }
    plan = generate_service_seeds(
        "Home Loan Broking", pool_roots=[], pool_targets={}, catalog=catalog, prioritization=prio,
        other_service_names=["Asset Finance"],
    )
    seeds = {s.lower() for s in plan.seeds}
    # Sub-services are seeds again (2026-10-27); keyword qualifiers are never promoted to seeds.
    assert {"home loan broking", "refinancing"} <= seeds
    assert not {"first home buyer", "hindi brokers"} & seeds
    assert "truck finance for tradies" not in seeds
    assert all(t["service"] == "Home Loan Broking" for t in plan.seed_targets.values())


def test_qualifiers_of_an_excluded_sub_service_are_not_seeded():
    catalog = [
        {
            "id": "home loan broking", "name": "Home Loan Broking", "source": "cdd",
            "subservices": [
                {"id": "equity home loan", "name": "Equity Home Loan", "source": "website",
                 "taxonomy": {"keyword_qualifiers": ["First Home Buyer"]}},
                {"id": "refinancing", "name": "Refinancing", "source": "website"},
            ],
        }
    ]
    prio = {
        "confirmed_at": "2026-10-01T00:00:00+00:00",
        "selected_service_ids": ["home loan broking"],
        "service_catalog": catalog,
        "service_scope": {
            "home loan broking": {
                "name": "Home Loan Broking",
                "offered": ["Refinancing"],
                "excluded": ["Equity Home Loan"],
            }
        },
    }
    plan = generate_service_seeds(
        "Home Loan Broking", pool_roots=[], pool_targets={}, catalog=catalog, prioritization=prio
    )
    seeds = {s.lower() for s in plan.seeds}
    assert "refinancing" in seeds
    assert "first home buyer" not in seeds and "equity home loan" not in seeds


def test_a_service_selected_under_an_old_spelling_still_finds_its_row():
    catalog = [
        {
            "id": "home loan broking", "name": "Home Loan Broking", "source": "cdd",
            "taxonomy": {"aliases": ["Mortgage Broking"]},
            "subservices": [{"id": "refinancing", "name": "Refinancing", "source": "website"}],
        }
    ]
    plan = generate_service_seeds(
        "Mortgage Broking", pool_roots=[], pool_targets={}, catalog=catalog,
        prioritization={
            "confirmed_at": "2026-10-01T00:00:00+00:00",
            "selected_service_ids": ["home loan broking"],
            "service_catalog": catalog,
        },
    )
    assert "refinancing" in {s.lower() for s in plan.seeds}
