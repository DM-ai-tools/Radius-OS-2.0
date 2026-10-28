"""The URL map's L1 to L4 tree (2026-10-27, Task 8).

The client's service catalog (services, sub-services, sub-sub-services) is the skeleton.
Sub-topics the keywords show add levels under it. A level exists only when keywords back
it, modifiers never make a level, the depth stops at 4, a row at L3 always has an L2 and
an L1, and every cluster lands in exactly one place: a node, the home row, or the visible
needs-review list.
"""

from __future__ import annotations

import copy

from app.services.keyword_cluster_structure import stable_cluster_id
from app.services.url_mapping import (
    _sheet_page_type,
    build_final_url_map,
    check_url_map_consistency,
)
from app.services.url_tree import apply_url_tree

LEVELS = ("l1_category", "l2_subcategory", "l3_subsubcategory", "l4_attribution")
EMPTY_REPORT = {"placed": 0, "needs_review": [], "violations": []}


def _entry(primary, *, secondaries=(), volume=100, status="measured", intent="commercial", cid=None, **extra):
    """A URL-map row the way build_final_url_map hands it to the tree."""
    row = {
        "cluster_id": cid or f"c-{primary.replace(' ', '-')}",
        "cluster": primary.title(),
        "primary_keyword": primary,
        "secondary_keywords": list(secondaries),
        "search_intent": intent,
        "search_volume": volume,
        "search_volume_status": status,
        "proposed_url": "/" + primary.replace(" ", "-") + "/",
        "current_url": None,
        # What the label-containment taxonomy left on the row before the tree runs.
        "l1_category": primary.title(),
        "l2_subcategory": None,
        "l3_subsubcategory": None,
        "l4_attribution": None,
        "level": 1,
        "page_type": "Hub Page",
        "decision_trail": [],
    }
    row.update(extra)
    return row


def _seo_catalog(*, deeper=None):
    ecommerce = {"id": "ecommerce-seo", "name": "Ecommerce SEO", "source": "website"}
    if deeper is not None:
        ecommerce["subservices"] = deeper
    return [
        {"id": "seo", "name": "SEO", "source": "website", "subservices": [ecommerce]},
        {"id": "ppc-management", "name": "PPC Management", "source": "website", "subservices": []},
    ]


def _levels(entry):
    return tuple(entry.get(key) for key in LEVELS)


def _by_primary(entries):
    return {e["primary_keyword"]: e for e in entries}


# ---------------------------------------------------------------- the owner's example


def test_seo_ecommerce_seo_shopify_seo_builds_three_levels():
    entries = [
        _entry("seo"),
        _entry("ecommerce seo"),
        # The searchers combine Shopify and ecommerce SEO, so Shopify SEO sits under it.
        _entry("shopify seo", secondaries=["shopify ecommerce seo", "shopify seo expert"]),
    ]
    report = apply_url_tree(entries, service_catalog=_seo_catalog())
    rows = _by_primary(entries)

    assert _levels(rows["seo"]) == ("SEO", None, None, None)
    assert _levels(rows["ecommerce seo"]) == ("SEO", "Ecommerce SEO", None, None)
    assert _levels(rows["shopify seo"]) == ("SEO", "Ecommerce SEO", "Shopify SEO", None)
    assert [rows[k]["level"] for k in ("seo", "ecommerce seo", "shopify seo")] == [1, 2, 3]
    assert rows["shopify seo"]["page_type"] == "Sub-collection"
    assert rows["ecommerce seo"]["page_type"] == "Collection"
    assert rows["seo"]["page_type"] == "Hub Page"
    assert report == {"placed": 3, "needs_review": [], "violations": []}
    assert any("shopify ecommerce seo" in line for line in rows["shopify seo"]["decision_trail"])


def test_without_a_keyword_linking_them_shopify_seo_is_its_own_branch_of_seo():
    # Nothing in the keywords says Shopify belongs to ecommerce SEO, so the tree does not claim it.
    entries = [_entry("seo"), _entry("ecommerce seo"), _entry("shopify seo")]
    apply_url_tree(entries, service_catalog=_seo_catalog())
    assert _levels(_by_primary(entries)["shopify seo"]) == ("SEO", "Shopify SEO", None, None)


def test_only_the_rows_own_cluster_can_link_it_under_a_deeper_node():
    # "shopify ecommerce seo" sits in the Ecommerce SEO cluster, not the Shopify one: it does
    # not move Shopify SEO under Ecommerce SEO.
    entries = [_entry("ecommerce seo", secondaries=["shopify ecommerce seo"]), _entry("shopify seo")]
    apply_url_tree(entries, service_catalog=_seo_catalog())
    assert _levels(_by_primary(entries)["shopify seo"]) == ("SEO", "Shopify SEO", None, None)


def test_supporting_rows_attach_and_never_make_a_node():
    catalog = [{"name": "SEO", "subservices": [{"name": "Local SEO"}]}]
    software = _entry("seo reporting software", dedicated_url=False, url_status="no_dedicated_url")
    # Names no service; its parent row is Local SEO, so it sits on that node.
    tools = _entry("rank tracker", dedicated_url=False, parent_cluster_id="c-local-seo")
    lost = _entry("domain name ideas", dedicated_url=False)
    entries = [_entry("seo"), _entry("local seo"), software, tools, lost]
    report = apply_url_tree(entries, service_catalog=catalog)
    assert _levels(software) == ("SEO", None, None, None)  # not "SEO Reporting Software"
    assert not any("not a sub-topic" in line for line in software["decision_trail"])
    assert _levels(tools) == ("SEO", "Local SEO", None, None)
    assert any("supports another row" in line for line in tools["decision_trail"])
    assert [r["primary_keyword"] for r in report["needs_review"]] == ["domain name ideas"]
    assert report["placed"] == 4 and report["violations"] == []


def test_a_sub_sub_service_in_the_catalog_is_used_as_l3():
    catalog = _seo_catalog(deeper=[{"id": "shopify-seo", "name": "Shopify SEO"}])
    entries = [_entry("shopify seo services")]
    apply_url_tree(entries, service_catalog=catalog)
    assert _levels(entries[0]) == ("SEO", "Ecommerce SEO", "Shopify SEO", None)


def test_a_standalone_service_stops_at_l1():
    entries = [_entry("ppc management"), _entry("seo company")]
    report = apply_url_tree(entries, service_catalog=_seo_catalog())
    rows = _by_primary(entries)
    assert _levels(rows["ppc management"]) == ("PPC Management", None, None, None)
    assert rows["ppc management"]["level"] == 1
    assert rows["ppc management"]["page_type"] == "Hub Page"
    assert _levels(rows["seo company"]) == ("SEO", None, None, None)
    assert report["placed"] == 2


def test_modifiers_do_not_create_levels():
    entries = [
        _entry("seo"),
        _entry("best seo agency"),
        _entry("affordable seo services"),
        _entry("seo pricing"),
        _entry("seo company near me"),
        _entry("ecommerce seo cost"),
        _entry("top ecommerce seo firm"),
    ]
    apply_url_tree(entries, service_catalog=_seo_catalog())
    rows = _by_primary(entries)
    for primary in ("seo", "best seo agency", "affordable seo services", "seo pricing", "seo company near me"):
        assert _levels(rows[primary]) == ("SEO", None, None, None), primary
        assert rows[primary]["level"] == 1
    for primary in ("ecommerce seo cost", "top ecommerce seo firm"):
        assert _levels(rows[primary]) == ("SEO", "Ecommerce SEO", None, None), primary


def test_a_sub_topic_needs_two_clusters_or_a_measured_volume():
    # One cluster, nothing measured: the words are not a level yet; the row stays on its service.
    alone = [_entry("seo"), _entry("shopify seo", volume=None, status="unverified")]
    apply_url_tree(alone, service_catalog=_seo_catalog())
    assert _levels(_by_primary(alone)["shopify seo"]) == ("SEO", None, None, None)
    assert any("not a sub-topic" in line for line in _by_primary(alone)["shopify seo"]["decision_trail"])

    # An estimated volume is not a measurement.
    estimated = [_entry("shopify seo", volume=900, status="estimated")]
    apply_url_tree(estimated, service_catalog=_seo_catalog())
    assert _levels(estimated[0]) == ("SEO", None, None, None)

    # Two clusters showing the same sub-topic make it a level, measured or not.
    pair = [
        _entry("shopify seo", volume=None, status="unverified"),
        _entry("shopify seo agency", volume=None, status="unverified"),
    ]
    apply_url_tree(pair, service_catalog=_seo_catalog())
    for row in pair:
        assert _levels(row) == ("SEO", "Shopify SEO", None, None)

    # One measured cluster is enough on its own.
    measured = [_entry("shopify seo", volume=90)]
    apply_url_tree(measured, service_catalog=_seo_catalog())
    assert _levels(measured[0]) == ("SEO", "Shopify SEO", None, None)


def test_a_broader_row_never_lands_on_a_narrower_node():
    # "shopify seo" alone is not a sub-topic (one cluster, nothing measured); "shopify seo audit"
    # is. The broader row stays on SEO: the keyword "shopify seo audit" contains it, but that
    # does not make Shopify SEO Audit its home.
    entries = [_entry("shopify seo", volume=None, status="unverified"), _entry("shopify seo audit")]
    apply_url_tree(entries, service_catalog=_seo_catalog())
    rows = _by_primary(entries)
    assert _levels(rows["shopify seo audit"]) == ("SEO", "Shopify SEO Audit", None, None)
    assert _levels(rows["shopify seo"]) == ("SEO", None, None, None)
    assert any("(shopify)" in line for line in rows["shopify seo"]["decision_trail"])  # words, not stems


def test_the_minimum_number_of_clusters_is_a_parameter():
    rows = [
        _entry("shopify seo", volume=None, status="unverified"),
        _entry("shopify seo agency", volume=None, status="unverified"),
    ]
    apply_url_tree(rows, service_catalog=_seo_catalog(), min_subtopic_keywords=3)
    assert [_levels(r) for r in rows] == [("SEO", None, None, None)] * 2


def test_a_sub_topic_of_a_sub_topic_nests_under_it():
    entries = [_entry("seo"), _entry("shopify seo"), _entry("shopify seo audit")]
    apply_url_tree(entries, service_catalog=_seo_catalog())
    rows = _by_primary(entries)
    assert _levels(rows["shopify seo"]) == ("SEO", "Shopify SEO", None, None)
    assert _levels(rows["shopify seo audit"]) == ("SEO", "Shopify SEO", "Shopify SEO Audit", None)


def test_depth_is_capped_at_four():
    catalog = _seo_catalog(
        deeper=[
            {
                "id": "shopify-seo",
                "name": "Shopify SEO",
                # A fifth catalog level is beyond the tree and is not a node.
                "subservices": [
                    {
                        "id": "shopify-plus-seo",
                        "name": "Shopify Plus SEO",
                        "subservices": [{"id": "shopify-plus-seo-audit", "name": "Shopify Plus SEO Audit"}],
                    }
                ],
            }
        ]
    )
    entries = [
        _entry("shopify plus seo"),
        _entry("shopify plus seo audit"),
        _entry("shopify plus seo audit checklist"),
    ]
    report = apply_url_tree(entries, service_catalog=catalog)
    full = ("SEO", "Ecommerce SEO", "Shopify SEO", "Shopify Plus SEO")
    for row in entries:
        assert _levels(row) == full, row["primary_keyword"]
        assert row["level"] == 4
        assert row["page_type"] == "Attribution Collection"
    assert any("capped at 4" in line for line in _by_primary(entries)["shopify plus seo audit"]["decision_trail"])
    assert report["violations"] == []


def test_no_l3_without_l2():
    entries = [
        _entry("seo"),
        _entry("ecommerce seo"),
        _entry("shopify seo", secondaries=["shopify ecommerce seo"]),
        _entry("shopify seo audit"),
        _entry("local seo"),
        _entry("local seo audit"),
        _entry("what is shopify seo", intent="informational"),
    ]
    report = apply_url_tree(entries, service_catalog=_seo_catalog())
    for row in entries:
        filled = [bool(row.get(key)) for key in LEVELS]
        assert filled == sorted(filled, reverse=True), row["primary_keyword"]  # no gap below a filled level
        assert row["level"] == sum(filled)
        assert row["page_type"] == _sheet_page_type({k: row.get(k) for k in LEVELS})
    assert report["violations"] == []

    # A row the tree cannot place keeps its earlier levels; a gap there is reported, not hidden.
    broken = [_entry("wordpress hosting", l1_category=None, l2_subcategory="Hosting")]
    report = apply_url_tree(broken, service_catalog=_seo_catalog())
    assert any("L2 without L1" in v for v in report["violations"])


def test_every_cluster_is_placed_exactly_once():
    home = _entry("digital marketing agency", is_home=True, merged_cluster_ids=["ind-1", "ind-2"])
    for key in LEVELS:
        home[key] = None
    home["level"] = 0
    entries = [
        home,
        _entry("seo"),
        _entry("ecommerce seo"),
        _entry("ppc management"),
        _entry("wordpress hosting"),
        _entry("seo tips", intent="informational"),
    ]
    report = apply_url_tree(entries, service_catalog=_seo_catalog())
    assert report["violations"] == []
    assert report["placed"] == 4
    assert [r["cluster_id"] for r in report["needs_review"]] == ["c-wordpress-hosting"]
    # The home row sits above the tree: it gets no level.
    assert _levels(home) == (None, None, None, None) and home["level"] == 0

    # The same cluster twice is a violation (a warning in the Phase 6 validation).
    twice = [_entry("seo"), _entry("seo company", cid="c-seo")]
    report = apply_url_tree(twice, service_catalog=_seo_catalog())
    assert any("c-seo" in v and "more than once" in v for v in report["violations"])

    # A cluster merged into the home row and mapped again as its own row is placed twice.
    again = [copy.deepcopy(home), _entry("marketing services", cid="ind-1")]
    report = apply_url_tree(again, service_catalog=_seo_catalog())
    assert any("ind-1" in v for v in report["violations"])


def test_unmatched_cluster_goes_to_needs_review_with_reason():
    entries = [_entry("seo"), _entry("wordpress hosting")]
    before = copy.deepcopy(entries[1])
    report = apply_url_tree(entries, service_catalog=_seo_catalog())
    assert len(entries) == 2  # never dropped
    assert report["needs_review"] == [
        {
            "cluster_id": "c-wordpress-hosting",
            "primary_keyword": "wordpress hosting",
            "reason": report["needs_review"][0]["reason"],
        }
    ]
    reason = report["needs_review"][0]["reason"]
    assert "wordpress hosting" in reason and "catalog" in reason
    row = entries[1]
    assert _levels(row) == _levels(before) and row["level"] == before["level"]  # placed nowhere, so untouched
    assert row["needs_human_review"] is True
    assert any(reason in line for line in row["decision_trail"])
    assert report["placed"] == 1


def test_informational_cluster_attaches_under_its_service():
    entries = [
        _entry("seo"),
        _entry("ecommerce seo"),
        _entry("shopify seo"),
        _entry("shopify seo app"),
        _entry("how to do shopify seo", intent="informational"),
        _entry("what is ecommerce seo", intent="informational"),
        # Measured, but informational: it attaches to SEO, it does not become "SEO Tips".
        _entry("seo tips", intent="informational", volume=5000),
        _entry("seo tips and tricks", intent="informational", volume=700),
    ]
    report = apply_url_tree(entries, service_catalog=_seo_catalog())
    rows = _by_primary(entries)
    assert _levels(rows["how to do shopify seo"]) == ("SEO", "Shopify SEO", None, None)
    assert _levels(rows["what is ecommerce seo"]) == ("SEO", "Ecommerce SEO", None, None)
    assert _levels(rows["seo tips"]) == ("SEO", None, None, None)
    assert _levels(rows["seo tips and tricks"]) == ("SEO", None, None, None)
    # Informational rows add no node: Shopify SEO App is the only level under Shopify SEO.
    assert _levels(rows["shopify seo app"]) == ("SEO", "Shopify SEO", "Shopify SEO App", None)
    assert report["needs_review"] == []

    # An informational cluster with no service in it is not forced anywhere.
    lost = [_entry("what is a domain name", intent="informational")]
    report = apply_url_tree(lost, service_catalog=_seo_catalog())
    assert [r["primary_keyword"] for r in report["needs_review"]] == ["what is a domain name"]


def test_a_row_that_fits_two_sibling_nodes_equally_takes_the_first_and_says_so():
    catalog = [
        {
            "id": "seo",
            "name": "SEO",
            "subservices": [{"id": "local-seo", "name": "Local SEO"}, {"id": "technical-seo", "name": "Technical SEO"}],
        }
    ]
    entries = [_entry("local technical seo")]
    report = apply_url_tree(entries, service_catalog=catalog)
    # Under the first of the two, "technical" is a measured narrowing of Local SEO.
    assert _levels(entries[0]) == ("SEO", "Local SEO", "Local Technical SEO", None)
    assert any("also fit Technical SEO" in line for line in entries[0]["decision_trail"])
    assert report["placed"] == 1 and report["violations"] == []


def test_a_catalog_alias_names_the_same_node():
    catalog = [{"id": "ppc", "name": "PPC", "taxonomy": {"aliases": ["Google Ads Management"]}, "subservices": []}]
    entries = [_entry("google ads management agency")]
    apply_url_tree(entries, service_catalog=catalog)
    assert _levels(entries[0]) == ("PPC", None, None, None)


def test_a_place_is_a_sub_topic_and_the_row_stays_a_landing_page():
    entries = [_entry("seo"), _entry("seo melbourne")]
    apply_url_tree(entries, service_catalog=_seo_catalog())
    row = _by_primary(entries)["seo melbourne"]
    assert _levels(row) == ("SEO", "SEO Melbourne", None, None)
    assert row["page_type"] == "Landing Page"


def test_empty_catalog_leaves_existing_levels_untouched():
    entries = [_entry("seo"), _entry("ecommerce seo", l1_category="SEO", l2_subcategory="Ecommerce SEO", level=2)]
    before = copy.deepcopy(entries)
    assert apply_url_tree(entries, service_catalog=[]) == EMPTY_REPORT
    assert entries == before
    # A catalog with nothing usable in it is the same as none.
    assert apply_url_tree(entries, service_catalog=[{"id": "x"}, "SEO", {"name": "  "}]) == EMPTY_REPORT
    assert entries == before


# ---------------------------------------------------------------- the real URL map


def _kw(keyword, volume, **extra):
    return {"keyword": keyword, "volume": volume, "intent": "commercial", "relevance_reason": "seed_phrase", **extra}


def _cluster(name, rows, intent="commercial"):
    primary = rows[0]["keyword"]
    members = [{**r, "role": "Primary" if r["keyword"] == primary else "Secondary"} for r in rows]
    return {"name": name, "primary_keyword": primary, "intent": intent, "keywords": members}


PAGES = [
    {"url": "https://acme.example/", "title": "Acme | Digital Marketing Agency", "h1": "Digital marketing agency"},
    {"url": "https://acme.example/seo/", "title": "SEO Services", "h1": "SEO services"},
]


def _clusters():
    return [
        _cluster(
            "Digital Marketing Agency",
            [_kw("digital marketing agency", 1300, seed_origin="industry"), _kw("online marketing company", 320, seed_origin="industry")],
        ),
        _cluster("SEO", [_kw("seo services", 2400), _kw("seo company", 500)]),
        _cluster("Ecommerce SEO", [_kw("ecommerce seo", 880), _kw("ecommerce seo services", 140)]),
        _cluster("Shopify SEO", [_kw("shopify seo", 590), _kw("shopify ecommerce seo", 40)]),
        _cluster("Shopify SEO Checklist", [_kw("shopify seo checklist", 170)], intent="informational"),
        _cluster("Wordpress Hosting", [_kw("wordpress hosting", 1600)]),
    ]


def _map(service_catalog):
    return build_final_url_map(
        {"clusters": copy.deepcopy(_clusters())},
        website={"pages": PAGES},
        client_domain="acme.example",
        service_catalog=service_catalog,
    )


def test_build_final_url_map_builds_the_tree_from_the_catalog():
    report = _map(_seo_catalog())
    entries = report["final_url_map"]
    rows = _by_primary(entries)

    assert _levels(rows["seo services"]) == ("SEO", None, None, None)
    assert _levels(rows["ecommerce seo"]) == ("SEO", "Ecommerce SEO", None, None)
    assert _levels(rows["shopify seo"]) == ("SEO", "Ecommerce SEO", "Shopify SEO", None)
    assert _levels(rows["shopify seo checklist"]) == ("SEO", "Ecommerce SEO", "Shopify SEO", None)
    assert [rows[k]["level"] for k in ("seo services", "ecommerce seo", "shopify seo")] == [1, 2, 3]
    for row in entries:
        taxonomy = {k: row.get(k) for k in LEVELS}
        if row["primary_keyword"] != "wordpress hosting" and not row.get("is_home"):
            assert row["page_type"] == _sheet_page_type(taxonomy), row["primary_keyword"]

    home = next(e for e in entries if e.get("is_home"))
    assert _levels(home) == (None, None, None, None) and home["level"] == 0

    tree = report["url_tree"]
    assert tree["placed"] == 4
    assert tree["violations"] == []
    assert [r["primary_keyword"] for r in tree["needs_review"]] == ["wordpress hosting"]
    wp = rows["wordpress hosting"]
    assert wp["l1_category"]  # it keeps the label the workbook taxonomy gave it
    assert wp["needs_human_review"] is True
    assert tree["needs_review"][0]["cluster_id"] == wp["cluster_id"] == stable_cluster_id(_clusters()[5])

    assert check_url_map_consistency(entries) == []
    assert report["inconsistent_rows"] == []


def test_another_clusters_keyword_does_not_file_a_head_term_under_a_sub_service():
    catalog = [{"name": "SEO", "subservices": [{"name": "Local SEO"}, {"name": "Technical SEO"}]}]
    clusters = [
        _cluster("SEO", [_kw("seo services", 2400)]),
        _cluster("Local SEO", [_kw("local seo", 900), _kw("local seo packages", 50)]),
        _cluster("SEO Packages", [_kw("seo packages", 700)]),
        _cluster("Technical SEO", [_kw("technical seo", 600), _kw("technical seo audit", 90)]),
        _cluster("SEO Audit", [_kw("seo audit", 1900)]),
    ]
    report = build_final_url_map(
        {"clusters": clusters}, website={"pages": PAGES}, client_domain="acme.example", service_catalog=catalog
    )
    rows = _by_primary(report["final_url_map"])
    assert _levels(rows["seo packages"]) == ("SEO", "SEO Packages", None, None)
    assert _levels(rows["seo audit"]) == ("SEO", "SEO Audit", None, None)
    assert _levels(rows["local seo"]) == ("SEO", "Local SEO", None, None)
    assert report["url_tree"]["violations"] == []
    assert check_url_map_consistency(report["final_url_map"]) == []


def test_without_a_catalog_build_final_url_map_keeps_the_workbook_levels():
    plain = build_final_url_map(
        {"clusters": copy.deepcopy(_clusters())}, website={"pages": PAGES}, client_domain="acme.example"
    )
    empty = _map([])
    assert plain["url_tree"] == EMPTY_REPORT == empty["url_tree"]
    assert [_levels(e) for e in plain["final_url_map"]] == [_levels(e) for e in empty["final_url_map"]]
    assert [e["page_type"] for e in plain["final_url_map"]] == [e["page_type"] for e in empty["final_url_map"]]


def test_each_row_records_whether_its_volume_was_measured():
    report = build_final_url_map(
        {
            "clusters": [
                _cluster("SEO", [_kw("seo services", 2400)]),
                _cluster("Local SEO", [_kw("local seo", 900, provider_fallback=True)]),
                _cluster("Shopify SEO", [_kw("shopify seo", None)]),
            ]
        },
        website={"pages": PAGES},
        client_domain="acme.example",
    )
    rows = _by_primary(report["final_url_map"])
    assert rows["seo services"]["search_volume_status"] == "measured"
    assert rows["local seo"]["search_volume_status"] == "estimated"
    assert rows["shopify seo"]["search_volume_status"] == "unverified"


# ---------------------------------------------------------------- Phase 6 wiring


async def test_site_architecture_passes_the_service_catalog_to_the_url_map(monkeypatch):
    from app.services import site_architecture, url_mapping

    async def _click_depth(seed_url, *, page_cap=80):
        return {"urls_crawled": 1, "issues": [], "max_depth": 0, "pages_by_depth": {}, "orphan_pages": [], "phantom_directories": []}

    captured: dict = {}

    def _fake_map(cluster_report, **kwargs):
        captured.update(kwargs)
        return {"final_url_map": [], "crawl_page_count": 0, "mapped_cluster_count": 0, "summary": {}}

    monkeypatch.setattr(site_architecture, "measure_click_depth", _click_depth)
    monkeypatch.setattr(url_mapping, "build_final_url_map", _fake_map)
    catalog = _seo_catalog()
    await site_architecture.run_site_architecture_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        domain="acme.example",
        demand={"cluster_report": {"clusters": _clusters()[1:2]}, "service_catalog": catalog},
        commercial={},
        website={"pages": PAGES},
    )
    assert captured["service_catalog"] == catalog


def test_tree_violations_are_a_warning_in_the_phase_6_validation():
    from app.services.phase_validation.deterministic import run_deterministic_checks
    from app.services.phase_validation.schema import CheckStatus, ValidationDecision, decide_from_checks

    def _checks(tree, *, report=None):
        output = {
            "target_url_tree": [{"url": "/seo/", "parent": "/", "depth": 1}],
            "url_map_report": report if report is not None else {"final_url_map": [], "url_tree": tree},
        }
        return [
            c
            for c in run_deterministic_checks(agent_key="site_architecture", output=output, company_ctx={}, prior={})
            if c.check_id == "url_tree_placement"
        ]

    bad = _checks({"placed": 3, "needs_review": [], "violations": ["Cluster c-seo is placed more than once."]})
    assert [c.status for c in bad] == [CheckStatus.WARNING.value]
    assert "c-seo" in (bad[0].evidence or "")
    assert decide_from_checks(bad) == ValidationDecision.PASS_WITH_WARNINGS.value  # a warning never blocks

    good = _checks({"placed": 3, "needs_review": [{"cluster_id": "x", "primary_keyword": "y", "reason": "z"}], "violations": []})
    assert [c.status for c in good] == [CheckStatus.PASSED.value]
    assert "1" in good[0].message

    assert _checks(None) == []  # a URL map built before the tree existed has nothing to check
    # No catalog: the tree was never built, so it is not reported as a pass.
    assert _checks({"placed": 0, "needs_review": [], "violations": []}) == []
    assert _checks(None, report="garbage") == [] and _checks(None, report=["x"]) == []
