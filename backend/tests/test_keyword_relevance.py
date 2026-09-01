"""CDD / service / website keyword relevance cleaning."""

from app.services.keyword_clustering import build_service_seed_clusters, clean_and_dedupe
from app.services.keyword_opportunity import business_fit_score, rank_opportunities
from app.services.keyword_relevance import (
    build_relevance_context,
    evaluate_keyword,
    filter_relevant_keywords,
    merge_cleaning_audits,
    page_terms_from_website,
)
from app.services.keyword_seeding import MATCH_CLASSES, build_seed_clusters
from app.services.memory_packs import slim_search_demand_memory


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


def test_keeps_cdd_and_service_keywords():
    ctx = _ctx()
    keep, reason, _ev = evaluate_keyword("seo services melbourne", ctx, match_class="phrase", seed="SEO Services")
    assert keep and reason == "seed_phrase"
    keep, reason, _ev = evaluate_keyword("b2b lead generation", ctx)
    assert keep and reason in {"cdd_phrase", "service_overlap", "seed_exact"}


def test_keeps_related_of_supported_seed_and_drops_unrelated_broad():
    ctx = _ctx()
    keep, reason, _ = evaluate_keyword(
        "services for seo", ctx, match_class="related", seed="SEO Services"
    )
    assert keep and reason == "seed_related"
    keep, reason, _ = evaluate_keyword(
        "salesforce crm software", ctx, match_class="broad", seed="SEO Services"
    )
    assert not keep and reason == "no_business_evidence"


def test_keeps_page_terms_and_nested_website_pages():
    website = {
        "seo_audit": {
            "summary": {
                "pages": [
                    {
                        "title": "Technical SEO",
                        "page_type": "service",
                        "cdd_terms": ["technical seo audit"],
                        "path": "/services/technical-seo",
                    }
                ]
            }
        }
    }
    terms = page_terms_from_website(website)
    assert any("technical seo" in t.lower() for t in terms)
    ctx = build_relevance_context(
        services=["SEO Services"],
        website=website,
        competitor_names=[],
        competitor_domains=[],
    )
    keep, reason, _ = evaluate_keyword("technical seo audit checklist", ctx)
    assert keep and reason in {"page_overlap", "cdd_phrase", "service_overlap"}


def test_drops_competitor_brand_and_stale_and_url():
    ctx = _ctx()
    keep, reason, _ = evaluate_keyword("king kong", ctx)
    assert not keep and reason == "competitor_brand"
    keep, reason, _ = evaluate_keyword("king kong seo", ctx)
    assert not keep and reason == "competitor_brand"
    keep, reason, _ = evaluate_keyword("seo services 2020", ctx, match_class="phrase", seed="SEO Services")
    assert not keep and reason == "stale_year"
    keep, reason, _ = evaluate_keyword("https://webfx.com/seo", ctx)
    assert not keep and reason == "url_noise"


def test_gap_without_business_evidence_is_dropped():
    ctx = _ctx()
    keep, reason, _ = evaluate_keyword("salesforce automation", ctx, gap_flag=True)
    assert not keep and reason == "no_business_evidence"
    keep, reason, _ = evaluate_keyword("local seo pricing", ctx, gap_flag=True)
    assert keep


def test_filter_records_audit_and_preserves_seed_clusters():
    ctx = _ctx()
    rows = [
        {"keyword": "seo services", "match_class": "exact", "seed": "SEO Services", "volume": 1000},
        {"keyword": "best seo services", "match_class": "phrase", "seed": "SEO Services", "volume": 400},
        {"keyword": "python programming", "match_class": "broad", "seed": "SEO Services", "volume": 800},
        {"keyword": "king kong", "match_class": "broad", "seed": "SEO Services", "volume": 200},
        {"keyword": "lead generation", "match_class": "exact", "seed": "Lead Generation", "volume": 900},
    ]
    kept, excluded, audit = filter_relevant_keywords(rows, ctx, source_label="seeding")
    kept_kws = {r["keyword"] for r in kept}
    assert "python programming" not in kept_kws
    assert "king kong" not in kept_kws
    assert "seo services" in kept_kws
    assert "lead generation" in kept_kws
    assert audit["removed_count"] == 2
    assert audit["removed_by_reason"]["no_business_evidence"] == 1
    assert audit["removed_by_reason"]["competitor_brand"] == 1
    assert any(row["keyword"] == "python programming" for row in excluded)

    clusters = build_seed_clusters(kept, ["SEO Services", "Lead Generation", "careers"])
    seeds = [c["seed"] for c in clusters]
    assert seeds == ["SEO Services", "Lead Generation", "careers"]
    careers = next(c for c in clusters if c["seed"] == "careers")
    assert careers["keyword_count"] == 0
    for cls in MATCH_CLASSES:
        assert cls in careers
    groups = build_service_seed_clusters(clusters, ["SEO Services", "Lead Generation"])
    assigned = [seed["seed"] for g in groups for seed in g["seeds"]]
    assert sorted(assigned) == sorted(["SEO Services", "Lead Generation", "careers"])


def test_clean_and_dedupe_no_longer_keeps_off_topic_gap_or_head():
    ctx = _ctx()
    rows = [
        {"keyword": "seo services", "volume": 100, "opportunity_score": 10, "match_class": "phrase", "seed": "SEO Services"},
        {"keyword": "python programming", "volume": 900, "opportunity_score": 80, "gap_flag": True},
        {"keyword": "crm", "volume": 5000, "opportunity_score": 10},
    ]
    cleaned = clean_and_dedupe(rows, seeds=["seo services"], products=["SEO Services"], relevance_context=ctx)
    kws = {r["keyword"] for r in cleaned}
    assert "seo services" in kws
    assert "python programming" not in kws
    assert "crm" not in kws


def test_business_fit_is_zero_without_overlap():
    assert business_fit_score("salesforce crm", ["seo services"], ["SEO Services"]) == 0.0
    assert business_fit_score("seo audit", ["seo services"], ["SEO Services"]) >= 0.55


def test_rank_opportunities_drops_unsupported_when_services_exist():
    ctx = _ctx()
    ranked = rank_opportunities(
        [
            {"keyword": "seo services", "volume": 1000, "difficulty": 30, "match_class": "exact", "seed": "SEO Services"},
            {"keyword": "python programming", "volume": 8000, "difficulty": 10, "match_class": "broad"},
        ],
        seeds=["SEO Services"],
        products=["SEO Services"],
        relevance_context=ctx,
    )
    all_kws = {r["keyword"] for r in ranked["all_scored"]}
    assert "seo services" in all_kws
    assert "python programming" not in all_kws


def test_hygiene_only_when_no_business_evidence():
    ctx = build_relevance_context(services=[], cdd_keywords=[], page_terms=[], themes=[])
    keep, reason, _ = evaluate_keyword("anything goes here", ctx)
    assert keep and reason == "hygiene_only"


def test_merge_audits_and_slim_memory_keeps_compact_cleaning():
    a = {"input_count": 10, "kept_count": 7, "removed_count": 3, "removed_by_reason": {"competitor_brand": 3}, "sample": [{"keyword": "king kong", "reason": "competitor_brand"}]}
    b = {"input_count": 5, "kept_count": 4, "removed_count": 1, "removed_by_reason": {"no_business_evidence": 1}, "sample": [{"keyword": "python", "reason": "no_business_evidence"}]}
    merged = merge_cleaning_audits(a, b)
    assert merged["input_count"] == 15
    assert merged["removed_count"] == 4
    slim = slim_search_demand_memory(
        {
            "best_opportunities": [{"keyword": "seo services", "volume": 100}],
            "clusters": [{"name": "SEO", "primary_keyword": "seo", "keywords": []}],
            "keyword_cleaning": merged,
            "services": ["SEO Services"],
        }
    )
    assert slim["keyword_cleaning"]["removed_count"] == 4
    assert slim["services"] == ["SEO Services"]
    assert "python" not in str(slim.get("best_opportunities"))
