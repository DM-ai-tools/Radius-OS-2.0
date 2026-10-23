"""Per-service DataForSEO Labs keyword and competitor research.

Keyword expansion (suggestions, related keywords, ideas) already runs once per
service, with only that service's seeds. Competitor endpoints do not take a
seed: they return every keyword a domain ranks for. This module keeps those
rows on the service they actually describe, and drops a keyword that belongs
to another service even when its search volume is higher.

Labs endpoints used (all live, Basic auth, POST, location_code + language_code):

- /dataforseo_labs/google/keyword_suggestions/live
- /dataforseo_labs/google/related_keywords/live
- /dataforseo_labs/google/keyword_ideas/live
- /dataforseo_labs/google/competitors_domain/live
- /dataforseo_labs/google/ranked_keywords/live
- /dataforseo_labs/google/keywords_for_site/live

Metrics read from those responses: keyword_info.search_volume, competition,
cpc, keyword_properties.keyword_difficulty, search_intent_info.main_intent.
A missing metric stays unused. Nothing is invented.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.integrations import dataforseo
from app.logging_config import get_logger

log = get_logger("service_labs_research")

_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "of",
        "to",
        "in",
        "on",
        "near",
        "me",
        "best",
        "cheap",
        "top",
        "vs",
        "with",
        "from",
        "your",
        "our",
        "service",
        "services",
        "company",
        "agency",
    }
)

_COMMERCIAL_INTENTS = frozenset({"commercial", "transactional"})


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _tokens(text: Any) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[a-z0-9]+", _norm(text))
        if len(tok) > 2 and tok not in _STOP
    }


def isolated_seeds(own_seeds: list[str], foreign_seeds: list[str]) -> list[str]:
    """Seeds this service may send to Labs. Another service's seeds are removed
    unless this service listed the same phrase itself."""
    own_norms = {_norm(s) for s in own_seeds if _norm(s)}
    foreign = {_norm(s) for s in foreign_seeds if _norm(s)} - own_norms
    out: list[str] = []
    seen: set[str] = set()
    for seed in own_seeds:
        key = _norm(seed)
        if not key or key in seen or key in foreign:
            continue
        seen.add(key)
        out.append(str(seed).strip())
    return out


def _phrase_hit(keyword: str, phrases: list[str]) -> bool:
    kw = _norm(keyword)
    if not kw:
        return False
    for phrase in phrases:
        p = _norm(phrase)
        if len(p) < 4:
            continue
        if p in kw or kw in p:
            return True
    return False


def keyword_matches_service(
    keyword: str,
    *,
    service: str,
    seeds: list[str],
    other_phrases: list[str],
) -> bool:
    """True when the keyword describes this service and not another one.

    Relevance outranks volume: a keyword with no token in common with this
    service is rejected, including when it is an exact seed of another service.
    """
    kw = _norm(keyword)
    if not kw:
        return False
    own_phrases = [service, *seeds]
    own_tokens = set().union(*(_tokens(p) for p in own_phrases)) if own_phrases else set()
    other_tokens = set().union(*(_tokens(p) for p in other_phrases)) if other_phrases else set()
    other_tokens -= own_tokens
    kw_tokens = _tokens(keyword)
    own_phrase = _phrase_hit(keyword, seeds) or _phrase_hit(keyword, [service])
    other_phrase = _phrase_hit(keyword, other_phrases)
    own_token = bool(kw_tokens & own_tokens)
    other_token = bool(kw_tokens & other_tokens)
    if other_phrase and not own_phrase:
        return False
    if other_token and not own_token and not own_phrase:
        return False
    return own_phrase or own_token


def relevance_score(
    keyword: str,
    *,
    service: str,
    seeds: list[str],
) -> float:
    """0–1 overlap with this service. Full seed containment scores highest."""
    if _phrase_hit(keyword, seeds):
        return 1.0
    if _phrase_hit(keyword, [service]):
        return 0.9
    own = set().union(*(_tokens(p) for p in [service, *seeds])) if seeds or service else set()
    kw = _tokens(keyword)
    if not own or not kw:
        return 0.0
    overlap = len(kw & own) / max(len(own), 1)
    return min(0.85, 0.35 + overlap)


def _volume_score(volume: Any) -> float:
    if volume is None:
        return 0.0
    try:
        vol = float(volume)
    except (TypeError, ValueError):
        return 0.0
    if vol <= 0:
        return 0.0
    return min(1.0, math.log10(vol + 1) / 4.0)


def _commercial_score(row: dict[str, Any]) -> float:
    intent = _norm(row.get("intent"))
    score = 0.35
    if intent in _COMMERCIAL_INTENTS:
        score = 0.9
    elif intent == "informational":
        score = 0.25
    elif intent == "navigational":
        score = 0.15
    cpc = row.get("cpc")
    if isinstance(cpc, (int, float)) and cpc > 0:
        score = min(1.0, score + 0.1)
    return score


def _difficulty_penalty(difficulty: Any) -> float:
    if difficulty is None:
        return 0.0
    try:
        return max(0.0, min(1.0, float(difficulty) / 100.0))
    except (TypeError, ValueError):
        return 0.0


def score_keyword(
    row: dict[str, Any],
    *,
    service: str,
    seeds: list[str],
) -> float:
    """Rank a keyword that has already passed the relevance gate.

    Relevance is the largest term. Volume, commercial intent (Labs
    search_intent_info / CPC) and a competitor gap add to it. Keyword
    difficulty, when Labs returned it, subtracts.
    """
    relevance = relevance_score(str(row.get("keyword") or ""), service=service, seeds=seeds)
    volume = _volume_score(row.get("volume"))
    commercial = _commercial_score(row)
    gap = 1.0 if row.get("gap_flag") or row.get("competitor_domain") else 0.2
    difficulty = _difficulty_penalty(row.get("difficulty"))
    raw = (
        0.50 * relevance
        + 0.20 * volume
        + 0.15 * commercial
        + 0.15 * gap
        - 0.20 * difficulty
    )
    return round(max(0.0, min(1.0, raw)) * 100, 1)


def _near_key(keyword: str) -> str:
    tokens = sorted(_tokens(keyword))
    return " ".join(tokens) if tokens else _norm(keyword)


def select_keywords_for_service(
    rows: list[dict[str, Any]],
    *,
    service: str,
    service_id: str,
    seeds: list[str],
    other_phrases: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter, score, dedupe and rank one service's candidate rows.

    Returns (kept, rejected). Kept rows are stamped with the service and the
    seed they matched, and sorted best-first.
    """
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    best_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        seed = str(row.get("seed") or "").strip()
        if seed and _norm(seed) not in {_norm(s) for s in seeds}:
            rejected.append({**row, "rejection_reason": "foreign_seed"})
            continue
        if not keyword_matches_service(
            keyword, service=service, seeds=seeds, other_phrases=other_phrases
        ):
            rejected.append({**row, "rejection_reason": "irrelevant_to_service"})
            continue
        scored = dict(row)
        scored["labs_score"] = score_keyword(scored, service=service, seeds=seeds)
        scored["service"] = service
        scored["service_id"] = service_id
        if not scored.get("seed"):
            scored["seed"] = _best_seed(keyword, seeds)
        scored["parent_topic"] = scored.get("parent_topic") or scored.get("seed")
        scored["target"] = service
        scored["target_type"] = scored.get("target_type") or "service"
        key = _near_key(keyword)
        prev = best_by_key.get(key)
        if prev is None or float(scored["labs_score"]) > float(prev.get("labs_score") or 0):
            if prev is not None:
                rejected.append({**prev, "rejection_reason": "near_duplicate"})
            best_by_key[key] = scored
        else:
            rejected.append({**scored, "rejection_reason": "near_duplicate"})
    kept = sorted(best_by_key.values(), key=lambda r: float(r.get("labs_score") or 0), reverse=True)
    return kept, rejected


def _best_seed(keyword: str, seeds: list[str]) -> str | None:
    kw = _norm(keyword)
    kw_tokens = _tokens(keyword)
    best: str | None = None
    best_score = 0
    for seed in seeds:
        if _phrase_hit(keyword, [seed]) or _norm(seed) in kw:
            return seed
        overlap = len(kw_tokens & _tokens(seed))
        if overlap > best_score:
            best_score = overlap
            best = seed
    return best


def partition_keywords_by_service(
    rows: list[dict[str, Any]],
    plans: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    """Assign each row to the services it matches. A row is never given to a
    service whose seeds it does not describe."""
    usable = [p for p in plans if getattr(p, "seeds", None) and getattr(p, "status", "planned") == "planned"]
    grouped: dict[str, list[dict[str, Any]]] = {p.service_id: [] for p in usable}
    for plan in usable:
        others: list[str] = []
        for other in usable:
            if other.service_id == plan.service_id:
                continue
            others.append(other.service)
            others.extend(other.seeds)
        kept, _rejected = select_keywords_for_service(
            rows,
            service=plan.service,
            service_id=plan.service_id,
            seeds=list(plan.seeds),
            other_phrases=others,
        )
        grouped[plan.service_id] = kept
    return grouped


async def research_keywords_for_service(
    seeds: list[str],
    *,
    location_code: int,
    language_code: str,
    foreign_seeds: list[str] | None = None,
    min_volume: int = 10,
    limit: int = 40,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand only this service's seeds through Labs keyword research.

    Each seed is sent on its own to keyword suggestions, related keywords and
    keyword ideas. A failure on one seed is recorded and the others continue.
    """
    from app.services.keyword_seeding import expand_seed_dataforseo

    errors: list[str] = []
    if not language_code:
        return [], ["dataforseo_language_missing"]
    own = isolated_seeds(seeds, foreign_seeds or [])
    rows: list[dict[str, Any]] = []
    for seed in own:
        try:
            got, errs = await expand_seed_dataforseo(
                seed,
                location_code=location_code,
                language_code=language_code,
                min_volume=min_volume,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 — one seed must not sink the service
            log.warning("service_keyword_research_failed", seed=seed, error=str(exc)[:160])
            errors.append(f"dataforseo_keyword_research_error:{type(exc).__name__}")
            continue
        errors.extend(errs)
        for row in got:
            row["seed"] = seed
        rows.extend(got)
    return rows, errors


async def collect_competitor_keywords(
    *,
    client_domain: str | None,
    competitor_domains: list[str],
    location_code: int,
    language_code: str,
    min_volume: int = 10,
    limit: int = 40,
    max_domains: int = 4,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Labs competitor research for domains we already know or Labs discovered.

    Discovery uses competitors_domain on the client domain when one exists.
    Keywords come from ranked_keywords and keywords_for_site. A failed domain
    is skipped. No domain is invented when both sources are empty.
    """
    errors: list[str] = []
    if not language_code:
        return [], ["dataforseo_language_missing"]
    domains: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        host = dataforseo.normalize_target(raw)
        if not host or host in seen:
            return
        seen.add(host)
        domains.append(host)

    client_host = dataforseo.normalize_target(client_domain or "")
    for domain in competitor_domains:
        _add(domain)
    if client_host:
        try:
            discovered = await dataforseo.competitors_domain(
                client_host,
                limit=max_domains,
                location_code=location_code,
                language_code=language_code,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("competitors_domain_failed", error=str(exc)[:160])
            errors.append(f"dataforseo_competitors_domain_error:{type(exc).__name__}")
            discovered = []
        for item in discovered:
            _add(str(item.get("url") or ""))
    if client_host and client_host in seen:
        domains = [d for d in domains if d != client_host]
    if not domains:
        errors.append("competitor_research_unavailable")
        return [], errors

    rows: list[dict[str, Any]] = []
    for domain in domains[: max(1, max_domains)]:
        try:
            ranked, ranked_errs = await dataforseo.ranked_keywords(
                domain,
                location_code=location_code,
                language_code=language_code,
                limit=limit,
                min_volume=min_volume,
            )
            site_rows, site_errs = await dataforseo.keywords_for_site(
                domain,
                location_code=location_code,
                language_code=language_code,
                limit=limit,
                min_volume=min_volume,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("competitor_keywords_failed", domain=domain, error=str(exc)[:160])
            errors.append(f"dataforseo_competitor_keywords_error:{domain}:{type(exc).__name__}")
            continue
        errors.extend(ranked_errs)
        errors.extend(site_errs)
        for row in list(ranked) + list(site_rows):
            if not isinstance(row, dict):
                continue
            stamped = dict(row)
            stamped["competitor_domain"] = domain
            stamped.setdefault("gap_flag", True)
            stamped["source"] = stamped.get("source") or "dataforseo"
            rows.append(stamped)
    return rows, errors
