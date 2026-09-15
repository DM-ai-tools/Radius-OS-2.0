"""Keyword cleaning pipeline — stage order and outputs."""

from app.services.keyword_clustering import clean_and_dedupe
from app.services.keyword_pipeline import (
    extract_entities_and_topics,
    run_keyword_pipeline,
)
from app.services.keyword_relevance import build_relevance_context


def _ctx(**kwargs):
    defaults = {
        "services": ["SEO Services", "Lead Generation"],
        "cdd_keywords": ["local seo", "b2b lead generation"],
        "page_terms": ["technical seo audit"],
        "themes": ["conversion rate optimisation"],
        "seeds": ["SEO Services", "Lead Generation", "local seo"],
        "competitor_names": ["King Kong", "WebFX"],
        "competitor_domains": ["kingkong.co", "webfx.com"],
        "brand_name": "ClickTrends",
        "domain": "clicktrends.com.au",
    }
    defaults.update(kwargs)
    return build_relevance_context(**defaults)


def test_pipeline_stage_order_and_counts():
    ctx = _ctx()
    rows = [
        {"keyword": "  SEO Services  ", "volume": 100, "match_class": "exact", "seed": "SEO Services"},
        {"keyword": "seo services", "volume": 90, "match_class": "exact", "seed": "SEO Services"},
        {"keyword": "King Kong pricing", "volume": 200},
        {"keyword": "python programming", "volume": 5000, "match_class": "broad"},
        {"keyword": "https://example.com/seo", "volume": 10},
    ]
    out = run_keyword_pipeline(rows, relevance_context=ctx, brand_name="ClickTrends")
    stages = out["pipeline"]
    assert stages["raw"]["count"] == 5
    assert stages["normalize"]["count"] == 4  # URL caught early
    assert stages["dedupe"]["count"] == 3  # duplicate seo services collapsed
    assert stages["final"]["count"] == 1
    kws = {r["keyword"] for r in out["keywords"]}
    assert kws == {"seo services"}
    assert out["keywords"][0]["intent"] in {"informational", "commercial", "transactional", "navigational"}
    assert out["keywords"][0]["parent_topic"]
    assert any(s["stage"] == "brand" for s in out["excluded"])


def test_extract_entities_uses_service_evidence():
    ctx = _ctx()
    meta = extract_entities_and_topics("technical seo audit checklist", ctx)
    assert meta["parent_topic"]
    assert meta["entities"]


def test_clean_and_dedupe_uses_pipeline_with_relevance():
    ctx = _ctx()
    rows = [
        {"keyword": "seo services", "volume": 100, "match_class": "phrase", "seed": "SEO Services"},
        {"keyword": "python programming", "volume": 900, "gap_flag": True},
        {"keyword": "crm", "volume": 5000},
    ]
    cleaned = clean_and_dedupe(rows, relevance_context=ctx, brand_name="ClickTrends")
    kws = {r["keyword"] for r in cleaned}
    assert "seo services" in kws
    assert "python programming" not in kws
    assert "crm" not in kws
