"""Phase 5 — keywords harvested from competitor pages, before clustering."""

from app.services import competitor_keyword_intel as intel
from app.services.competitor_keyword_intel import (
    build_competitor_keyword_rows,
    extract_candidate_terms,
    filter_candidate_terms,
)
from app.services.keyword_relevance import build_relevance_context

SNAPSHOTS = [
    {
        "name": "Rival Plumbing",
        "domain": "rival.example",
        "pages": [
            {
                "url": "https://rival.example/services/drain-cleaning",
                "path": "/services/drain-cleaning",
                "title": "Drain Cleaning Services | Rival Plumbing",
                "h1s": ["Emergency Drain Cleaning"],
                "h2s": ["Hydro Jetting Services", "Blocked Drain Repair"],
                "meta_description": "Fast drain cleaning for homeowners.",
                "kind": "commercial",
            }
        ],
    },
    {
        "name": "Second Rival",
        "domain": "second.example",
        "pages": [
            {
                "url": "https://second.example/services/drain-cleaning",
                "path": "/services/drain-cleaning",
                "title": "Drain Cleaning Experts",
                "h1s": ["Drain Cleaning Services"],
                "h2s": [],
                "meta_description": "",
                "kind": "commercial",
            }
        ],
    },
]


def _ctx():
    return build_relevance_context(
        services=["Drain Cleaning", "Hydro Jetting"],
        cdd_keywords=["blocked drain"],
        competitor_names=["Rival Plumbing", "Second Rival"],
        competitor_domains=["rival.example", "second.example"],
        brand_name="Northside Plumbing",
        domain="northside.example",
    )


def test_extract_pulls_multiword_phrases_from_page_signals():
    candidates = extract_candidate_terms(SNAPSHOTS)
    keywords = {c["keyword"] for c in candidates}
    assert "drain cleaning" in keywords
    assert "hydro jetting" in keywords
    assert "blocked drain repair" in keywords


def test_extract_records_evidence_for_each_candidate():
    candidates = extract_candidate_terms(SNAPSHOTS)
    row = next(c for c in candidates if c["keyword"] == "hydro jetting")
    assert row["evidence_url"] == "https://rival.example/services/drain-cleaning"
    assert row["evidence_field"] in ("title", "h1", "h2", "meta_description", "slug")
    assert row["competitor_domain"] == "rival.example"


def test_extract_counts_competitors_sharing_a_term():
    candidates = extract_candidate_terms(SNAPSHOTS)
    shared = next(c for c in candidates if c["keyword"] == "drain cleaning")
    assert shared["competitor_count"] == 2
    solo = next(c for c in candidates if c["keyword"] == "hydro jetting")
    assert solo["competitor_count"] == 1


def test_extract_ranks_shared_terms_first():
    candidates = extract_candidate_terms(SNAPSHOTS)
    assert candidates[0]["keyword"] == "drain cleaning"


def test_extract_skips_single_words_and_boilerplate():
    candidates = extract_candidate_terms(
        [
            {
                "name": "Rival",
                "domain": "rival.example",
                "pages": [
                    {
                        "url": "https://rival.example/",
                        "path": "/",
                        "title": "Home",
                        "h1s": ["Welcome"],
                        "h2s": ["Contact Us", "About Us"],
                        "meta_description": "",
                        "kind": "other",
                    }
                ],
            }
        ]
    )
    keywords = {c["keyword"] for c in candidates}
    assert "home" not in keywords
    assert "welcome" not in keywords
    assert "contact us" not in keywords
    assert "about us" not in keywords


def test_filter_drops_competitor_brand_terms():
    candidates = extract_candidate_terms(SNAPSHOTS)
    kept, audit = filter_candidate_terms(candidates, _ctx())
    keywords = {c["keyword"] for c in kept}
    assert not any("rival" in k for k in keywords)
    assert audit["input_count"] >= len(kept)


def test_filter_keeps_service_relevant_terms():
    candidates = extract_candidate_terms(SNAPSHOTS)
    kept, _audit = filter_candidate_terms(candidates, _ctx())
    assert "drain cleaning" in {c["keyword"] for c in kept}


def test_rows_are_shaped_like_seeded_keywords():
    rows = build_competitor_keyword_rows(
        [
            {
                "keyword": "drain cleaning",
                "competitor_domain": "rival.example",
                "evidence_url": "https://rival.example/services/drain-cleaning",
                "evidence_field": "title",
                "competitor_count": 2,
            }
        ],
        volumes={"drain cleaning": {"volume": 480, "cpc": 6.2, "difficulty": 24}},
        client_keywords={"other keyword"},
        min_volume=10,
    )
    row = rows[0]
    assert row["keyword"] == "drain cleaning"
    assert row["volume"] == 480
    assert row["source"] == "competitor_page"
    assert row["match_class"] in ("exact", "phrase", "related", "broad")
    assert row["intent"]
    assert row["funnel"]
    assert row["gap_flag"] is True
    assert row["competitor_domain"] == "rival.example"


def test_rows_drop_terms_below_the_volume_floor():
    rows = build_competitor_keyword_rows(
        [{"keyword": "drain cleaning", "competitor_domain": "r.example"}],
        volumes={"drain cleaning": {"volume": 5}},
        client_keywords=set(),
        min_volume=10,
    )
    assert rows == []


def test_rows_drop_terms_with_no_volume_evidence():
    rows = build_competitor_keyword_rows(
        [{"keyword": "unverified phrase", "competitor_domain": "r.example"}],
        volumes={},
        client_keywords=set(),
        min_volume=10,
    )
    assert rows == []


def test_rows_mark_non_gap_when_client_already_ranks():
    rows = build_competitor_keyword_rows(
        [{"keyword": "drain cleaning", "competitor_domain": "r.example"}],
        volumes={"drain cleaning": {"volume": 300}},
        client_keywords={"drain cleaning"},
        min_volume=10,
    )
    assert rows[0]["gap_flag"] is False


async def test_collect_returns_empty_when_crawl_fails(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("WAF blocked")

    monkeypatch.setattr(intel, "fetch_competitor_site_snapshots", _boom)
    out = await intel.collect_competitor_page_keywords(
        competitors=[{"name": "Rival", "url": "https://rival.example"}],
        relevance_context=_ctx(),
        location_code=2840,
    )
    assert out["keywords"] == []
    assert out["snapshots"] == []
    assert out["stats"]["error"]


async def test_collect_returns_empty_on_timeout(monkeypatch):
    import asyncio

    async def _hang(*args, **kwargs):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(intel, "fetch_competitor_site_snapshots", _hang)
    out = await intel.collect_competitor_page_keywords(
        competitors=[{"name": "Rival", "url": "https://rival.example"}],
        relevance_context=_ctx(),
        location_code=2840,
        crawl_timeout=0.1,
    )
    assert out["keywords"] == []
    assert out["stats"]["error"] == "crawl_timeout"


async def test_collect_volume_checks_candidates_and_returns_rows(monkeypatch):
    async def _snaps(*args, **kwargs):
        return SNAPSHOTS

    async def _volumes(keywords, *, location_code=2840, **kwargs):
        assert "drain cleaning" in keywords
        return [{"keyword": "drain cleaning", "volume": 720, "cpc": 5.0}], []

    monkeypatch.setattr(intel, "fetch_competitor_site_snapshots", _snaps)
    monkeypatch.setattr(intel.dataforseo, "search_volume", _volumes)
    out = await intel.collect_competitor_page_keywords(
        competitors=[{"name": "Rival", "url": "https://rival.example"}],
        relevance_context=_ctx(),
        location_code=2840,
    )
    assert [r["keyword"] for r in out["keywords"]] == ["drain cleaning"]
    assert out["snapshots"] == SNAPSHOTS
    assert out["stats"]["candidates"] > 0
    assert out["stats"]["qualified"] == 1


async def test_collect_survives_volume_provider_failure(monkeypatch):
    async def _snaps(*args, **kwargs):
        return SNAPSHOTS

    async def _boom(*args, **kwargs):
        raise RuntimeError("dataforseo down")

    monkeypatch.setattr(intel, "fetch_competitor_site_snapshots", _snaps)
    monkeypatch.setattr(intel.dataforseo, "search_volume", _boom)
    out = await intel.collect_competitor_page_keywords(
        competitors=[{"name": "Rival", "url": "https://rival.example"}],
        relevance_context=_ctx(),
        location_code=2840,
    )
    assert out["keywords"] == []
    # The crawl still succeeded, so snapshots remain useful downstream.
    assert out["snapshots"] == SNAPSHOTS
