from types import SimpleNamespace

from app.services.chat_qa import build_memory_brief, direct_answer, looks_like_question
from app.services.chat_revisions import looks_like_revision


def test_run_is_not_a_question():
    assert not looks_like_question("Run keyword research / search demand")
    assert not looks_like_question("Refresh competitor scan")


def test_questions_detected():
    assert looks_like_question("what keywords did we find?")
    assert looks_like_question("Who are the competitors")
    assert looks_like_question("list the priority topics")
    assert not looks_like_revision("what keywords did we find?")
    assert not looks_like_revision("who are the competitors?")


def test_direct_answer_keywords_from_memory():
    brief = {
        "search_demand": {
            "best_opportunities": [
                {"keyword": "local seo melbourne", "volume": 320, "difficulty": 28}
            ]
        },
        "statuses": {"search_demand": "complete"},
    }
    ans = direct_answer("what keywords did we find?", brief)
    assert ans and "local seo melbourne" in ans
    assert "320" in ans


def test_direct_answer_missing_pack():
    ans = direct_answer("who are the competitors?", {"statuses": {}})
    assert ans and "Phase 4" in ans


def test_direct_answer_architecture_urls():
    brief = {
        "site_architecture": {
            "target_url_tree": [
                {"url": "/seo/", "title": "SEO services", "keyword": "seo melbourne"}
            ],
            "current_state": {"urls_crawled": 42},
        }
    }
    ans = direct_answer("what's in the site architecture?", brief)
    assert ans and "/seo/" in ans
    assert "42" in ans


def test_build_memory_brief_slims_demand():
    client = SimpleNamespace(
        display_name="Click Trends",
        primary_url="https://clicktrends.com.au/",
        industry="Digital Marketing Agency",
    )
    profile = SimpleNamespace(
        discovery_status="complete",
        tracking_status="complete",
        website_status="complete",
        competitor_status="complete",
        search_demand_status="complete",
        seo_strategy_status="not_started",
        site_architecture_status="not_started",
        technical_seo_status="not_started",
        content_audit_status="not_started",
        content_planning_status="not_started",
        content_production_status="not_started",
        on_page_seo_status="not_started",
        publishing_status="not_started",
        commercial_scope={"geographic_focus": "Melbourne"},
        marketing_context={},
        competitive_landscape_summary={"competitors": [{"name": "Rival", "url": "https://rival.com"}]},
        search_demand_summary={
            "best_opportunities": [{"keyword": "seo agency melbourne", "volume": 100, "difficulty": 40}],
            "note": "x" * 500,
        },
        seo_strategy_summary={},
        site_architecture_summary={},
        technical_seo_summary={},
        content_audit_summary={},
        content_planning_summary={},
        content_production_summary={},
        on_page_seo_summary={},
        publishing_summary={},
    )
    brief = build_memory_brief(client, profile)
    assert brief["client"]["name"] == "Click Trends"
    assert brief["search_demand"]["best_opportunities"][0]["keyword"] == "seo agency melbourne"
    assert brief["competitors"]["competitors"][0]["name"] == "Rival"
