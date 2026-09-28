"""Signals 2 and 3 — AI-detected and operator-selected competitors.

Both read what the program already stored. ``CompetitorProfile`` carries
``source`` (how it was found) and ``confirmed`` (the operator kept it), so the
AI-vs-selected split needs no new storage.

Priority is fixed by the spec: when the operator has selected competitors they
win. ``selected_only`` drops the AI-detected set entirely; otherwise selected
competitors lead and AI-detected ones fill in behind them.

Competitor page/keyword intel comes from Phase 4 and Phase 5 blobs already on
the profile (``competitive_landscape_summary``, ``search_demand_summary``), so
this signal issues no new provider calls.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.content_signals.base import SignalResult, unavailable


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _domain(value: Any) -> str:
    raw = _norm(value)
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    return raw.split("/")[0].removeprefix("www.")


def split_competitors(rows: list[dict[str, Any]] | None) -> tuple[list[dict], list[dict]]:
    """(operator_selected, ai_detected) from stored competitor records."""
    selected: list[dict[str, Any]] = []
    detected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        domain = _domain(row.get("domain") or row.get("url"))
        if not domain or domain in seen:
            continue
        seen.add(domain)
        record = {
            "name": str(row.get("name") or domain).strip(),
            "domain": domain,
            "url": str(row.get("url") or f"https://{domain}"),
            "source": str(row.get("source") or "").strip() or "unknown",
        }
        # `confirmed` is the operator keeping it; a manual source is inherently theirs.
        if row.get("confirmed") is True or "manual" in _norm(row.get("source")):
            record["selected_by"] = "operator"
            selected.append(record)
        else:
            record["selected_by"] = "ai"
            detected.append(record)
    return selected, detected


def _intel_for(domains: set[str], search_demand: dict[str, Any] | None) -> dict[str, Any]:
    """Topics/formats already harvested from competitor pages in Phase 5."""
    sd = dict(search_demand or {})
    pages = [p for p in (sd.get("competitor_page_keywords") or []) if isinstance(p, dict)]
    snapshots = [s for s in (sd.get("competitor_sites") or []) if isinstance(s, dict)]

    topics: list[str] = []
    coverage: list[dict[str, str]] = []

    def _add_coverage(domain: str, topic: str, how: str) -> None:
        topic = topic.strip()
        how = how.strip()
        if not topic or not how or len(coverage) >= 12:
            return
        coverage.append({"competitor": domain, "topic": topic, "how": how})

    for row in pages:
        domain = _domain(row.get("domain") or row.get("url"))
        if domains and domain not in domains:
            continue
        kw = str(row.get("keyword") or "").strip()
        if kw:
            topics.append(kw)
        how_bits: list[str] = []
        kind = str(row.get("page_kind") or row.get("kind") or "").strip()
        field = str(row.get("evidence_field") or "").strip()
        title = str(row.get("evidence_title") or row.get("title") or "").strip()
        if kind:
            how_bits.append(f"{kind} page")
        if field:
            how_bits.append(f"placed in {field}")
        if title:
            how_bits.append(f"title: {title}")
        if kw and how_bits:
            _add_coverage(domain, kw, "; ".join(how_bits))

    for snap in snapshots:
        domain = _domain(snap.get("domain") or snap.get("url"))
        if domains and domain not in domains:
            continue
        for page in (snap.get("pages") or [])[:6]:
            if not isinstance(page, dict):
                continue
            title = str(page.get("title") or "").strip()
            kind = str(page.get("kind") or "page").strip() or "page"
            h1s = page.get("h1s") or []
            h1 = str(h1s[0]).strip() if isinstance(h1s, list) and h1s else ""
            topic = title or h1
            if not topic:
                continue
            how = f"{kind} page"
            if h1 and h1.lower() != topic.lower():
                how += f"; H1: {h1}"
            _add_coverage(domain, topic, how)

    gap_keywords = [
        str(r.get("keyword") or "").strip()
        for r in (sd.get("keyword_dataset") or [])
        if isinstance(r, dict) and r.get("gap_flag") and r.get("keyword")
    ]
    return {
        "harvested_topics": list(dict.fromkeys(topics))[:40],
        "gap_keywords": list(dict.fromkeys(gap_keywords))[:30],
        "page_snapshots": len(snapshots),
        "coverage": coverage,
    }


def collect_selected_competitors(
    *,
    competitors: list[dict[str, Any]] | None = None,
    search_demand: dict[str, Any] | None = None,
    weight: str = "high",
) -> SignalResult:
    selected, _ = split_competitors(competitors)
    if not selected:
        return unavailable(
            "competitors_selected",
            "No operator-selected competitors. Add them to give them priority "
            "over AI-detected ones.",
            weight=weight,
        )
    intel = _intel_for({c["domain"] for c in selected}, search_demand)
    names = ", ".join(c["name"] for c in selected[:5])
    return SignalResult(
        name="competitors_selected",
        available=True,
        weight=weight,
        data={"competitors": selected, **intel, "priority": "operator-selected take precedence"},
        notes=[f"Operator-selected competitors ({len(selected)}): {names}."],
        suggests=[f"beat competitor angle: {t}" for t in intel["harvested_topics"][:5]],
    )


def collect_ai_competitors(
    *,
    competitors: list[dict[str, Any]] | None = None,
    search_demand: dict[str, Any] | None = None,
    selected_only: bool = False,
    weight: str = "medium",
) -> SignalResult:
    selected, detected = split_competitors(competitors)

    if selected_only:
        return unavailable(
            "competitors_ai",
            "selected_only mode: AI-detected competitors excluded by operator setting.",
            weight=weight,
        )
    if not detected:
        return unavailable(
            "competitors_ai",
            "No AI-detected competitors stored for this client.",
            weight=weight,
        )

    # Operator picks lead; detected ones fill in behind, never displacing them.
    demoted = bool(selected)
    intel = _intel_for({c["domain"] for c in detected}, search_demand)
    names = ", ".join(c["name"] for c in detected[:5])
    notes = [f"AI-detected competitors ({len(detected)}): {names}."]
    if demoted:
        notes.append(
            "Operator-selected competitors exist, so these are secondary context only."
        )
    return SignalResult(
        name="competitors_ai",
        available=True,
        weight=weight,
        data={
            "competitors": detected,
            **intel,
            "secondary_to_selected": demoted,
        },
        notes=notes,
        suggests=[f"competitor topic to beat: {t}" for t in intel["harvested_topics"][:5]],
    )
