"""Close variants count once (2026-10-19).

A keyword table listed "seo agency", "agency seo", "seo agencies", "agency for
seo" and "seo optimisation agency" as five keywords, each at 4,400/mo: one
search, with Google's grouped volume repeated. They fold into one keyword that
lists the other wordings; distinct searches never fold.
"""

from __future__ import annotations

from app.services.keyword_pipeline import run_keyword_pipeline
from app.services.keyword_seeding import build_seed_clusters
from app.services.keyword_variants import collapse_close_variants, variant_key

AGENCY = [
    "agency for seo", "agency seo", "seo agencies", "seo agency", "seo optimisation agency",
    "seo optimization agencies", "seo optimization agency", "search engine optimization agency",
]
WEB_DEV = [
    "seo and web development", "seo in web development", "seo web development",
    "web development and seo", "web development for seo",
]


def test_screenshot_groups_each_fold_to_one_key():
    assert len({variant_key(k) for k in AGENCY}) == 1
    assert len({variant_key(k) for k in WEB_DEV}) == 1
    assert variant_key("pay per click agency") == variant_key("ppc agency")
    assert variant_key("small business web design") == variant_key("web design for small business")


def test_distinct_searches_never_fold():
    distinct = ["seo agency", "seo agency melbourne", "seo audit", "seo audit tool", "seo analysis",
                "seo analyst", "seo", "web development", "seo course", "seo courses online"]
    assert len({variant_key(k) for k in distinct}) == len(distinct)


def test_collapse_keeps_the_clearest_wording_and_lists_the_rest():
    rows = [{"keyword": k, "volume": 4400} for k in AGENCY]
    kept, folded = collapse_close_variants(rows)
    assert len(kept) == 1 and len(folded) == len(AGENCY) - 1
    assert kept[0]["keyword"] == "seo agency"
    assert set(kept[0]["close_variants"]) == set(AGENCY) - {"seo agency"}
    # On a tie, the wording that follows the seed beats a provider-first one.
    tie = [{"keyword": "agency seo", "volume": 50, "seed": "seo"}, {"keyword": "seo agencies", "volume": 50, "seed": "seo"},
           {"keyword": "audit seo website", "volume": 9, "seed": "seo audit"}, {"keyword": "seo audit for website", "volume": 9, "seed": "seo audit"}]
    kept, _ = collapse_close_variants(tie)
    assert [k["keyword"] for k in kept] == ["seo agencies", "seo audit for website"]
    # A higher measured volume wins over a nicer wording.
    kept, _ = collapse_close_variants([{"keyword": "seo agency", "volume": 10}, {"keyword": "agency seo", "volume": 90}])
    assert kept[0]["keyword"] == "agency seo"


def test_seed_lists_fold_variants_across_classes_and_recount():
    rows = [
        {"keyword": "seo agency", "seed": "seo agency", "match_class": "exact", "volume": 4400},
        {"keyword": "seo agencies", "seed": "seo agency", "match_class": "phrase", "volume": 4400},
        {"keyword": "agency for seo", "seed": "seo agency", "match_class": "related", "volume": 4400},
        {"keyword": "what is an seo agency", "seed": "seo agency", "match_class": "phrase", "volume": 320},
    ]
    [bucket] = build_seed_clusters(rows, ["seo agency"])
    kws = [r["keyword"] for cls in ("exact", "phrase", "related", "broad") for r in bucket[cls]]
    assert sorted(kws) == ["seo agency", "what is an seo agency"]
    assert bucket["keyword_count"] == 2 and bucket["volume_sum"] == 4720  # not 13,520
    assert bucket["close_variants_folded"] == 2
    assert set(bucket["exact"][0]["close_variants"]) == {"seo agencies", "agency for seo"}


def test_cleaning_records_each_folded_variant():
    rows = [{"keyword": k, "volume": 40, "seed": "seo"} for k in WEB_DEV]
    result = run_keyword_pipeline(rows, source_label="test")
    kept = [r["keyword"] for r in result["keywords"]]
    assert kept == ["seo web development"]
    folded = [e for e in result["excluded"] if e.get("reason") == "close_variant"]
    assert len(folded) == len(WEB_DEV) - 1
    assert all(e["variant_of"] == "seo web development" for e in folded)
    assert len(result["keywords"][0]["close_variants"]) == len(WEB_DEV) - 1
