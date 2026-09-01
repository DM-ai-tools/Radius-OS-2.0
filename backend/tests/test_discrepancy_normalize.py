"""LLM discrepancy notes must not crash D1 when they are strings."""

from app.integrations.llm import _normalize_discrepancies


def test_normalize_discrepancies_accepts_strings():
    out = _normalize_discrepancies(
        [
            "Could not confirm ticket size from the website.",
            {
                "field_key": "products",
                "explanation": "Offerings listed on /services may be incomplete.",
            },
            {"field": "positioning", "note": "Homepage headline is generic."},
        ]
    )
    assert out[0]["field_key"] == "business_model"
    assert "ticket size" in out[0]["explanation"]
    assert out[1] == {
        "field_key": "products",
        "explanation": "Offerings listed on /services may be incomplete.",
    }
    assert out[2]["field_key"] == "positioning"


def test_normalize_discrepancies_does_not_index_strings_as_dicts():
    raw = ["thin evidence on competitors"]
    out = _normalize_discrepancies(raw)
    assert all(isinstance(row, dict) and "field_key" in row for row in out)
    mapped = {d["field_key"]: d["explanation"] for d in out}
    assert "business_model" in mapped
