"""The home page takes the industry head term (2026-10-27, Task 7).

Clusters whose keywords came from the industry layer (``seed_origin == "industry"``) are
merged into ONE home row mapped to ``/``. Its primary is the highest-volume in-scope
industry keyword; the other industry keywords are its secondary keywords (up to 10). With
no industry cluster the URL map is built as before and says the home page fell back.

Also pins that the ``seed_origin`` tag survives from the seeded rows into
``cluster_report["clusters"][i]["keywords"]``, through each step that rebuilds a cluster row.
"""

from __future__ import annotations

import copy
from unittest.mock import patch

from app.services.keyword_clustering import run_keyword_clustering
from app.services.keyword_cluster_structure import stable_cluster_id
from app.services.keyword_llm_clustering import build_cluster_report_from_llm
from app.services.keyword_seeding import build_seed_clusters, flatten_dataset
from app.services.memory_packs import slim_search_demand_memory
from app.services.url_mapping import build_final_url_map, is_industry_cluster

DOMAIN = "acme.example"
PAGES = [
    {"url": "https://acme.example/", "title": "Acme | Digital Marketing Agency", "h1": "Digital marketing agency"},
    {"url": "https://acme.example/seo/", "title": "SEO Services", "h1": "SEO services"},
    {"url": "https://acme.example/ppc/", "title": "PPC Management", "h1": "PPC management"},
]


def _row(keyword, volume, origin=None, **extra):
    row = {"keyword": keyword, "volume": volume, "intent": "commercial", "relevance_reason": "seed_phrase"}
    if origin:
        row["seed_origin"] = origin
    row.update(extra)
    return row


def _cluster(name, rows, primary=None):
    primary = primary or rows[0]["keyword"]
    members = [{**r, "role": "Primary" if r["keyword"] == primary else "Secondary"} for r in rows]
    return {"name": name, "primary_keyword": primary, "intent": "commercial", "keywords": members}


def _industry_a():
    return _cluster(
        "Digital Marketing Agency",
        [_row("digital marketing agency", 1300, "industry"), _row("online marketing company", 320, "industry")],
    )


def _industry_b():
    return _cluster(
        "Marketing Services",
        [_row("marketing services", 880, "industry"), _row("internet marketing firm", 210, "industry")],
    )


def _seo():
    return _cluster("SEO", [_row("seo services", 2400), _row("seo company", 500)])


def _ppc():
    return _cluster("PPC", [_row("ppc management", 700), _row("ppc agency", 260)])


def _map(clusters, pages=PAGES, **kwargs):
    website = {"pages": pages} if pages is not None else None
    return build_final_url_map(
        {"clusters": copy.deepcopy(clusters)}, website=website, client_domain=DOMAIN, **kwargs
    )


def _homes(report):
    return [e for e in report["final_url_map"] if e.get("is_home")]


def _plain(entries):
    """Entries without the row number, which shifts when a home row is added."""
    return [{k: v for k, v in e.items() if k != "row_no"} for e in entries]


# ---------------------------------------------------------------- is_industry_cluster


def test_a_cluster_is_an_industry_cluster_when_most_rows_are_tagged_industry():
    ind = lambda n: [_row(f"kw {i}", 10, "industry") for i in range(n)]  # noqa: E731
    svc = lambda n: [_row(f"svc {i}", 10) for i in range(n)]  # noqa: E731
    assert is_industry_cluster({"keywords": ind(2) + svc(1)}) is True
    assert is_industry_cluster({"keywords": ind(1) + svc(2)}) is False
    assert is_industry_cluster({"keywords": ind(2) + svc(2)}) is False  # a tie is not a majority
    assert is_industry_cluster({"keywords": svc(3) + [_row("x", 1, "synonym")]}) is False
    assert is_industry_cluster({"keywords": []}) is False
    assert is_industry_cluster({}) is False
    assert is_industry_cluster({"keywords": ["plain string", None]}) is False


# ---------------------------------------------------------------- the home row


def test_industry_clusters_map_to_the_home_page():
    report = _map([_seo(), _industry_a(), _industry_b(), _ppc()])
    entries = report["final_url_map"]

    homes = _homes(report)
    assert len(homes) == 1
    home = homes[0]
    assert entries[0] is home  # the home page sits above everything else
    assert home["primary_keyword"] == "digital marketing agency"
    assert home["current_url"] == "/" and home["selected_url"] == "/" and home["target_url"] == "/"
    assert home["proposed_url"] is None
    assert home["action"] == "OPTIMIZE_EXISTING"
    assert home["status"] == "LIVE"
    assert home["ia_page_type"] == "home"
    assert home["is_home"] is True

    # Both industry clusters became this one row; the service clusters kept theirs.
    assert sorted(home["merged_cluster_ids"]) == sorted(
        stable_cluster_id(c) for c in (_industry_a(), _industry_b())
    )
    assert [e["primary_keyword"] for e in entries if not e.get("is_home")] == ["seo services", "ppc management"]
    assert report["mapped_cluster_count"] == 3

    assert report["home_page"] == {"keyword": "digital marketing agency", "source": "industry"}
    assert report["inconsistent_rows"] == []
    assert "consistency_errors" not in home
    assert "industry" in home["mapping_reason"].lower()
    # Nothing was scored for the root (the crawl pool leaves it out), and the row says so.
    assert home["band_source"] == "not_scored" and home["url_score"] is None and home["score_band"] is None
    assert home["decision_trail"][0].startswith("Home page:")
    assert not any("No live page matched" in line or "Initial action" in line for line in home["decision_trail"])
    # Every row of the merged clusters is carried, so nothing is reported as left off.
    assert not any("left off" in line for line in home["decision_trail"])


def test_the_home_row_is_not_scored_so_it_takes_no_band_penalty_and_no_band_count():
    from app.services.url_map_report import confidence_text, decision_row, score_text

    report = _map([_seo(), _industry_a(), _industry_b(), _ppc()])
    plain = _map([_seo(), _ppc()])
    (home,) = _homes(report)

    # The band, the score and the confidence are unavailable, not LOW / 0.
    assert home["score_band"] is None and home["url_score"] is None and home["match_confidence"] is None
    assert home["band_source"] == "not_scored"

    # Hub Page +3, live +0.5, 2,710/mo +3 = 6.5: P0. The LOW penalty would have made it 5.5, P1.
    assert home["priority"] == "P0"
    assert home["priority_reason"] == "Hub Page +3, live +0.5, 2,710/mo +3 = 6.5 -> P0"
    assert "LOW match" not in " ".join(home["decision_trail"])

    # No band count includes the row; the service rows count exactly as before.
    for key in ("high_band", "medium_band", "low_band"):
        assert report["summary"][key] == plain["summary"][key]

    # The renderers read the row without a band, a score or a confidence.
    assert score_text(home) == "Not scored: the site root is outside the URL candidate pool."
    assert confidence_text(home) == "—"
    assert decision_row(home)[2] == ("Not scored: the site root is outside the URL candidate pool.",)

    # Service rows keep their bands and priorities (the shared rule is unchanged).
    services = [e for e in report["final_url_map"] if not e.get("is_home")]
    assert [(e["score_band"], e["priority"], e["priority_reason"]) for e in services] == [
        (e["score_band"], e["priority"], e["priority_reason"]) for e in plain["final_url_map"]
    ]


def test_the_excel_sheet_shows_the_home_row_with_its_live_url():
    from app.services.bw_workbook import build_category_url_mapping_rows

    report = _map([_seo(), _industry_a(), _industry_b()])
    data = [r for r in build_category_url_mapping_rows(report["final_url_map"]) if r["row_type"] == "data"]
    by_keyword = {r["primary_keyword"]: r for r in data}

    home = by_keyword["digital marketing agency"]
    assert home["current_url"] == "/" and home["proposed_url"] is None
    assert home["status"] == "LIVE"
    assert home["priority"] == "P0"
    assert home["secondary_keywords"] == "marketing services, online marketing company, internet marketing firm"
    # Other rows still resolve through the shared rule.
    assert by_keyword["seo services"]["current_url"] == "/seo"


def test_the_home_row_maps_to_the_site_root_even_when_the_crawl_does_not_list_it():
    pages = [p for p in PAGES if p["url"] != "https://acme.example/"]
    for site in (pages, None):
        report = _map([_industry_a(), _seo()], pages=site)
        (home,) = _homes(report)
        assert home["current_url"] == "/" and home["action"] == "OPTIMIZE_EXISTING"
        assert report["inconsistent_rows"] == []


def test_home_primary_is_the_highest_volume_industry_keyword_and_others_are_secondary():
    industry = [
        _row("digital marketing agency", 1300, "industry"),
        _row("marketing agency", 2900, "industry"),  # the highest-volume in-scope keyword
        _row("online marketing company", 320, "industry"),
        _row("internet marketing firm", 210, "industry"),
        _row("marketing services", 880, "industry"),
        _row("digital marketing company", 720, "industry"),
        _row("web marketing agency", 590, "industry"),
        _row("marketing consultancy", 480, "industry"),
        _row("full service marketing agency", 410, "industry"),
        _row("integrated marketing agency", 300, "industry"),
        _row("marketing solutions", 260, "industry"),
        _row("online advertising agency", 150, "industry"),
        _row("growth marketing agency", 90, "industry"),
        # Not eligible for the home page: unverified, or not tagged industry.
        _row("brand marketing agency", 9000, "industry", relevance_unverified=True),
        _row("seo agency", 9500),
    ]
    cluster = _cluster("Marketing Agency", industry, primary="digital marketing agency")
    report = _map([cluster, _seo()])

    (home,) = _homes(report)
    assert home["primary_keyword"] == "marketing agency"
    assert home["search_volume"] == 2900
    expected = [
        "digital marketing agency",  # 1300
        "marketing services",  # 880
        "digital marketing company",  # 720
        "web marketing agency",  # 590
        "marketing consultancy",  # 480
        "full service marketing agency",  # 410
        "online marketing company",  # 320
        "integrated marketing agency",  # 300
        "marketing solutions",  # 260
        "internet marketing firm",  # 210
    ]
    assert home["secondary_keywords"] == expected  # up to 10, by volume, the primary excluded
    assert home["secondary_keywords_sheet"] == expected
    assert report["home_page"] == {"keyword": "marketing agency", "source": "industry"}
    everything = [home["primary_keyword"], *home["secondary_keywords"]]
    assert "seo agency" not in everything and "brand marketing agency" not in everything

    # The two rows left off are counted on the trail, by number only (no keyword text).
    left_off = [line for line in home["decision_trail"] if "left off" in line]
    assert left_off == [
        "Rows of the merged clusters left off this row: 1 not from the industry layer, "
        "1 industry row(s) not in scope."
    ]


def test_a_measured_volume_always_outranks_an_estimated_one():
    first = _cluster(
        "Marketing Agency",
        [
            _row("marketing agency", 9000, "industry", volume_estimated=True),  # replaced by the measured row below
            _row("digital marketing agency", 400, "industry"),
            _row("online marketing company", 700, "industry", provider_fallback=True),
        ],
    )
    second = _cluster(
        "Marketing Services",
        [
            _row("marketing agency", 300, "industry"),
            _row("marketing services", 150, "industry"),
            _row("marketing firm", None, "industry"),
            _row("web marketing agency", 50, "industry", volume_status="estimated"),
        ],
    )
    (home,) = _homes(_map([first, second]))
    # Measured first (400, 300, 150), then estimated (700, 50), then a keyword with no volume.
    assert home["primary_keyword"] == "digital marketing agency"
    assert home["secondary_keywords"] == [
        "marketing agency",
        "marketing services",
        "online marketing company",
        "web marketing agency",
        "marketing firm",
    ]
    assert home["search_volume"] == 400 and "400/mo)" in home["mapping_reason"]
    assert "estimated" not in home["mapping_reason"]


def test_an_estimated_primary_is_labelled_as_estimated_when_nothing_is_measured():
    cluster = _cluster(
        "Marketing Agency",
        [
            _row("marketing agency", 900, "industry", volume_estimated=True),
            _row("digital marketing agency", 1200, "industry", provider_fallback=True),
        ],
    )
    report = _map([cluster])
    (home,) = _homes(report)
    assert home["primary_keyword"] == "digital marketing agency"
    assert "(1,200/mo, estimated)" in home["mapping_reason"]


def test_the_home_primary_is_a_place_free_term():
    cluster = _cluster(
        "Marketing Agency",
        [
            _row("marketing agency sydney", 5000, "industry"),
            _row("marketing agency near me", 4000, "industry"),
            _row("marketing agency", 1200, "industry"),
        ],
    )
    report = _map([cluster])
    (home,) = _homes(report)
    assert home["primary_keyword"] == "marketing agency"
    assert home["secondary_keywords"] == ["marketing agency sydney", "marketing agency near me"]


def test_a_keyword_without_measured_volume_never_outranks_a_measured_one():
    cluster = _cluster(
        "Marketing Agency",
        [_row("marketing agency", None, "industry"), _row("digital marketing agency", 40, "industry")],
    )
    (home,) = _homes(_map([cluster]))
    assert home["primary_keyword"] == "digital marketing agency"
    assert home["secondary_keywords"] == ["marketing agency"]


def test_no_industry_cluster_falls_back_and_says_so():
    clusters = [_seo(), _ppc()]
    report = _map(clusters)
    assert _homes(report) == []
    assert report["home_page"] == {"keyword": None, "source": "service_fallback"}
    assert [e["primary_keyword"] for e in report["final_url_map"]] == ["seo services", "ppc management"]

    # An industry cluster with nothing usable (every term names a place, or is unverified)
    # does not become the home page: it is mapped like any other cluster, as before.
    unusable = _cluster(
        "Local Agency",
        [
            _row("marketing agency sydney", 900, "industry"),
            _row("marketing agency near me", 700, "industry"),
            _row("marketing agency", 500, "industry", relevance_unverified=True),
        ],
    )
    report = _map([unusable, _seo()])
    assert _homes(report) == []
    assert report["home_page"] == {"keyword": None, "source": "service_fallback"}
    assert {e["primary_keyword"] for e in report["final_url_map"]} == {"marketing agency sydney", "seo services"}


def test_service_clusters_are_unaffected():
    services = [_seo(), _ppc()]
    plain = _map(services)
    with_home = _map([_industry_a(), *services, _industry_b()])

    assert _plain([e for e in with_home["final_url_map"] if not e.get("is_home")]) == _plain(plain["final_url_map"])
    assert [e["row_no"] for e in with_home["final_url_map"]] == [1, 2, 3]
    assert with_home["url_collisions"] == plain["url_collisions"]

    # A service cluster that merely holds a few industry rows is still a service row.
    mixed = _cluster(
        "SEO",
        [_row("seo services", 2400), _row("seo company", 500), _row("marketing agency", 300, "industry")],
    )
    report = _map([mixed, _industry_a()])
    assert [e["primary_keyword"] for e in report["final_url_map"] if not e.get("is_home")] == ["seo services"]


def test_the_home_row_does_not_swallow_a_service_page_of_the_same_offer_line():
    # "digital marketing services" is the same offer line as the home term "digital marketing
    # agency"; the service page must stay its own row.
    service = _cluster(
        "Digital Marketing Services",
        [_row("digital marketing services", 1500), _row("digital marketing packages", 200)],
    )
    report = _map([_industry_a(), service, _seo()])
    (home,) = _homes(report)
    rows = {e["primary_keyword"]: e for e in report["final_url_map"] if not e.get("is_home")}
    assert set(rows) == {"digital marketing services", "seo services"}
    assert not any("Folded" in note for note in home["decision_trail"])
    assert home["l1_category"] is None and home["level"] == 0  # the home row sits above the tree


def test_a_supporting_row_whose_parent_was_merged_into_the_home_page_resolves_to_it():
    first, second = _industry_a(), _industry_b()
    supporting = _cluster("Agency Pricing", [_row("marketing agency pricing", 180), _row("agency retainer cost", 60)])
    supporting["supporting_parent"] = {
        "parent_cluster": second["name"],
        "parent_cluster_id": stable_cluster_id(second),
    }
    report = _map([first, second, supporting])
    (home,) = _homes(report)
    row = next(e for e in report["final_url_map"] if e["primary_keyword"] == "marketing agency pricing")
    assert row["parent_cluster_id"] == home["cluster_id"]
    assert row["current_url"] == "/" and row["url_status"] == "no_dedicated_url"
    assert not any("could not be resolved" in w for w in row.get("link_warnings") or [])
    assert report["inconsistent_rows"] == []


# ---------------------------------------------------------------- seed_origin survives


def _seeded_rows():
    """What seeding hands over: rows of an industry seed and of a service seed, via the real
    ``build_seed_clusters`` and ``flatten_dataset``, tagged the way the seed plans tag them."""
    seeds = ["digital marketing agency", "seo"]
    seed_targets = {
        "digital marketing agency": {
            "target": "digital marketing agency",
            "target_type": "industry",
            "seed_origin": "industry",
        },
        "seo": {"target": "SEO", "target_type": "service"},
    }
    fetched = [
        ("digital marketing agency", "digital marketing agency", 1300),
        ("digital marketing agency", "online marketing company", 320),
        ("digital marketing agency", "marketing agency pricing", 90),
        ("seo", "seo services", 2400),
        ("seo", "seo company", 500),
        ("seo", "local seo", 400),
    ]
    rows = [
        {"seed": seed, "keyword": kw, "volume": vol, "match_class": "phrase", "intent": "commercial"}
        for seed, kw, vol in fetched
    ]
    clusters = build_seed_clusters(rows, seeds, seed_targets=seed_targets)
    flat = flatten_dataset(clusters)
    for row in flat:
        row["relevance_reason"] = "seed_phrase"  # what the relevance gate stamps on a kept row
    return flat


def _origins(report):
    return {
        row["keyword"]: row.get("seed_origin")
        for cluster in report["clusters"]
        for row in cluster["keywords"]
    }


async def test_seed_origin_survives_into_cluster_keywords():
    rows = _seeded_rows()
    assert {r["keyword"]: r.get("seed_origin") for r in rows}["online marketing company"] == "industry"

    report = await run_keyword_clustering(rows, seeds=["digital marketing agency", "seo"], brand_name="Acme")

    origins = _origins(report)
    assert origins["digital marketing agency"] == "industry"
    assert origins["online marketing company"] == "industry"
    assert origins["marketing agency pricing"] == "industry"
    assert origins["seo services"] is None and origins["local seo"] is None

    # The clusters this produces feed the URL map: the industry clusters become the home row.
    url_map = build_final_url_map(report, website={"pages": PAGES}, client_domain=DOMAIN)
    (home,) = [e for e in url_map["final_url_map"] if e.get("is_home")]
    assert home["primary_keyword"] == "digital marketing agency"
    assert url_map["home_page"] == {"keyword": "digital marketing agency", "source": "industry"}
    assert "seo services" not in [home["primary_keyword"], *home["secondary_keywords"]]


async def test_seed_origin_survives_when_the_model_does_the_clustering():
    cleaned = _seeded_rows()
    payload = {
        "clusters": [
            {
                "name": "Digital Marketing Agency",
                "keywords": [
                    {"keyword": "digital marketing agency", "intent": "commercial", "funnel": "BOFU"},
                    {"keyword": "online marketing company", "intent": "commercial", "funnel": "BOFU"},
                ],
            },
            {
                "name": "SEO",
                "keywords": [
                    {"keyword": "seo services", "intent": "commercial", "funnel": "BOFU"},
                    {"keyword": "seo company", "intent": "commercial", "funnel": "BOFU"},
                ],
            },
        ],
        "orphans": [],
    }
    report = build_cluster_report_from_llm(payload, cleaned)
    origins = _origins(report)
    assert origins["digital marketing agency"] == "industry"
    assert origins["online marketing company"] == "industry"
    assert origins["seo services"] is None


async def test_seed_origin_survives_the_shared_memory_slim():
    # Phase 6 reads the shared-memory pack when the approved report is short.
    rows = _seeded_rows()
    report = await run_keyword_clustering(rows, seeds=["digital marketing agency", "seo"], brand_name="Acme")
    slim = slim_search_demand_memory({"cluster_report": report, "clusters": report["clusters"]})
    slim_origins = {
        row["keyword"]: row.get("seed_origin")
        for cluster in slim["clusters"]
        for row in cluster["keywords"]
    }
    assert slim_origins["digital marketing agency"] == "industry"
    assert slim_origins["online marketing company"] == "industry"
    assert slim_origins["seo services"] is None

    url_map = build_final_url_map(slim["cluster_report"], website={"pages": PAGES}, client_domain=DOMAIN)
    assert url_map["home_page"]["source"] == "industry"


async def test_seed_origin_survives_the_whole_search_demand_phase():
    from tests.test_industry_keywords import _canned, _run_phase

    async def fetch(seeds, *, seed_targets=None, **kw):
        return _canned(seeds, seed_targets)

    async def relevant(rows, **kw):  # the relevance judge is a model call; every row passes
        return [{**r, "relevance_reason": "seed_phrase"} for r in rows], [], {}

    async def coherent(clusters, **kw):  # so is the cluster coherence judge
        return list(clusters), [], {"skipped": True, "reason": "test"}

    with (
        patch("app.services.keyword_validation_gate.validate_keywords", relevant),
        patch("app.services.cluster_validation_gate.validate_clusters_coherence", coherent),
    ):
        summary, _events, _captured, _related = await _run_phase(fetch)

    report = summary["cluster_report"]
    origins = _origins(report)
    assert origins["digital marketing agency cost"] == "industry"
    assert origins["digital marketing agency services cost"] == "industry"
    assert origins["seo cost"] is None
    assert origins["local seo cost"] == "subservice_catalog"

    # The Phase 5 report goes straight into the URL map: the industry cluster is the home row.
    url_map = build_final_url_map(report, website={"pages": PAGES}, client_domain=DOMAIN)
    (home,) = [e for e in url_map["final_url_map"] if e.get("is_home")]
    assert home["primary_keyword"] == "digital marketing agency cost"
    assert home["secondary_keywords"] == ["digital marketing agency services cost"]
    assert url_map["home_page"] == {"keyword": "digital marketing agency cost", "source": "industry"}
    assert {e["primary_keyword"] for e in url_map["final_url_map"] if not e.get("is_home")} == {
        "seo cost",
        "local seo cost",
        "meta ads cost",
        "instagram ads cost",
    }
