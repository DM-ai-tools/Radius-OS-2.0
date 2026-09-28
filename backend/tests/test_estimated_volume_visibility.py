"""An estimated volume must never render as a measured one."""

from __future__ import annotations

from app.services.bw_workbook import (
    SEARCH_DEMAND_COLUMNS,
    build_search_demand_analysis_rows,
)


def _dataset():
    return [
        {"keyword": "drain cleaning", "volume": 720, "source": "dataforseo"},
        {
            "keyword": "drain cleaning services",
            "volume": 35,
            "source": "fallback",
            "provider_fallback": True,
            "volume_estimated": True,
        },
    ]


def test_workbook_exposes_a_volume_basis_column():
    assert "volume_basis" in SEARCH_DEMAND_COLUMNS


def test_workbook_marks_estimated_and_measured_rows_differently():
    rows = build_search_demand_analysis_rows(_dataset())
    by_kw = {r["keyword"]: r for r in rows if r.get("row_type") == "data"}
    assert by_kw["drain cleaning"]["volume_basis"] == "measured"
    assert by_kw["drain cleaning services"]["volume_basis"] == "estimated"


def test_every_data_row_declares_a_basis():
    rows = build_search_demand_analysis_rows(_dataset())
    data = [r for r in rows if r.get("row_type") == "data"]
    assert data and all(
        r.get("volume_basis") in ("measured", "estimated", "unmeasured") for r in data
    )


def test_a_null_volume_is_unmeasured_not_measured():
    """The provider returned nothing for this keyword. Calling that "measured"
    let a blank cell read as a real zero."""
    rows = build_search_demand_analysis_rows(
        [{"keyword": "blocked drain", "volume": None, "source": "dataforseo"}]
    )
    row = next(r for r in rows if r.get("row_type") == "data")
    assert row["volume_basis"] == "unmeasured"
    assert row["search_volume_mo"] == "N/A"


def test_a_measured_zero_stays_a_number():
    """Zero volume is a measurement, not a gap."""
    rows = build_search_demand_analysis_rows(
        [{"keyword": "blocked drain", "volume": 0, "source": "dataforseo"}]
    )
    row = next(r for r in rows if r.get("row_type") == "data")
    assert row["volume_basis"] == "measured"
    assert row["search_volume_mo"] == 0
