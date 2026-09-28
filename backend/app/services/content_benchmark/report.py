"""Assemble a benchmark report: per competitor, overall, and what to do about it.

A competitor whose content could not be retrieved appears in the report as a
failed row with its reason, and the rest of the report is unaffected — the point
of the report is the comparison it *can* make.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.logging_config import get_logger
from app.services.content_benchmark.dimensions import (
    DIMENSION_WEIGHTS,
    compare_one,
)
from app.services.content_benchmark.fetch import (
    PageContent,
    gather_competitor_content,
    word_count,
)

log = get_logger("content_benchmark.report")

REPORT_VERSION = "content_benchmark_v1"

_STOP = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with"}


def service_terms(service_name: str, keywords: list[str] | None = None) -> set[str]:
    """Words that identify this service, used to pick which competitor page to compare."""
    blob = " ".join([str(service_name or ""), *(str(k) for k in (keywords or []))])
    return {
        t for t in re.findall(r"[a-z0-9]+", blob.lower())
        if t not in _STOP and len(t) > 2
    }


def _weighted_score(dimensions: list[dict[str, Any]]) -> int | None:
    """Overall score from the dimensions that could actually be measured."""
    total = 0.0
    weight_sum = 0.0
    for dim in dimensions:
        if not dim.get("measurable") or dim.get("score") is None:
            continue
        weight = DIMENSION_WEIGHTS.get(dim["key"], 1.0)
        total += float(dim["score"]) * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None
    return int(round(total / weight_sum))


def _verdict(score: int | None) -> str:
    if score is None:
        return "not measurable"
    if score >= 75:
        return "we are ahead"
    if score >= 55:
        return "we are slightly ahead"
    if score >= 45:
        return "roughly level"
    if score >= 25:
        return "they are ahead"
    return "they are well ahead"


def prioritise_recommendations(
    per_competitor: list[dict[str, Any]], *, limit: int = 12
) -> list[dict[str, Any]]:
    """One ranked, de-duplicated action list across every competitor.

    Ranked by how weak we are on the dimension and how many competitors raise the
    same point — a gap two competitors both exploit outranks one competitor's
    stylistic edge.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in per_competitor:
        if not row.get("ok"):
            continue
        for dim in row.get("dimensions") or []:
            if not dim.get("measurable"):
                continue
            weight = DIMENSION_WEIGHTS.get(dim["key"], 1.0)
            # Weakness: 0 when we are dominant, 1 when we are beaten outright.
            weakness = max(0.0, (100 - float(dim.get("score") or 0)) / 100)
            for text in dim.get("recommendations") or []:
                key = re.sub(r"\s+", " ", text.strip().lower())[:120]
                entry = buckets.setdefault(
                    key,
                    {
                        "recommendation": text.strip(),
                        "dimension": dim["key"],
                        "raised_by": [],
                        "weight": weight,
                        "weakness": weakness,
                    },
                )
                entry["raised_by"].append(row.get("name") or row.get("domain") or "?")
                entry["weakness"] = max(entry["weakness"], weakness)

    ranked = sorted(
        buckets.values(),
        key=lambda e: -(e["weakness"] * e["weight"] * (1 + 0.5 * (len(e["raised_by"]) - 1))),
    )
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(ranked[:limit], start=1):
        out.append(
            {
                "priority": index,
                "recommendation": entry["recommendation"],
                "dimension": entry["dimension"],
                "raised_by": sorted(set(entry["raised_by"])),
            }
        )
    return out


def _collect_gaps(per_competitor: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Content gaps, de-duplicated, with who exploits each."""
    gaps: dict[str, dict[str, Any]] = {}
    for row in per_competitor:
        if not row.get("ok"):
            continue
        for dim in row.get("dimensions") or []:
            for gap in dim.get("gaps") or []:
                key = re.sub(r"\s+", " ", str(gap).strip().lower())[:120]
                if not key:
                    continue
                entry = gaps.setdefault(
                    key,
                    {"gap": str(gap).strip(), "dimension": dim["key"], "competitors": []},
                )
                entry["competitors"].append(row.get("name") or row.get("domain") or "?")
    ordered = sorted(gaps.values(), key=lambda g: -len(set(g["competitors"])))
    for entry in ordered:
        entry["competitors"] = sorted(set(entry["competitors"]))
    return ordered[:25]


async def run_benchmark(
    *,
    service_id: str,
    service_name: str,
    our_content: dict[str, Any] | None,
    competitors: list[dict[str, Any]],
    target_keywords: list[str] | None = None,
    snapshots: list[dict[str, Any]] | None = None,
    pasted: dict[str, str] | None = None,
    allow_live: bool = True,
) -> dict[str, Any]:
    """Benchmark our content for one service against its selected competitors."""
    generated_at = datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "version": REPORT_VERSION,
        "service_id": service_id,
        "service_name": service_name,
        "generated_at": generated_at,
        "competitors": [],
        "overall": None,
        "gaps": [],
        "recommendations": [],
        "notes": [],
    }

    if not competitors:
        base["notes"].append(
            "No competitors are selected for this service. Add competitors to benchmark against."
        )
        return base

    ours = dict(our_content or {})
    our_words = word_count(
        ours.get("page_markdown") or ours.get("markdown") or ours.get("body") or ""
    )
    if not ours or our_words == 0:
        base["notes"].append(
            "No generated content for this service yet — run content production first. "
            "Competitor content was still retrieved, so the gaps below are what they cover."
        )

    terms = service_terms(service_name, target_keywords)
    contents: list[PageContent] = await gather_competitor_content(
        competitors,
        snapshots=snapshots,
        service_terms=terms,
        pasted=pasted,
        allow_live=allow_live,
    )

    per_competitor: list[dict[str, Any]] = []
    for content in contents:
        row: dict[str, Any] = {
            "competitor_id": content.competitor_id,
            "name": content.name,
            "domain": content.domain,
            "url": content.url,
            "source": content.source,
            "ok": content.ok,
            "error": content.error,
            "content": content.to_dict(),
            "dimensions": [],
            "score": None,
            "verdict": "not retrieved",
        }
        if not content.ok:
            row["verdict"] = "content unavailable"
            per_competitor.append(row)
            continue
        dims = [d.to_dict() for d in compare_one(ours, content, target_keywords=target_keywords)]
        row["dimensions"] = dims
        row["score"] = _weighted_score(dims)
        row["verdict"] = _verdict(row["score"])
        if not content.depth_available:
            row.setdefault("warnings", []).append(
                f"Depth and readability unavailable — content came from {content.source}."
            )
        per_competitor.append(row)

    base["competitors"] = per_competitor
    scored = [r["score"] for r in per_competitor if r.get("score") is not None]
    retrieved = [r for r in per_competitor if r.get("ok")]
    failed = [r for r in per_competitor if not r.get("ok")]

    base["overall"] = {
        "score": int(round(sum(scored) / len(scored))) if scored else None,
        "verdict": _verdict(int(round(sum(scored) / len(scored))) if scored else None),
        "competitors_compared": len(retrieved),
        "competitors_failed": len(failed),
        "our_word_count": our_words,
    }
    base["gaps"] = _collect_gaps(per_competitor)
    base["recommendations"] = prioritise_recommendations(per_competitor)

    if failed:
        base["notes"].append(
            f"{len(failed)} of {len(per_competitor)} competitors could not be retrieved: "
            + "; ".join(f"{r['name'] or r['domain']} ({r['error']})" for r in failed[:4])
        )
    if retrieved and not any(r["content"].get("depth_available") for r in retrieved):
        base["notes"].append(
            "No competitor body text was available, so depth and readability were skipped. "
            "Allow a live fetch or paste competitor content to score those."
        )
    log.info(
        "benchmark_complete",
        service_id=service_id,
        compared=len(retrieved),
        failed=len(failed),
        overall=(base["overall"] or {}).get("score"),
    )
    return base
