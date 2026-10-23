"""Server-side noise filters for DataForSEO Labs keyword pulls.

Labs bills per returned row, so rows that keyword cleaning always rejects are
asked not to be returned. The filters must never be broader than the local rules
(a row the server drops must be one cleaning drops), must fit Labs' limits (8
filter atoms, ~1000-char regex), and must use only regex forms Labs accepts
(measured live 2026-10-18: whole-keyword ``^...$`` only, no hyphen in a class).
A rejected request must surface as an error, not as a seed with no keywords.
"""

from __future__ import annotations

import itertools
import re
from unittest.mock import patch

import pytest

from app.integrations import dataforseo
from app.services.keyword_exclusions import (
    CONSUMER_AI_TERMS,
    CONSUMER_MAILBOX_TERMS,
    HARD_REJECT_TERMS,
    hard_reject_term,
)
from app.services.keyword_opportunity import is_stale_year_keyword
from app.services.keyword_provider_filters import (
    MAX_REGEX_CHARS,
    current_or_future_year_regex,
    hard_reject_patterns,
    hard_reject_regex,
    noise_filters,
    past_year_regex,
)


def _atoms(condition) -> int:
    """Count [field, op, value] triples in a (possibly nested) Labs filter."""
    if isinstance(condition, list) and len(condition) == 3 and isinstance(condition[0], str):
        return 1
    return sum(_atoms(c) for c in condition if isinstance(c, list))


def _server_drops(keyword: str, endpoint: str = "keyword_suggestions", year: int = 2026) -> bool:
    for cond in noise_filters(endpoint, year=year):
        if isinstance(cond[0], str):
            if re.search(cond[2], keyword):
                return True
        else:
            (_, _, past), _, (_, _, future) = cond
            if re.search(past, keyword) and not re.search(future, keyword):
                return True
    return False


def _sample_keywords() -> list[str]:
    terms = sorted(set(HARD_REJECT_TERMS) | set(CONSUMER_AI_TERMS) | set(CONSUMER_MAILBOX_TERMS))
    out: list[str] = []
    for term in terms:
        out += [
            term,
            f"seo {term}",
            f"{term} seo",
            f"seo {term} melbourne",
            f"seo-{term}",
            f"seo {term}s",  # plural is a different token; must not match unless listed
            f"seo{term}",  # glued; must not match
            f"pre{term} seo",
        ]
    out += ["seo", "plumber near me", "web design agency", "seo agency melbourne", "best seo tools"]
    return out


def test_hard_reject_patterns_match_exactly_what_cleaning_rejects():
    patterns = [re.compile(p) for p in hard_reject_patterns()]
    wrong = [k for k in _sample_keywords() if any(p.search(k) for p in patterns) != bool(hard_reject_term(k))]
    assert wrong == []


def test_split_patterns_equal_the_single_pattern():
    single = re.compile(hard_reject_regex())
    patterns = [re.compile(p) for p in hard_reject_patterns()]
    for keyword in _sample_keywords():
        assert bool(single.search(keyword)) == any(p.search(keyword) for p in patterns), keyword


def test_patterns_fit_the_labs_regex_limit_and_accepted_forms():
    for pattern in [*hard_reject_patterns(), past_year_regex(2026), current_or_future_year_regex(2026)]:
        assert len(pattern) <= MAX_REGEX_CHARS
        # Labs only honours $ in a whole-keyword pattern; \b and \z are not supported.
        assert pattern.startswith("^") and pattern.endswith("$")
        assert "\\b" not in pattern and "\\z" not in pattern
        # A literal hyphen inside a character class breaks Labs (ranges are fine).
        for cls in re.findall(r"\[[^\]]*\]", pattern):
            assert not re.search(r"(^\[\^?-)|(-\]$)|\\-", cls), cls


@pytest.mark.parametrize("endpoint", ["keyword_suggestions", "keyword_ideas", "related_keywords"])
def test_noise_filters_leave_room_in_the_8_atom_budget(endpoint):
    noise = sum(_atoms(c) for c in noise_filters(endpoint))
    # Variant ideas calls already use volume-or-null (2) + on-topic like (1) + intent (1).
    assert noise + 4 <= 8
    field = "keyword_data.keyword" if endpoint == "related_keywords" else "keyword"
    assert all(_flat_field(c) == field for c in noise_filters(endpoint))


def _flat_field(condition):
    return condition[0] if isinstance(condition[0], str) else _flat_field(condition[0])


def test_year_filter_is_never_broader_than_the_stale_year_rule():
    words = ["seo", "guide", "vs", "trends", "seo-", "2024", "2026", "2019", "2035", "2029", "(2023)", "2025!"]
    for n in (1, 2, 3):
        for combo in itertools.product(words, repeat=n):
            keyword = " ".join(combo)
            if _server_drops(keyword):
                assert is_stale_year_keyword(keyword, now=2026) or hard_reject_term(keyword), keyword


def test_year_filter_drops_plain_past_year_keywords():
    assert _server_drops("seo trends 2024")
    assert _server_drops("2019 seo guide")
    assert not _server_drops("seo trends 2026")
    assert not _server_drops("seo 2026 vs 2024")  # current year present: kept


async def test_rejected_labs_task_is_reported_not_silent():
    body = {
        "status_code": 20000,
        "tasks": [{"status_code": 40501, "status_message": "Invalid Field: 'filters' (regex).", "result": None}],
    }

    async def fake_post(path, payload):
        return body

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        for fn in (dataforseo.keyword_suggestions, dataforseo.keyword_ideas, dataforseo.related_keywords):
            rows, errors = await fn("seo", location_code=2036, language_code="en", limit=10)
            assert rows == []
            assert any("task_error" in e and "Invalid Field" in e for e in errors), (fn.__name__, errors)


async def test_seeding_sends_noise_filters_on_every_labs_keyword_call():
    sent: list[dict] = []

    async def fake_post(path, payload):
        sent.append({"path": path, **payload[0]})
        return {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": []}]}]}

    from app.services.keyword_seeding import expand_seed_dataforseo

    with patch.object(dataforseo, "_auth_header", return_value="Basic x"), patch.object(
        dataforseo, "_post", side_effect=fake_post
    ):
        await expand_seed_dataforseo("seo", location_code=2036, language_code="en")

    labs = [s for s in sent if "dataforseo_labs" in s["path"]]
    assert labs
    for request in labs:
        flat = repr(request.get("filters"))
        assert "not_regex" in flat, request["path"]
        assert _atoms(request["filters"]) <= 8, request["path"]
