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
from dataclasses import dataclass, replace
from typing import Any

from app.config import get_settings
from app.integrations.llm import _parse_json_content, keyword_analysis_chat, keyword_analysis_ready
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
    intent: str = ""
    topic: str = ""


# v4 (2026-10-22): judged on business potential. Tool seekers, learning
# material, platform navigation, consumer search use, other languages and other
# markets are drops; informational queries stay only when the service can be
# pitched from the answer. The version is in the cache key, so v3 verdicts are
# not reused.
PROMPT_VERSION = "kwjudge-v4"


_SYSTEM = """\
You are an SEO keyword relevance judge for ONE business and ONE of its services.

You are given that business's context, the specific service, competitor context,
and a numbered list of candidate keywords expanded from that service. Judge EACH
keyword independently.

A keyword is "keep" only if a real prospective BUYER of THIS service, from THIS
business, in THIS market, would plausibly search it while looking for that
service or researching the problem it solves.

Judge business potential: could THIS business naturally pitch THIS service to
the person searching? Keep the query when it is the service, a capability of it,
a commercial need for it, or a question from someone who could become a client
(what it costs, how to choose a provider, whether they need it, a problem the
service fixes). An informational query is kept only when the business could
pitch the service from the answer. A definition, a general fact about how
something works, or a do-it-yourself task where hiring help is never the answer
has no business potential: drop it. Do not keep a keyword because it shares one
token (AI, email, marketing, google, website) with the service, or because it
would have high volume. You are not given metrics. Do not invent them.

Use "drop" when the searcher is not a buyer at all (jobs, salary, careers,
courses, certifications, templates, examples, "how to become"); when they want a
tool, software, an app, a checker or a free resource rather than someone to do
the work (unless this business sells that tool); when they are going to a
platform itself (a login, or just the platform's name such as "google ads" or
"google keyword planner"); when they are using a search engine rather than
marketing on one ("advanced search on google", "ai search engine"); when the
query is in another language or names a country or city this business does not
serve; when the keyword belongs to a different industry or a service this
business does not sell; when it names a competitor's brand; or when it is a bare
generic head term with no buying context.

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
- A useful long-tail query can be relevant without repeating the exact service
  name, when it describes a capability, problem, or buyer task of that service.
  Do not drop it only because the service name is absent.
- "intent" is informational, commercial, transactional, or navigational.
- "topic" is a short name for the subject, taken from the query itself.

Return JSON only, no markdown fences, one row per input keyword, in input order:
{"r":[["<exact input keyword>","k","c","short topic"],["<exact input keyword>","d","i","short topic","why, <=8 words"]]}
Row fields, in order:
1. the keyword, echoed character-for-character;
2. "k" if you would keep it (relevant), "d" if you would drop it;
3. intent: "i" informational, "c" commercial, "t" transactional, "n" navigational;
4. topic: at most 4 words, taken from the query;
5. only on "d" rows: the reason, at most 8 words.
Do not add keywords. Do not output volume, CPC, difficulty or scores."""

# Haiku's keep and drop are final. Only "u" (unsure), a missing row, or a
# failed screen is sent to Sonnet. That is what keeps this phase under its
# dollar cap: the last live run sent nearly every keyword to Sonnet.
_SCREEN_SYSTEM = """\
You are the keyword filter for ONE business and ONE service. Your keep and
your drop are final. A second, more expensive judge runs only on keywords
you mark unsure.

- "k" when the query is clearly something a prospective buyer of THIS service
  would search. That decision stands.
- "d" when it is clearly not: jobs, salary, careers, hiring, courses,
  certifications, templates, examples, "how to become", login, a tool, software,
  app or checker the searcher wants for themselves (unless the business sells
  it), a platform's own name on its own ("google ads", "mailchimp"), someone
  using a search engine rather than marketing on one, another language, a
  market the business does not serve, a named product of another company, or a
  different trade.
- An informational query is "k" only when this business could pitch the
  service from the answer; a definition or a do-it-yourself task is "d".
- "u" only when you genuinely cannot tell. Use it rarely.

Hard rules:
- Return EXACTLY one row for every keyword in the input list.
- Echo each keyword back character-for-character.
- Do not invent keywords. Do not use volume.

Return JSON only, no markdown fences:
{"r":[["<exact input keyword>","k","c","topic"],["<exact input keyword>","d","n","topic","why, <=8 words"],["<exact input keyword>","u","c","topic"]]}
Row fields: keyword, "k", "d", or "u", intent i/c/t/n, topic of at most 4 words,
and a reason only on "d" rows.
"""

# The compact row format above replaced one JSON object per keyword (2026-10-09).
# Same instructions, same judgement — only the reply is shorter: no keys, codes for
# the verdict and intent, and a reason only where it is read (drops). Measured on
# real Click Trends keywords: 99.5% agreement with the object format's stable
# verdicts (the object format agrees with itself 98.4–98.9%), 43% fewer output
# tokens, calls ~30 s instead of ~50 s. Verdicts mean the same, so cached
# verdicts stay valid and PROMPT_VERSION is unchanged.
_VERDICT_CODES = {"k": True, "d": False, "u": None}
_INTENT_CODES = {
    "i": "informational",
    "c": "commercial",
    "t": "transactional",
    "n": "navigational",
}


def _from_compact(row: list[Any]) -> dict[str, Any] | None:
    """A compact ``[keyword, k|d, intent, topic, reason?]`` row as an object row."""
    if len(row) < 2 or not isinstance(row[0], str):
        return None
    code = str(row[1] or "").strip().lower()[:1]
    intent = str(row[2] if len(row) > 2 else "").strip().lower()
    return {
        "keyword": row[0],
        # Neither k nor d: undecided, which the object format expressed as null.
        "relevant": _VERDICT_CODES.get(code),
        "intent": _INTENT_CODES.get(intent[:1], intent),
        "topic": row[3] if len(row) > 3 else "",
        "reason": row[4] if len(row) > 4 else "",
    }


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


# Output tokens one keyword costs, including the reasoning Claude does before it
# answers (about half of the total). Measured 2026-10-09 on real batches: ~45 per
# keyword in the compact row format (the old object format cost ~80, and 80-keyword
# batches overran the 8192 cap). A 200-keyword batch against the old 2048-token
# default was cut off mid-array and quarantined all 200: on a real Click Trends run
# that lost 174 of 283 keywords to "unjudged".
TOKENS_PER_VERDICT = 70


def tokens_for_batch(batch_size: int) -> int:
    """Output budget big enough to return a verdict for every keyword sent."""
    settings = get_settings()
    floor = int(getattr(settings, "keyword_relevance_max_tokens", 2048))
    return max(floor, int(max(1, batch_size)) * TOKENS_PER_VERDICT + 256)


def _provider_ready() -> bool:
    """Whether a real judge can run (Anthropic, or Claude Sonnet on OpenRouter)."""
    return keyword_analysis_ready()


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
        raw = await keyword_analysis_chat(
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


async def _screen_judge(
    system: str, user: str, *, batch_size: int = 0
) -> dict[str, Any] | None:
    """Haiku pre-screen. No OpenRouter retry — a miss goes to the Sonnet judge."""
    settings = get_settings()
    raw = await keyword_analysis_chat(
        system=system,
        user=user,
        model=settings.keyword_screen_model,
        max_tokens=tokens_for_batch(batch_size),
        allow_fallback=False,
    )
    if not raw or not str(raw).strip():
        return None
    try:
        parsed = _parse_json_content(raw)
    except Exception:  # noqa: BLE001
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return {"_raw": raw}


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
    offered: list[str] | None = None,
    audience: str | None = None,
) -> str:
    return "\n".join(
        [
            "BUSINESS",
            f"Name: {_sanitize(client_name, limit=120) or '(not provided)'}",
            f"Website: {_sanitize(primary_url, limit=120) or '(not provided)'}",
            f"Industry: {_sanitize(industry, limit=80) or '(not stated)'}",
            f"Market / geography: {_sanitize(geography, limit=80) or '(not stated)'}",
            f"Target audience: {_sanitize(audience, limit=160) or '(not stated)'}",
            "All services this business sells: "
            + (", ".join(_sanitize(s) for s in services[:20]) or "(none on file)"),
            "Sub-services this business offers: "
            + (", ".join(_sanitize(s) for s in (offered or [])[:30]) or "(none recorded)"),
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
    # Stack of `{` / `[` positions, so a row nested inside the `{"r": [...]}`
    # wrapper is still recovered — the wrapper itself never closes when the reply
    # is truncated, so depth never returns to zero. Compact rows are arrays.
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
        elif ch in "{[":
            starts.append(i)
        elif ch in "}]" and starts:
            start = starts.pop()
            try:
                obj = json.loads(raw[start : i + 1])
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("keyword"):
                rows.append(obj)
            elif isinstance(obj, list) and obj and isinstance(obj[0], str):
                converted = _from_compact(obj)
                if converted is not None and converted["keyword"]:
                    rows.append(converted)
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
        rows = (
            payload.get("verdicts")
            or payload.get("results")
            or payload.get("keywords")
            or payload.get("r")
        )
        if not isinstance(rows, list) and isinstance(payload.get("_raw"), str):
            # The model's reply was cut off mid-array; recover the whole rows.
            rows = _salvage_verdict_rows(payload["_raw"])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None
    if not isinstance(rows, list) or not rows:
        return {}
    rows = [
        (_from_compact(row) if isinstance(row, list) else row) for row in rows
    ]

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
                intent=_intent_label(row.get("intent")),
                topic=_sanitize(row.get("topic"), limit=80),
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
            intent=_intent_label(row.get("intent")),
            topic=_sanitize(row.get("topic"), limit=80),
        )
    return out


def _intent_label(raw: Any) -> str:
    label = str(raw or "").strip().lower()
    if label in {"informational", "commercial", "transactional", "navigational"}:
        return label
    return ""


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
        f"{_norm(service)}|{_norm(keyword)}|{model}|{PROMPT_VERSION}".encode()
    ).hexdigest()[:20]
    return f"searchfit:kwverdict:{PROMPT_VERSION}:{client_id}:{context_fp}:{digest}"


def _offered_subservices(prioritization: dict[str, Any] | None) -> list[str]:
    scope = (prioritization or {}).get("service_scope") if isinstance(prioritization, dict) else None
    names: list[str] = []
    if not isinstance(scope, dict):
        return names
    for entry in scope.values():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("offered") or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                names.append(name)
    return names


def _context_fingerprint(
    *,
    industry: str | None,
    services: list[str],
    cdd_keywords: list[str],
    geography: str | None,
    competitor_names: list[str] | None = None,
    offered: list[str] | None = None,
    audience: str | None = None,
) -> str:
    blob = json.dumps(
        {
            "prompt": PROMPT_VERSION,
            "industry": _norm(industry),
            "services": sorted(_norm(s) for s in services),
            "cdd": sorted(_norm(k) for k in cdd_keywords),
            "geo": _norm(geography),
            "competitors": sorted(_norm(c) for c in (competitor_names or [])),
            "offered": sorted(_norm(s) for s in (offered or [])),
            "audience": _norm(audience),
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
    prioritization: dict[str, Any] | None = None,
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
    deadline: float | None = None,
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
        "scope_dropped": 0,
        "quarantined": 0,
        "cached": 0,
        "api_calls": 0,
        "batches": 0,
        "quarantined_by_cause": {},
        "errors": [],
        "sample": [],
        "skipped": None,
        "screen_calls": 0,
        "screen_dropped": 0,
        "screen_kept": 0,
    }

    if not rows:
        return [], [], audit

    from app.services.keyword_service_contract import partition_service_rejects
    from app.services.service_scope import partition_scope_rejects

    # Operator-declared scope first: a keyword about a sub-service we do not
    # offer is dropped before any ontology or LLM judgement is spent on it.
    rows, scope_dropped = partition_scope_rejects(rows, prioritization)
    audit["scope_dropped"] = len(scope_dropped)
    rows, contract_dropped = partition_service_rejects(
        rows, cdd_keywords=cdd_keywords, geography=geography
    )
    hard_dropped = [*scope_dropped, *contract_dropped]
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
    offered = _offered_subservices(prioritization)
    audience = ""
    if isinstance(prioritization, dict):
        audience = str(
            prioritization.get("target_audience") or prioritization.get("audience") or ""
        )
    context_fp = _context_fingerprint(
        industry=industry,
        services=services,
        cdd_keywords=cdd_keywords,
        geography=geography,
        competitor_names=competitor_names,
        offered=offered,
        audience=audience,
    )

    # --- cache lookup ------------------------------------------------------
    def _service_of(row: dict[str, Any]) -> str:
        return str(row.get("service") or row.get("target") or row.get("seed") or "").strip() or "(unmapped)"

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
                    intent=_intent_label(entry.get("intent")),
                    topic=str(entry.get("topic") or ""),
                )
                decided[i] = (_decide(verdict, threshold=threshold), verdict)
                audit["cached"] += 1
                continue
        pending.append((i, row))

    # Identical keyword + service + context in this run shares one judgement.
    siblings: dict[str, list[int]] = {}
    unique_pending: list[tuple[int, dict[str, Any]]] = []
    seen_keys: set[str] = set()
    for i, row in pending:
        key = keys_by_index[i]
        siblings.setdefault(key, []).append(i)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_pending.append((i, row))
    in_run_saved = len(pending) - len(unique_pending)
    pending = unique_pending

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
        target_type = str(items[0][1].get("target_type") or "service")
        context = _context_block(
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
            offered=offered,
            audience=audience,
        )
        to_judge = list(items)
        # Haiku keep and drop are final. Unsure, omitted, and failed screens
        # still go to Sonnet. Injected test judges skip this.
        if caller is None and get_settings().keyword_screen_enabled:
            async with sem:
                async with lock:
                    if calls_made >= max_calls:
                        for i, _row in items:
                            decided[i] = ("quarantine:call_budget", None)  # type: ignore[assignment]
                        return
                    if deadline is not None and time.monotonic() >= deadline:
                        for i, _row in items:
                            decided[i] = ("quarantine:time_budget", None)  # type: ignore[assignment]
                        return
                    calls_made += 1
                    audit["api_calls"] = calls_made
                    audit["screen_calls"] += 1
                screen_sent = {
                    n: str(row.get("keyword") or "") for n, (_i, row) in enumerate(items, 1)
                }
                try:
                    screen_payload = await asyncio.wait_for(
                        _screen_judge(
                            _SCREEN_SYSTEM,
                            _build_user_message(context, list(screen_sent.values())),
                            batch_size=len(items),
                        ),
                        timeout=call_timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    audit["errors"].append(f"screen:{type(exc).__name__}"[:200])
                    screen_payload = None
            screen_verdicts = (
                _parse_verdicts(screen_payload, screen_sent) if screen_payload else {}
            )
            suspect = bool(screen_verdicts) and (
                len(screen_verdicts) == len(items)
                and all(v.verdict == "drop" for v in screen_verdicts.values())
                and all_drop_is_suspect(
                    [
                        {"keyword": v.keyword, "verdict": v.verdict, "reason": v.reason}
                        for v in screen_verdicts.values()
                    ]
                )
            )
            if screen_verdicts and not suspect:
                to_judge = []
                for i, row in items:
                    verdict = screen_verdicts.get(_norm(str(row.get("keyword") or "")))
                    if verdict is not None and verdict.verdict == "drop":
                        reason = verdict.reason or "obvious miss"
                        decided[i] = (
                            "drop",
                            replace(verdict, reason=f"screen: {reason}"),
                        )
                        audit["screen_dropped"] += 1
                    elif verdict is not None and verdict.verdict == "keep":
                        decided[i] = (
                            "keep",
                            replace(verdict, reason=verdict.reason or "screen: keep"),
                        )
                        audit["screen_kept"] = int(audit.get("screen_kept") or 0) + 1
                    else:
                        to_judge.append((i, row))
                if not to_judge:
                    return

        sent = {n: str(row.get("keyword") or "") for n, (_i, row) in enumerate(to_judge, 1)}
        user = _build_user_message(context, list(sent.values()))

        async with sem:
            async with lock:
                if calls_made >= max_calls:
                    for i, _row in to_judge:
                        decided[i] = ("quarantine:call_budget", None)  # type: ignore[assignment]
                    return
                if deadline is not None and time.monotonic() >= deadline:
                    for i, _row in to_judge:
                        decided[i] = ("quarantine:time_budget", None)  # type: ignore[assignment]
                    return
                calls_made += 1
                audit["api_calls"] = calls_made
            cause = "empty_or_unparsed"
            try:
                # The output budget has to cover one verdict per keyword sent;
                # a fixed cap truncated big batches and lost every verdict.
                try:
                    coro = judge(_SYSTEM, user, batch_size=len(to_judge))  # type: ignore[call-arg]
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
            for i, _row in to_judge:
                decided[i] = (f"quarantine:{cause}", None)  # type: ignore[assignment]
            return

        verdicts = _parse_verdicts(payload, sent)
        if not verdicts:
            for i, _row in to_judge:
                decided[i] = ("quarantine:malformed", None)  # type: ignore[assignment]
            return

        # A whole batch rejected can be a confused model — but on a junk-heavy
        # pool it is also the correct answer. Trust it when every row carries a
        # distinct, specific reason; quarantine only when the response looks
        # like boilerplate. Quarantining a correct all-drop batch just leaves
        # those keywords in the pool flagged "unverified" instead of dropped.
        if (
            len(verdicts) == len(to_judge)
            and all(v.verdict == "drop" for v in verdicts.values())
            and all_drop_is_suspect(
                [
                    {"keyword": v.keyword, "verdict": v.verdict, "reason": v.reason}
                    for v in verdicts.values()
                ]
            )
        ):
            for i, _row in to_judge:
                decided[i] = ("quarantine:all_drop_guard", None)  # type: ignore[assignment]
            return

        for i, row in to_judge:
            verdict = verdicts.get(_norm(str(row.get("keyword") or "")))
            if verdict is None:
                decided[i] = ("quarantine:omitted", None)  # type: ignore[assignment]
                continue
            decided[i] = (_decide(verdict, threshold=threshold), verdict)

    if batches:
        await asyncio.gather(*[_run_batch(service, items) for service, items in batches])
    for indexes in siblings.values():
        if len(indexes) < 2 or indexes[0] not in decided:
            continue
        for other in indexes[1:]:
            decided[other] = decided[indexes[0]]
            audit["cached"] += 1
    if in_run_saved:
        log.info("keyword_validation_cache_hit", in_run=in_run_saved)
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
                "intent": verdict.intent,
                "topic": verdict.topic,
            }
        stamped = dict(row)
        if verdict is not None and verdict.intent and row.get("intent_basis") != "measured":
            stamped["intent"] = verdict.intent
            stamped["intent_basis"] = "llm"
        if verdict is not None and verdict.intent:
            # Kept even when a provider label won, so a disagreement is visible.
            stamped["llm_intent"] = verdict.intent
        if verdict is not None and verdict.topic:
            stamped["topic"] = verdict.topic
        if outcome == "keep":
            kept.append(stamped)
            audit["kept"] += 1
        elif outcome == "drop":
            reason = (verdict.reason if verdict else "") or "llm_not_relevant"
            dropped.append({**stamped, "relevance_drop_reason": reason})
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
            kept.append(_quarantine(stamped, "review_band"))
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
        screen_dropped=audit["screen_dropped"],
    )
    return kept, dropped, audit
