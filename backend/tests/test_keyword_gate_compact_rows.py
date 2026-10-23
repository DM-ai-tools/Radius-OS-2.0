"""The keyword gate's compact reply rows: ``[keyword, k|d, intent, topic, reason?]``.

The compact format exists only to make the reply shorter (and the gate faster). It must
mean exactly what the object format meant: same verdicts, same intent and topic stamped
on the row, a reason on every drop, nothing the model names outside the batch, and the
complete rows of a truncated reply still recovered.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import keyword_validation_gate as gate
from app.services.keyword_validation_gate import validate_keywords


@pytest.fixture(autouse=True)
def _no_shared_cache(monkeypatch):
    async def _get_many(keys):
        return {}

    async def _set_many(values, ttl):
        return None

    monkeypatch.setattr(gate, "cache_get_many", _get_many)
    monkeypatch.setattr(gate, "cache_set_many", _set_many)


CONTEXT = {
    "client_name": "Northside Dental",
    "primary_url": "https://northsidedental.com.au",
    "industry": "Dental",
    "services": ["Teeth Whitening"],
    "cdd_keywords": ["dentist melbourne"],
    "geography": "Melbourne",
    "competitor_names": ["SmileCo Dental"],
    "competitor_domains": ["smileco.com.au"],
    "client_id": "client-1",
}


def rows(*keywords: str) -> list[dict[str, Any]]:
    return [
        {"keyword": kw, "seed": "teeth whitening", "target": "Teeth Whitening",
         "target_type": "service", "match_class": "broad", "volume": 100}
        for kw in keywords
    ]


def test_the_prompt_asks_for_compact_rows():
    assert '{"r":[["<exact input keyword>","k","c","short topic"]' in gate._SYSTEM
    assert "echoed character-for-character" in gate._SYSTEM
    assert "only on \"d\" rows: the reason" in gate._SYSTEM


def test_compact_rows_parse_to_the_same_verdicts_as_objects():
    sent = {1: "teeth whitening cost", 2: "dental assistant salary", 3: "whitening strips"}
    compact = gate._parse_verdicts(
        {
            "r": [
                ["teeth whitening cost", "k", "c", "whitening cost"],
                ["dental assistant salary", "d", "i", "dental careers", "job seeker, not a patient"],
                ["whitening strips", "?", "t", "home whitening"],
            ]
        },
        sent,
    )
    objects = gate._parse_verdicts(
        {
            "keywords": [
                {"keyword": "teeth whitening cost", "relevant": True, "intent": "commercial", "topic": "whitening cost"},
                {"keyword": "dental assistant salary", "relevant": False, "intent": "informational",
                 "topic": "dental careers", "reason": "job seeker, not a patient"},
                {"keyword": "whitening strips", "relevant": None, "intent": "transactional", "topic": "home whitening"},
            ]
        },
        sent,
    )
    assert compact == objects
    assert compact["teeth whitening cost"].verdict == "keep"
    assert compact["dental assistant salary"].verdict == "drop"
    assert compact["dental assistant salary"].reason == "job seeker, not a patient"
    assert compact["whitening strips"].verdict == "review"


def test_a_keyword_that_was_not_sent_is_ignored():
    verdicts = gate._parse_verdicts(
        {"r": [["teeth whitening cost", "k", "c", "cost"], ["invented keyword", "k", "c", "x"]]},
        {1: "teeth whitening cost"},
    )
    assert set(verdicts) == {"teeth whitening cost"}


def test_a_truncated_compact_reply_keeps_every_complete_row():
    raw = (
        '{"r":[["teeth whitening cost","k","c","whitening cost"],'
        '["dental assistant salary","d","i","dental careers","job seeker"],'
        '["whitening strips","k","t","home whi'
    )
    verdicts = gate._parse_verdicts(
        {"_raw": raw},
        {1: "teeth whitening cost", 2: "dental assistant salary", 3: "whitening strips"},
    )
    assert set(verdicts) == {"teeth whitening cost", "dental assistant salary"}
    assert verdicts["dental assistant salary"].verdict == "drop"


async def test_the_gate_applies_compact_verdicts_end_to_end():
    async def judge(system: str, user: str) -> dict[str, Any]:
        return {
            "r": [
                ["teeth whitening cost", "k", "c", "whitening cost"],
                ["free dental jobs", "d", "i", "dental jobs", "job seeker, not a patient"],
            ]
        }

    kept, dropped, audit = await validate_keywords(
        rows("teeth whitening cost", "free dental jobs"), caller=judge, **CONTEXT
    )
    assert [r["keyword"] for r in kept] == ["teeth whitening cost"]
    assert kept[0]["intent"] == "commercial" and kept[0]["topic"] == "whitening cost"
    assert [r["keyword"] for r in dropped] == ["free dental jobs"]
    assert dropped[0]["relevance_drop_reason"] == "job seeker, not a patient"
    assert audit["judged"] == 2 and audit["quarantined"] == 0


async def test_an_all_drop_compact_batch_with_distinct_reasons_is_trusted():
    async def judge(system: str, user: str) -> dict[str, Any]:
        return {
            "r": [
                ["dental assistant salary", "d", "i", "careers", "job seeker"],
                ["dental school admission", "d", "i", "education", "student, not a patient"],
                ["dentist login", "d", "n", "portal", "existing account access"],
                ["dental hygienist careers", "d", "i", "careers", "career research"],
            ]
        }

    kept, dropped, audit = await validate_keywords(
        rows("dental assistant salary", "dental school admission", "dentist login", "dental hygienist careers"),
        caller=judge,
        **CONTEXT,
    )
    assert kept == [] and len(dropped) == 4
    assert "all_drop_guard" not in audit["quarantined_by_cause"]
