"""DataForSEO-side filters that drop only keywords the pipeline always discards.

DataForSEO Labs bills per returned row. Rows that keyword cleaning rejects anyway
(employment, login, school and consumer-tool searches; past-year searches) were
paid for and thrown away. These filters ask Labs not to return them. They mirror
the local rules exactly or more narrowly, never more broadly: a row the server
drops is always one cleaning would have dropped, so results do not change.

- Hard-reject terms: ``keyword_exclusions.hard_reject_term`` matches whole tokens
  (``[a-z0-9]+``) or whole phrases. The regex uses the same token boundary.
- Past years: ``is_stale_year_keyword`` rejects when the first 20xx year is past.
  The server only drops keywords with a past year and no current or later year,
  a subset of that rule.

Labs regex quirks (measured live 2026-10-18), which shape every pattern here:
- ``$`` only works in a pattern that starts with ``^`` and ends with ``$`` (a
  whole-keyword match). ``^``/``$`` inside a group never match; ``jobs$`` is an
  internal error. So each pattern is ``^(.*SEP)?(terms)(SEP.*)?$``.
- ``\\b``, ``\\z`` and a literal hyphen in a character class are not usable.
- A pattern over ~1000 characters is rejected ("regexp max length exceeded").
"""

from __future__ import annotations

import re
from typing import Any

# Token boundary identical to keyword_exclusions._tokens: anything not [a-z0-9].
_TOKEN_SEP = "[^a-z0-9]"
# Year boundary for the past-year drop. Python's \b (used by _STALE_YEAR_RE) also
# treats "_" and accented letters as word characters, so only space and ASCII
# punctuation count here and the server never drops a year the local rule keeps.
# No hyphen (unusable in a Labs class): "seo-2024" is left to local cleaning.
_YEAR_SEP = "[ /.,:;!?()'\"+&]"
# Year boundary for "a current or later year is present" (keeps the row): broader
# than Python's \b, so the server keeps every row the local rule keeps.
_FUTURE_SEP = "[^0-9a-z_]"
MAX_REGEX_CHARS = 900


def _whole_keyword(alternatives: list[str], sep: str) -> str:
    """Matches a keyword containing one of ``alternatives`` bounded by ``sep`` or the ends."""
    return f"^(.*{sep})?({'|'.join(alternatives)})({sep}.*)?$"


def _term_pattern(term: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", term.lower())
    return "[^a-z0-9]+".join(re.escape(t) for t in tokens)


def _hard_reject_alternatives(industry: str | None = None) -> list[str]:
    from app.services.keyword_exclusions import (
        CONSUMER_AI_TERMS,
        CONSUMER_MAILBOX_TERMS,
        HARD_REJECT_TERMS,
        INDUSTRY_HARD_REJECT_TERMS,
        industry_slug,
    )

    terms = set(HARD_REJECT_TERMS) | set(CONSUMER_AI_TERMS) | set(CONSUMER_MAILBOX_TERMS)
    slug = industry_slug(industry)
    if slug:
        terms |= set(INDUSTRY_HARD_REJECT_TERMS.get(slug, frozenset()))
    return sorted({p for p in (_term_pattern(t) for t in terms) if p}, key=lambda p: (-len(p), p))


def hard_reject_patterns(industry: str | None = None) -> list[str]:
    """Regexes (each under the Labs length limit) that together match exactly the
    keywords hard_reject_term rejects."""
    overhead = len(_whole_keyword([], _TOKEN_SEP))
    chunks: list[list[str]] = [[]]
    for alt in _hard_reject_alternatives(industry):
        if chunks[-1] and overhead + len("|".join([*chunks[-1], alt])) > MAX_REGEX_CHARS:
            chunks.append([])
        chunks[-1].append(alt)
    return [_whole_keyword(c, _TOKEN_SEP) for c in chunks if c]


def hard_reject_regex(industry: str | None = None) -> str:
    """The same rule as one pattern (for tests and local checks; too long for Labs)."""
    return _whole_keyword(_hard_reject_alternatives(industry), _TOKEN_SEP)


def past_year_regex(year: int) -> str:
    """Matches a 20xx year token before ``year`` (same token rule as _STALE_YEAR_RE)."""
    past = [str(y) for y in range(2000, year)]
    return _whole_keyword(past, _YEAR_SEP) if past else ""


def current_or_future_year_regex(year: int) -> str:
    # Any 20xx year from now on (the local rule treats every one as not stale).
    future = [str(y) for y in range(year, 2100)]
    return _whole_keyword(future, _FUTURE_SEP) if future else ""


def keyword_field(endpoint: str) -> str:
    """Where Labs puts the keyword text; related keywords nest it under keyword_data."""
    return "keyword_data.keyword" if endpoint == "related_keywords" else "keyword"


def noise_filters(endpoint: str, *, industry: str | None = None, year: int | None = None) -> list[list[Any]]:
    """Labs filter conditions to AND onto a keyword request (4 atoms of the 8 allowed)."""
    field = keyword_field(endpoint)
    from app.services.keyword_opportunity import current_year

    yr = year or current_year()
    out: list[list[Any]] = [[field, "not_regex", pattern] for pattern in hard_reject_patterns(industry)]
    past = past_year_regex(yr)
    if past:
        future = current_or_future_year_regex(yr)
        out.append([[field, "not_regex", past], "or", [field, "regex", future]] if future else [field, "not_regex", past])
    return out
