"""Synonym and full-form seeds for a service, from the model.

When a service becomes a seed root, the other names people search it by are seeded too:
"SEO" also seeds "search engine optimisation" and "organic search"; "paid advertising"
also seeds "pay per click". There is no dictionary of these. The model is asked, per service, how
people actually type that service into Google (see ``prompts.py``), so it works for any
service in any industry without manual updates.

One ``SeedSynonymResolver`` lives for one pipeline run. It asks the model once per
service name and answers every repeat from memory, including repeats that arrive while
the first call is still running. A failure, a timeout or a missing model is never an
error for the run: that service simply gets no synonyms and is seeded by its own name.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from app.config import get_settings
from app.integrations.llm import _parse_json_content, keyword_analysis_chat
from app.logging_config import get_logger
from app.services.seed_synonyms.prompts import SYSTEM_PROMPT, build_user_prompt
from app.services.service_prioritization import service_identity_key

log = get_logger("seed_synonyms")

# 4 to 6 per service (the operator's rule, 2026-10-13); never more than 6.
MAX_SYNONYMS = 6
MAX_SYNONYM_WORDS = 5
# Claude may reason before answering, and that reasoning counts against this budget.
LLM_MAX_TOKENS = 4096
LLM_TIMEOUT_SECONDS = 45.0
MAX_CONCURRENT_CALLS = 4

# A question is a search about the service, not another name for it.
_QUESTION_START = re.compile(
    r"^(?:what|how|why|when|where|which|who|is|are|can|does|do|should)\b"
)
_EDGE_PUNCTUATION = " \t\r\n\"'`.,;:!?-–—•*"
# "near me" asks for a nearby provider, never another name for the service.
_NEAR_ME = re.compile(r"\bnear me\b")
# Words that dress a name up without changing what is searched for. "seo services",
# "best seo" and "professional web design" are the service's own name with one of these
# added, so the root's keyword fetch already finds them. Used for sub-service topics,
# where "seo services" is never a topic under SEO.
_MODIFIER_WORDS = frozenset(
    {
        "service",
        "services",
        "company",
        "companies",
        "agency",
        "agencies",
        "firm",
        "firms",
        "provider",
        "providers",
        "solution",
        "solutions",
        "professional",
        "professionals",
        "expert",
        "experts",
        "specialist",
        "specialists",
        "best",
        "top",
        "affordable",
        "cheap",
        "near",
        "me",
    }
)

# The narrower list for synonyms: the qualifiers the operator ruled out (2026-10-13:
# not "service + company/agency/near me", nor an adjective on the service's own name).
# "services" and "solutions" stay allowed on a one-word service, since people do search
# "electrical services" or "catering solutions" as a name for it (their own examples).
# On a multi-word service they are just the name repeated: "email marketing services".
_QUALIFIER_WORDS = _MODIFIER_WORDS - {"service", "services", "solution", "solutions"}
# Words that only lengthen a name we already have ("email campaigns" -> "email
# campaign management", "smm" -> "smm services").
_LENGTHENING_WORDS = frozenset({"service", "services", "solution", "solutions", "management"})

AskFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class SynonymResult:
    """What the model gave for one service.

    ``status`` is ``ok`` (at least one usable synonym), ``no_synonyms`` (a reply with
    nothing usable in it), ``llm_unavailable`` (no model configured), ``llm_failed``,
    ``timeout``, ``invalid_reply`` or ``empty_service``. ``cached`` is True when the
    answer came from this run's memory instead of a new call.
    """

    service: str
    synonyms: tuple[str, ...] = ()
    status: str = "ok"
    error: str | None = None
    cached: bool = False


def _provider_ready() -> bool:
    """Whether a real model can answer. Anthropic is first; OpenRouter is the fallback."""
    settings = get_settings()
    if settings.use_mock_llm:
        return False
    return bool(
        (settings.anthropic_api_key or "").strip()
        or (settings.openrouter_api_key or "").strip()
    )


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _variant_key(phrase: str) -> str:
    """Singular and plural forms of one phrase share a key ("bridging loans" and
    "bridging loan"). The keyword fetch for one already finds the other, so a second
    seed for it is pure bloat."""
    return " ".join(
        word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        for word in phrase.split()
    )


def _core_key(phrase: str, words: frozenset[str] = _MODIFIER_WORDS) -> str:
    """A phrase without its modifier words, plural-folded: "best seo services" and "seo"
    share a core, so the first is only a dressed-up form of the second."""
    return _variant_key(" ".join(w for w in phrase.split() if w not in words))


def _folded_tokens(phrase: str) -> list[str]:
    return [t for t in _variant_key(phrase).split() if t]


def _bare_abbreviation(phrase: str) -> str | None:
    """smm services is the abbreviation with a generic word stuck on. The seed is smm."""
    words = phrase.split()
    content = [w for w in words if w not in _LENGTHENING_WORDS]
    if (
        len(content) == 1
        and content != words
        and content[0].isalpha()
        and 2 <= len(content[0]) <= 4
    ):
        return content[0]
    return None


def _same_name_with_services_added(service_name: str, phrase: str) -> bool:
    """email marketing services is Email Marketing again. electrical services is how
    people name the one-word trade Electrical, so that form stays."""
    if len(service_name.split()) < 2:
        return False
    content = [w for w in phrase.split() if w not in _MODIFIER_WORDS]
    return bool(content) and content != phrase.split() and _variant_key(" ".join(content)) == _variant_key(
        service_name
    )


_LENGTHENING_FOLDED = frozenset(_variant_key(word) for word in _LENGTHENING_WORDS)


def _lengthens_another(phrase: str, shorter: str) -> bool:
    """True when phrase is shorter with only service/solutions/management added,
    in any position: email campaign management lengthens email campaigns."""
    longer = _folded_tokens(phrase)
    short = _folded_tokens(shorter)
    if not short or len(short) >= len(longer):
        return False
    width = len(short)
    for start in range(len(longer) - width + 1):
        if longer[start : start + width] != short:
            continue
        extra = longer[:start] + longer[start + width :]
        if extra and all(word in _LENGTHENING_FOLDED for word in extra):
            return True
    return False


def clean_synonyms(
    service: str, phrases: Iterable[Any], *, limit: int = MAX_SYNONYMS
) -> list[str]:
    """The model's phrases as clean search phrasings: lowercase, at most 5 words, not a
    question, not the service's own name (or its plural, or that name with only a
    qualifier like "company", "agency", "near me" or "best" added), no repeats, at most
    ``limit``, in the model's order. A multi-word service with "services" stuck on the
    end is the same name. A phrase that only lengthens another kept name ("email
    campaign management" over "email campaigns") is dropped. Seed-level cleaning and
    validation happen later, exactly as for any other seed."""
    out: list[str] = []
    service_name = _norm(service)
    seen = {_variant_key(service_name), _core_key(service_name, _QUALIFIER_WORDS)}
    for raw in phrases:
        if isinstance(raw, dict):
            raw = raw.get("phrase") or raw.get("synonym") or raw.get("keyword") or raw.get("text")
        if not isinstance(raw, str):
            continue
        phrase = _bare_abbreviation(_norm(raw.strip(_EDGE_PUNCTUATION))) or _norm(
            raw.strip(_EDGE_PUNCTUATION)
        )
        if _same_name_with_services_added(service_name, phrase):
            continue
        core = _core_key(phrase, _QUALIFIER_WORDS)
        if len(phrase) < 2 or not core or _variant_key(phrase) in seen or core in seen:
            continue
        if len(phrase.split()) > MAX_SYNONYM_WORDS or _QUESTION_START.match(phrase) or _NEAR_ME.search(phrase):
            continue
        seen.update({_variant_key(phrase), core})
        out.append(phrase)
    kept: list[str] = []
    for phrase in out:
        if any(_lengthens_another(phrase, other) for other in out if other != phrase):
            continue
        if _lengthens_another(phrase, service_name) and len(service_name.split()) >= 2:
            continue
        kept.append(phrase)
        if len(kept) >= limit:
            break
    return kept


def _phrases_from_reply(reply: Any) -> list[Any] | None:
    if isinstance(reply, dict):
        for key in ("synonyms", "variations", "phrasings", "alternates"):
            value = reply.get(key)
            if isinstance(value, list):
                return value
        return None
    if isinstance(reply, list):
        return reply
    return None


async def ask_model(
    service: str,
    *,
    industry: str | None = None,
    market: str | None = None,
    other_services: Iterable[str] = (),
) -> Any:
    """One model call for one service. Raises when the model cannot answer.

    Claude on Anthropic is tried first. A failure or an empty reply is retried
    once on Claude Sonnet through OpenRouter, the same path keyword analysis uses.
    """
    text = await keyword_analysis_chat(
        system=SYSTEM_PROMPT + "\nRespond with valid JSON only. No markdown fences.",
        user=build_user_prompt(
            service, industry=industry, market=market, other_services=other_services
        ),
        max_tokens=LLM_MAX_TOKENS,
    )
    if not text or not str(text).strip():
        raise RuntimeError("LLM returned empty content")
    parsed = _parse_json_content(text)
    if not parsed:
        raise RuntimeError("LLM returned empty content")
    return parsed


class SeedSynonymResolver:
    """Synonyms for the services of ONE pipeline run.

    Create one per run. ``synonyms_for`` asks the model at most once per service name
    (names are compared the way the rest of the app compares services, so "SEO" and
    "seo " are one name); repeats, concurrent ones included, are served from memory.
    Failures are remembered too, so a slow model is never waited on twice in a run.
    """

    def __init__(
        self,
        *,
        industry: str | None = None,
        market: str | None = None,
        other_services: Iterable[str] = (),
        ask: AskFn | None = None,
        timeout: float = LLM_TIMEOUT_SECONDS,
        max_concurrent: int = MAX_CONCURRENT_CALLS,
    ) -> None:
        self._industry = industry
        self._market = market
        self._other_services = [str(s) for s in other_services if str(s or "").strip()]
        self._ask = ask or ask_model
        self._timeout = float(timeout)
        self._gate = asyncio.Semaphore(max(1, int(max_concurrent)))
        self._results: dict[str, SynonymResult] = {}
        self._pending: dict[str, asyncio.Future[SynonymResult]] = {}
        self.llm_calls = 0

    @staticmethod
    def _key(service: str) -> str:
        return service_identity_key(service) or _norm(service)

    async def synonyms_for(self, service: str) -> SynonymResult:
        name = " ".join(str(service or "").split())
        key = self._key(name)
        if not key:
            return SynonymResult(service=name, status="empty_service")
        done = self._results.get(key)
        if done is not None:
            return replace(done, service=name, cached=True)
        task = self._pending.get(key)
        owner = task is None
        if task is None:
            task = asyncio.ensure_future(self._fetch(name))
            self._pending[key] = task

            def _store(finished: asyncio.Future[SynonymResult], key: str = key) -> None:
                self._pending.pop(key, None)
                if not finished.cancelled() and finished.exception() is None:
                    self._results[key] = finished.result()

            task.add_done_callback(_store)
        result = await asyncio.shield(task)
        return result if owner else replace(result, service=name, cached=True)

    async def resolve_all(self, services: Iterable[str]) -> dict[str, SynonymResult]:
        """Synonyms for every service, asked concurrently, keyed by the name given."""
        names = list(dict.fromkeys(" ".join(str(s or "").split()) for s in services))
        names = [n for n in names if n]
        results = await asyncio.gather(*(self.synonyms_for(n) for n in names))
        return dict(zip(names, results))

    async def _fetch(self, service: str) -> SynonymResult:
        async with self._gate:
            self.llm_calls += 1
            started = time.perf_counter()
            try:
                reply = await asyncio.wait_for(
                    self._ask(
                        service,
                        industry=self._industry,
                        market=self._market,
                        other_services=self._other_services,
                    ),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                result = SynonymResult(
                    service=service,
                    status="timeout",
                    error=f"no reply within {self._timeout:.0f}s",
                )
            except Exception as exc:  # noqa: BLE001 — synonyms are optional; never fail the run
                result = SynonymResult(
                    service=service,
                    status="llm_failed" if _provider_ready() else "llm_unavailable",
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                )
            else:
                phrases = _phrases_from_reply(reply)
                if phrases is None:
                    result = SynonymResult(
                        service=service,
                        status="invalid_reply",
                        error="reply has no list of synonyms",
                    )
                else:
                    synonyms = tuple(clean_synonyms(service, phrases))
                    result = SynonymResult(
                        service=service,
                        synonyms=synonyms,
                        status="ok" if synonyms else "no_synonyms",
                    )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if result.status == "ok":
            log.info(
                "seed_synonyms_generated",
                service=service,
                synonyms=list(result.synonyms),
                latency_ms=elapsed_ms,
            )
        else:
            log.warning(
                "seed_synonyms_unavailable",
                service=service,
                status=result.status,
                error=result.error,
                latency_ms=elapsed_ms,
            )
        return result


# ------------------------------------------------------------------------ report


def audit_entries(
    results: Mapping[str, SynonymResult], plans: Iterable[Any]
) -> list[dict[str, Any]]:
    """One row per service for the run's audit output: what the model suggested,
    which became seeds, and which were skipped and why. ``plans`` are the run's
    ``ServiceSeedPlan`` objects (their ``synonyms_added`` / ``synonyms_skipped``)."""
    by_key = {SeedSynonymResolver._key(name): result for name, result in results.items()}
    out: list[dict[str, Any]] = []
    for plan in plans:
        if getattr(plan, "is_other", False):
            continue
        result = results.get(plan.service) or by_key.get(SeedSynonymResolver._key(plan.service))
        if result is None:
            continue
        out.append(
            {
                "service": plan.service,
                "service_id": plan.service_id,
                "status": result.status,
                "suggested": list(result.synonyms),
                "added": list(getattr(plan, "synonyms_added", []) or []),
                "skipped": [dict(s) for s in getattr(plan, "synonyms_skipped", []) or []],
                "error": result.error,
                "cached": result.cached,
            }
        )
    return out


_FALLBACK_LABELS = {
    "llm_unavailable": "no model configured",
    "llm_failed": "model call failed",
    "timeout": "model timed out",
    "invalid_reply": "model reply unusable",
    "no_synonyms": "no usable synonyms",
}


def operator_notice(entries: list[dict[str, Any]]) -> str | None:
    """A one-line chat notice: synonyms added per service, or why a service is seeded
    by its own name only."""
    if not entries:
        return None
    parts: list[str] = []
    for entry in entries:
        added = entry.get("added") or []
        if added:
            parts.append(f"{entry['service']} +{len(added)} ({', '.join(added)})")
        elif entry.get("status") == "ok":
            parts.append(f"{entry['service']}: suggestions were already seeds")
        else:
            label = _FALLBACK_LABELS.get(str(entry.get("status")), str(entry.get("status")))
            parts.append(f"{entry['service']}: {label}, seeded by its own name only")
    return "Synonym seeds: " + "; ".join(parts) + "."
