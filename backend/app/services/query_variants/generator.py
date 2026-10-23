"""Question- and research-style query variants for a seed, from the model.

Seeds are commercial terms ("physiotherapy", "mortgage broker"), so DataForSEO returns
mostly buying-stage keywords for them. For each seed the model writes 3–4 variants
("what is physiotherapy", "mortgage broker vs bank") that widen the pull toward
informational and consideration-stage keywords.

The variants are NOT seeds: they are only extra queries for this run's DataForSEO calls,
and keywords they bring back keep their own seed. They are cached per seed (Redis, 30
days, plus this run's memory), and asked for in batches so a service's seeds cost one
model call. A failure leaves a seed without variants; the run carries on.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from app.integrations.llm import synthesize_json
from app.logging_config import get_logger
from app.services.cache import cache_get_many, cache_set_many
from app.services.query_variants.prompts import SYSTEM_PROMPT, build_user_prompt

log = get_logger("query_variants")

PROMPT_VERSION = "qvariants-v1"
MAX_VARIANTS = 4
MAX_VARIANT_WORDS = 8
BATCH_SIZE = 25
CACHE_TTL_SECONDS = 30 * 24 * 3600
LLM_MAX_TOKENS = 8192
LLM_TIMEOUT_SECONDS = 60.0

# Words that never decide what a seed is about.
_GENERIC = frozenset(
    {
        "a", "an", "and", "the", "of", "for", "to", "in", "on", "with", "vs", "or",
        "service", "services", "company", "companies", "agency", "agencies", "firm",
        "best", "top", "near", "me", "cheap", "affordable", "professional", "online",
        "local", "cost", "price", "pricing", "hire",
    }
)
_BUYING = re.compile(r"\b(?:near me|hire|quote|price|pricing|cost)\b")

AskFn = Callable[..., Awaitable[Any]]


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t]


def subject_terms(seed: str) -> list[str]:
    """The seed's subject words: everything but generic modifiers ("seo" in "seo agency")."""
    return [t for t in _tokens(seed) if t not in _GENERIC and len(t) > 1]


def anchor_term(seed: str, service: str | None = None) -> str | None:
    """One word every variant keyword must contain to stay on the seed's topic.

    A word shared with the owning service wins ("seo" for "seo melbourne" under SEO),
    else the seed's longest subject word. Used as a ``like %word%`` filter, because
    keyword_ideas is category-based and otherwise drifts ("copd treatment" for
    "what is physiotherapy" in a live test).
    """
    terms = subject_terms(seed)
    if not terms:
        return None
    if service:
        shared = [t for t in terms if t in set(subject_terms(service))]
        if shared:
            return max(shared, key=len)
    return max(terms, key=len)


def clean_variants(seed: str, phrases: Iterable[Any], *, limit: int = MAX_VARIANTS) -> list[str]:
    """Usable variants: lowercase, at most 8 words, not the seed itself, not a buying
    phrase, and about the seed (sharing one of its subject words), no repeats."""
    subject = set(subject_terms(seed))
    own = _norm(seed)
    out: list[str] = []
    for raw in phrases:
        if not isinstance(raw, str):
            continue
        phrase = _norm(raw.strip(" \t\r\n\"'`.,;:!?-•*"))
        if not phrase or phrase == own or phrase in out:
            continue
        if len(phrase.split()) > MAX_VARIANT_WORDS or _BUYING.search(phrase):
            continue
        if subject and not (subject & set(_tokens(phrase))):
            continue
        out.append(phrase)
        if len(out) >= limit:
            break
    return out


def _ask_model(seeds: list[str], *, industry: str | None, market: str | None) -> Awaitable[Any]:
    return synthesize_json(
        SYSTEM_PROMPT,
        build_user_prompt(seeds, industry=industry, market=market),
        max_tokens=LLM_MAX_TOKENS,
        raise_on_error=True,
    )


class QueryVariantResolver:
    """Informational query variants for the seeds of ONE pipeline run.

    ``variants_for(seeds)`` returns ``{seed: [variants]}`` for every seed it could
    answer. Seeds are looked up in this run's memory, then the Redis cache, and only
    the rest go to the model, in batches of 25. Concurrent callers asking for the same
    seed share one request.
    """

    def __init__(
        self,
        *,
        industry: str | None = None,
        market: str | None = None,
        language: str | None = None,
        ask: AskFn | None = None,
        timeout: float = LLM_TIMEOUT_SECONDS,
    ) -> None:
        self._industry = industry
        self._market = market
        self._language = language
        self._ask = ask or _ask_model
        self._timeout = float(timeout)
        self._memo: dict[str, list[str]] = {}
        self._pending: dict[str, asyncio.Future[list[str]]] = {}
        self.llm_calls = 0
        self.cache_hits = 0
        self.failures: list[str] = []

    def _cache_key(self, seed: str) -> str:
        digest = hashlib.sha1(
            "|".join([_norm(seed), _norm(self._industry), _norm(self._market), _norm(self._language)]).encode()
        ).hexdigest()[:20]
        return f"searchfit:qvariants:{PROMPT_VERSION}:{digest}"

    async def variants_for(self, seeds: Iterable[str]) -> dict[str, list[str]]:
        wanted = list(dict.fromkeys(s.strip() for s in seeds if str(s or "").strip()))
        result: dict[str, list[str]] = {}
        waiting: dict[str, asyncio.Future[list[str]]] = {}
        to_load: list[str] = []
        for seed in wanted:
            key = _norm(seed)
            if key in self._memo:
                result[seed] = self._memo[key]
            elif key in self._pending:
                waiting[seed] = self._pending[key]
            else:
                to_load.append(seed)
        if to_load:
            loop = asyncio.get_running_loop()
            mine = {_norm(s): loop.create_future() for s in to_load}
            self._pending.update(mine)
            try:
                loaded = await self._load(to_load)
            except Exception as exc:  # noqa: BLE001 — variants are optional
                loaded = {}
                self.failures.append(f"{type(exc).__name__}: {str(exc)[:120]}")
            for seed in to_load:
                key = _norm(seed)
                variants = loaded.get(key, [])
                self._memo[key] = variants
                fut = self._pending.pop(key, None)
                if fut is not None and not fut.done():
                    fut.set_result(variants)
                result[seed] = variants
        for seed, fut in waiting.items():
            result[seed] = await fut
        return {s: v for s, v in result.items() if v}

    async def _load(self, seeds: list[str]) -> dict[str, list[str]]:
        keys = {self._cache_key(s): s for s in seeds}
        cached = await cache_get_many(list(keys))
        out: dict[str, list[str]] = {}
        missing: list[str] = []
        for cache_key, seed in keys.items():
            value = cached.get(cache_key)
            if isinstance(value, list):
                out[_norm(seed)] = [str(v) for v in value]
                self.cache_hits += 1
            else:
                missing.append(seed)
        fresh: dict[str, list[str]] = {}
        batches = [missing[i : i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
        replies = await asyncio.gather(*(self._ask_batch(b) for b in batches), return_exceptions=True)
        for batch, reply in zip(batches, replies):
            if isinstance(reply, BaseException):
                self.failures.append(f"{type(reply).__name__}: {str(reply)[:120]}")
                continue
            for seed in batch:
                variants = clean_variants(seed, reply.get(_norm(seed), []))
                out[_norm(seed)] = variants
                if variants:
                    fresh[self._cache_key(seed)] = variants
        if fresh:
            await cache_set_many(fresh, CACHE_TTL_SECONDS)
        return out

    async def _ask_batch(self, seeds: list[str]) -> dict[str, list[Any]]:
        self.llm_calls += 1
        reply = await asyncio.wait_for(
            self._ask(seeds, industry=self._industry, market=self._market), timeout=self._timeout
        )
        variants = (reply or {}).get("variants") if isinstance(reply, dict) else None
        if not isinstance(variants, dict):
            raise TypeError("reply has no variants map")
        by_seed = {_norm(k): v for k, v in variants.items() if isinstance(v, list)}
        log.info("query_variants_generated", seeds=len(seeds), answered=len(by_seed))
        return by_seed
