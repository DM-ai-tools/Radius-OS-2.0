"""The comparison dimensions, each independent and each able to abstain.

Every dimension returns a ``DimensionResult``. When the inputs cannot support it
— most often because the competitor content came from a cached snapshot with no
body text — it returns ``measurable=False`` with a reason instead of a score. A
fabricated zero would read as "the competitor has no content", which is the
opposite of the truth.

Adding a dimension means writing one function and adding it to ``DIMENSIONS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.content_benchmark.fetch import PageContent, word_count

_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "is",
    "are", "your", "our", "you", "we", "that", "this", "it", "as", "at", "by",
    "from", "be", "can", "will", "how", "what", "why", "when", "more", "most",
}


@dataclass
class DimensionResult:
    key: str
    label: str
    measurable: bool
    # 0-100 where higher means WE are stronger on this dimension.
    score: int | None = None
    ours: Any = None
    theirs: Any = None
    detail: str = ""
    reason: str = ""
    gaps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "measurable": self.measurable,
            "score": self.score,
            "ours": self.ours,
            "theirs": self.theirs,
            "detail": self.detail,
            "reason": self.reason,
            "gaps": list(self.gaps),
            "recommendations": list(self.recommendations),
        }


def _terms(text: Any) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9']+", str(text or "").lower())
        if t not in _STOP and len(t) > 2
    }


def _headings(page: PageContent | dict[str, Any]) -> list[str]:
    if isinstance(page, PageContent):
        return [*page.h1s, *page.h2s, *page.h3s]
    return [
        *(page.get("h1s") or []),
        *(page.get("h2s") or []),
        *(page.get("h3s") or []),
    ]


def _ours_text(ours: dict[str, Any]) -> str:
    """Our draft's body — write_one_page returns markdown under a few keys."""
    for key in ("page_markdown", "markdown", "body", "content"):
        value = ours.get(key)
        if isinstance(value, str) and value.strip():
            return value
    sections = ours.get("sections")
    if isinstance(sections, list):
        return "\n\n".join(
            str(s.get("body") or "") for s in sections if isinstance(s, dict)
        )
    return ""


def _ratio_score(ours: float, theirs: float) -> int:
    """0-100 from a ratio, 50 == parity. Saturates rather than running away."""
    if theirs <= 0:
        return 100 if ours > 0 else 50
    ratio = ours / theirs
    if ratio >= 2:
        return 100
    if ratio <= 0.25:
        return 0
    # 0.25..2 mapped onto 0..100 with parity at 50.
    if ratio <= 1:
        return int(round((ratio - 0.25) / 0.75 * 50))
    return int(round(50 + (ratio - 1) / 1.0 * 50))


# --- dimensions ------------------------------------------------------------


def keyword_coverage(
    ours: dict[str, Any], theirs: PageContent, *, target_keywords: list[str]
) -> DimensionResult:
    """Which target keywords each side actually uses."""
    keywords = [str(k).strip() for k in (target_keywords or []) if str(k).strip()]
    if not keywords:
        return DimensionResult(
            "keyword_coverage", "Keyword coverage", False,
            reason="No target keywords supplied for this service.",
        )
    our_blob = " ".join(
        [str(ours.get("title") or ""), " ".join(_headings(ours)), _ours_text(ours)]
    ).lower()
    their_blob = " ".join(
        [theirs.title, theirs.meta_description, " ".join(_headings(theirs)), theirs.body_text]
    ).lower()

    ours_hit = [k for k in keywords if k.lower() in our_blob]
    theirs_hit = [k for k in keywords if k.lower() in their_blob]
    missing = [k for k in theirs_hit if k not in ours_hit]

    score = _ratio_score(len(ours_hit), len(theirs_hit))
    recs = []
    if missing:
        recs.append(
            "Cover these target keywords the competitor ranks for and we omit: "
            + ", ".join(missing[:6])
        )
    return DimensionResult(
        "keyword_coverage", "Keyword coverage", True,
        score=score,
        ours=f"{len(ours_hit)}/{len(keywords)}",
        theirs=f"{len(theirs_hit)}/{len(keywords)}",
        detail=f"We use {len(ours_hit)} of {len(keywords)} target keywords; they use {len(theirs_hit)}.",
        gaps=missing[:10],
        recommendations=recs,
    )


def topic_gaps(ours: dict[str, Any], theirs: PageContent, **_: Any) -> DimensionResult:
    """Subjects they cover in headings that we do not cover at all."""
    their_headings = [h for h in _headings(theirs) if str(h).strip()]
    if not their_headings:
        return DimensionResult(
            "topic_gaps", "Topics covered vs missing", False,
            reason="No competitor headings were retrieved, so topics cannot be compared.",
        )
    our_terms = _terms(
        " ".join([str(ours.get("title") or ""), " ".join(_headings(ours)), _ours_text(ours)])
    )
    missing: list[str] = []
    for heading in their_headings:
        heading_terms = _terms(heading)
        if not heading_terms:
            continue
        # Uncovered when we share less than a third of the heading's content words.
        overlap = len(heading_terms & our_terms) / len(heading_terms)
        if overlap < 0.34:
            missing.append(str(heading).strip()[:120])

    covered = len(their_headings) - len(missing)
    score = _ratio_score(covered, len(their_headings))
    recs = []
    if missing:
        recs.append(
            f"Add sections covering {len(missing)} subject(s) they treat and we skip, "
            f"starting with: {missing[0]}"
        )
    return DimensionResult(
        "topic_gaps", "Topics covered vs missing", True,
        score=score,
        ours=f"{covered}/{len(their_headings)} of their topics covered",
        theirs=f"{len(their_headings)} topics",
        detail=f"{len(missing)} of their {len(their_headings)} topics are absent from our draft.",
        gaps=missing[:12],
        recommendations=recs,
    )


def content_depth(ours: dict[str, Any], theirs: PageContent, **_: Any) -> DimensionResult:
    """Word count. Only tier 2/3 competitor content can support this."""
    our_words = word_count(_ours_text(ours))
    if not theirs.depth_available:
        return DimensionResult(
            "content_depth", "Content depth", False,
            ours=our_words,
            reason=(
                f"Competitor body text unavailable (source: {theirs.source}). "
                "Depth needs a live fetch or pasted content."
            ),
        )
    their_words = word_count(theirs.body_text)
    score = _ratio_score(our_words, their_words)
    recs = []
    if their_words > our_words * 1.25 and their_words - our_words > 150:
        recs.append(
            f"Their page runs {their_words} words to our {our_words}. Close the gap with "
            "substance that answers a real question, not padding."
        )
    return DimensionResult(
        "content_depth", "Content depth", True,
        score=score, ours=our_words, theirs=their_words,
        detail=f"Our draft is {our_words} words; theirs is {their_words}.",
        recommendations=recs,
    )


def heading_structure(ours: dict[str, Any], theirs: PageContent, **_: Any) -> DimensionResult:
    """Whether the page is navigable: one H1, a real H2 spine."""
    our_h1 = [h for h in (ours.get("h1s") or []) if str(h).strip()]
    our_h2 = [h for h in (ours.get("h2s") or []) if str(h).strip()]
    if not our_h2 and isinstance(ours.get("sections"), list):
        our_h2 = [
            str(s.get("title") or "") for s in ours["sections"] if isinstance(s, dict)
        ]
        our_h2 = [h for h in our_h2 if h.strip()]
    their_h2 = [h for h in theirs.h2s if str(h).strip()]

    if not their_h2 and not theirs.h1s:
        return DimensionResult(
            "heading_structure", "Heading structure", False,
            ours=f"{len(our_h1)} H1 / {len(our_h2)} H2",
            reason="No competitor headings retrieved.",
        )

    score = _ratio_score(len(our_h2), len(their_h2) or 1)
    recs = []
    if len(our_h1) > 1:
        recs.append(f"Our draft has {len(our_h1)} H1s — a page should have exactly one.")
        score = min(score, 40)
    if not our_h2:
        recs.append("Our draft has no H2 spine; readers cannot scan it.")
        score = min(score, 20)
    elif len(their_h2) > len(our_h2) * 1.5 and len(their_h2) - len(our_h2) >= 3:
        recs.append(
            f"They structure the page with {len(their_h2)} H2 sections to our {len(our_h2)}; "
            "break our long passages into scannable sections."
        )
    return DimensionResult(
        "heading_structure", "Heading structure", True,
        score=score,
        ours=f"{len(our_h1)} H1 / {len(our_h2)} H2",
        theirs=f"{len(theirs.h1s)} H1 / {len(their_h2)} H2",
        detail=f"We use {len(our_h2)} H2 sections; they use {len(their_h2)}.",
        recommendations=recs,
    )


def _flesch(text: str) -> float | None:
    """Flesch Reading Ease. Approximate syllables by vowel groups."""
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    if len(words) < 30 or not sentences:
        return None
    syllables = 0
    for word in words:
        groups = re.findall(r"[aeiouy]+", word.lower())
        count = len(groups)
        if word.lower().endswith("e") and count > 1:
            count -= 1
        syllables += max(1, count)
    wps = len(words) / len(sentences)
    spw = syllables / len(words)
    return round(206.835 - 1.015 * wps - 84.6 * spw, 1)


def readability(ours: dict[str, Any], theirs: PageContent, **_: Any) -> DimensionResult:
    """Flesch Reading Ease, ours vs theirs. Closer to 60 is better for the web."""
    our_score = _flesch(_ours_text(ours))
    if our_score is None:
        return DimensionResult(
            "readability", "Readability", False,
            reason="Our draft is too short to score reliably (needs 30+ words).",
        )
    their_score = _flesch(theirs.body_text) if theirs.depth_available else None
    if their_score is None:
        return DimensionResult(
            "readability", "Readability", False, ours=our_score,
            reason=(
                f"Competitor body text unavailable (source: {theirs.source}); "
                "nothing to compare against."
            ),
        )
    # Distance from the web-prose target of 60 decides who reads better.
    our_gap, their_gap = abs(our_score - 60), abs(their_score - 60)
    score = 50 if our_gap == their_gap else (100 if our_gap < their_gap else 0)
    score = max(0, min(100, int(round(score if our_gap != their_gap else 50))))
    recs = []
    if our_score < 45:
        recs.append(
            f"Our prose scores {our_score} Flesch — hard going. Shorten sentences "
            "and prefer plain words."
        )
    elif our_score < their_score - 10:
        recs.append(
            f"They read easier ({their_score} vs our {our_score}). Trim sentence length."
        )
    return DimensionResult(
        "readability", "Readability", True,
        score=score, ours=our_score, theirs=their_score,
        detail=f"Flesch Reading Ease — ours {our_score}, theirs {their_score} (60 is the web target).",
        recommendations=recs,
    )


def onpage_seo(ours: dict[str, Any], theirs: PageContent, **_: Any) -> DimensionResult:
    """Title, meta description, H1 and schema presence — ours vs theirs."""
    our_title = str(ours.get("meta_title") or ours.get("title") or "").strip()
    our_meta = str(ours.get("meta_description") or "").strip()
    our_h1 = [h for h in (ours.get("h1s") or []) if str(h).strip()] or (
        [our_title] if our_title else []
    )
    our_schema = ours.get("schema") or ours.get("schema_suggestion") or ours.get("schema_types")
    have_schema = bool(our_schema)

    checks: list[tuple[str, bool, bool]] = [
        ("title", bool(our_title), bool(theirs.title)),
        ("meta description", bool(our_meta), bool(theirs.meta_description)),
        ("H1", bool(our_h1), bool(theirs.h1s)),
        ("schema", have_schema, bool(theirs.schema_types)),
    ]
    ours_present = [name for name, mine, _ in checks if mine]
    theirs_present = [name for name, _, yours in checks if yours]
    missing_but_they_have = [
        name for name, mine, yours in checks if yours and not mine
    ]

    score = _ratio_score(len(ours_present), len(theirs_present) or 1)
    recs = []
    for name in missing_but_they_have:
        recs.append(f"Add a {name} — the competitor has one and we do not.")
    if our_title and len(our_title) > 60:
        recs.append(f"Our title is {len(our_title)} characters; it will truncate in results.")
    if our_meta and not (110 <= len(our_meta) <= 160):
        recs.append(
            f"Our meta description is {len(our_meta)} characters; aim for 110-160."
        )
    return DimensionResult(
        "onpage_seo", "On-page SEO elements", True,
        score=score,
        ours=", ".join(ours_present) or "none",
        theirs=", ".join(theirs_present) or "none",
        detail=f"We have {len(ours_present)} of 4 on-page elements; they have {len(theirs_present)}.",
        gaps=missing_but_they_have,
        recommendations=recs,
    )


# Order matters: this is the order the report and the UI render them in.
DIMENSIONS = (
    keyword_coverage,
    topic_gaps,
    content_depth,
    heading_structure,
    readability,
    onpage_seo,
)

# Weights for the overall score. Gaps and keyword coverage matter most for a
# service page; readability is a tiebreaker, not the point.
DIMENSION_WEIGHTS: dict[str, float] = {
    "keyword_coverage": 1.5,
    "topic_gaps": 1.5,
    "content_depth": 1.0,
    "heading_structure": 1.0,
    "readability": 0.5,
    "onpage_seo": 1.0,
}


def compare_one(
    ours: dict[str, Any], theirs: PageContent, *, target_keywords: list[str] | None = None
) -> list[DimensionResult]:
    """Run every dimension. One dimension raising must not sink the comparison."""
    results: list[DimensionResult] = []
    for fn in DIMENSIONS:
        try:
            results.append(fn(ours, theirs, target_keywords=target_keywords or []))
        except Exception as exc:  # noqa: BLE001
            results.append(
                DimensionResult(
                    fn.__name__, fn.__name__.replace("_", " ").title(), False,
                    reason=f"Dimension failed: {type(exc).__name__}",
                )
            )
    return results
