"""Sub-service seeds for a service, from the model.

The catalog often lists few sub-services for a service, or none, and only those were
seeded, so the keyword universe stayed narrow. For every selected service the model is
asked for the specialisations, delivery formats, audience segments and topic verticals
people search for within it ("SEO" -> technical seo, local seo, seo audit ...). They
become seeds of that service with ``target_type="sub_service"``.

These are not synonyms (``seed_synonyms`` handles other names of the service itself) and
not new catalog entries: they widen keyword research only. The operator's scope still
rules: a suggestion matching an excluded sub-service is dropped before it is seeded.

One ``SubserviceExpansionResolver`` lives for one pipeline run and asks the model at most
once per service name. A failure, a timeout or a missing model is never an error for the
run: that service is seeded from its catalog sub-services alone.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from app.integrations.llm import synthesize_json
from app.logging_config import get_logger
from app.services.seed_synonyms.generator import (
    _EDGE_PUNCTUATION,
    _NEAR_ME,
    _QUESTION_START,
    _core_key,
    _norm,
    _provider_ready,
    _variant_key,
)
from app.services.service_prioritization import service_identity_key
from app.services.subservice_expansion.prompts import SYSTEM_PROMPT, build_user_prompt

log = get_logger("subservice_expansion")

# 5 to 10 per service (the operator's rule, 2026-10-12); never more than 10.
MAX_SUBSERVICES = 10
MAX_SUBSERVICE_WORDS = 5
# Claude may reason before answering, and that reasoning counts against this budget.
LLM_MAX_TOKENS = 4096
LLM_TIMEOUT_SECONDS = 45.0
MAX_CONCURRENT_CALLS = 4

AskFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ExpansionResult:
    """What the model gave for one service.

    ``status`` is ``ok`` (at least one usable topic), ``no_subservices`` (a reply with
    nothing usable in it), ``llm_unavailable`` (no model configured), ``llm_failed``,
    ``timeout``, ``invalid_reply`` or ``empty_service``. ``cached`` is True when the
    answer came from this run's memory instead of a new call.
    """

    service: str
    terms: tuple[str, ...] = ()
    status: str = "ok"
    error: str | None = None
    cached: bool = False


def clean_subservices(
    service: str,
    phrases: Iterable[Any],
    *,
    existing: Iterable[str] = (),
    limit: int = MAX_SUBSERVICES,
) -> list[str]:
    """The model's topics as clean seed phrases: lowercase, at most 5 words, not a
    question, not the service itself (or the service with only a modifier such as
    "services" added), not a sub-service the service already has, no repeats, at most
    ``limit``, in the model's order. Scope and seed-list checks happen when the seeds
    are planned (``service_seeding``)."""
    out: list[str] = []
    service_name = _norm(service)
    seen = {_variant_key(service_name), _core_key(service_name)}
    for sub in existing:
        name = _norm(sub)
        if name:
            seen.update({_variant_key(name), _core_key(name)})
    for raw in phrases:
        if isinstance(raw, dict):
            raw = raw.get("name") or raw.get("topic") or raw.get("phrase") or raw.get("keyword")
        if not isinstance(raw, str):
            continue
        phrase = _norm(raw.strip(_EDGE_PUNCTUATION))
        core = _core_key(phrase)
        if len(phrase) < 2 or not core or _variant_key(phrase) in seen or core in seen:
            continue
        if len(phrase.split()) > MAX_SUBSERVICE_WORDS or _QUESTION_START.match(phrase) or _NEAR_ME.search(phrase):
            continue
        seen.update({_variant_key(phrase), core})
        out.append(phrase)
        if len(out) >= limit:
            break
    return out


def _phrases_from_reply(reply: Any) -> list[Any] | None:
    if isinstance(reply, dict):
        for key in ("sub_services", "subservices", "topics", "sub_topics", "items"):
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
    existing: Iterable[str] = (),
    excluded: Iterable[str] = (),
    other_services: Iterable[str] = (),
) -> Any:
    """One model call for one service. Raises when the model cannot answer."""
    return await synthesize_json(
        SYSTEM_PROMPT,
        build_user_prompt(
            service,
            industry=industry,
            market=market,
            existing=existing,
            excluded=excluded,
            other_services=other_services,
        ),
        max_tokens=LLM_MAX_TOKENS,
        raise_on_error=True,
    )


class SubserviceExpansionResolver:
    """Sub-service topics for the services of ONE pipeline run.

    ``existing`` and ``excluded`` map a service name to the sub-services it already has
    and the ones the operator excluded; both go into that service's question. The model
    is asked at most once per service name; repeats, concurrent ones included, are
    served from memory, failures too.
    """

    def __init__(
        self,
        *,
        industry: str | None = None,
        market: str | None = None,
        existing: Mapping[str, Iterable[str]] | None = None,
        excluded: Mapping[str, Iterable[str]] | None = None,
        other_services: Iterable[str] = (),
        ask: AskFn | None = None,
        timeout: float = LLM_TIMEOUT_SECONDS,
        max_concurrent: int = MAX_CONCURRENT_CALLS,
        limit: int = MAX_SUBSERVICES,
    ) -> None:
        self._industry = industry
        self._market = market
        self._existing = {self._key(k): [str(v) for v in vs or []] for k, vs in (existing or {}).items()}
        self._excluded = {self._key(k): [str(v) for v in vs or []] for k, vs in (excluded or {}).items()}
        self._other_services = [str(s) for s in other_services if str(s or "").strip()]
        self._ask = ask or ask_model
        self._timeout = float(timeout)
        self._limit = max(0, min(int(limit), MAX_SUBSERVICES))
        self._gate = asyncio.Semaphore(max(1, int(max_concurrent)))
        self._results: dict[str, ExpansionResult] = {}
        self._pending: dict[str, asyncio.Future[ExpansionResult]] = {}
        self.llm_calls = 0

    @staticmethod
    def _key(service: str) -> str:
        return service_identity_key(service) or _norm(service)

    async def expand(self, service: str) -> ExpansionResult:
        name = " ".join(str(service or "").split())
        key = self._key(name)
        if not key:
            return ExpansionResult(service=name, status="empty_service")
        done = self._results.get(key)
        if done is not None:
            return replace(done, service=name, cached=True)
        task = self._pending.get(key)
        owner = task is None
        if task is None:
            task = asyncio.ensure_future(self._fetch(name, key))
            self._pending[key] = task

            def _store(finished: asyncio.Future[ExpansionResult], key: str = key) -> None:
                self._pending.pop(key, None)
                if not finished.cancelled() and finished.exception() is None:
                    self._results[key] = finished.result()

            task.add_done_callback(_store)
        result = await asyncio.shield(task)
        return result if owner else replace(result, service=name, cached=True)

    async def resolve_all(self, services: Iterable[str]) -> dict[str, ExpansionResult]:
        """Topics for every service, asked concurrently, keyed by the name given."""
        names = list(dict.fromkeys(" ".join(str(s or "").split()) for s in services))
        names = [n for n in names if n]
        if self._limit == 0:
            return {n: ExpansionResult(service=n, status="disabled") for n in names}
        results = await asyncio.gather(*(self.expand(n) for n in names))
        return dict(zip(names, results))

    async def _fetch(self, service: str, key: str) -> ExpansionResult:
        existing = self._existing.get(key, [])
        async with self._gate:
            self.llm_calls += 1
            started = time.perf_counter()
            try:
                reply = await asyncio.wait_for(
                    self._ask(
                        service,
                        industry=self._industry,
                        market=self._market,
                        existing=existing,
                        excluded=self._excluded.get(key, []),
                        other_services=self._other_services,
                    ),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                result = ExpansionResult(
                    service=service, status="timeout", error=f"no reply within {self._timeout:.0f}s"
                )
            except Exception as exc:  # noqa: BLE001 — expansion is optional; never fail the run
                result = ExpansionResult(
                    service=service,
                    status="llm_failed" if _provider_ready() else "llm_unavailable",
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                )
            else:
                phrases = _phrases_from_reply(reply)
                if phrases is None:
                    result = ExpansionResult(
                        service=service, status="invalid_reply", error="reply has no list of sub-services"
                    )
                else:
                    terms = tuple(clean_subservices(service, phrases, existing=existing, limit=self._limit))
                    result = ExpansionResult(
                        service=service, terms=terms, status="ok" if terms else "no_subservices"
                    )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if result.status == "ok":
            log.info("subservice_expansion_generated", service=service, terms=list(result.terms), latency_ms=elapsed_ms)
        else:
            log.warning(
                "subservice_expansion_unavailable",
                service=service,
                status=result.status,
                error=result.error,
                latency_ms=elapsed_ms,
            )
        return result


# ------------------------------------------------------------------------ report


def audit_entries(
    results: Mapping[str, ExpansionResult], plans: Iterable[Any]
) -> list[dict[str, Any]]:
    """One row per service for the run's audit: what the model suggested, which became
    seeds, and which were skipped and why. ``plans`` are the run's ``ServiceSeedPlan``s."""
    by_key = {SubserviceExpansionResolver._key(name): result for name, result in results.items()}
    out: list[dict[str, Any]] = []
    for plan in plans:
        if getattr(plan, "is_other", False):
            continue
        result = results.get(plan.service) or by_key.get(SubserviceExpansionResolver._key(plan.service))
        if result is None:
            continue
        out.append(
            {
                "service": plan.service,
                "service_id": plan.service_id,
                "status": result.status,
                "suggested": list(result.terms),
                "added": list(getattr(plan, "expansions_added", []) or []),
                "skipped": [dict(s) for s in getattr(plan, "expansions_skipped", []) or []],
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
    "no_subservices": "no usable sub-services",
}


def operator_notice(entries: list[dict[str, Any]]) -> str | None:
    """A one-line chat notice: sub-service seeds added per service, or why none were."""
    if not entries or all(e.get("status") == "disabled" for e in entries):
        return None
    parts: list[str] = []
    for entry in entries:
        added = entry.get("added") or []
        if added:
            parts.append(f"{entry['service']} +{len(added)}")
        elif entry.get("status") == "ok":
            parts.append(f"{entry['service']}: suggestions were already seeds or out of scope")
        else:
            label = _FALLBACK_LABELS.get(str(entry.get("status")), str(entry.get("status")))
            parts.append(f"{entry['service']}: {label}, catalog sub-services only")
    return "Sub-service seeds: " + "; ".join(parts) + "."
