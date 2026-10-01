"""Phase 6 must not invent Fill-in topics to pad the priority queue."""

from __future__ import annotations

from app.services.content_strategy import build_calendar, build_priority_queue


def test_priority_queue_does_not_pad_fill_ins_from_best_buckets():
    best = [
        {"keyword": f"extra keyword {i}", "volume": 1000 - i, "opportunity_score": 90 - i, "difficulty": 20}
        for i in range(30)
    ]
    q = build_priority_queue(
        best=best,
        evergreen=[{"keyword": "evergreen pad", "volume": 500, "opportunity_score": 80}],
        trends=[{"keyword": "trend pad", "volume": 400, "opportunity_score": 70}],
        avoid=[],
        report_clusters=[],
        domain="clicktrends.com.au",
        topic_plan={
            "topic_ideas": [
                {
                    "title": "Meta Ads Guide",
                    "keyword": "meta ads",
                    "intent": "commercial",
                    "opportunity_score": 80,
                },
                {
                    "title": "Link Building Case Study",
                    "keyword": "link building agency",
                    "intent": "commercial",
                    "opportunity_score": 40,
                },
            ]
        },
    )
    assert len(q) == 2
    kws = {str(r.get("keyword") or "").lower() for r in q}
    assert kws == {"meta ads", "link building agency"}
    assert all(r.get("priority") != "Fill-in" for r in q)
    assert all(r.get("from_phase5_topic") for r in q)


def test_priority_queue_empty_without_phase5_topics_does_not_invent_from_buckets():
    q = build_priority_queue(
        best=[{"keyword": "seo agency", "volume": 1000, "opportunity_score": 90}],
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[{"primary_keyword": "content marketing", "best_score": 70}],
        domain="example.com",
        topic_plan={"topic_ideas": []},
    )
    assert q == []


def test_calendar_does_not_recycle_topics_to_fill_empty_weeks():
    queue = [
        {
            "title": "One Topic",
            "keyword": "one topic",
            "priority": "Big bet",
            "content_type": "service",
            "est_words": 1500,
            "suggested_url": "/one-topic",
        }
    ]
    months = build_calendar(queue, weeks=12)
    scheduled = [
        w
        for m in months
        for w in (m.get("weeks") or [])
        if w.get("keyword")
    ]
    assert len(scheduled) == 1
    assert scheduled[0]["keyword"] == "one topic"


def test_selected_services_lead_the_queue_and_calendar():
    q = build_priority_queue(
        best=[{"keyword": "extra keyword", "volume": 900, "opportunity_score": 90}],
        evergreen=[],
        trends=[],
        avoid=[],
        report_clusters=[],
        domain="example.com",
        topic_plan={
            "topic_ideas": [
                {"title": "Meta Ads Guide", "keyword": "meta ads", "intent": "commercial"}
            ]
        },
        selected_services=[
            {"id": "email marketing", "name": "Email Marketing", "url": "/email-marketing/", "subservices": []},
            {"id": "ppc", "name": "PPC", "subservices": []},
        ],
    )
    labels = [row.get("work_label") for row in q]
    assert labels[0] == "Optimize service page"
    assert labels.count("Optimize service page") == 2
    creates = [row for row in q if row.get("action") == "create"]
    kinds = {row.get("page_type") for row in creates}
    assert {"guide", "blog", "comparison", "faq", "listicle", "landing", "tool"} <= kinds
    assert labels.count("Create blog page") == 2
    assert "Create guide page" in labels
    assert "Create faq page" in labels
    assert "meta ads" in {str(row.get("keyword")).lower() for row in q}
    assert "extra keyword" not in {str(row.get("keyword")).lower() for row in q}
    months = build_calendar(q, weeks=12)
    scheduled = [w for m in months for w in (m.get("weeks") or []) if w.get("keyword")]
    assert scheduled[0]["work_label"] == "Optimize service page"
    scheduled_kinds = {w.get("work_label") for w in scheduled}
    assert "Create blog page" in scheduled_kinds
    assert "Create guide page" in scheduled_kinds
    assert "Create comparison page" in scheduled_kinds
    assert any(w.get("keyword") == "meta ads" for w in scheduled)
