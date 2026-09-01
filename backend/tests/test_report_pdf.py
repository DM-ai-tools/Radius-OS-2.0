from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from app.services.report_pdf import build_reports_pdf


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_build_reports_pdf_empty_bundle():
    bundle = {
        "client": {"name": "Acme Corp", "primary_url": "https://acme.com"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [],
        "phase_statuses": {"overall_readiness_score": 42},
    }
    pdf = build_reports_pdf(bundle)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500


def test_build_reports_pdf_with_wide_list():
    long_text = "word " * 400
    items = [
        {
            "id": f"row-{i}",
            "title": f"Topic {i}",
            "url": f"https://example.com/{i}",
            "notes": long_text,
            "meta": {"nested": long_text},
        }
        for i in range(30)
    ]
    bundle = {
        "client": {"name": "Acme Corp"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "title": "Content Audit",
                "payload": {"pages": items, "priority_queue": items[:10]},
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle)
    assert pdf[:4] == b"%PDF"


def test_build_reports_pdf_with_report():
    bundle = {
        "client": {"name": "Acme Corp"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "title": "Content Strategy: Acme Corp",
                "card_type": "content_strategy_report",
                "created_at": "2026-08-12T11:00:00+00:00",
                "source": "chat",
                "payload": {
                    "card_type": "content_strategy_report",
                    "title": "Content Strategy: Acme Corp",
                    "summary": "Three pillars identified.",
                    "priority_queue": [
                        {"title": "Best CRM for startups", "priority": "Quick win", "volume": 1200},
                    ],
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_build_site_architecture_blueprint_pdf():
    bundle = {
        "client": {"name": "Acme Corp", "primary_url": "https://acme.com"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "title": "Site Architecture Blueprint: Acme Corp",
                "card_type": "site_architecture_blueprint",
                "payload": {
                    "card_type": "site_architecture_blueprint",
                    "title": "Site Architecture Blueprint: Acme Corp",
                    "client": "Acme Corp",
                    "primary_url": "https://acme.com",
                    "executive_summary": "Blueprint with hub-and-spoke IA.",
                    "current_state": {
                        "urls_crawled": 120,
                        "status_200": 115,
                        "max_click_depth": 4,
                        "within_3_clicks": 98,
                        "depth_4_plus": 8,
                        "orphans": 2,
                        "phantom_dirs": 1,
                        "issues": [
                            {"issue": "Deep pages", "count": 8, "severity": "medium", "note": "Fix nav"},
                        ],
                    },
                    "page_type_model": [
                        {
                            "type": "Hub",
                            "url_pattern": "/services/",
                            "parent": "/",
                            "breadcrumb": "Home > Services",
                            "indexable": True,
                            "count": 3,
                        }
                    ],
                    "target_url_tree": [
                        {
                            "type": "hub",
                            "path": "/services/",
                            "depth": 1,
                            "primary_keyword": "services",
                            "cluster": "services",
                            "parent": "/",
                        }
                    ],
                    "cluster_ownership": [
                        {
                            "cluster": "services",
                            "canonical_owner_url": "/services/",
                            "disposition": "keep",
                        }
                    ],
                    "handoffs": [{"item": "Redirect map", "receiving": "Technical SEO"}],
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 2000


def test_render_document_report_content_strategy():
    from app.services.report_pdf import build_reports_pdf

    bundle = {
        "client": {"name": "Acme Corp"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "title": "Content Strategy: Acme Corp",
                "card_type": "content_strategy_report",
                "payload": {
                    "card_type": "content_strategy_report",
                    "summary": "Three pillars and a prioritized queue.",
                    "priority_queue": [
                        {"title": "Best CRM", "priority": "Quick win", "volume": 1200},
                    ],
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"


def test_render_document_report_tracking():
    from app.services.report_pdf import build_reports_pdf

    bundle = {
        "client": {"name": "Acme Corp"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "tracking_t6_signoff",
                "payload": {
                    "card_type": "tracking_t6_signoff",
                    "summary": "Tracking sign-off complete.",
                    "status": "ready",
                    "score": 92,
                },
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"

    bundle = {
        "client": {"name": "Acme Corp", "primary_url": "https://acme.com"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "title": "Website Situation",
                "card_type": "website_audit",
                "payload": {"summary": "Healthy site", "pages_found": 42},
            }
        ],
        "phase_statuses": {},
    }
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 400


# --- Downloaded PDF must reflect Phase 8/9/10 content, not just the raw JSON ------
# The generic auto-section renderer doesn't know about new fields until a blueprint
# names them — these pin the actual rendered text, not just "PDF bytes exist".

def test_content_audit_pdf_shows_themed_inventory():
    bundle = {
        "client": {"name": "Acme"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "content_audit_report",
                "payload": {
                    "card_type": "content_audit_report",
                    "note": "Locked audit",
                    "inventory": [
                        {"path": "/what-is-seo", "title": "What Is SEO", "theme": "SEO Fundamentals", "disposition": "keep"},
                    ],
                    "themes": [
                        {
                            "theme": "SEO Fundamentals",
                            "url_count": 1,
                            "dispositions": {"keep": 1},
                            "urls": [{"path": "/what-is-seo", "title": "What Is SEO", "disposition": "keep"}],
                        },
                    ],
                },
            }
        ],
        "phase_statuses": {},
    }
    text = _pdf_text(build_reports_pdf(bundle, single=True))
    assert "Themed Inventory" in text
    assert "SEO Fundamentals" in text
    assert "keep: 1" in text


def test_content_planning_pdf_shows_funnel_and_content_signals_no_duplicate_roadmap():
    bundle = {
        "client": {"name": "Acme"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "content_planning_report",
                "payload": {
                    "card_type": "content_planning_report",
                    "note": "Locked roadmap",
                    "pages": [
                        {
                            "priority_rank": 1,
                            "url_n": "/seo-pricing",
                            "primary_keyword": "seo pricing",
                            "action": "create",
                            "priority_tier": "quick_win",
                            "funnel": "BOFU",
                            "angle": "comparison",
                            "business_fit": {"score": 90, "reason": "core_service"},
                            "competitor_domains": ["rival-a.com"],
                        }
                    ],
                    "roadmap": [
                        {
                            "priority_rank": 1,
                            "url_n": "/seo-pricing",
                            "primary_keyword": "seo pricing",
                            "action": "create",
                            "priority_tier": "quick_win",
                            "funnel": "BOFU",
                        }
                    ],
                    "excluded": [],
                },
            }
        ],
        "phase_statuses": {},
    }
    text = _pdf_text(build_reports_pdf(bundle, single=True))
    assert "Funnel" in text
    assert "BOFU" in text
    assert "Content Signals" in text
    assert "comparison" in text
    assert "rival-a.com" in text
    # "pages" and "roadmap" are the same list from content_planning.py — there must
    # be no separate auto-rendered "Roadmap" section duplicating "Merged pages".
    assert "Merged pages" in text
    assert "Roadmap" not in text


def test_content_production_pdf_shows_briefs_and_funnel_balance_once():
    bundle = {
        "client": {"name": "Acme"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "content_production_report",
                "payload": {
                    "card_type": "content_production_report",
                    "note": "Briefed queue",
                    "briefs": [
                        {
                            "keyword": "seo pricing",
                            "url": "/seo-pricing",
                            "funnel": "BOFU",
                            "angle": "comparison",
                            "status": "ready",
                        }
                    ],
                    "drafts": [],
                    "funnel_balance": {"TOFU": 0, "MOFU": 0, "BOFU": 1},
                    "funnel_balance_warnings": ["No TOFU topics in this plan — funnel is unbalanced."],
                },
            }
        ],
        "phase_statuses": {},
    }
    text = _pdf_text(build_reports_pdf(bundle, single=True))
    assert "Briefed Topics" in text
    assert "seo pricing" in text
    assert "comparison" in text
    assert "Funnel Balance Warnings" in text
    assert "3. Funnel Balance\n" in text
    # The metrics section must render exactly once, not once explicitly and once
    # more via the generic auto-section fallback (the bug this section pins) —
    # a duplicate would push a numbered "5." section onto the end.
    assert "5." not in text


def _url_map_fixture_payload() -> dict:
    return {
        "card_type": "site_architecture_blueprint",
        "title": "Site Architecture Blueprint: Acme Corp",
        "client": "Acme Corp",
        "primary_url": "https://acme.com",
        "executive_summary": "Blueprint with URL mapping.",
        "current_state": {"urls_crawled": 50, "status_200": 48},
        "url_map_report": {
            "final_url_map": [
                {
                    "level": 2,
                    "l1_category": "Services",
                    "l2_subcategory": "Local Seo Melbourne",
                    "l3_subsubcategory": None,
                    "l4_attribution": None,
                    "current_url": "/services/local-seo-melbourne",
                    "proposed_url": None,
                    "status": "Optimize Existing",
                    "primary_keyword": "local seo melbourne",
                    "search_volume": 1200,
                    "cpc": 4.5,
                    "secondary_keywords_sheet": [
                        "seo agency melbourne",
                        "melbourne seo services",
                    ],
                    "combined_cluster_volume": 2000,
                    "page_type": "service",
                    "priority": "High",
                    "notes": "SERP validated (service)",
                    "est_products": 2,
                    "in_scope": "Yes",
                }
            ],
        },
    }


def test_site_architecture_pdf_shows_url_mapping_columns():
    bundle = {
        "client": {"name": "Acme Corp"},
        "exported_at": "2026-08-12T12:00:00+00:00",
        "reports": [
            {
                "card_type": "site_architecture_blueprint",
                "payload": _url_map_fixture_payload(),
            }
        ],
        "phase_statuses": {},
    }
    text = _pdf_text(build_reports_pdf(bundle, single=True))
    text_norm = " ".join(text.split())
    assert "URL Mapping & Taxonomy Plan" in text
    assert "Taxonomy & Status" in text
    assert "Keywords & Opportunity" in text
    assert "local seo melbourne" in text_norm
    assert "seo agency melbourne" in text_norm
    assert "2,000" in text_norm
    assert "$4.50" in text_norm
    assert " Yes " in f" {text_norm} " or text_norm.endswith(" Yes")
    assert "Secondary Keywords" in text_norm
    assert "Optimize Existing" in text_norm
    assert "1,200" in text_norm

