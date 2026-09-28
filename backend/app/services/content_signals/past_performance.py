"""Signal 1 — our own past content, from data the program already holds.

No external analytics. There is no GA4 or Search Console performance feed in
this system (verified: nothing ever writes ``gsc_pages`` / ``page_performance``),
so "what worked" is derived from the program's own record of what it produced
and what happened to it:

  * content_production_summary — pieces already drafted (keyword, page type,
    framework, length, angle)
  * publishing_summary        — what actually shipped
  * content_audit_summary     — dispositions on existing pages (keep / improve /
    consolidate / remove), which is this system's own judgement of what is
    carrying its weight
  * site_architecture_summary — URLs and topics already covered

Two jobs, both achievable from that:

  1. Cannibalisation guard — never commission a second page for a topic already
     covered. Covered topics are returned in ``avoids``.
  2. Format learning — favour the page types, lengths and angles that survived
     audit as "keep" rather than being marked thin or consolidated.

With no history at all the signal reports ``available=False`` and the generated
output says so, rather than pretending to have learned something.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.content_signals.base import SignalResult, unavailable

# content_audit dispositions that indicate a page earned its place.
_GOOD_DISPOSITIONS = {"keep", "optimize", "optimise", "improve", "expand"}
_BAD_DISPOSITIONS = {"consolidate", "remove", "prune", "redirect", "merge"}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _topic_key(value: Any) -> str:
    """Comparable topic key — word set, so word order and stopwords don't hide a dup."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", _norm(value)) if len(t) > 2]
    return " ".join(sorted(tokens))


def _rows(blob: Any, *keys: str) -> list[dict[str, Any]]:
    """Pull the first list-of-dicts found under any of `keys`."""
    if not isinstance(blob, dict):
        return []
    for key in keys:
        value = blob.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def covered_topics(
    *,
    production: dict[str, Any] | None = None,
    publishing: dict[str, Any] | None = None,
    architecture: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Topics this client already has a page for, with where each came from."""
    seen: dict[str, dict[str, str]] = {}

    def _add(keyword: Any, url: Any, source: str) -> None:
        key = _topic_key(keyword) or _topic_key(url)
        if not key or key in seen:
            return
        seen[key] = {
            "topic_key": key,
            "keyword": str(keyword or "").strip(),
            "url": str(url or "").strip(),
            "source": source,
        }

    for row in _rows(production, "pages", "drafts", "content", "items"):
        _add(row.get("keyword") or row.get("title"), row.get("url") or row.get("proposed_url"), "drafted")
    for row in _rows(publishing, "published", "pages", "items"):
        _add(row.get("keyword") or row.get("title"), row.get("url"), "published")
    for row in _rows(architecture, "pages", "nodes", "tree"):
        _add(row.get("keyword") or row.get("title"), row.get("url") or row.get("path"), "in site tree")
    return list(seen.values())


def format_track_record(audit: dict[str, Any] | None) -> dict[str, Any]:
    """Which page types held up under this system's own content audit."""
    good: dict[str, int] = {}
    bad: dict[str, int] = {}
    lengths: list[int] = []
    for row in _rows(audit, "pages", "items", "decisions", "audit"):
        disposition = _norm(row.get("disposition") or row.get("action"))
        page_type = _norm(row.get("page_type") or row.get("content_type")) or "unknown"
        words = row.get("words") or row.get("word_count")
        bucket = None
        if any(d in disposition for d in _GOOD_DISPOSITIONS):
            bucket = good
        elif any(d in disposition for d in _BAD_DISPOSITIONS):
            bucket = bad
        if bucket is None:
            continue
        bucket[page_type] = bucket.get(page_type, 0) + 1
        if bucket is good and isinstance(words, (int, float)) and words > 0:
            lengths.append(int(words))

    median_words = None
    if lengths:
        lengths.sort()
        median_words = lengths[len(lengths) // 2]
    return {
        "worked": sorted(good.items(), key=lambda kv: -kv[1]),
        "underperformed": sorted(bad.items(), key=lambda kv: -kv[1]),
        "median_words_of_kept_pages": median_words,
    }


def collect_past_performance(
    *,
    keyword: str = "",
    production: dict[str, Any] | None = None,
    publishing: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    architecture: dict[str, Any] | None = None,
    weight: str = "medium",
) -> SignalResult:
    covered = covered_topics(
        production=production, publishing=publishing, architecture=architecture
    )
    record = format_track_record(audit)
    has_history = bool(covered or record["worked"] or record["underperformed"])

    if not has_history:
        return unavailable(
            "past_performance",
            "No content history yet — nothing drafted, published or audited for this "
            "client, so no format or cannibalisation signal is available.",
            weight=weight,
        )

    notes: list[str] = []
    avoids: list[str] = []
    suggests: list[str] = []

    target_key = _topic_key(keyword)
    clash = next((c for c in covered if c["topic_key"] == target_key), None) if target_key else None
    if clash:
        notes.append(
            f"CANNIBALISATION RISK: {keyword!r} is already covered by "
            f"{clash['url'] or clash['keyword']!r} ({clash['source']}). "
            "Differentiate the angle or update the existing page instead."
        )
        avoids.append(f"duplicating existing page: {clash['url'] or clash['keyword']}")

    if covered:
        avoids.extend(
            f"already covered: {c['keyword'] or c['url']}" for c in covered[:20] if c is not clash
        )
        notes.append(f"{len(covered)} topic(s) already covered — avoid overlap.")

    if record["worked"]:
        top = ", ".join(f"{t} ({n})" for t, n in record["worked"][:3])
        suggests.append(f"formats that held up: {top}")
        notes.append(f"Formats that held up in audit: {top}.")
    if record["underperformed"]:
        weak = ", ".join(f"{t} ({n})" for t, n in record["underperformed"][:3])
        notes.append(f"Formats marked for consolidation/removal: {weak}.")
    if record["median_words_of_kept_pages"]:
        notes.append(
            f"Median length of pages kept at audit: "
            f"{record['median_words_of_kept_pages']} words."
        )

    return SignalResult(
        name="past_performance",
        available=True,
        weight=weight,
        data={
            "covered_topic_count": len(covered),
            "covered_topics": covered[:40],
            "cannibalisation_clash": clash,
            "track_record": record,
            "source": "internal content history (no external analytics)",
        },
        notes=notes,
        suggests=suggests,
        avoids=avoids,
    )
