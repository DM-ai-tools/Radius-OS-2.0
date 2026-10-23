"""Anthropic Claude skill path — post-cleaning relevance check + cluster/intent/funnel."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from app.config import get_settings
from app.integrations.llm import keyword_analysis_chat, keyword_analysis_ready
from app.logging_config import get_logger
from app.services.keyword_cluster_roles import (
    RoleContext,
    finalize_cluster,
    label_keyword,
    normalize_provider_intent,
)
from app.services.keyword_cluster_structure import (
    SPLIT_THRESHOLD,
    split_name,
    split_oversized,
)
from app.services.keyword_clustering import (
    _ORPHAN_LABEL_FIELDS,
    _content_type,
    _grouping_funnel,
    _grouping_intent,
    _modifier_bucket,
    _norm,
    _slug,
)
from app.services.keyword_relevance import evaluate_keyword

log = get_logger("keyword_llm_clustering")

DEFAULT_MODEL = "claude-sonnet-5"

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
- Group by meaning, search intent, and topic. Shared words alone are not a cluster.
- Keep informational, commercial, and transactional queries in separate clusters.
- Keep TOFU, MOFU, and BOFU queries in separate clusters. One cluster is one intent and one funnel.
- A long-tail keyword can belong with its topic even when it does not repeat the service name.
- A keyword that does not belong with the others stays in orphans, as its own topic.
- The primary keyword and roles you suggest are advisory: the primary and roles are
  decided afterwards from measured metrics, so focus on grouping and on each keyword's
  intent and funnel.
- Do not copy or invent volume, CPC, difficulty, or trends.
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
        raw = await keyword_analysis_chat(
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
    role_context: RoleContext | None = None,
) -> dict[str, Any]:
    """Run Anthropic Claude Sonnet on all cleaned keywords."""
    settings = get_settings()
    use_model = model or settings.skill_model or DEFAULT_MODEL

    if not keyword_analysis_ready():
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
    run_cache: dict[str, dict[str, Any]] = {}

    async def _run(i: int, batch: list[dict[str, Any]]) -> None:
        async with sem:
            user = _user_message(
                batch,
                brand_name=brand_name,
                domain=domain,
                products=products,
                seeds=seeds,
                chunk_index=i + 1,
                chunk_total=len(chunks),
            )
            digest = hashlib.sha256(f"{use_model}|{user}".encode()).hexdigest()
            cached = run_cache.get(digest)
            if cached is not None:
                log.info("keyword_cluster_cache_hit", chunk=i + 1)
                results.append(cached)
                return
            parsed = await _call_llm(
                batch,
                brand_name=brand_name,
                domain=domain,
                products=products,
                seeds=seeds,
                chunk_index=i + 1,
                chunk_total=len(chunks),
                model=use_model,
            )
            run_cache[digest] = parsed
            results.append(parsed)

    await asyncio.gather(*[_run(i, batch) for i, batch in enumerate(chunks)])

    merged = _merge_chunk_results(results)
    report = build_cluster_report_from_llm(
        merged,
        cleaned,
        relevance_context=relevance_context,
        model=use_model,
        chunks_processed=len(chunks),
        role_context=role_context
        or RoleContext.build(
            cleaned, brand_name=brand_name, domain=domain, relevance_context=relevance_context
        ),
    )
    return report


def build_cluster_report_from_llm(
    llm_payload: dict[str, Any],
    cleaned: list[dict[str, Any]],
    *,
    relevance_context: Any | None = None,
    model: str = DEFAULT_MODEL,
    chunks_processed: int = 1,
    role_context: RoleContext | None = None,
) -> dict[str, Any]:
    """Ground LLM cluster output against cleaned keyword metrics.

    The model groups keywords and labels each one. Its labels are kept as one
    source among three (``llm_intent``/``llm_funnel``); the primary, the roles and
    the final labels come from ``keyword_cluster_roles.finalize_cluster``, the
    same decider the deterministic path uses.
    """
    by_kw = {_norm(str(r.get("keyword") or "")): r for r in cleaned}
    ctx = role_context or RoleContext.build(cleaned, relevance_context=relevance_context)
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
    split_originals = 0
    clusters_from_splits = 0
    oversized_unsplit = 0

    def _labels_for(member: dict[str, Any]) -> tuple[str, str]:
        src = by_kw.get(_norm(str(member.get("keyword") or "")))
        labels = label_keyword(
            src or {"keyword": member.get("keyword")},
            brand_tokens=ctx.brand_tokens,
            llm_intent=member.get("llm_intent"),
            llm_funnel=member.get("llm_funnel"),
        )
        return labels["intent"], labels["funnel"]

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
            if not kw or kw in assigned:
                continue
            src = by_kw.get(kw)
            if src is None and relevance_context is not None:
                relevant, reason, _evidence = evaluate_keyword(kw, relevance_context)
                if not relevant:
                    ungrounded_dropped.append(
                        {"keyword": kw, "reason": reason, "cluster": c.get("name")}
                    )
                    continue
            # One page is one intent and one funnel. The model's cluster is
            # split on both before it is named.
            group_intent = str(item.get("intent") or _grouping_intent(kw, src or {})).lower()
            llm_funnel = str(item.get("funnel") or "").strip().upper()
            if llm_funnel not in ("TOFU", "MOFU", "BOFU"):
                llm_funnel = _grouping_funnel(kw, src or {}, group_intent)
            fixed.append(
                {
                    "keyword": kw or item.get("keyword"),
                    "volume": (src or {}).get("volume") if src else None,
                    "difficulty": (src or {}).get("difficulty") if src else None,
                    "cpc": (src or {}).get("cpc") if src else None,
                    "opportunity_score": (src or {}).get("opportunity_score"),
                    "ungrounded": src is None,
                    # The model's own labels: one input to the label decision.
                    "llm_intent": normalize_provider_intent(item.get("intent")),
                    "llm_funnel": llm_funnel if llm_funnel in ("TOFU", "MOFU", "BOFU") else None,
                    "_group_focus": f"{group_intent}|{llm_funnel}",
                }
            )
            assigned.add(kw)

        if not fixed:
            continue

        by_focus: dict[str, list[dict[str, Any]]] = {}
        for item in fixed:
            by_focus.setdefault(str(item.pop("_group_focus") or "informational|TOFU"), []).append(item)
        base_name = str(c.get("name") or "").strip()
        # Size discipline: a part over SPLIT_THRESHOLD is split by modifier
        # pattern, then intent, then funnel stage (same rule as the rule path).
        decided = []
        for group_label, members in by_focus.items():
            parts = (
                split_oversized(members, _labels_for) if len(members) > SPLIT_THRESHOLD else [(members, [])]
            )
            if len(members) > SPLIT_THRESHOLD:
                if len(parts) > 1:
                    split_originals += 1
                    clusters_from_splits += len(parts)
                else:
                    oversized_unsplit += 1
            for part, path in parts:
                decided.append(
                    (group_label, finalize_cluster({"keywords": part}, ctx), path, len(members))
                )
        for group_label, d, path, whole_size in decided:
            members = d["keywords"]
            intent, funnel = d["intent"], d["funnel"]
            primary_kw = str(d["primary_keyword"] or "")
            ctype = str(
                c.get("content_type") or _content_type(_modifier_bucket(primary_kw), intent)[0]
            )
            vols = [int(k["volume"]) for k in members if isinstance(k.get("volume"), (int, float))]
            difficulties = [
                float(k["difficulty"]) for k in members if isinstance(k.get("difficulty"), (int, float))
            ]
            name = base_name or primary_kw or "Cluster"
            if len(by_focus) > 1:
                # Parts are told apart by the stage and intent that split them.
                name = f"{name} ({funnel} · {intent})" if not path else f"{name} ({group_label})"
            whole_name = name
            name = split_name(name, path)
            clusters_out.append(
                {
                    "name": name,
                    "intent": intent,
                    "funnel": funnel,
                    "recommended_content": c.get("recommended_content") or _content_type(None, intent)[1],
                    "content_type": ctype,
                    "recommended_url": c.get("recommended_url") or f"/blog/{_slug(primary_kw or 'cluster')}",
                    "primary_keyword": d["primary_keyword"],
                    "est_traffic": sum(vols) if vols else None,
                    "keyword_count": len(members),
                    "avg_difficulty": (
                        round(sum(difficulties) / len(difficulties), 1) if difficulties else None
                    ),
                    "best_score": max((k.get("opportunity_score") or 0) for k in members),
                    "competitor_domains": [],
                    "intent_distribution": d["intent_distribution"],
                    "funnel_distribution": d["funnel_distribution"],
                    "role_counts": d["role_counts"],
                    "label_confidence": d["label_confidence"],
                    "primary_selection": d["primary_selection"],
                    # Set when this cluster is one part of an oversized group.
                    "split_from": whole_name if path else None,
                    "split_by": [dimension for dimension, _v in path] if path else None,
                    "split_from_size": whole_size if path else None,
                    "keywords": members,
                }
            )

    def _orphan_labels(kw: str, llm_intent: Any = None, llm_funnel: Any = None) -> dict[str, Any]:
        # Orphans are labelled by the same decider as cluster members.
        labels = label_keyword(
            by_kw.get(kw) or {"keyword": kw},
            brand_tokens=ctx.brand_tokens,
            llm_intent=llm_intent,
            llm_funnel=llm_funnel,
        )
        return {k: labels[k] for k in _ORPHAN_LABEL_FIELDS}

    orphans: list[dict[str, Any]] = []
    for o in llm_payload.get("orphans") or []:
        if isinstance(o, dict) and o.get("keyword"):
            kw = _norm(str(o.get("keyword") or ""))
            orphans.append(
                {
                    "keyword": o.get("keyword"),
                    "notes": o.get("notes") or "orphan",
                    "volume": (by_kw.get(kw) or {}).get("volume"),
                    **_orphan_labels(kw, o.get("intent"), o.get("funnel")),
                    "exclusion_reason": "no_topic_fit",
                }
            )
            assigned.add(kw)
        elif isinstance(o, str):
            kw = _norm(o)
            orphans.append(
                {
                    "keyword": o,
                    "notes": "orphan",
                    "volume": (by_kw.get(kw) or {}).get("volume"),
                    **_orphan_labels(kw),
                    "exclusion_reason": "no_topic_fit",
                }
            )
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
                **_orphan_labels(kw),
                "exclusion_reason": "unassigned_by_model",
            }
        )

    # Real keywords the model judged irrelevant. They stay in the analysis as
    # orphans with that reason, so the accounting shows where they went.
    for d in dropped_llm:
        kw = _norm(str(d.get("keyword") or ""))
        if kw not in input_keys or kw in assigned:
            continue
        src = by_kw.get(kw) or {}
        orphans.append(
            {
                "keyword": src.get("keyword") or kw,
                "notes": str(d.get("reason") or "dropped by the model as not relevant"),
                "volume": src.get("volume"),
                **_orphan_labels(kw),
                "exclusion_reason": "dropped_for_relevance",
            }
        )
        assigned.add(kw)

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
        # Every orphan: the caller counts them for the keyword accounting and
        # caps only what it displays.
        "orphans": orphans,
        "content_roadmap": roadmap,
        "llm_dropped": dropped_llm[:50],
        "llm_invented_dropped": ungrounded_dropped[:20],
        "llm_invented_dropped_count": len(ungrounded_dropped),
        "size_discipline": {
            "split_threshold": SPLIT_THRESHOLD,
            # The small-cluster merge belongs to the rule path; the model groups
            # its own clusters.
            "merged_small_clusters": 0,
            "split_clusters": split_originals,
            "clusters_from_splits": clusters_from_splits,
            "oversized_unsplit": oversized_unsplit,
        },
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
