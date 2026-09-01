"""Phase 11/12 guardrails — trademark block, skill wiring, publish authority."""

from __future__ import annotations

import json

import pytest

from app.services.on_page_seo import (
    apply_trademark_block,
    build_internal_linking_plan,
    competitor_terms,
    run_on_page_seo_plan,
    selected_on_page_rows,
)
from app.services.role_skills import ROLE_PHASE_PERMISSIONS
from app.agents.prompts import load_skill

COMPETITIVE = {
    "competitor_names": ["Rival A"],
    "competitor_domains": ["rival-a.com"],
    "competitors": [{"name": "Beta Co", "domain": "https://www.beta-co.com/"}],
}


def test_competitor_terms_covers_names_domains_and_labels():
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    assert "rival a" in terms
    assert "beta co" in terms
    assert "rival-a.com" in terms
    assert "beta-co" in terms  # domain label, so "beta-co" in copy is caught too


def test_competitor_terms_never_blocks_the_clients_own_brand():
    """Stripping the client's own name out of their titles would be worse than the bug."""
    terms = competitor_terms(
        {"competitor_names": ["Acme Retail Co"]}, client_name="Acme Retail Co"
    )
    assert "acme retail co" not in terms


def test_short_terms_are_not_stripped():
    """Two-letter brands would shred unrelated words; require 3+ chars."""
    assert "ai" not in competitor_terms({"competitor_names": ["AI"]}, client_name="X")


def _package():
    pages = [
        {
            "url": "/seo-services",
            "keyword": "seo services",
            "title": {"before": "Rival A is great", "after": "Beta Co SEO Services", "length_after": 0},
            "meta_description": {"before": "", "after": "Better than rival-a.com.", "length_after": 0},
            "headings": {"h1": "Beta Co Alternative", "h2s": ["Why Rival A wins", "Pricing"]},
            "schema_json_ld": {"name": "Beta Co Services", "headline": "Clean headline"},
        }
    ]
    links = [{"from": "/a", "to": "/services/seo-audit", "anchor": "see Rival A"}]
    return pages, links


def test_no_competitor_term_survives_in_shipped_fields():
    pages, links = _package()
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    apply_trademark_block(pages, links, terms=terms, client_name="Acme Retail Co")
    page = pages[0]
    shipped = json.dumps(
        {
            "title": page["title"]["after"],
            "meta": page["meta_description"]["after"],
            "headings": page["headings"],
            "schema": page["schema_json_ld"],
            "anchor": links[0]["anchor"],
        }
    ).lower()
    for term in ("beta co", "rival a", "rival-a.com", "beta-co"):
        assert term not in shipped


def test_generated_fields_are_regenerated_not_left_broken():
    """Excising a brand mid-sentence used to leave copy like "Better than  for SEO."."""
    pages, links = _package()
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    apply_trademark_block(pages, links, terms=terms, client_name="Acme Retail Co")
    page = pages[0]
    assert page["title"]["after"] == "Seo Services | Acme Retail Co"
    assert page["title"]["length_after"] == len(page["title"]["after"])
    assert page["headings"]["h1"] == "Seo Services"
    assert links[0]["anchor"] == "Seo Audit"  # derived from the link target, not blanked


def test_observed_before_value_is_redacted_not_regenerated():
    """`before` records what the live page says; keep it accurate, minus the brand."""
    pages, links = _package()
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    apply_trademark_block(pages, links, terms=terms, client_name="Acme Retail Co")
    assert pages[0]["title"]["before"] == "[competitor] is great"


def test_offending_schema_property_is_dropped_not_substituted():
    """Schema is machine-read: an invented replacement would be a structured-data lie."""
    pages, links = _package()
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    apply_trademark_block(pages, links, terms=terms, client_name="Acme Retail Co")
    schema = pages[0]["schema_json_ld"]
    assert "name" not in schema
    assert schema["headline"] == "Clean headline"


def test_offending_h2_is_dropped_clean_ones_kept():
    pages, links = _package()
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    apply_trademark_block(pages, links, terms=terms, client_name="Acme Retail Co")
    assert pages[0]["headings"]["h2s"] == ["Pricing"]


def test_block_reports_every_hit_for_review():
    pages, links = _package()
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    blocked = apply_trademark_block(pages, links, terms=terms, client_name="Acme Retail Co")
    fields = {b["field"] for b in blocked}
    assert {"title.after", "headings.h1", "internal_links.anchor"} <= fields
    assert all(b.get("term") for b in blocked)


def test_clean_package_is_untouched():
    pages = [
        {
            "url": "/x",
            "keyword": "seo services",
            "title": {"before": "Old", "after": "SEO Services | Acme", "length_after": 19},
            "headings": {"h1": "SEO Services", "h2s": ["Pricing"]},
        }
    ]
    terms = competitor_terms(COMPETITIVE, client_name="Acme Retail Co")
    blocked = apply_trademark_block(pages, [], terms=terms, client_name="Acme Retail Co")
    assert blocked == []
    assert pages[0]["title"]["after"] == "SEO Services | Acme"


# --- Skill wiring ------------------------------------------------------------------

def test_generate_schema_skill_resolves():
    """Declared in role_skills but had no directory — load_skill returned "" silently."""
    assert load_skill("generate_schema").strip()


def test_phase11_skills_carry_pipeline_framing():
    """These skills ship written for interactive use; in-pipeline they must not ask the
    operator for inputs that already exist in locked shared memory."""
    on_page = load_skill("on_page_seo")
    assert "do not ask the user" in on_page.lower()
    assert "trademark keyword block" in on_page.lower()
    assert load_skill("internal_linking").strip()
    assert load_skill("schema_markup").strip()


# --- Phase 12 publish authority (v1.9 step 13) --------------------------------------

def _publish_perms(role: str) -> tuple[bool, bool]:
    rows = {a: (t, ap) for a, t, ap in ROLE_PHASE_PERMISSIONS.get(role, [])}
    return rows.get("publishing", (False, False))


def test_publish_trigger_is_lead_only():
    for lead in ("seo_strategist", "on_page_seo_specialist", "head_of_department"):
        assert _publish_perms(lead)[0] is True, lead
    for other in (
        "technical_seo_specialist",
        "structured_data_specialist",
        "content_seo_specialist",
        "client_success_manager",
        "seo_qa_lead",
    ):
        assert _publish_perms(other)[0] is False, other


def test_qa_lead_triages_the_publish_queue_without_publish_rights():
    """v1.9: reviewers "can approve into the queue, but cannot trigger this step"."""
    trigger, approve = _publish_perms("seo_qa_lead")
    assert approve is True
    assert trigger is False


def test_selected_on_page_rows_uses_draft_not_full_brief_queue():
    rows = selected_on_page_rows(
        {
            "briefs": [
                {"keyword": "seo company", "url": "/seo", "funnel": "BoFu"},
                {"keyword": "web design", "url": "/web-design", "funnel": "BoFu"},
            ],
            "drafts": [{"keyword": "seo company", "url": "/seo", "title": "SEO Company"}],
            "selected_url": "/seo",
            "selected_keyword": "seo company",
        }
    )
    assert len(rows) == 1
    assert rows[0]["keyword"] == "seo company"
    assert "/web-design" not in str(rows[0].get("url") or "")


@pytest.mark.asyncio
async def test_on_page_plan_packages_selected_draft_only():
    out = await run_on_page_seo_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        content_production_status="pending_signoff",
        content_production={
            "briefs": [
                {"keyword": "seo company", "url": "/seo"},
                {"keyword": "web design", "url": "/web-design"},
            ],
            "drafts": [{"keyword": "seo company", "url": "/seo", "title": "SEO Company"}],
            "selected_keyword": "seo company",
            "selected_url": "/seo",
        },
        content_planning={
            "roadmap": [
                {"keyword": "seo company", "url": "/seo"},
                {"keyword": "web design", "url": "/web-design"},
            ]
        },
    )
    assert not out.get("blocked")
    assert len(out.get("pages") or []) == 1
    assert out["pages"][0]["keyword"] == "seo company"
    assert out.get("one_page_only") is True


@pytest.mark.asyncio
async def test_on_page_plan_blocks_without_selected_topic():
    out = await run_on_page_seo_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        content_production_status="in_progress",
        content_production={
            "briefs": [
                {"keyword": "seo company", "url": "/seo"},
                {"keyword": "web design", "url": "/web-design"},
            ]
        },
        content_planning={"roadmap": [{"url": "/seo"}, {"url": "/web-design"}]},
    )
    assert out.get("blocked") is True
    assert "selected" in str(out.get("reason") or "").lower()


@pytest.mark.asyncio
async def test_on_page_plan_includes_internal_linking_from_ia():
    out = await run_on_page_seo_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        content_production_status="pending_signoff",
        content_production={
            "briefs": [
                {
                    "keyword": "seo company",
                    "url": "/blog/seo-company",
                    "parent": "/blog",
                    "page_type": "article",
                    "internal_links": [{"to": "/services/seo", "anchor": "SEO services"}],
                }
            ],
            "drafts": [
                {"keyword": "seo company", "url": "/blog/seo-company", "title": "SEO Company"}
            ],
            "selected_keyword": "seo company",
            "selected_url": "/blog/seo-company",
        },
        content_planning={
            "pages": [
                {
                    "url": "/blog/seo-company",
                    "parent": "/blog",
                    "keyword": "seo company",
                    "action": "create",
                },
                {
                    "url": "/blog/local-seo",
                    "parent": "/blog",
                    "keyword": "local seo",
                    "action": "create",
                },
                {
                    "url": "/services/seo",
                    "parent": "/services",
                    "keyword": "seo services",
                    "action": "create",
                },
            ]
        },
        site_architecture={
            "target_url_tree": [
                {"path": "/blog", "parent": "/", "type": "hub", "depth": 1},
                {
                    "path": "/blog/seo-company",
                    "parent": "/blog",
                    "type": "article",
                    "depth": 2,
                    "keyword": "seo company",
                },
                {
                    "path": "/blog/local-seo",
                    "parent": "/blog",
                    "type": "article",
                    "depth": 2,
                    "keyword": "local seo",
                },
                {
                    "path": "/services/seo",
                    "parent": "/services",
                    "type": "service",
                    "depth": 2,
                    "keyword": "seo services",
                },
            ],
            "cluster_ownership": [{"cluster": "SEO", "owner_url": "/services/seo"}],
            "current_state": {"orphans": ["/old-orphan"]},
        },
    )
    assert not out.get("blocked")
    assert "internal_linking" in out
    links = out.get("internal_links") or []
    assert links
    assert any(ln.get("to") == "/blog" for ln in links)
    assert any(ln.get("reason") == "brief_target" for ln in links)
    assert "internal_linking" in (out.get("skills_used") or [])


def test_build_internal_linking_plan_hub_spoke():
    plan = build_internal_linking_plan(
        page_path="/blog/share-of-search",
        page_keyword="share of search",
        parent="/blog",
        site_architecture={
            "target_url_tree": [
                {"path": "/blog", "parent": "/", "type": "hub", "depth": 1},
                {
                    "path": "/blog/share-of-search",
                    "parent": "/blog",
                    "type": "article",
                    "depth": 2,
                },
                {
                    "path": "/blog/brand-search",
                    "parent": "/blog",
                    "type": "article",
                    "depth": 2,
                },
            ]
        },
        content_planning={},
        brief_links=[],
        page_type="article",
    )
    assert plan["link_count"] >= 2
    assert any(ln["to"] == "/blog" for ln in plan["links"])
    assert any(
        ln["from"] == "/blog" and ln["to"] == "/blog/share-of-search" for ln in plan["links"]
    )
