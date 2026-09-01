from __future__ import annotations

from app.services.report_pdf import build_reports_pdf
from app.services.report_pdf_normalize import normalize_report_payload


def test_normalize_website_audit_tabs():
    payload = {
        "card_type": "website_audit",
        "tabs": {
            "technical": {
                "pages_found": 42,
                "indexable": 40,
                "broken_links": {"broken_count": 3},
                "status_samples": [
                    {"url": "https://acme.com/", "status": 200},
                    {"url": "https://acme.com/old", "status": 404},
                ],
            },
            "authority": {"referring_domains": 12, "status": "ok"},
        },
        "severities": {"technical": "warning", "authority": "info"},
        "tabs_run": ["technical", "authority"],
    }
    out = normalize_report_payload("website_audit", payload)
    assert out["pages_found"] == 42
    assert len(out["audit_tabs"]) == 2
    assert len(out["issues"]) == 2


def test_normalize_tracking_signoff():
    payload = {
        "card_type": "tracking_t6_signoff",
        "tracking_score": 88,
        "blockers": ["missing_gsc"],
        "rows": [{"element": "GSC", "check_result": "fail", "detail": {"reason": "no access"}}],
        "missing": ["Verify GSC property"],
    }
    out = normalize_report_payload("tracking_t6_signoff", payload)
    assert out["score"] == 88
    assert len(out["findings"]) == 1
    assert out["next_steps"] == ["Verify GSC property"]


def test_normalize_competitor_landscape():
    payload = {
        "card_type": "competitor_landscape",
        "executive_summary": "Three direct competitors identified.",
        "competitors_scored": 5,
        "client_baseline": {"maturity_score": 62},
        "tier_overview": [{"name": "Rival", "tier_name": "Tier 2", "score": 71}],
        "recommendations": {"benchmark": ["Improve content depth"], "differentiate": ["Local proof"]},
        "competitors": [{"name": "Rival", "url": "https://rival.com", "source": "ads"}],
    }
    out = normalize_report_payload("competitor_landscape", payload)
    assert "Three direct" in out["summary"]
    assert out["competitor_count"] == 5
    assert out["tier_overview"][0]["composite"] == 71


def test_pdf_website_audit_has_tab_rows():
    bundle = {
        "client": {"name": "Acme Corp", "primary_url": "https://acme.com"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "website_audit",
                "payload": {
                    "card_type": "website_audit",
                    "tabs": {
                        "technical": {
                            "pages_found": 18,
                            "indexable": 16,
                            "broken_links": {"broken_count": 2},
                            "status_samples": [{"url": "https://acme.com/x", "status": 404}],
                        }
                    },
                    "severities": {"technical": "warning"},
                    "tabs_run": ["technical"],
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 2500


def test_pdf_competitor_landscape_has_content():
    bundle = {
        "client": {"name": "Acme Corp"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "competitor_landscape",
                "payload": {
                    "card_type": "competitor_landscape",
                    "executive_summary": "Competitive set locked.",
                    "competitors_scored": 4,
                    "client_baseline": {"maturity_score": 55},
                    "tier_overview": [
                        {
                            "rank": 1,
                            "name": "Rival Co",
                            "tier_name": "Tier 2",
                            "composite": 68,
                            "future_threat": 72,
                        }
                    ],
                    "competitors": [{"name": "Rival Co", "url": "https://rival.com", "source": "ads"}],
                    "recommendations": {
                        "benchmark": ["Publish comparison pages"],
                        "differentiate": ["Highlight local case studies"],
                    },
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 3000


def test_normalize_content_calendar_nested_months():
    payload = {
        "card_type": "content_strategy_report",
        "executive_summary": "Strategy ready.",
        "content_calendar": [
            {
                "month": 1,
                "label": "Foundation",
                "weeks": [
                    {
                        "week": 1,
                        "title": "Best CRM for startups",
                        "keyword": "best crm",
                        "content_type": "pillar",
                        "priority": "Quick win",
                    },
                    {
                        "week": 2,
                        "title": "CRM pricing guide",
                        "keyword": "crm pricing",
                        "content_type": "guide",
                        "priority": "Big bet",
                    },
                ],
            },
            {
                "month": 2,
                "label": "Expansion",
                "weeks": [
                    {
                        "week": 5,
                        "title": "CRM integrations",
                        "keyword": "crm integrations",
                        "content_type": "cluster",
                        "priority": "Fill-in",
                    }
                ],
            },
        ],
        "handoffs": [{"concern": "URL tree", "route_to": "site-architecture"}],
    }
    out = normalize_report_payload("content_strategy_report", payload)
    assert len(out["content_calendar"]) == 3
    assert out["content_calendar"][0]["title"] == "Best CRM for startups"
    assert out["content_calendar"][0]["month"] == "Foundation"
    assert out["handoffs"][0]["item"] == "URL tree"
    assert out["handoffs"][0]["receiving"] == "site-architecture"


def test_pdf_content_strategy_calendar_rows_populated():
    bundle = {
        "client": {"name": "Reload Media"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "content_strategy_report",
                "payload": {
                    "card_type": "content_strategy_report",
                    "executive_summary": "Content strategy for Reload Media.",
                    "priority_queue": [
                        {
                            "title": "Share of search",
                            "keyword": "share of search",
                            "priority": "Quick win",
                            "volume": 1200,
                            "difficulty": 34,
                            "suggested_url": "https://www.reloadmedia.com.au/blog/share-of-search",
                        }
                    ],
                    "content_calendar": [
                        {
                            "month": 1,
                            "label": "Foundation",
                            "weeks": [
                                {
                                    "week": 1,
                                    "title": "Share of search",
                                    "keyword": "share of search",
                                    "content_type": "pillar",
                                    "priority": "Quick win",
                                }
                            ],
                        }
                    ],
                    "handoffs": [{"concern": "URL tree", "route_to": "site-architecture"}],
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"
    # Ensure we are not generating a tiny empty shell
    assert len(pdf) > 3000

    bundle = {
        "client": {"name": "Acme Corp"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "search_demand_report",
                "payload": {
                    "card_type": "search_demand_report",
                    "note": "Keyword research complete.",
                    "clusters": [
                        {
                            "name": "CRM software",
                            "primary_keyword": "crm software",
                            "total_volume": 12000,
                            "keyword_count": 8,
                            "intent": "commercial",
                        }
                    ],
                    "best_opportunities": [
                        {
                            "keyword": "best crm",
                            "volume": 2400,
                            "difficulty": 34,
                            "opportunity_score": 78,
                            "intent": "commercial",
                        }
                    ],
                    "cluster_report": {"clusters_created": 3},
                    "keyword_count": 120,
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 2500
