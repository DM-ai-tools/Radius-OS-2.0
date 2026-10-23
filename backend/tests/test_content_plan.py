"""One content plan: each piece of work once, with the fields the old tables split apart."""

from app.services.content_queue import build_content_plan, schedule_content_plan
from app.services.report_pdf_document import _content_strategy_sections
from app.services.report_pdf_normalize import normalize_report_payload


def test_strategy_audit_and_architecture_collapse_to_one_row_per_piece():
    plan = build_content_plan(
        content_strategy={
            "priority_queue": [
                {
                    "title": "SEO guide",
                    "keyword": "seo guide",
                    "action": "create",
                    "work_label": "Create page",
                    "priority": "Quick win",
                    "volume": 900,
                    "difficulty": 22,
                    "intent": "informational",
                    "funnel": "tofu",
                    "est_words": 1500,
                    "content_type": "cluster article",
                    "opportunity_score": 70,
                    "suggested_url": "https://example.com/blog/seo-guide",
                    "service": "SEO",
                    "angle": "How ranking works",
                    "secondary_keywords": ["seo basics"],
                },
                {
                    "title": "Avoid this",
                    "keyword": "free seo",
                    "priority": "Avoid",
                    "rationale": "No commercial intent",
                },
            ],
            "pillars": [{"name": "SEO"}],
        },
        content_audit={
            "inventory": [
                {
                    "path": "/guides/seo",
                    "url": "https://example.com/guides/seo",
                    "keyword": "seo guide",
                    "disposition": "refresh",
                    "title": "Old SEO guide",
                    "effort": "Medium",
                    "reason": "Traffic decayed",
                }
            ],
            "summary_counts": {"refresh": 1},
        },
        site_architecture={
            "redirect_map": [{"from": "/old-seo", "to": "/seo/", "keyword": "old seo"}],
        },
    )
    rows = plan["content_plan"]
    titles = [row["title"] for row in rows]
    assert titles.count("SEO guide") == 1
    assert "Avoid this" not in titles
    guide = next(row for row in rows if row["keyword"] == "seo guide")
    assert guide["action"] == "optimize"
    assert guide["disposition"] == "refresh"
    assert guide["source"] == "content-strategy"
    assert guide["owner"] == "content-audit"
    assert guide["volume"] == 900
    assert guide["difficulty"] == 22
    assert guide["intent"] == "informational"
    assert guide["funnel"] == "tofu"
    assert guide["est_words"] == 1500
    assert guide["content_type"] == "cluster article"
    assert guide["opportunity_score"] == 70
    assert guide["service"] == "SEO"
    assert guide["angle"] == "How ranking works"
    assert guide["secondary_keywords"] == ["seo basics"]
    assert guide["existing_url"] == "https://example.com/guides/seo"
    assert guide["reason"] == "Traffic decayed"
    assert guide["id"].startswith("work-")
    redirect = next(row for row in rows if row["action"] == "redirect")
    assert redirect["source"] == "site-architecture"
    assert redirect["owner"] == "site-architecture"
    assert redirect["existing_url"] == "/old-seo"
    assert redirect["suggested_url"] == "/seo/"
    assert len({row["id"] for row in rows}) == len(rows)
    assert plan["exclusions"][0]["keyword"] == "free seo"
    assert plan["exclusions"][0]["reason"] == "No commercial intent"
    assert "combined_priority_queue" not in plan
    assert "priority_queue_strategy_only" not in plan

    schedule_content_plan(rows)
    assert guide["week"] == 1
    assert guide["month_theme"] == "Foundation"
    rendered = normalize_report_payload(
        "content_strategy_report",
        {
            "card_type": "content_strategy_report",
            "executive_summary": "Plan",
            "content_plan": rows,
            "exclusions": plan["exclusions"],
            "priority_queue": [dict(guide), dict(guide)],
            "combined_priority_queue": [{"title": "SEO guide", "keyword": "seo guide", "action": "create"}],
        },
    )
    rendered_titles = [row.get("title") for row in rendered["content_plan"]]
    assert rendered_titles.count("SEO guide") == 1
    assert "priority_queue" not in rendered
    assert "combined_priority_queue" not in rendered
    assert all("title" not in month for month in rendered["content_calendar"])
    sections = [spec.title for spec in _content_strategy_sections()]
    assert "Priority Content Queue" not in sections
    assert "Combined Priority Queue" not in sections
    assert "12-Week Content Calendar" not in sections
    assert sections.count("Content Plan — Work") == 1
    assert sections.count("Content Plan — Targeting") == 1


def test_architecture_redirect_replaces_audit_consolidate_on_the_same_url():
    plan = build_content_plan(
        content_strategy={"priority_queue": [], "pillars": [{"name": "SEO"}]},
        content_audit={
            "inventory": [
                {
                    "path": "/old-seo",
                    "keyword": "old seo",
                    "disposition": "consolidate",
                    "title": "Old SEO",
                }
            ],
            "summary_counts": {"consolidate": 1},
        },
        site_architecture={
            "redirect_map": [{"from": "/old-seo", "to": "/seo/"}],
        },
    )
    rows = [row for row in plan["content_plan"] if row.get("keyword") == "old seo" or "/old-seo" in str(row.get("existing_url"))]
    assert len(rows) == 1
    assert rows[0]["action"] == "redirect"
    assert rows[0]["owner"] == "site-architecture"
