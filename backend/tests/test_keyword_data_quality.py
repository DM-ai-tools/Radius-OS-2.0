"""Fabricated keyword rows must be countable and visible to every consumer.

The rows were already flagged `provider_fallback` / `volume_estimated`, but the
flag was read nowhere — so invented search volumes rendered in client PDFs
exactly like measured provider data.
"""

from __future__ import annotations

from app.services.keyword_seeding import summarize_keyword_data_quality


def _real(kw, vol):
    return {"keyword": kw, "volume": vol, "source": "dataforseo"}


def _fabricated(kw, vol):
    return {
        "keyword": kw,
        "volume": vol,
        "source": "fallback",
        "provider_fallback": True,
        "volume_estimated": True,
    }


def test_all_measured_data_reports_clean():
    q = summarize_keyword_data_quality([_real("a", 100), _real("b", 50)])
    assert q["estimated_count"] == 0
    assert q["measured_count"] == 2
    assert q["has_estimated_volumes"] is False
    assert q["estimated_share"] == 0.0


def test_fabricated_rows_are_counted_and_flagged():
    q = summarize_keyword_data_quality(
        [_real("a", 100), _fabricated("b", 35), _fabricated("c", 25)]
    )
    assert q["estimated_count"] == 2
    assert q["measured_count"] == 1
    assert q["has_estimated_volumes"] is True
    assert q["estimated_share"] == 66.7


def test_fully_fabricated_pool_is_reported_as_such():
    q = summarize_keyword_data_quality([_fabricated("a", 35), _fabricated("b", 25)])
    assert q["estimated_share"] == 100.0
    assert q["all_estimated"] is True


def test_warning_text_names_the_problem_in_plain_language():
    q = summarize_keyword_data_quality([_real("a", 10), _fabricated("b", 35)])
    text = q["warning"]
    assert "estimated" in text.lower()
    assert "1" in text
    # Must not be presented as measured demand
    assert "measured" not in text.lower() or "not" in text.lower()


def test_clean_pool_has_no_warning():
    assert summarize_keyword_data_quality([_real("a", 10)])["warning"] is None


def test_empty_pool_is_safe():
    q = summarize_keyword_data_quality([])
    assert q["estimated_count"] == 0
    assert q["has_estimated_volumes"] is False
    assert q["warning"] is None


def test_either_flag_alone_marks_a_row_estimated():
    q = summarize_keyword_data_quality(
        [
            {"keyword": "a", "volume": 1, "provider_fallback": True},
            {"keyword": "b", "volume": 1, "volume_estimated": True},
        ]
    )
    assert q["estimated_count"] == 2


def test_non_dict_rows_are_ignored():
    q = summarize_keyword_data_quality([_real("a", 10), "junk", None])
    assert q["measured_count"] == 1
