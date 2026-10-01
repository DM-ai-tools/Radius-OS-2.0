"""Organic Search policy gate: report findings, never rewrite, block finalization."""

from __future__ import annotations

import asyncio

from app.integrations.wordpress import WordPressConnection
from app.services.google_policy_gate import (
    SPAM_POLICIES_URL,
    attach_google_policy,
    evaluate_content_policy,
    finalization_allowed,
)
from app.services.publishing import MODE_DRAFT, run_publishing_plan

CLEAN = {
    "item_id": "https://example.com/drain-cleaning",
    "url": "https://example.com/drain-cleaning",
    "keyword": "drain cleaning cost",
    "title": "What drain cleaning costs",
    "meta_description": "A plain guide to drain cleaning cost for a single home.",
    "markdown": (
        "Drain cleaning cost depends on how blocked the line is and whether the "
        "crew needs a camera. This page explains the usual range for one household "
        "and what changes the quote.\n\n"
        "## What the visit includes\n\n"
        "A technician clears the line and tells you what caused the backup. "
        "Ask for the price before they start the machine.\n\n"
        "Call the office on the [contact page](/contact) when you want a quote."
    ),
    "cta": "/contact",
}


def _statuses(report: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for finding in report["findings"]:
        area = finding["policy_area"]
        rank = {"PASS": 0, "FLAG": 1, "NEEDS_REVIEW": 2, "FAIL": 3}
        if area not in out or rank[finding["status"]] > rank[out[area]]:
            out[area] = finding["status"]
    return out


def test_clean_page_passes_and_ignores_ads_character_caps():
    report = evaluate_content_policy(CLEAN)
    assert report["overall_status"] == "PASS"
    assert report["allows_finalization"] is True
    assert report["target_surface"] == "organic_search"
    limits = next(f for f in report["findings"] if f["policy_area"] == "keyword_limits")
    assert limits["status"] == "PASS"
    assert limits["character_count"]["title"] == len(CLEAN["title"])
    assert "not applied" in limits["reason"].lower() or "were not applied" in limits["reason"]
    assert all(f["policy_reference"] for f in report["findings"])


def test_exact_phrase_repeated_inside_a_short_block_is_flagged():
    sentence = "drain cleaning cost " * 3 + "is the only line on the page."
    item = {**CLEAN, "markdown": sentence}
    report = evaluate_content_policy(item)
    assert _statuses(report)["keyword_repetition"] == "FLAG"
    hit = next(f for f in report["findings"] if f["policy_area"] == "keyword_repetition" and f["status"] == "FLAG")
    assert hit["occurrence_count"] >= 3
    assert hit["judgment"] == "excessive_repetition"


def test_city_list_is_flagged_and_blocks_automatic_finalization():
    item = {
        **CLEAN,
        "markdown": CLEAN["markdown"]
        + "\n\nWe serve Austin, Dallas, Houston, San Antonio, Fort Worth.",
    }
    review = evaluate_content_policy(item, human_in_loop=True)
    assert _statuses(review)["keyword_stuffing"] == "FLAG"
    assert review["overall_status"] == "NEEDS_REVIEW"
    assert review["allows_finalization"] is False
    stuffing = next(f for f in review["findings"] if f["status"] == "FLAG" and f["policy_area"] == "keyword_stuffing")
    assert stuffing["judgment"] == "stuffed"
    assert stuffing["word_count"] > 0
    assert SPAM_POLICIES_URL in stuffing["policy_reference"]
    assert finalization_allowed(review, human_approved=True) is True

    blocked = evaluate_content_policy(item, human_in_loop=False)
    assert blocked["overall_status"] == "BLOCKED"
    assert finalization_allowed(blocked, human_approved=True) is False


def test_hidden_text_fails_and_approval_cannot_override_it():
    item = {
        **CLEAN,
        "markdown": CLEAN["markdown"] + '\n\n<span style="display: none">cheap loans cheap loans</span>',
    }
    report = evaluate_content_policy(item)
    assert _statuses(report)["prohibited_practices"] == "FAIL"
    assert report["overall_status"] == "BLOCKED"
    assert finalization_allowed(report, human_approved=True) is False
    failed = next(f for f in report["findings"] if f["status"] == "FAIL")
    assert "hidden-text" in failed["policy_reference"]


def test_counterfeit_offer_is_a_fail():
    item = {**CLEAN, "markdown": "Buy a counterfeit watch that looks real."}
    report = evaluate_content_policy(item)
    assert report["overall_status"] == "BLOCKED"
    assert any(f["policy_area"] == "prohibited_content" and f["status"] == "FAIL" for f in report["findings"])


def test_keyword_missing_from_the_page_is_a_flag():
    item = {**CLEAN, "keyword": "emergency plumber", "title": "What drain cleaning costs"}
    report = evaluate_content_policy(item)
    match = next(f for f in report["findings"] if f["policy_area"] == "keyword_content_match")
    assert match["status"] == "FLAG"
    assert match["keyword"] == "emergency plumber"
    assert report["overall_status"] == "NEEDS_REVIEW"


def test_house_rule_is_labeled_internal_and_is_not_a_google_cap():
    words = " ".join(["drain"] * 8 + ["cleaning cost guide for one home"] * 4)
    item = {**CLEAN, "markdown": words, "keyword": "drain cleaning"}
    report = evaluate_content_policy(
        item,
        house_keyword_rule="no single keyword >3× per 100 words",
    )
    house = next(f for f in report["findings"] if f["policy_area"] == "house_keyword_rule")
    assert house["rule_source"] == "internal_house_rule"
    assert house["policy_reference"] == "internal"
    assert "not a Google requirement" in house["reason"]
    assert house["status"] == "FLAG"


def test_other_google_surfaces_are_not_judged_with_search_rules():
    long_title = {**CLEAN, "title": "X" * 40}
    report = evaluate_content_policy(long_title, target_surface="google_ads")
    assert report["overall_status"] == "NEEDS_REVIEW"
    assert report["allows_finalization"] is False
    assert report["findings"][0]["policy_area"] == "policy_set"
    assert "were not checked" in report["findings"][0]["reason"] or "was not checked" in report["findings"][0]["reason"]


def test_unreachable_landing_page_is_needs_review_not_pass():
    item = {**CLEAN, "landing_reachable": False}
    report = evaluate_content_policy(item)
    editorial = [
        f for f in report["findings"]
        if f["policy_area"] == "editorial_technical" and f["status"] == "NEEDS_REVIEW"
    ]
    assert editorial
    assert report["overall_status"] == "NEEDS_REVIEW"


def test_attach_does_not_rewrite_the_draft():
    draft = {
        "url": CLEAN["url"],
        "keyword": CLEAN["keyword"],
        "title": CLEAN["title"],
        "markdown": CLEAN["markdown"] + " Buy a counterfeit watch.",
        "cta": "/contact",
    }
    original = draft["markdown"]
    attach_google_policy(draft, config={"google_policy_surface": "organic_search"})
    assert draft["markdown"] == original
    assert draft["google_policy"]["overall_status"] == "BLOCKED"


def test_publish_write_is_refused_when_the_gate_fails(monkeypatch):
    called = {"upsert": 0}

    async def _verify(_conn):
        return {"ok": True, "user": "editor", "capabilities_publish": True, "site": _conn.base_url}

    async def _upsert(*_args, **_kwargs):
        called["upsert"] += 1
        return {"ok": True, "status": "draft", "post_id": 1}

    async def _caps(_conn):
        return {"elementor_active": False, "seo_plugin": None}

    from app.integrations import wordpress

    monkeypatch.setattr(wordpress, "verify_connection", _verify)
    monkeypatch.setattr(wordpress, "upsert_object", _upsert)
    monkeypatch.setattr(wordpress, "detect_site_capabilities", _caps)

    page = {
        "url": "https://example.com/blog/local-seo-pricing",
        "keyword": "local seo pricing",
        "title": {"after": "Local SEO Pricing | Acme"},
        "meta_description": {"after": "What local SEO costs."},
        "headings": {"h1": "Local SEO Pricing", "h2s": []},
    }
    production = {
        "drafts": [
            {
                "url": page["url"],
                "title": "Local SEO Pricing | Acme",
                "keyword": "local seo pricing",
                "markdown": "Buy a counterfeit passport with this page.",
            }
        ]
    }
    out = asyncio.run(
        run_publishing_plan(
            client_name="Acme",
            primary_url="https://example.com",
            on_page_seo_status="complete",
            on_page_seo={"pages": [page]},
            content_production=production,
            mode=MODE_DRAFT,
            wordpress_connection=WordPressConnection(
                base_url="https://client-site.example",
                username="editor",
                app_password="app-pw",
            ),
        )
    )
    assert called["upsert"] == 0
    assert out["publish_queue"][0]["status"] == "policy_blocked"
    assert out["publish_queue"][0]["google_policy"]["overall_status"] == "BLOCKED"
    assert production["drafts"][0]["markdown"] == "Buy a counterfeit passport with this page."
