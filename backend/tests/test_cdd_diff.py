"""CDD version diffing — normalized, so a re-export is not 'everything changed'."""

from __future__ import annotations

from app.services.cdd_diff import diff_cdd, normalize_field_value


def test_identical_documents_produce_no_changes():
    fields = {"business_keywords": "seo, ppc", "products": "SEO Audits"}
    diff = diff_cdd(fields, dict(fields))
    assert diff.has_changes is False
    assert diff.added == {} and diff.removed == {} and diff.modified == {}


def test_added_field_is_detected():
    diff = diff_cdd({"products": "SEO"}, {"products": "SEO", "geographic_focus": "Melbourne"})
    assert "geographic_focus" in diff.added
    assert diff.added["geographic_focus"] == "Melbourne"


def test_removed_field_is_detected_with_its_old_value():
    diff = diff_cdd({"products": "SEO", "positioning": "premium"}, {"products": "SEO"})
    assert diff.removed == {"positioning": "premium"}


def test_modified_field_records_old_and_new():
    diff = diff_cdd({"geographic_focus": "Melbourne"}, {"geographic_focus": "Sydney"})
    assert diff.modified["geographic_focus"] == ("Melbourne", "Sydney")


def test_blank_new_value_counts_as_removed_not_modified():
    diff = diff_cdd({"positioning": "premium"}, {"positioning": "   "})
    assert "positioning" in diff.removed
    assert "positioning" not in diff.modified


def test_list_field_reordering_is_not_a_change():
    """A re-exported spreadsheet must not diff as a full rewrite."""
    diff = diff_cdd(
        {"business_keywords": "seo, ppc, content"},
        {"business_keywords": "ppc, content, seo"},
    )
    assert diff.has_changes is False


def test_list_field_case_and_whitespace_are_ignored():
    diff = diff_cdd({"products": "SEO Audits,  PPC "}, {"products": "seo audits, ppc"})
    assert diff.has_changes is False


def test_list_field_with_a_genuinely_new_entry_is_modified():
    diff = diff_cdd(
        {"business_keywords": "seo, ppc"},
        {"business_keywords": "seo, ppc, local seo"},
    )
    assert "business_keywords" in diff.modified


def test_scalar_whitespace_and_case_are_ignored():
    diff = diff_cdd({"b2b_b2c": "B2B"}, {"b2b_b2c": " b2b "})
    assert diff.has_changes is False


def test_objectives_list_is_compared_as_a_set():
    diff = diff_cdd(
        {"objectives": ["Improve conversion rate", "Increase organic traffic"]},
        {"objectives": ["Increase organic traffic", "Improve conversion rate"]},
    )
    assert diff.has_changes is False


def test_changed_keys_lists_every_touched_field():
    diff = diff_cdd(
        {"products": "SEO", "positioning": "premium"},
        {"products": "SEO, PPC", "geographic_focus": "Melbourne"},
    )
    assert set(diff.changed_keys()) == {"products", "positioning", "geographic_focus"}


def test_normalize_splits_list_like_fields():
    assert normalize_field_value("business_keywords", "SEO, ppc") == {"seo", "ppc"}
    assert normalize_field_value("positioning", " Premium  Brand ") == "premium brand"


def test_diff_survives_none_and_non_dict_inputs():
    assert diff_cdd(None, {"products": "SEO"}).added == {"products": "SEO"}
    assert diff_cdd({"products": "SEO"}, None).removed == {"products": "SEO"}
    assert diff_cdd(None, None).has_changes is False


def test_summary_is_human_readable():
    diff = diff_cdd({"geographic_focus": "Melbourne"}, {"geographic_focus": "Sydney"})
    text = diff.summary()
    assert "geographic_focus" in text
    assert "Melbourne" in text and "Sydney" in text
