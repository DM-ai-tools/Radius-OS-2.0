"""Truncated LLM JSON must still yield the complete objects that arrived."""

from __future__ import annotations

import json

import pytest

from app.integrations.llm import _parse_json_content, _repair_truncated_json


def test_parse_complete_json_unchanged():
    raw = '{"checks": [{"parameter": "quality", "status": "passed"}], "summary": "ok"}'
    assert _parse_json_content(raw) == json.loads(raw)


def test_repair_unterminated_string_keeps_prior_checks():
    truncated = (
        '{"checks": ['
        '{"parameter": "company_alignment", "status": "passed", "message": "aligned"},'
        '{"parameter": "completeness", "status": "passed", "message": "baseline present"},'
        '{"parameter": "quality", "status": "warning", "message": "GA4 connected but '
    )
    repaired = _repair_truncated_json(truncated)
    assert isinstance(repaired, dict)
    checks = repaired["checks"]
    assert len(checks) == 2
    assert checks[0]["parameter"] == "company_alignment"
    assert checks[1]["parameter"] == "completeness"


def test_parse_json_content_repairs_unterminated_string():
    truncated = (
        '{"summary": "fine", "checks": ['
        '{"parameter": "honesty_anti_hallucination", "status": "passed", "message": "ok"}'
        '], "revision_guidance": "tighten baseli'
    )
    parsed = _parse_json_content(truncated)
    assert parsed["summary"] == "fine"
    assert len(parsed["checks"]) == 1
    assert "revision_guidance" not in parsed or parsed.get("revision_guidance") is None


def test_blank_model_reply_does_not_raise():
    assert _parse_json_content("") is None
    assert _parse_json_content("   ") is None
    assert _parse_json_content("```json\n```") is None


def test_unsalvageable_junk_still_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_content("not json at all")


def test_keyword_style_truncated_array_keeps_complete_rows():
    truncated = (
        '{"keywords": ['
        '{"keyword": "email automation", "relevant": true, "reason": "core service"},'
        '{"keyword": "ai girlfriend", "relevant": false, "reason": "consumer toy"},'
        '{"keyword": "half a ro'
    )
    parsed = _parse_json_content(truncated)
    assert [row["keyword"] for row in parsed["keywords"]] == [
        "email automation",
        "ai girlfriend",
    ]
