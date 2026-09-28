"""Phase 5 — per-keyword LLM relevance gate.

Every seeded keyword is judged against three things: the client's CDD business
context, the **specific service** it was expanded from, and competitor context.

This replaces a stage that was not really a gate. That stage skipped the LLM
entirely for any seed with fewer rows than its cap, kept everything on provider
error, re-added low-volume rows the model never saw, and backfilled keywords the
model had explicitly rejected to hit a per-service quota.

The rule here is the opposite, and it has no exceptions:

    a keyword is either explicitly judged, or explicitly quarantined.

Quarantined means kept in the dataset for reporting but flagged
``relevance_unverified`` / ``commercial_target_eligible=False``, which excludes
it from clustering, topic drafting, briefs and commercial targeting. Nothing is
ever silently kept as though it had been verified.

Transport is micro-batched — ~20 keywords per call, one verdict row per keyword.
The judgement is still per-keyword; only the shared business context is
amortised. One call per keyword would mean ~800 calls per run at Standard depth,
each opening its own DB session in ``api_meter.record_api_call``, against a phase
that already has a 720s budget.
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
from app.integrations.llm import _parse_json_content, _skill_chat
from app.logging_config import get_logger
from app.services.cache import cache_get_many, cache_set_many

log = get_logger("keyword_validation_gate")

VERDICTS = ("keep", "drop", "review")

# A batch this size or larger that comes back entirely "drop" is far more likely
# to be a confused model than a genuinely worthless service. Precedent:
# ml/tier_competitors.py sanity-guards an all-low scoring response the same way.
ALL_DROP_GUARD_MIN_BATCH = 4

# Judge takes (system, user) and returns the parsed JSON object, or None.
Judge = Callable[[str, str], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class KeywordVerdict:
    keyword: str
    verdict: str
    score: float
    reason: str


_SYSTEM = """\
You are an SEO keyword relevance judge for ONE business and ONE of its services.

You are given that business's context, the specific service, competitor context,
and a numbered list of candidate keywords expanded from that service. Judge EACH
keyword independently.

A keyword is "keep" only if a real prospective BUYER of THIS service, from THIS
business, in THIS market, would plausibly search it while looking for that
service or researching the problem it solves.

A keyword is relevant only when the whole query is the service, a capability of
that service, a commercial need for it, or a useful informational topic that
directly supports it. Do not keep a keyword because it shares one token
(AI, email, marketing, google, website) with the service, or because it would
have high volume. You are not given metrics. Do not invent them.

Use "drop" when the searcher is not a buyer at all (jobs, salary, careers,
courses, "how to become"), when the keyword belongs to a different industry or a
service this business does not sell, when it names a competitor's brand, or when
it is a bare generic head term with no buying context.

Use "review" only when the keyword is genuinely ambiguous given the context.
Do not use "review" to avoid deciding.

Hard rules:
- Return EXACTLY one row for every id in the input list. Never skip an id.
- Never return an id or keyword that was not in the input list.
- Echo each keyword back character-for-character.
- Do not invent keywords, services, competitors, or facts about the business.
- Drop cross-platform paid-media mismatches: Google Ads / AdWords / PPC search
  keywords do not belong under Meta / Facebook / Instagram Ads, and vice versa.
  Shared words like "ads" or "ppc" alone are not enough to keep them.
- "score" is your confidence 0.00-1.00 that this is a genuine buying-journey
  query for the named service at THIS business. Use the full range. If you mark
  a keyword "keep" but are not confident, give it a low score.
- "reason" is at most 12 words, plain text.

Return JSON only, no markdown fences, exactly:
{"keywords":[{"keyword":"<exact input>","relevant":true,"reason":"<=12 words"}]}
"relevant" is true only for a keyword you would have marked keep. Do not add keywords. Do not output volume, CPC, difficulty, or intent."""


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _sanitize(text: Any, *, limit: int = 80) -> str:
    """Flatten a value for prompt interpolation.

    Competitor page keywords are harvested from third-party HTML, so they are
    hostile-origin text — collapse whitespace and control characters so they
    cannot restructure the prompt.
    """
    cleaned = re.sub(r"[\x00-\x1f\x7f`]", " ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


# One verdict row — keyword, verdict, score and a short reason — costs roughly
# this many output tokens. A 200-keyword batch against the 2048-token default
# was cut off mid-array, and the JSONDecodeError quarantined all 200: on a real
# Click Trends run that lost 174 of 283 keywords to "unjudged".
TOKENS_PER_VERDICT = 40


def tokens_for_batch(batch_size: int) -> int:
    """Output budget big enough to return a verdict for every keyword sent."""
    settings = get_settings()
    floor = int(getattr(settings, "keyword_relevance_max_tokens", 2048))
    return max(floor, int(max(1, batch_size)) * TOKENS_PER_VERDICT + 256)


def _provider_ready() -> bool:
    """Whether a real judge can run. Skills require a direct Anthropic key."""
    settings = get_settings()
    if settings.use_mock_llm:
        return False
    return bool(settings.anthropic_api_key)


async def _skill_judge(
    system: str, user: str, *, batch_size: int = 0
) -> dict[str, Any] | None:
    """Default judge — Anthropic Claude Sonnet via the skill chat path.

    Retries once on empty/unparseable responses. Under Phase 5 concurrency the
    first call can return blank content without raising; treating that as a
    hard miss fail-closed the entire keyword set in smoke runs.
    """
    settings = get_settings()
    last_raw: str | None = None
    for attempt in range(2):
        raw = await _skill_chat(
            system=system,
            user=user,
            model=settings.skill_model,
            max_tokens=tokens_for_batch(batch_size),
        )
        last_raw = raw
        if raw:
            try:
                parsed = _parse_json_content(raw)
            except Exception:  # noqa: BLE001
                # A truncated reply raises here rather than returning None, which
                # skipped the salvage branch entirely and quarantined the batch.
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            # A truncated reply is still worth something — hand the raw text
            # back so _parse_verdicts can salvage the rows that did arrive.
            if attempt == 1 and raw.strip():
                return {"_raw": raw}
        if attempt == 0:
            log.warning(
                "keyword_judge_retry",
                attempt=attempt + 1,
                raw_len=len(raw or ""),
            )
            await asyncio.sleep(1.0)
    log.warning(
        "keyword_judge_empty",
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
    competitor_names: list[str],
    competitor_domains: list[str],
    service_label: str,
    target_type: str,
) -> str:
    return "\n".join(
        [
            "BUSINESS",
            f"Name: {_sanitize(client_name, limit=120) or '(not provided)'}",
            f"Website: {_sanitize(primary_url, limit=120) or '(not provided)'}",
            f"Industry: {_sanitize(industry, limit=80) or '(not stated)'}",
            f"Market / geography: {_sanitize(geography, limit=80) or '(not stated)'}",
            "All services this business sells: "
            + (", ".join(_sanitize(s) for s in services[:20]) or "(none on file)"),
            "Client's own business keywords (from their discovery document): "
            + (", ".join(_sanitize(k) for k in cdd_keywords[:25]) or "(none on file)"),
            "",
            "SERVICE BEING JUDGED IN THIS BATCH",
            f"Service: {_sanitize(service_label, limit=120)}",
            f"Service type: {_sanitize(target_type, limit=40) or 'service'}",
            "",
            "COMPETITOR CONTEXT — use this to EXCLUDE, not to include",
            "Competitors: "
            + (", ".join(_sanitize(c) for c in competitor_names[:8]) or "(none identified)"),
            "Competitor domains: "
            + (", ".join(_sanitize(d) for d in competitor_domains[:8]) or "(none identified)"),
            "A competitor's brand name is a drop. A keyword a competitor targets is"
            " NOT automatically relevant — it must still fit the service above.",
        ]
    )


def _build_user_message(context_block: str, keywords: list[str]) -> str:
    numbered = "\n".join(f"{i}. {_sanitize(kw, limit=120)}" for i, kw in enumerate(keywords, 1))
    return (
        f"{context_block}\n\n"
        f"CANDIDATE KEYWORDS ({len(keywords)}) — judge every one, "
        f"return exactly {len(keywords)} rows:\n{numbered}"
    )


def _salvage_verdict_rows(raw: str) -> list[dict[str, Any]]:
    """Pull complete `{...}` verdict objects out of a truncated JSON reply.

    When the model runs out of output tokens the array is cut mid-row and
    json.loads fails on the whole payload. Every row that fully arrived is still
    a real judgement, and discarding them quarantined entire batches. Rows that
    were cut off are simply absent — they stay unjudged rather than guessed.
    """
    rows: list[dict[str, Any]] = []
    # Stack of `{` positions, so an object nested inside the `{"verdicts": [...]}`
    # wrapper is still recovered — the wrapper itself never closes when the reply
    # is truncated, so depth never returns to zero.
    starts: list[int] = []
    in_string = False
    escaped = False
    for i, ch in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            starts.append(i)
        elif ch == "}" and starts:
            start = starts.pop()
            try:
                obj = json.loads(raw[start : i + 1])
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("keyword"):
                rows.append(obj)
    return rows


def all_drop_is_suspect(rows: list[dict[str, Any]]) -> bool:
    """Whether an all-drop batch looks like a confused model rather than a verdict.

    On a junk-heavy pool a whole batch genuinely is irrelevant — a Click Trends
    run had 63 correctly-dropped keywords quarantined by this guard, which only
    left them in the pool flagged "unverified" instead of dropped. A response
    carrying a distinct, specific reason per keyword is a judgement; one with
    blank or copy-pasted reasons is not.
    """
    if len(rows) < ALL_DROP_GUARD_MIN_BATCH:
        # Too few rows to infer confusion — one or two drops is just a verdict.
        return False
    reasons = [str(r.get("reason") or "").strip().lower() for r in rows]
    if not all(reasons):
        return True
    # A single reason echoed across every row is boilerplate, not per-keyword work.
    return len(set(reasons)) < 2


def _parse_verdicts(
    payload: Any,
    sent: dict[int, str],
) -> dict[str, KeywordVerdict]:
    """Verdicts for keywords actually sent, keyed by normalized keyword.

    Anything the model names that was not in this batch is discarded — a model
    must not be able to add keywords to the set.
    """
    if isinstance(payload, str):
        rows = _salvage_verdict_rows(payload)
    elif isinstance(payload, dict):
        rows = payload.get("verdicts") or payload.get("results") or payload.get("keywords")
        if not isinstance(rows, list) and isinstance(payload.get("_raw"), str):
            # The model's reply was cut off mid-array; recover the whole rows.
            rows = _salvage_verdict_rows(payload["_raw"])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None
    if not isinstance(rows, list) or not rows:
        return {}

    sent_by_norm = {_norm(kw): kw for kw in sent.values()}
    out: dict[str, KeywordVerdict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        keyword = str(row.get("keyword") or "").strip()
        key = _norm(keyword)
        if key not in sent_by_norm:
            continue
        if "relevant" in row and "verdict" not in row:
            relevant = row.get("relevant")
            if relevant is True:
                verdict, score = "keep", 1.0
            elif relevant is False:
                verdict, score = "drop", 0.0
            else:
                verdict, score = "review", 0.5
            out[key] = KeywordVerdict(
                keyword=sent_by_norm[key],
                verdict=verdict,
                score=score,
                reason=_sanitize(row.get("reason"), limit=160),
            )
            continue
        # When an id is supplied it must agree with the keyword string.
        raw_id = row.get("id")
        if isinstance(raw_id, (int, float, str)):
            try:
                idx = int(raw_id)
            except (TypeError, ValueError):
                idx = None
            if idx is not None and idx in sent and _norm(sent[idx]) != key:
                continue
        verdict = str(row.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            verdict = "review"
        score = _coerce_score(row.get("score"))
        if score is None:
            verdict, score = "review", 0.5
        out[key] = KeywordVerdict(
            keyword=sent_by_norm[key],
            verdict=verdict,
            score=score,
            reason=_sanitize(row.get("reason"), limit=160),
        )
    return out


def _decide(verdict: KeywordVerdict, *, threshold: float) -> str:
    """keep | drop | review. Label and score must agree before a keyword is kept.

    A "keep" the model is not confident about is not a keep — the score is the
    binding signal, so a low-confidence keep is dropped rather than quietly
    admitted. Only an explicit "review" lands in the quarantine band.
    """
    if verdict.verdict == "review":
        return "review"
    if verdict.verdict == "keep":
        return "keep" if verdict.score >= threshold else "drop"
    return "drop"


def _quarantine(row: dict[str, Any], cause: str) -> dict[str, Any]:
    return {
        **row,
        "relevance_unverified": True,
        "commercial_target_eligible": False,
        "relevance_reason": "relevance_unverified",
        "relevance_unjudged_cause": cause,
        "relevance_status": (
            "unclassified"
            if cause in ("empty_or_unparsed", "malformed", "timeout")
            else "unverified"
        ),
    }


def _cache_key(client_id: str, context_fp: str, service: str, keyword: str, model: str) -> str:
    digest = hashlib.sha1(
        f"{_norm(service)}|{_norm(keyword)}|{model}".encode()
    ).hexdigest()[:20]
    return f"searchfit:kwverdict:v2:{client_id}:{context_fp}:{digest}"


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


_STAMPED_FIELDS = (
    "relevance_unverified",
    "commercial_target_eligible",
    "relevance_reason",
    "relevance_unjudged_cause",
)


def stamp_relevance_flags(
    seed_clusters: list[dict[str, Any]],
    judged_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Write gate verdicts back onto the seed-cluster rows.

    The gate judges the flat dataset, but ``flatten_dataset`` rebuilds that
    dataset from the cluster rows — and every downstream consumer
    (``build_service_seed_clusters``, the CDP cluster list, brief members) reads
    cluster rows too. Without this the verdicts would be silently discarded a
    few lines after they were produced.

    Matching is on (seed, keyword) together: the same keyword can sit under two
    services and be judged differently for each.
    """
    flags_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in judged_rows or []:
        if not isinstance(row, dict):
            continue
        key = (_norm(row.get("seed")), _norm(row.get("keyword")))
        flags = {f: row[f] for f in _STAMPED_FIELDS if f in row}
        if flags:
            flags_by_key[key] = flags

    if not flags_by_key:
        return seed_clusters

    for cluster in seed_clusters:
        if not isinstance(cluster, dict):
            continue
        seed_key = _norm(cluster.get("seed"))
        for cls in ("exact", "phrase", "related", "broad"):
            for entry in cluster.get(cls) or []:
                if not isinstance(entry, dict):
                    continue
                flags = flags_by_key.get((seed_key, _norm(entry.get("keyword"))))
                if flags:
                    entry.update(flags)
    return seed_clusters


async def validate_keywords(
    rows: list[dict[str, Any]],
    *,
    client_id: str,
    client_name: str | None = None,
    primary_url: str = "",
    industry: str | None = None,
    services: list[str] | None = None,
    cdd_keywords: list[str] | None = None,
    geography: str | None = None,
    competitor_names: list[str] | None = None,
    competitor_domains: list[str] | None = None,
    caller: Judge | None = None,
    enabled: bool | None = None,
    batch_size: int | None = None,
    threshold: float | None = None,
    max_calls: int | None = None,
    concurrency: int | None = None,
    call_timeout: float | None = None,
    cache_ttl_seconds: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Judge every keyword. Returns ``(kept, dropped, audit)``.

    ``kept`` includes quarantined rows — they are flagged, not removed, so the
    operator can see what could not be verified.
    """
    settings = get_settings()
    services = list(services or [])
    cdd_keywords = list(cdd_keywords or [])
    competitor_names = list(competitor_names or [])
    competitor_domains = list(competitor_domains or [])

    enabled = settings.keyword_validation_enabled if enabled is None else enabled
    batch_size = int(batch_size or settings.keyword_validation_batch_size)
    threshold = float(threshold if threshold is not None else settings.keyword_validation_threshold)
    max_calls = int(max_calls if max_calls is not None else settings.keyword_validation_max_calls)
    concurrency = int(concurrency or settings.keyword_validation_concurrency)
    call_timeout = float(
        call_timeout if call_timeout is not None else settings.keyword_validation_call_timeout
    )
    cache_ttl_seconds = int(
        cache_ttl_seconds
        if cache_ttl_seconds is not None
        else settings.keyword_validation_cache_days * 24 * 3600
    )

    audit: dict[str, Any] = {
        "gate": "llm_per_keyword_v2",
        "input_count": len(rows),
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

    if not rows:
        return [], [], audit

    from app.services.keyword_service_contract import partition_service_rejects

    rows, hard_dropped = partition_service_rejects(
        rows, cdd_keywords=cdd_keywords, geography=geography
    )
    audit["hard_rule_dropped"] = len(hard_dropped)
    audit["classifier_version"] = "service_contract_v2"
    for row in hard_dropped:
        audit["dropped"] += 1
        if len(audit["sample"]) < 12:
            audit["sample"].append(
                {
                    "keyword": row.get("keyword"),
                    "service": row.get("originating_service"),
                    "reason": row.get("relevance_drop_reason"),
                }
            )

    if not rows:
        return [], hard_dropped, audit

    if not enabled:
        audit["skipped"] = "disabled"
        return list(rows), hard_dropped, audit

    if caller is None and not _provider_ready():
        # Fail closed: no provider means nothing was judged, and unjudged is
        # never the same as verified.
        audit["skipped"] = "no_provider"
        audit["quarantined"] = len(rows)
        audit["quarantined_by_cause"] = {"no_provider": len(rows)}
        return [_quarantine(r, "no_provider") for r in rows], hard_dropped, audit

    judge: Judge = caller or _skill_judge
    model = settings.skill_model
    context_fp = _context_fingerprint(
        industry=industry, services=services, cdd_keywords=cdd_keywords, geography=geography
    )

    # --- cache lookup ------------------------------------------------------
    def _service_of(row: dict[str, Any]) -> str:
        return str(row.get("target") or row.get("seed") or "").strip() or "(unmapped)"

    keys_by_index: dict[int, str] = {
        i: _cache_key(client_id, context_fp, _service_of(row), str(row.get("keyword") or ""), model)
        for i, row in enumerate(rows)
    }
    cached_raw = await cache_get_many(sorted(set(keys_by_index.values())))

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    pending: list[tuple[int, dict[str, Any]]] = []
    decided: dict[int, tuple[str, KeywordVerdict]] = {}

    for i, row in enumerate(rows):
        entry = cached_raw.get(keys_by_index[i])
        if isinstance(entry, dict) and entry.get("verdict") in VERDICTS:
            score = _coerce_score(entry.get("score"))
            if score is not None:
                verdict = KeywordVerdict(
                    keyword=str(row.get("keyword") or ""),
                    verdict=str(entry["verdict"]),
                    score=score,
                    reason=str(entry.get("reason") or ""),
                )
                decided[i] = (_decide(verdict, threshold=threshold), verdict)
                audit["cached"] += 1
                continue
        pending.append((i, row))

    # --- batch by service --------------------------------------------------
    by_service: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for i, row in pending:
        by_service.setdefault(_service_of(row), []).append((i, row))

    batches: list[tuple[str, list[tuple[int, dict[str, Any]]]]] = []
    for service, items in by_service.items():
        for start in range(0, len(items), batch_size):
            batches.append((service, items[start : start + batch_size]))
    audit["batches"] = len(batches)

    sem = asyncio.Semaphore(max(1, concurrency))
    calls_made = 0
    lock = asyncio.Lock()
    started = time.monotonic()

    async def _run_batch(service: str, items: list[tuple[int, dict[str, Any]]]) -> None:
        nonlocal calls_made
        sent = {n: str(row.get("keyword") or "") for n, (_i, row) in enumerate(items, 1)}
        target_type = str(items[0][1].get("target_type") or "service")
        user = _build_user_message(
            _context_block(
                client_name=client_name,
                primary_url=primary_url,
                industry=industry,
                services=services,
                cdd_keywords=cdd_keywords,
                geography=geography,
                competitor_names=competitor_names,
                competitor_domains=competitor_domains,
                service_label=service,
                target_type=target_type,
            ),
            list(sent.values()),
        )

        async with sem:
            async with lock:
                if calls_made >= max_calls:
                    for i, _row in items:
                        decided[i] = ("quarantine:call_budget", None)  # type: ignore[assignment]
                    return
                calls_made += 1
                audit["api_calls"] = calls_made
            cause = "empty_or_unparsed"
            try:
                # The output budget has to cover one verdict per keyword sent;
                # a fixed cap truncated big batches and lost every verdict.
                try:
                    coro = judge(_SYSTEM, user, batch_size=len(items))  # type: ignore[call-arg]
                except TypeError:
                    coro = judge(_SYSTEM, user)  # injected test judges take two args
                payload = await asyncio.wait_for(coro, timeout=call_timeout)
            except asyncio.TimeoutError:
                cause = "timeout"
                payload = None
            except Exception as exc:  # noqa: BLE001
                cause = f"error:{type(exc).__name__}"
                audit["errors"].append(str(exc)[:200])
                payload = None

        if payload is None:
            for i, _row in items:
                decided[i] = (f"quarantine:{cause}", None)  # type: ignore[assignment]
            return

        verdicts = _parse_verdicts(payload, sent)
        if not verdicts:
            for i, _row in items:
                decided[i] = ("quarantine:malformed", None)  # type: ignore[assignment]
            return

        # A whole batch rejected can be a confused model — but on a junk-heavy
        # pool it is also the correct answer. Trust it when every row carries a
        # distinct, specific reason; quarantine only when the response looks
        # like boilerplate. Quarantining a correct all-drop batch just leaves
        # those keywords in the pool flagged "unverified" instead of dropped.
        if (
            len(verdicts) == len(items)
            and all(v.verdict == "drop" for v in verdicts.values())
            and all_drop_is_suspect(
                [
                    {"keyword": v.keyword, "verdict": v.verdict, "reason": v.reason}
                    for v in verdicts.values()
                ]
            )
        ):
            for i, _row in items:
                decided[i] = ("quarantine:all_drop_guard", None)  # type: ignore[assignment]
            return

        for i, row in items:
            verdict = verdicts.get(_norm(str(row.get("keyword") or "")))
            if verdict is None:
                decided[i] = ("quarantine:omitted", None)  # type: ignore[assignment]
                continue
            decided[i] = (_decide(verdict, threshold=threshold), verdict)

    if batches:
        await asyncio.gather(*[_run_batch(service, items) for service, items in batches])
    audit["elapsed_seconds"] = round(time.monotonic() - started, 2)

    # --- apply decisions ---------------------------------------------------
    to_cache: dict[str, Any] = {}
    for i, row in enumerate(rows):
        outcome, verdict = decided.get(i, ("quarantine:unprocessed", None))
        if outcome.startswith("quarantine:"):
            cause = outcome.split(":", 1)[1]
            kept.append(_quarantine(row, cause))
            audit["quarantined"] += 1
            audit["quarantined_by_cause"][cause] = (
                audit["quarantined_by_cause"].get(cause, 0) + 1
            )
            continue

        audit["judged"] += 1
        if verdict is not None:
            to_cache[keys_by_index[i]] = {
                "verdict": verdict.verdict,
                "score": verdict.score,
                "reason": verdict.reason,
            }
        if outcome == "keep":
            kept.append(row)
            audit["kept"] += 1
        elif outcome == "drop":
            reason = (verdict.reason if verdict else "") or "llm_not_relevant"
            dropped.append({**row, "relevance_drop_reason": reason})
            audit["dropped"] += 1
            if len(audit["sample"]) < 12:
                audit["sample"].append(
                    {
                        "keyword": row.get("keyword"),
                        "service": _service_of(row),
                        "score": verdict.score if verdict else None,
                        "reason": reason,
                    }
                )
        else:  # review — kept, but never commercially targetable
            kept.append(_quarantine(row, "review_band"))
            audit["quarantined"] += 1
            audit["quarantined_by_cause"]["review_band"] = (
                audit["quarantined_by_cause"].get("review_band", 0) + 1
            )

    if to_cache:
        await cache_set_many(to_cache, cache_ttl_seconds)

    dropped.extend(hard_dropped)

    log.info(
        "keyword_validation_complete",
        input=len(rows),
        kept=audit["kept"],
        dropped=audit["dropped"],
        quarantined=audit["quarantined"],
        api_calls=audit["api_calls"],
        cached=audit["cached"],
    )
    return kept, dropped, audit
