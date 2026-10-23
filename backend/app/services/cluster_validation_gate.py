"""Phase 5 — post-clustering LLM coherence gate.

After deterministic clustering, each cluster is judged as a unit: do the
member keywords form one sensible topic for ONE page for THIS business?

Fail-closed: anything not explicitly kept is flagged
``cluster_coherence_ok=False`` / ``relevance_unverified=True`` so
``clusters_for_cdp`` and topic drafting skip it. Rejected clusters stay in the
report for audit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.integrations.llm import _parse_json_content, keyword_analysis_chat, keyword_analysis_ready
from app.logging_config import get_logger
from app.services.cache import cache_get_many, cache_set_many

log = get_logger("cluster_validation_gate")

VERDICTS = ("keep", "drop", "review")
ALL_DROP_GUARD_MIN_BATCH = 3

Judge = Callable[[str, str], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class ClusterVerdict:
    cluster_id: int
    verdict: str
    score: float
    reason: str


_SYSTEM = """\
You are an SEO cluster coherence judge for ONE business.

You are given that business's context and a numbered list of keyword clusters.
Each cluster is meant to become ONE page (service page or blog). Judge EACH
cluster independently.

A cluster is "keep" only if:
- The member keywords clearly share ONE topic a buyer would expect on a single page
- The primary keyword is a fair title for that topic
- The topic fits a service or content angle THIS business can own
- Mixing Google Ads / AdWords / PPC-search terms with Meta / Facebook / Instagram
  Ads terms is a drop (platform mismatch), even if both say "ads"

Use "drop" when keywords are unrelated soup, when the primary does not represent
the members, when the topic is for a different industry or a service this
business does not sell, or when intents collide so badly one page cannot serve them.

Use "review" only when genuinely ambiguous. Do not use "review" to avoid deciding.

Hard rules:
- Return EXACTLY one row for every id in the input list. Never skip an id.
- Never invent clusters or keywords.
- "score" is confidence 0.00-1.00 that this cluster is a coherent single-page topic.
- "reason" is at most 12 words, plain text.

Return JSON only, no markdown fences, exactly:
{"verdicts":[{"id":1,"verdict":"keep|drop|review","score":0.00,"reason":"<=12 words"}]}"""


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _sanitize(text: Any, *, limit: int = 80) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f`]", " ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def _provider_ready() -> bool:
    return keyword_analysis_ready()


async def _skill_judge(system: str, user: str) -> dict[str, Any] | None:
    """Anthropic skill judge with one retry on empty/unparseable responses."""
    settings = get_settings()
    last_raw: str | None = None
    for attempt in range(2):
        raw = await keyword_analysis_chat(
            system=system,
            user=user,
            model=settings.skill_model,
            max_tokens=settings.keyword_relevance_max_tokens,
        )
        last_raw = raw
        if raw and str(raw).strip():
            try:
                parsed = _parse_json_content(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        if attempt == 0:
            log.warning(
                "cluster_judge_retry",
                attempt=attempt + 1,
                raw_len=len(raw or ""),
            )
            await asyncio.sleep(1.0)
    log.warning(
        "cluster_judge_empty",
        raw_len=len(last_raw or ""),
        raw_head=(last_raw or "")[:120],
    )
    return None


def _coerce_score(raw: Any) -> float | None:
    if isinstance(raw, dict):
        raw = raw.get("score")
    if isinstance(raw, str):
        raw = raw.strip().rstrip("%")
        try:
            value = float(raw)
        except ValueError:
            return None
        if value > 1:
            value = value / 100
    elif isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1:
            value = value / 100
    else:
        return None
    return max(0.0, min(1.0, value))


def _context_block(
    *,
    client_name: str | None,
    primary_url: str,
    industry: str | None,
    services: list[str],
    cdd_keywords: list[str],
    geography: str | None,
) -> str:
    return "\n".join(
        [
            "BUSINESS",
            f"Name: {_sanitize(client_name, limit=120) or '(not provided)'}",
            f"Website: {_sanitize(primary_url, limit=120) or '(not provided)'}",
            f"Industry: {_sanitize(industry, limit=80) or '(not stated)'}",
            f"Market / geography: {_sanitize(geography, limit=80) or '(not stated)'}",
            "Services this business sells: "
            + (", ".join(_sanitize(s) for s in services[:20]) or "(none on file)"),
            "Client business keywords: "
            + (", ".join(_sanitize(k) for k in cdd_keywords[:25]) or "(none on file)"),
        ]
    )


def _cluster_summary(cluster: dict[str, Any], *, max_members: int = 8) -> str:
    primary = _sanitize(cluster.get("primary_keyword"), limit=120)
    name = _sanitize(cluster.get("name"), limit=120)
    intent = _sanitize(cluster.get("intent"), limit=40)
    ctype = _sanitize(cluster.get("content_type") or cluster.get("recommended_content"), limit=40)
    members: list[str] = []
    for row in cluster.get("keywords") or []:
        if not isinstance(row, dict):
            continue
        kw = _sanitize(row.get("keyword"), limit=80)
        if kw:
            members.append(kw)
        if len(members) >= max_members:
            break
    member_line = ", ".join(members) if members else "(no members)"
    return (
        f"name={name or '(unnamed)'} | primary={primary or '(none)'} | "
        f"intent={intent or '?'} | type={ctype or '?'} | members: {member_line}"
    )


def _build_user_message(context_block: str, clusters: list[tuple[int, dict[str, Any]]]) -> str:
    numbered = "\n".join(
        f"{cid}. {_cluster_summary(cluster)}" for cid, cluster in clusters
    )
    return (
        f"{context_block}\n\n"
        f"CANDIDATE CLUSTERS ({len(clusters)}) — judge every one, "
        f"return exactly {len(clusters)} rows:\n{numbered}"
    )


def _parse_verdicts(
    payload: Any,
    sent_ids: set[int],
) -> dict[int, ClusterVerdict]:
    if isinstance(payload, dict):
        rows = payload.get("verdicts") or payload.get("results") or payload.get("clusters")
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None
    if not isinstance(rows, list) or not rows:
        return {}

    out: dict[int, ClusterVerdict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id")
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if cid not in sent_ids:
            continue
        verdict = str(row.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            verdict = "review"
        score = _coerce_score(row.get("score"))
        if score is None:
            verdict, score = "review", 0.5
        out[cid] = ClusterVerdict(
            cluster_id=cid,
            verdict=verdict,
            score=score,
            reason=_sanitize(row.get("reason"), limit=160),
        )
    return out


def _decide(verdict: ClusterVerdict, *, threshold: float) -> str:
    if verdict.verdict == "review":
        return "review"
    if verdict.verdict == "keep":
        return "keep" if verdict.score >= threshold else "drop"
    return "drop"


def _mark_rejected(cluster: dict[str, Any], *, cause: str, reason: str = "") -> dict[str, Any]:
    return {
        **cluster,
        "cluster_coherence_ok": False,
        "relevance_unverified": True,
        "cluster_sense_verdict": "drop" if cause != "review" else "review",
        "cluster_sense_reason": reason or cause,
        "cluster_sense_cause": cause,
    }


def _mark_kept(cluster: dict[str, Any], verdict: ClusterVerdict) -> dict[str, Any]:
    return {
        **cluster,
        "cluster_coherence_ok": True,
        "cluster_sense_verdict": "keep",
        "cluster_sense_reason": verdict.reason,
        "cluster_sense_score": verdict.score,
    }


def _cache_key(client_id: str, context_fp: str, cluster: dict[str, Any], model: str) -> str:
    primary = _norm(cluster.get("primary_keyword"))
    members = sorted(
        _norm(k.get("keyword"))
        for k in (cluster.get("keywords") or [])
        if isinstance(k, dict) and k.get("keyword")
    )[:12]
    digest = hashlib.sha1(
        f"{primary}|{'|'.join(members)}|{model}".encode()
    ).hexdigest()[:20]
    return f"searchfit:clusterv:v1:{client_id}:{context_fp}:{digest}"


def _context_fingerprint(
    *, industry: str | None, services: list[str], cdd_keywords: list[str], geography: str | None
) -> str:
    blob = json.dumps(
        {
            "industry": _norm(industry),
            "services": sorted(_norm(s) for s in services),
            "cdd": sorted(_norm(k) for k in cdd_keywords),
            "geo": _norm(geography),
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


async def validate_clusters_coherence(
    clusters: list[dict[str, Any]],
    *,
    client_id: str,
    client_name: str | None = None,
    primary_url: str = "",
    industry: str | None = None,
    services: list[str] | None = None,
    cdd_keywords: list[str] | None = None,
    geography: str | None = None,
    caller: Judge | None = None,
    enabled: bool | None = None,
    batch_size: int | None = None,
    threshold: float | None = None,
    max_calls: int | None = None,
    deadline: float | None = None,
    concurrency: int | None = None,
    call_timeout: float | None = None,
    cache_ttl_seconds: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Judge every cluster. Returns ``(kept, rejected, audit)``.

    ``rejected`` includes drops and quarantine/review — they stay out of topic
    drafting. The full stamped list is also available via audit samples.
    """
    settings = get_settings()
    services = list(services or [])
    cdd_keywords = list(cdd_keywords or [])

    enabled = settings.cluster_validation_enabled if enabled is None else enabled
    batch_size = int(batch_size or settings.cluster_validation_batch_size)
    threshold = float(
        threshold if threshold is not None else settings.cluster_validation_threshold
    )
    max_calls = int(
        max_calls if max_calls is not None else settings.cluster_validation_max_calls
    )
    concurrency = int(concurrency or settings.cluster_validation_concurrency)
    call_timeout = float(
        call_timeout if call_timeout is not None else settings.cluster_validation_call_timeout
    )
    cache_ttl_seconds = int(
        cache_ttl_seconds
        if cache_ttl_seconds is not None
        else settings.cluster_validation_cache_days * 24 * 3600
    )

    audit: dict[str, Any] = {
        "gate": "llm_cluster_coherence_v1",
        "input_count": len(clusters),
        "judged": 0,
        "kept": 0,
        "dropped": 0,
        "quarantined": 0,
        "cached": 0,
        "api_calls": 0,
        "batches": 0,
        "quarantined_by_cause": {},
        "errors": [],
        "sample": [],
        "skipped": None,
    }

    if not clusters:
        return [], [], audit

    valid_clusters = [c for c in clusters if isinstance(c, dict)]
    if len(valid_clusters) != len(clusters):
        audit["errors"].append("non_dict_clusters_skipped")

    if not enabled:
        audit["skipped"] = "disabled"
        return list(valid_clusters), [], audit

    if caller is None and not _provider_ready():
        audit["skipped"] = "no_provider"
        audit["quarantined"] = len(valid_clusters)
        audit["quarantined_by_cause"] = {"no_provider": len(valid_clusters)}
        rejected = [_mark_rejected(c, cause="no_provider") for c in valid_clusters]
        return [], rejected, audit

    judge: Judge = caller or _skill_judge
    model = settings.skill_model
    context_fp = _context_fingerprint(
        industry=industry, services=services, cdd_keywords=cdd_keywords, geography=geography
    )
    context_block = _context_block(
        client_name=client_name,
        primary_url=primary_url,
        industry=industry,
        services=services,
        cdd_keywords=cdd_keywords,
        geography=geography,
    )

    indexed = list(enumerate(valid_clusters, start=1))
    keys_by_id: dict[int, str] = {
        cid: _cache_key(client_id, context_fp, cluster, model) for cid, cluster in indexed
    }
    cached_raw = await cache_get_many(sorted(set(keys_by_id.values())))

    decided: dict[int, tuple[str, ClusterVerdict]] = {}
    pending: list[tuple[int, dict[str, Any]]] = []

    for cid, cluster in indexed:
        entry = cached_raw.get(keys_by_id[cid])
        if isinstance(entry, dict) and entry.get("verdict") in VERDICTS:
            score = _coerce_score(entry.get("score"))
            if score is not None:
                verdict = ClusterVerdict(
                    cluster_id=cid,
                    verdict=str(entry["verdict"]),
                    score=score,
                    reason=str(entry.get("reason") or ""),
                )
                decided[cid] = (_decide(verdict, threshold=threshold), verdict)
                audit["cached"] += 1
                continue
        pending.append((cid, cluster))

    batches: list[list[tuple[int, dict[str, Any]]]] = []
    for i in range(0, len(pending), max(1, batch_size)):
        batches.append(pending[i : i + batch_size])

    cache_writes: dict[str, dict[str, Any]] = {}
    sem = asyncio.Semaphore(max(1, concurrency))
    calls_made = 0
    calls_lock = asyncio.Lock()

    async def _run_batch(batch: list[tuple[int, dict[str, Any]]]) -> None:
        nonlocal calls_made
        async with calls_lock:
            if calls_made >= max_calls:
                for cid, _cluster in batch:
                    decided[cid] = (
                        "review",
                        ClusterVerdict(cid, "review", 0.5, "max_calls"),
                    )
                return
            if deadline is not None and time.monotonic() >= deadline:
                for cid, _cluster in batch:
                    decided[cid] = (
                        "review",
                        ClusterVerdict(cid, "review", 0.5, "time_budget"),
                    )
                return
            calls_made += 1
            audit["api_calls"] = calls_made
            audit["batches"] += 1

        sent_ids = {cid for cid, _ in batch}
        user = _build_user_message(context_block, batch)
        payload: dict[str, Any] | None = None
        try:
            async with sem:
                payload = await asyncio.wait_for(judge(_SYSTEM, user), timeout=call_timeout)
        except Exception as exc:  # noqa: BLE001
            audit["errors"].append(str(exc)[:200])
            payload = None

        # A missing reply is a budget block or an empty model body. The keywords
        # are already collected, so the batch stays. A real verdict still decides.
        if payload is None:
            audit["errors"].append("judge_unavailable")
            for cid, _cluster in batch:
                decided[cid] = (
                    "keep",
                    ClusterVerdict(cid, "keep", 1.0, "coherence check skipped"),
                )
            return

        parsed = _parse_verdicts(payload, sent_ids)
        # All-drop guard: likely confused model.
        if (
            len(batch) >= ALL_DROP_GUARD_MIN_BATCH
            and parsed
            and all(v.verdict == "drop" for v in parsed.values())
            and len(parsed) == len(batch)
        ):
            audit["errors"].append("all_drop_guard_triggered")
            for cid, _cluster in batch:
                decided[cid] = (
                    "review",
                    ClusterVerdict(cid, "review", 0.5, "all_drop_guard"),
                )
            return

        for cid, cluster in batch:
            verdict = parsed.get(cid)
            if verdict is None:
                decided[cid] = (
                    "review",
                    ClusterVerdict(cid, "review", 0.5, "missing_verdict"),
                )
                continue
            decision = _decide(verdict, threshold=threshold)
            decided[cid] = (decision, verdict)
            cache_writes[keys_by_id[cid]] = {
                "verdict": verdict.verdict,
                "score": verdict.score,
                "reason": verdict.reason,
                "ts": int(time.time()),
            }

    if batches:
        await asyncio.gather(*[_run_batch(b) for b in batches])

    if cache_writes:
        try:
            await cache_set_many(cache_writes, ttl=cache_ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            audit["errors"].append(f"cache_write:{str(exc)[:120]}")

    # Anything still undecided after max_calls / errors → quarantine.
    for cid, _cluster in indexed:
        if cid not in decided:
            decided[cid] = (
                "review",
                ClusterVerdict(cid, "review", 0.5, "unjudged"),
            )

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for cid, cluster in indexed:
        decision, verdict = decided[cid]
        audit["judged"] += 1
        if decision == "keep":
            stamped = _mark_kept(cluster, verdict)
            kept.append(stamped)
            audit["kept"] += 1
            if len(audit["sample"]) < 8:
                audit["sample"].append(
                    {
                        "primary": cluster.get("primary_keyword"),
                        "verdict": "keep",
                        "score": verdict.score,
                        "reason": verdict.reason,
                    }
                )
        elif decision == "drop":
            stamped = _mark_rejected(cluster, cause="llm_drop", reason=verdict.reason)
            rejected.append(stamped)
            audit["dropped"] += 1
            if len(audit["sample"]) < 8:
                audit["sample"].append(
                    {
                        "primary": cluster.get("primary_keyword"),
                        "verdict": "drop",
                        "score": verdict.score,
                        "reason": verdict.reason,
                    }
                )
        else:
            cause = verdict.reason or "review"
            stamped = _mark_rejected(cluster, cause=cause, reason=verdict.reason)
            rejected.append(stamped)
            audit["quarantined"] += 1
            audit["quarantined_by_cause"][cause] = (
                audit["quarantined_by_cause"].get(cause, 0) + 1
            )

    log.info(
        "cluster_coherence_gate",
        input=audit["input_count"],
        kept=audit["kept"],
        dropped=audit["dropped"],
        quarantined=audit["quarantined"],
        cached=audit["cached"],
        api_calls=audit["api_calls"],
    )
    return kept, rejected, audit
