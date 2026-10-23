"""Phase 5 service hierarchy for seeds and extracted keywords."""

from app.services.keyword_clustering import build_service_seed_clusters


def _seed_cluster(
    seed: str,
    *,
    target: str | None = None,
    target_type: str = "keyword",
    page_path: str | None = None,
    parent_segment: str | None = None,
    exact: list[str] | None = None,
    phrase: list[str] | None = None,
    related: list[str] | None = None,
    broad: list[str] | None = None,
) -> dict:
    def rows(keywords: list[str] | None, match_class: str) -> list[dict]:
        return [
            {
                "keyword": keyword,
                "match_class": match_class,
                "volume": 100,
                "difficulty": 20,
                "source": "dataforseo",
            }
            for keyword in (keywords or [])
        ]

    cluster = {
        "seed": seed,
        "target": target or seed,
        "target_type": target_type,
        "exact": rows(exact or [seed], "exact"),
        "phrase": rows(phrase, "phrase"),
        "related": rows(related, "related"),
        "broad": rows(broad, "broad"),
        "classes_missing": [],
    }
    if page_path:
        cluster["page_path"] = page_path
    if parent_segment:
        cluster["parent_segment"] = parent_segment
    return cluster


def test_google_ads_seed_does_not_nest_under_meta_ads():
    groups = build_service_seed_clusters(
        [
            _seed_cluster(
                "Google Ads",
                target="Google Ads/PPC Management",
                target_type="service",
                phrase=["google ads management"],
            ),
            _seed_cluster(
                "Google Ads PPC Management",
                target="Google Ads/PPC Management",
                target_type="service",
                broad=["ppc management agency"],
            ),
            _seed_cluster(
                "Meta Ads",
                target="Meta Ads",
                target_type="service",
                phrase=["meta ads agency"],
            ),
        ],
        ["Meta Ads", "Google Ads/PPC Management"],
    )
    by_service = {g["service"]: g for g in groups}
    meta_seeds = {s["seed"] for s in by_service["Meta Ads"]["seeds"]}
    google_seeds = {s["seed"] for s in by_service["Google Ads/PPC Management"]["seeds"]}
    assert meta_seeds == {"Meta Ads"}
    assert google_seeds == {"Google Ads", "Google Ads PPC Management"}


def test_google_ads_seed_goes_to_other_when_only_meta_in_services():
    groups = build_service_seed_clusters(
        [
            _seed_cluster(
                "Google Ads",
                target_type="keyword",
                phrase=["google ads management"],
            )
        ],
        ["Meta Ads"],
    )
    by_service = {g["service"]: g for g in groups}
    assert "Meta Ads" not in by_service or not by_service["Meta Ads"]["seeds"]
    assert by_service["Other website topics"]["seeds"][0]["seed"] == "Google Ads"


def test_groups_explicit_service_seed_with_all_extracted_keywords():
    groups = build_service_seed_clusters(
        [
            _seed_cluster(
                "lead generation",
                target="Lead Generation",
                target_type="service",
                phrase=["b2b lead generation"],
                related=["generation of qualified leads"],
                broad=["sales prospecting"],
            )
        ],
        ["Lead Generation", "SEO Services"],
    )

    assert {g["service"] for g in groups} == {"Lead Generation", "SEO Services"}
    group = next(g for g in groups if g["service"] == "Lead Generation")
    assert group["seed_count"] == 1
    assert group["keyword_count"] == 4
    seed = group["seeds"][0]
    assert seed["assignment_reason"] == "explicit_service_target"
    assert seed["seed_index"] == 1
    assert seed["seed_label"] == "Seed 1"
    assert seed["class_counts"] == {"exact": 1, "phrase": 1, "related": 1, "broad": 1}
    assert {row["keyword"] for row in seed["keywords"]} == {
        "lead generation",
        "b2b lead generation",
        "generation of qualified leads",
        "sales prospecting",
    }
    empty = next(g for g in groups if g["service"] == "SEO Services")
    assert empty["seeds"] == []
    assert empty["keyword_count"] == 0


def test_selected_seo_themes_stay_seeds_of_the_parent_service():
    groups = build_service_seed_clusters(
        [
            _seed_cluster(
                "local seo",
                target="SEO",
                target_type="seed",
                parent_segment="SEO Services",
                phrase=["local seo services"],
            ),
            _seed_cluster(
                "technical seo",
                target="SEO",
                target_type="seed",
                parent_segment="SEO Services",
                phrase=["technical seo audit"],
            ),
            _seed_cluster(
                "seo audit",
                target_type="page",
                phrase=["technical seo audit"],
            ),
        ],
        ["SEO Services", "Lead Generation"],
    )

    seo = next(g for g in groups if g["service"] == "SEO Services")
    assert seo["subservice_count"] == 0
    assert seo["subservices"] == []
    assert {seed["seed"] for seed in seo["seeds"]} == {
        "local seo",
        "technical seo",
        "seo audit",
    }
    assert [seed["seed_label"] for seed in seo["seeds"]] == ["Seed 1", "Seed 2", "Seed 3"]


def test_maps_page_seed_to_service_by_overlap_and_keeps_unmapped_visible():
    groups = build_service_seed_clusters(
        [
            _seed_cluster(
                "technical seo audit",
                target="/services/technical-seo",
                target_type="page",
                phrase=["technical seo audit services"],
            ),
            _seed_cluster(
                "conversion rate optimization",
                target_type="keyword",
                broad=["landing page testing"],
            ),
        ],
        ["SEO Services", "Lead Generation"],
    )

    by_service = {group["service"]: group for group in groups}
    assert by_service["SEO Services"]["seeds"][0]["seed"] == "technical seo audit"
    assert (
        by_service["SEO Services"]["seeds"][0]["assignment_reason"]
        == "service_token_overlap"
    )
    assert (
        by_service["Other website topics"]["seeds"][0]["seed"]
        == "conversion rate optimization"
    )
    assert (
        by_service["Other website topics"]["seeds"][0]["assignment_reason"]
        == "no_service_overlap"
    )


def test_seeds_are_numbered_within_each_service():
    groups = build_service_seed_clusters(
        [
            _seed_cluster("seo", target="SEO Services", target_type="service", phrase=["seo audit"]),
            _seed_cluster(
                "search engine optimization",
                target="SEO Services",
                target_type="service",
            ),
            _seed_cluster("lead generation", target_type="service"),
        ],
        ["SEO Services", "Lead Generation"],
    )
    seo = next(g for g in groups if g["service"] == "SEO Services")
    labels = [(s["seed_index"], s["seed_label"], s["seed"]) for s in seo["seeds"]]
    # Two seeds under SEO Services, numbered Seed 1 / Seed 2 in order
    assert [(i, label) for i, label, _ in labels] == [(1, "Seed 1"), (2, "Seed 2")]
    # Every service cluster has at least one numbered seed
    for group in groups:
        assert group["seeds"]
        assert group["seeds"][0]["seed_index"] == 1


def test_every_seed_is_assigned_once():
    source = [
        _seed_cluster("seo services", target_type="service"),
        _seed_cluster("local seo", target_type="page"),
        _seed_cluster("lead generation", target_type="service"),
        _seed_cluster("email outreach", target_type="keyword"),
    ]
    groups = build_service_seed_clusters(source, ["SEO Services", "Lead Generation"])

    assigned = [
        seed["seed"]
        for group in groups
        for seed in group["seeds"]
    ]
    assert sorted(assigned) == sorted(cluster["seed"] for cluster in source)
    assert len(assigned) == len(set(assigned))


def test_empty_cleaned_seed_still_appears_once():
    groups = build_service_seed_clusters(
        [
            {
                "seed": "SEO Services",
                "target": "SEO Services",
                "target_type": "service",
                "exact": [],
                "phrase": [],
                "related": [],
                "broad": [],
                "keyword_count": 0,
                "classes_missing": ["exact", "phrase", "related", "broad"],
            }
        ],
        ["SEO Services", "Lead Generation"],
    )
    names = [g["service"] for g in groups]
    assert "SEO Services" in names
    # Selected services with no matching seed still appear as empty shells.
    assert "Lead Generation" in names
    seo = next(g for g in groups if g["service"] == "SEO Services")
    seed = seo["seeds"][0]
    assert seed["seed"] == "SEO Services"
    assert seed["seed_index"] == 1
    assert seed["keyword_count"] == 0
    assert seed["class_counts"] == {"exact": 0, "phrase": 0, "related": 0, "broad": 0}
    lead = next(g for g in groups if g["service"] == "Lead Generation")
    assert lead["seeds"] == []
    assert lead["keyword_count"] == 0


def test_enrich_subservice_competitor_matrix():
    from app.services.keyword_opportunity import (
        enrich_service_clusters_with_competitors,
    )

    groups = build_service_seed_clusters(
        [
            _seed_cluster(
                "local seo",
                target="Local SEO",
                target_type="sub_service",
                page_path="/seo/local",
                exact=["local seo agency"],
            )
        ],
        ["SEO Services"],
    )
    scored = [
        {
            "keyword": "local seo agency",
            "gap_flag": True,
            "competitor_domains": ["rival-a.com", "rival-b.com"],
            "volume": 500,
        }
    ]
    competitive = {
        "service_level_comparison": {
            "best_by_service": {
                "seo": {"service": "seo", "competitor": "Rival A", "score": 8.2},
            }
        }
    }
    enriched, matrix = enrich_service_clusters_with_competitors(
        groups,
        scored,
        competitive_landscape=competitive,
        competitors=[{"name": "Rival A", "domain": "rival-a.com"}],
    )
    assert enriched[0]["subservices"] == []
    seed = next(row for row in enriched[0]["seeds"] if row["seed"] == "local seo")
    assert seed["seed_label"].startswith("Seed ")
    assert seed["gap_keyword_count"] == 1
    assert enriched[0]["competitor_category"] == "seo"
    assert enriched[0]["category_leader"]["competitor"] == "Rival A"
    assert matrix == []
