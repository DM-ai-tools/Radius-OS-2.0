"""Lightweight OpenRouter LLM — post-cleaning relevance check + cluster/intent/funnel."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.config import get_settings
from app.integrations.llm import _openrouter_chat
from app.logging_config import get_logger
from app.services.keyword_clustering import (
    _content_type,
    _detect_funnel,
    _detect_intent,
    _modifier_bucket,
    _norm,
    _slug,
)
from app.services.keyword_relevance import evaluate_keyword

log = get_logger("keyword_llm_clustering")

DEFAULT_MODEL = "google/gemini-2.5-flash"

_SYSTEM = """\
You are an SEO keyword analyst. You receive keywords that already passed rule-based
cleaning. Your job:

1. **Relevance check** — drop keywords clearly unrelated to the client's business.
2. **Cluster** — group remaining keywords into topical clusters (one cluster = one page).
3. **Classify** — assign intent and funnel to each cluster AND each keyword.

**Intent** (search intent): informational | commercial | transactional | navigational
**Funnel** (buyer stage): TOFU (awareness) | MOFU (consideration) | BOFU (decision)

Return valid JSON only:
{
  "dropped": [{"keyword": "...", "reason": "..."}],
  "clusters": [{
    "name": "Human-readable cluster name",
    "intent": "informational|commercial|transactional|navigational",
    "funnel": "TOFU|MOFU|BOFU",
    "content_type": "guide|service page|comparison|landing page|etc",
    "primary_keyword": "...",
    "keywords": [{
      "keyword": "exact string from input",
      "role": "Primary|Secondary|Supporting",
      "intent": "informational|commercial|transactional|navigational",
      "funnel": "TOFU|MOFU|BOFU"
    }]
  }],
  "orphans": [{"keyword": "...", "notes": "why unclustered"}]
}

Rules:
- Use ONLY keywords from the input — never invent new keywords.
- Every input keyword must appear exactly once in clusters, dropped, or orphans.
- Prefer specific service keywords as Primary over generic head terms.
- Do not copy or invent volume/difficulty — metrics are attached separately.
"""


def _parse_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _compact_rows(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        kw = str(row.get("keyword") or "").strip()
        if not kw:
            continue
        vol = row.get("volume")
        kd = row.get("difficulty")
        seed = row.get("seed") or ""
        parts = [kw]
        if vol is not None:
            parts.append(f"vol={vol}")
        if kd is not None:
            parts.append(f"kd={kd}")
        if seed:
            parts.append(f"seed={seed}")
        lines.append(" | ".join(parts))
    return lines


def _chunk_rows(rows: list[dict[str, Any]], max_per_chunk: int) -> list[list[dict[str, Any]]]:
    if len(rows) <= max_per_chunk:
        return [rows]
    by_seed: dict[str, list[dict[str, Any]]] = {}
    no_seed: list[dict[str, Any]] = []
    for row in rows:
        seed = str(row.get("seed") or "").strip()
        if seed:
            by_seed.setdefault(seed, []).append(row)
        else:
            no_seed.append(row)

    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = []

    for seed in sorted(by_seed):
        group = by_seed[seed]
        if len(group) > max_per_chunk:
            flush()
            for i in range(0, len(group), max_per_chunk):
                chunks.append(group[i : i + max_per_chunk])
            continue
        if len(current) + len(group) > max_per_chunk:
            flush()
        current.extend(group)
    if no_seed:
        for row in no_seed:
            if len(current) >= max_per_chunk:
                flush()
            current.append(row)
    flush()
    return chunks or [rows]


def _user_message(
    rows: list[dict[str, Any]],
    *,
    brand_name: str | None,
    domain: str | None,
    products: list[str] | None,
    seeds: list[str] | None,
    chunk_index: int,
    chunk_total: int,
) -> str:
    lines = _compact_rows(rows)
    header = [
        f"Brand: {brand_name or '(not provided)'}",
        f"Domain: {domain or '(not provided)'}",
        f"Services/products: {', '.join(products or []) or '(not provided)'}",
        f"Seeds: {', '.join(seeds or []) or '(not provided)'}",
        f"Batch {chunk_index}/{chunk_total} — {len(lines)} keywords:",
        "",
    ]
    return "\n".join(header + lines)


async def _call_llm(
    rows: list[dict[str, Any]],
    *,
    brand_name: str | None,
    domain: str | None,
    products: list[str] | None,
    seeds: list[str] | None,
    chunk_index: int,
    chunk_total: int,
    model: str,
) -> dict[str, Any]:
    user = _user_message(
        rows,
        brand_name=brand_name,
        domain=domain,
        products=products,
        seeds=seeds,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
    )
    try:
        raw = await _openrouter_chat(
            system=_SYSTEM,
            user=user,
            model=model,
            max_tokens=8192,
        )
    except Exception as exc:
        log.warning("llm_cluster_call_failed", error=str(exc), chunk=chunk_index)
        return {"dropped": [], "clusters": [], "orphans": [], "error": str(exc)}

    parsed = _parse_json(raw or "")
    if not parsed:
        return {"dropped": [], "clusters": [], "orphans": [], "error": "parse_failed"}
    return parsed


def _merge_chunk_results(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "dropped": [],
        "clusters": [],
        "orphans": [],
        "errors": [],
    }
    for chunk in chunks:
        merged["dropped"].extend(chunk.get("dropped") or [])
        merged["clusters"].extend(chunk.get("clusters") or [])
        merged["orphans"].extend(chunk.get("orphans") or [])
        if chunk.get("error"):
            merged["errors"].append(str(chunk["error"]))
    return merged


async def llm_cluster_keywords(
    cleaned: list[dict[str, Any]],
    *,
    brand_name: str | None = None,
    domain: str | None = None,
    products: list[str] | None = None,
    seeds: list[str] | None = None,
    relevance_context: Any | None = None,
    max_per_chunk: int = 100,
    max_concurrent: int = 3,
    model: str | None = None,
) -> dict[str, Any]:
    """Run lightweight OpenRouter model on all cleaned keywords."""
    settings = get_settings()
    use_model = model or getattr(settings, "keyword_cluster_model", None) or DEFAULT_MODEL

    if settings.use_mock_llm or not settings.openrouter_api_key:
        return {
            "dropped": [],
            "clusters": [],
            "orphans": [],
            "model": use_model,
            "skipped": "mock_or_no_key",
        }

    chunks = _chunk_rows(cleaned, max_per_chunk)
    sem = asyncio.Semaphore(max_concurrent)
    results: list[dict[str, Any]] = []

    async def _run(i: int, batch: list[dict[str, Any]]) -> None:
        async with sem:
            results.append(
                await _call_llm(
                    batch,
                    brand_name=brand_name,
                    domain=domain,
                    products=products,
                    seeds=seeds,
                    chunk_index=i + 1,
                    chunk_total=len(chunks),
                    model=use_model,
                )
            )

    await asyncio.gather(*[_run(i, batch) for i, batch in enumerate(chunks)])

    merged = _merge_chunk_results(results)
    report = build_cluster_report_from_llm(
        merged,
        cleaned,
        relevance_context=relevance_context,
        model=use_model,
        chunks_processed=len(chunks),
    )
    return report


def build_cluster_report_from_llm(
    llm_payload: dict[str, Any],
    cleaned: list[dict[str, Any]],
    *,
    relevance_context: Any | None = None,
    model: str = DEFAULT_MODEL,
    chunks_processed: int = 1,
) -> dict[str, Any]:
    """Ground LLM cluster output against cleaned keyword metrics."""
    by_kw = {_norm(str(r.get("keyword") or "")): r for r in cleaned}
    input_keys = set(by_kw)

    dropped_llm = [
        d
        for d in (llm_payload.get("dropped") or [])
        if isinstance(d, dict) and d.get("keyword")
    ]
    dropped_keys = {_norm(str(d.get("keyword") or "")) for d in dropped_llm}

    clusters_out: list[dict[str, Any]] = []
    ungrounded_dropped: list[dict[str, Any]] = []
    assigned: set[str] = set()

    for c in llm_payload.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        kws_in = c.get("keywords") or []
        fixed: list[dict[str, Any]] = []
        for i, item in enumerate(kws_in if isinstance(kws_in, list) else []):
            if isinstance(item, str):
                item = {"keyword": item}
            if not isinstance(item, dict):
                continue
            kw = _norm(str(item.get("keyword") or ""))
            if not kw:
                continue
            src = by_kw.get(kw)
            if src is None and relevance_context is not None:
                relevant, reason, _evidence = evaluate_keyword(kw, relevance_context)
                if not relevant:
                    ungrounded_dropped.append(
                        {"keyword": kw, "reason": reason, "cluster": c.get("name")}
                    )
                    continue
            role = str(item.get("role") or ("Primary" if i == 0 else "Supporting"))
            item_intent = str(
                item.get("intent") or (src or {}).get("intent") or _detect_intent(kw)
            ).lower()
            item_funnel = str(
                item.get("funnel") or (src or {}).get("funnel") or _detect_funnel(kw, item_intent)
            ).upper()
            fixed.append(
                {
                    "keyword": kw or item.get("keyword"),
                    "volume": (src or {}).get("volume"),
                    "difficulty": (src or {}).get("difficulty"),
                    "cpc": (src or {}).get("cpc"),
                    "intent": item_intent,
                    "funnel": item_funnel,
                    "role": role,
                    "opportunity_score": (src or {}).get("opportunity_score"),
                    "ungrounded": src is None,
                }
            )
            assigned.add(kw)

        if not fixed:
            continue

        primary = next((k for k in fixed if k.get("role") == "Primary"), fixed[0])
        intent = str(c.get("intent") or primary.get("intent") or _detect_intent(str(primary.get("keyword") or ""))).lower()
        funnel = str(c.get("funnel") or primary.get("funnel") or _detect_funnel(str(primary.get("keyword") or ""), intent)).upper()
        primary_kw = str(primary.get("keyword") or "")
        ctype = str(
            c.get("content_type") or _content_type(_modifier_bucket(primary_kw), intent)[0]
        )
        vols = [int(k["volume"]) for k in fixed if isinstance(k.get("volume"), (int, float))]

        clusters_out.append(
            {
                "name": str(c.get("name") or primary_kw or "Cluster").strip(),
                "intent": intent,
                "funnel": funnel,
                "recommended_content": c.get("recommended_content") or _content_type(None, intent)[1],
                "content_type": ctype,
                "recommended_url": c.get("recommended_url") or f"/blog/{_slug(primary_kw or 'cluster')}",
                "primary_keyword": primary.get("keyword"),
                "est_traffic": c.get("est_traffic") if c.get("est_traffic") is not None else (sum(vols) if vols else None),
                "keyword_count": len(fixed),
                "avg_difficulty": None,
                "best_score": max((k.get("opportunity_score") or 0) for k in fixed),
                "competitor_domains": [],
                "keywords": fixed[:15],
            }
        )

    orphans: list[dict[str, Any]] = []
    for o in llm_payload.get("orphans") or []:
        if isinstance(o, dict) and o.get("keyword"):
            kw = _norm(str(o.get("keyword") or ""))
            orphans.append(
                {
                    "keyword": o.get("keyword"),
                    "notes": o.get("notes") or "orphan",
                    "volume": (by_kw.get(kw) or {}).get("volume"),
                    "intent": o.get("intent"),
                    "funnel": o.get("funnel"),
                }
            )
            assigned.add(kw)
        elif isinstance(o, str):
            kw = _norm(o)
            orphans.append({"keyword": o, "notes": "orphan", "volume": (by_kw.get(kw) or {}).get("volume")})
            assigned.add(kw)

    # Keywords LLM missed — treat as orphans rather than silently dropping
    missing = input_keys - assigned - dropped_keys
    for kw in sorted(missing):
        src = by_kw.get(kw) or {}
        orphans.append(
            {
                "keyword": src.get("keyword") or kw,
                "notes": "not assigned by LLM",
                "volume": src.get("volume"),
                "intent": src.get("intent"),
                "funnel": src.get("funnel"),
            }
        )

    roadmap = [
        {
            "priority": i + 1,
            "cluster": c["name"],
            "content_type": c["content_type"],
            "target_keyword": c["primary_keyword"],
            "est_traffic": c.get("est_traffic"),
            "intent": c.get("intent"),
            "funnel": c.get("funnel"),
        }
        for i, c in enumerate(clusters_out[:20])
    ]

    clusters_out.sort(key=lambda c: c.get("best_score") or 0, reverse=True)

    return {
        "clusters": clusters_out,
        "orphans": orphans[:30],
        "content_roadmap": roadmap,
        "llm_dropped": dropped_llm[:50],
        "llm_invented_dropped": ungrounded_dropped[:20],
        "llm_cluster_audit": {
            "model": model,
            "input_count": len(cleaned),
            "clusters_created": len(clusters_out),
            "llm_dropped_count": len(dropped_llm),
            "orphan_count": len(orphans),
            "chunks_processed": chunks_processed,
            "errors": llm_payload.get("errors") or [],
        },
    }
