"""Shared Google helpful-content evidence base is loadable and wired into Phase 10."""

from app.agents.prompts import load_shared_reference
from app.services.content_brief import _people_first_gates, _who_how_why


def test_shared_evidence_base_loads():
    text = load_shared_reference("google-helpful-content.md")
    assert "Creating Helpful, Reliable, People-First Content" in text
    assert "scaled content abuse" in text.lower() or "Scaled content abuse" in text
    assert "Who, How, Why" in text
    assert "(No, we don't.)" in text
    assert "topical authority" in text.lower()


def test_who_how_why_people_first_gates():
    whw = _who_how_why(
        author="Jamie Owner",
        standing="10 years SEO",
        ymyl=False,
        audience="Melbourne SMBs",
        differentiation="Hands-on technical audits with named crawl findings.",
        client_name="Click Trends",
    )
    assert whw["who"]["author"] == "Jamie Owner"
    assert whw["how"]["one_page_per_run"] is True
    assert whw["why"]["search_engine_first"] is False
    gates = _people_first_gates(
        audience="Melbourne SMBs",
        differentiation="Hands-on technical audits with named crawl findings.",
        author="Jamie Owner",
    )
    assert gates["no_word_count_target"] is True
    assert gates["named_human_review_required"] is True
