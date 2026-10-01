"""CDD upload extraction keeps spreadsheet cells when the model renames keys."""

from __future__ import annotations

import io

import pytest

from app.services.intake_ai import (
    _heuristic_cdd_map,
    _sanitize_cdd,
    extract_cdd_fields_from_text,
    extract_text_from_upload,
)


def test_document_stamp_is_not_treated_as_a_business_name():
    fields = _sanitize_cdd(
        {
            "business_name": "21/09/2026 (draft)",
            "inferred_industry": "21/09/2026 (draft)",
            "products": "21/09/2026 (draft)",
            "website_url": "https://clicktrends.com.au",
            "products_for_promotion": "AI-Powered Email Marketing",
        }
    )
    assert "business_name" not in fields
    assert "inferred_industry" not in fields
    assert "products" not in fields
    assert fields["website_url"] == "https://clicktrends.com.au"
    assert fields["products_for_promotion"] == "AI-Powered Email Marketing"


def test_wide_row_keeps_each_label_with_its_own_value():
    """A trailing date cell must not become Industry/Products/Business Name."""
    text = "\n".join(
        [
            "## Sheet: CDD",
            "Client Discovery Document|21/09/2026 (draft)",
            "Industry|Digital marketing|Business Name|Click Trends|Products|SEO, PPC, Email|21/09/2026 (draft)",
            "Website URL|https://clicktrends.com.au/",
            "Products /Services list - for promotion|AI-Powered Email Marketing",
        ]
    )
    fields = _sanitize_cdd(_heuristic_cdd_map(text))
    assert fields["inferred_industry"] == "Digital marketing"
    assert fields["business_name"] == "Click Trends"
    assert fields["products"] == "SEO, PPC, Email"
    assert fields["website_url"] == "https://clicktrends.com.au/"
    assert fields["products_for_promotion"] == "AI-Powered Email Marketing"
    assert "21/09/2026" not in str(fields.values())


def test_numbered_pipe_rows_still_map():
    text = "1||Industry|Digital marketing\n2||Business Keywords|seo, ppc"
    fields = _sanitize_cdd(_heuristic_cdd_map(text))
    assert fields["inferred_industry"] == "Digital marketing"
    assert fields["business_keywords"] == "seo, ppc"


def test_pipe_rows_and_stacked_labels_map_to_cdd_keys():
    text = "\n".join(
        [
            "## Sheet: CDD",
            "|Business Name|Click Trends|",
            "1||Industry|Digital marketing",
            "Website URL",
            "https://clicktrends.com.au",
            "Competitors",
            "Agency A, Agency B",
        ]
    )
    fields = _sanitize_cdd(_heuristic_cdd_map(text))
    assert fields["business_name"] == "Click Trends"
    assert fields["inferred_industry"] == "Digital marketing"
    assert fields["website_url"] == "https://clicktrends.com.au"
    assert fields["competitors"] == "Agency A, Agency B"


@pytest.mark.asyncio
async def test_model_key_drift_does_not_drop_sheet_values(monkeypatch):
    async def _fake_json(system, user):
        return {"company_name": "Ignored Co", "website": "https://ignored.example"}

    monkeypatch.setattr("app.services.intake_ai._perplexity_json", _fake_json)
    text = "Business Name|Radius Digital\nProducts|SEO audits"
    fields = await extract_cdd_fields_from_text(text, filename="cdd.xlsx")
    assert fields["business_name"] == "Radius Digital"
    assert fields["products"] == "SEO audits"
    assert fields["website_url"] == "https://ignored.example"


def test_xlsx_label_value_roundtrip():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Business Name", "ARG Finance"])
    ws.append(["Business Keywords", "home loans"])
    buf = io.BytesIO()
    wb.save(buf)
    text = extract_text_from_upload("client.xlsx", buf.getvalue())
    fields = _sanitize_cdd(_heuristic_cdd_map(text))
    assert fields["business_name"] == "ARG Finance"
    assert fields["business_keywords"] == "home loans"
