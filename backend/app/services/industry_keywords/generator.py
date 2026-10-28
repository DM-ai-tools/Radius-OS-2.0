"""Industry seeds: what a buyer types to find a business of the client's kind.

A home page targets a few broad terms that describe the whole business ("digital
marketing agency"), not one of its services, and the service seeds never fetch them.
The model is asked once per run for the category terms of the client's industry (see
``prompts.py``). It returns terms only, never a metric: every volume still comes from
the keyword provider. The terms become a seed plan of their own
(``service_seeding.industry_seed_plan``), tagged ``seed_origin="industry"``.

The terms are shared through the keyword pool, keyed by industry, country and language,
so the second dental clinic reuses the first one's terms instead of asking the model
again. A failure, a timeout or a missing model is never an error for the run: the
industry name itself is seeded ("dental clinic", "dental clinic services"). Those
fallback terms are never saved to the pool, and nothing is read from or saved to it
when the model or the provider is a mock or a sandbox.
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.integrations.llm import _parse_json_content, keyword_analysis_chat
from app.logging_config import get_logger
from app.services.industry_keywords.prompts import SYSTEM_PROMPT, build_user_prompt
from app.services.seed_synonyms.generator import (
    _EDGE_PUNCTUATION,
    _QUESTION_START,
    _norm,
    _provider_ready,
)
from app.services.service_prioritization import service_identity_key
from app.services.shared_keyword_pool.location import is_location_specific
from app.services.shared_keyword_pool.store import load_seed, save_seed

log = get_logger("industry_keywords")

MAX_TERM_WORDS = 5
# Claude may reason before answering, and that reasoning counts against this budget.
LLM_MAX_TOKENS = 4096
LLM_TIMEOUT_SECONDS = 45.0
# The keyword pool's kind for saved industry terms (seed fetches are "seed").
POOL_KIND = "industry_terms"

AskFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class IndustryTerms:
    """The industry terms of one run.

    ``source`` is ``model``, ``pool`` (saved by an earlier client in the same industry,
    country and language) or ``fallback`` (the industry name itself, because the model
    gave nothing usable, or there is no industry to ask about).
    """

    terms: list[str]
    source: str
    industry_key: str | None


def industry_key(industry: str | None) -> str | None:
    """The pool key of an industry's terms: its text, lowercase, single-spaced, accents
    folded. Industries share terms only when they are worded alike; a coarse slug would
    merge "dental lab" with "dental clinic" and every software company under "saas"."""
    decomposed = unicodedata.normalize("NFKD", str(industry or "")).lower()
    text = " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())
    return text or None


def _plural_key(term: str) -> str:
    """Singular and plural spellings share a key ("agencies" and "agency"); the keyword
    fetch for one already finds the other."""
    words: list[str] = []
    for word in term.split():
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def clean_terms(phrases: Iterable[Any], *, services: Iterable[str] = (), limit: int) -> list[str]:
    """The model's phrases as seeds: lowercase, at most 5 words, not a question, naming no
    place (nor "near me"), not one of the client's own services (those are seeded by the
    service already), no plural or singular repeats, at most ``limit``, in order."""
    service_keys = {service_identity_key(s) for s in services if str(s or "").strip()}
    cap = max(0, int(limit))
    out: list[str] = []
    seen: set[str] = set()
    for raw in phrases:
        if len(out) >= cap:
            break
        if isinstance(raw, dict):
            raw = raw.get("term") or raw.get("keyword") or raw.get("phrase") or raw.get("text")
        if not isinstance(raw, str):
            continue
        term = _norm(raw.strip(_EDGE_PUNCTUATION))
        if len(term) < 2 or len(term.split()) > MAX_TERM_WORDS or _QUESTION_START.match(term):
            continue
        if is_location_specific(term) or service_identity_key(term) in service_keys:
            continue
        if _plural_key(term) in seen:
            continue
        seen.add(_plural_key(term))
        out.append(term)
    return out


def fallback_terms(industry: str | None, *, services: Iterable[str] = (), limit: int) -> list[str]:
    """The industry name and "<industry> services", cleaned like model terms."""
    name = _norm(industry)
    if not name:
        return []
    return clean_terms([name, f"{name} services"], services=services, limit=limit)


def _terms_from_reply(reply: Any) -> list[Any] | None:
    if isinstance(reply, dict):
        for key in ("terms", "keywords", "industry_terms"):
            value = reply.get(key)
            if isinstance(value, list):
                return value
        return None
    if isinstance(reply, list):
        return reply
    return None


async def ask_model(industry: str, *, market: str | None = None, services: Iterable[str] = ()) -> Any:
    """One model call for one industry. Raises when the model cannot answer.

    The same path the synonym seeds use: Claude first, then the keyword analysis
    fallback model.
    """
    text = await keyword_analysis_chat(
        system=SYSTEM_PROMPT + "\nRespond with valid JSON only. No markdown fences.",
        user=build_user_prompt(industry, market=market, services=services),
        max_tokens=LLM_MAX_TOKENS,
    )
    if not text or not str(text).strip():
        raise RuntimeError("LLM returned empty content")
    parsed = _parse_json_content(text)
    if not parsed:
        raise RuntimeError("LLM returned empty content")
    return parsed


class IndustryKeywordResolver:
    """The industry terms for ONE pipeline run.

    Create one per run. ``resolve`` works the terms out once (saved terms first, then
    the model, then the fallback) and answers every later or concurrent call from
    memory. ``location_code`` and ``language_code`` key the saved terms; without them
    the pool is not used.
    """

    def __init__(
        self,
        industry: str | None,
        market: str | None,
        services: Iterable[str],
        *,
        location_code: int | None = None,
        language_code: str | None = None,
        ask: AskFn | None = None,
        timeout: float = LLM_TIMEOUT_SECONDS,
    ) -> None:
        self._industry = " ".join(str(industry or "").split())
        self._market = market
        self._services = [s for s in (" ".join(str(x or "").split()) for x in services or ()) if s]
        self._location_code = location_code
        self._language_code = language_code
        self._ask = ask or ask_model
        self._timeout = float(timeout)
        self._task: asyncio.Future[IndustryTerms] | None = None
        self.llm_calls = 0

    async def resolve(self) -> IndustryTerms:
        if self._task is None:
            self._task = asyncio.ensure_future(self._resolve())
        return await asyncio.shield(self._task)

    async def _resolve(self) -> IndustryTerms:
        key = industry_key(self._industry)
        limit = max(0, int(get_settings().keyword_industry_max_seeds or 0))
        if key is None or limit == 0:
            return IndustryTerms(terms=[], source="fallback", industry_key=key)
        saved = await self._load(key, limit)
        if saved:
            log.info("industry_terms_reused", industry_key=key, terms=saved)
            return IndustryTerms(terms=saved, source="pool", industry_key=key)
        terms = await self._from_model(limit)
        if terms:
            await self._save(key, terms, limit)
            return IndustryTerms(terms=terms, source="model", industry_key=key)
        return IndustryTerms(
            terms=fallback_terms(self._industry, services=self._services, limit=limit),
            source="fallback",
            industry_key=key,
        )

    def _pool_usable(self) -> bool:
        """The pool holds real terms only: never with a mock model or provider, a sandbox
        provider, the pool switched off, or no country and language to key them by."""
        settings = get_settings()
        return bool(
            settings.keyword_pool_enabled
            and self._location_code is not None
            and self._language_code
            and not getattr(settings, "dataforseo_sandbox", False)
            and not settings.use_mock_providers
            and not settings.use_mock_llm
        )

    async def _load(self, key: str, limit: int) -> list[str]:
        if not self._pool_usable():
            return []
        try:
            hit = await load_seed(
                key,
                kind=POOL_KIND,
                location_code=int(self._location_code),
                language_code=str(self._language_code),
                min_volume=0,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 - the pool is optional; ask the model instead
            log.warning("industry_terms_pool_error", stage="read", industry_key=key, error=type(exc).__name__)
            return []
        if hit is None:
            return []
        # The pool keeps no order; each saved term carries its place in the model's list.
        rows = sorted(hit.rows, key=lambda row: row.get("rank") if isinstance(row.get("rank"), int) else limit)
        return clean_terms((row.get("keyword") for row in rows), services=self._services, limit=limit)

    async def _save(self, key: str, terms: list[str], limit: int) -> None:
        if not self._pool_usable():
            return
        try:
            await save_seed(
                key,
                seed_text=_norm(self._industry),
                location_code=int(self._location_code),
                language_code=str(self._language_code),
                min_volume=0,
                limit=limit,
                rows=[{"keyword": term, "rank": rank} for rank, term in enumerate(terms)],
                errors=[],
                kind=POOL_KIND,
                industry_key=key,
            )
        except Exception as exc:  # noqa: BLE001 - the terms are in hand; only the save failed
            log.warning("industry_terms_pool_error", stage="save", industry_key=key, error=type(exc).__name__)

    async def _from_model(self, limit: int) -> list[str]:
        """The model's cleaned terms, or an empty list (logged with the reason)."""
        self.llm_calls += 1
        started = time.perf_counter()
        terms: list[str] = []
        try:
            reply = await asyncio.wait_for(
                self._ask(self._industry, market=self._market, services=self._services),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            status, error = "timeout", f"no reply within {self._timeout:.0f}s"
        except Exception as exc:  # noqa: BLE001 - industry terms are optional; never fail the run
            status = "llm_failed" if _provider_ready() else "llm_unavailable"
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
        else:
            phrases = _terms_from_reply(reply)
            if phrases is None:
                status, error = "invalid_reply", "reply has no list of terms"
            else:
                terms = clean_terms(phrases, services=self._services, limit=limit)
                status, error = ("ok", None) if terms else ("no_terms", None)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if terms:
            log.info("industry_terms_generated", industry=self._industry, terms=terms, latency_ms=elapsed_ms)
        else:
            log.warning(
                "industry_terms_fallback",
                industry=self._industry,
                status=status,
                error=error,
                latency_ms=elapsed_ms,
            )
        return terms
