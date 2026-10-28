"""Multi-mode keyword seeding from Ahrefs (exact / related / broad).

Phase 5 seeding:
  - Expand each service seed through DataForSEO Labs (suggestions, related, ideas)
  - Keep a keyword only when DataForSEO reported a search volume greater than 9
  - Classify each keyword: exact | phrase | related | broad
  - Emit a multi-keyword dataset keyed by seed with nested clusters
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings
from app.integrations import dataforseo
from app.logging_config import get_logger
from app.services.keyword_opportunity import (
    detect_funnel,
    detect_intent,
    is_stale_year_keyword,
    normalize_provider_intent,
)
from app.services.keyword_provider_filters import noise_filters
from app.services.keyword_service_contract import INDUSTRY_ORIGIN
from app.services.keyword_relevance import (
    RelevanceContext,
    compact_cleaning_audit,
    empty_cleaning_audit,
    filter_relevant_keywords,
)
from app.services.keyword_variants import collapse_close_variants
from app.services.query_variants import anchor_term
from app.services.shared_keyword_pool.reuse import PoolContext, pooled_seed_fetch

log = get_logger("keyword_seeding")

MATCH_CLASSES = ("exact", "phrase", "related", "broad")
# Volume-qualified rows need a DataForSEO search volume strictly above this.
# 10 qualifies. 9, 0, and a missing volume do not.
SEARCH_VOLUME_ABOVE = 9
_METRIC_FIELDS = (
    "volume",
    "difficulty",
    "cpc",
    "traffic_potential",
    "trend",
    "competition",
    "bid_low",
    "bid_high",
    "volume_clickstream",
    "serp_referring_domains",
    "serp_domain_rank",
    "intent",
    "detected_language",
    "is_another_language",
)
_CLASS_RANK = {"exact": 0, "phrase": 1, "related": 2, "broad": 3}
_STOP = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# Form/UI labels that get concatenated onto a service name before it reaches
# seeding. Sent verbatim these become queries nobody searches ("Focus service
# AI-Powered Email Marketing Automation"), and every expansion from them is
# noise — that is how single-token "ai" overlap filled a B2B email-marketing
# pool with consumer AI tools. Only a LEADING label is a label: "email
# marketing services" keeps its trailing word.
_SEED_LABEL_PREFIX = re.compile(
    r"^\s*(?:focus\s+service|priority\s+service|top\s+service|primary|secondary|tertiary|service)"
    r"\s*[:\-–—]\s*",
    re.IGNORECASE,
)


def clean_provider_seed(seed: str) -> str:
    """Strip characters DataForSEO rejects (``&``, ``/``, parentheses) so compound
    service names like 'Website & Conversion Optimisation (CRO)' still expand.

    Also drops leading UI labels, a trailing dash-qualifier ("Email Marketing –
    email strategy" is the service plus a note, not one search term), and an
    unbalanced ``(`` — a list cut mid-item, where absorbing the fragment would
    invent a term the client never named.

    Parenthetical acronyms are dropped (they rarely help expansion) and separators
    become spaces. Falls back to the original seed if cleaning empties it."""
    raw = seed or ""
    q = _SEED_LABEL_PREFIX.sub("", raw, count=1)
    # Balanced parentheticals are dropped whole; an unbalanced one truncates.
    q = re.sub(r"\([^)]*\)", " ", q)
    q = q.split("(", 1)[0]
    # A dash-qualifier after the service name is commentary, not the keyword.
    q = re.split(r"\s+[–—]\s+|\s+-\s+", q, maxsplit=1)[0]
    q = q.replace("&", " ").replace("/", " ")
    q = re.sub(r"[^\w\s-]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q or raw.strip()


def _tokens(kw: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(kw)) if t not in _STOP and len(t) > 1}


def _singular(tok: str) -> str:
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok


def _token_list(s: str) -> list[str]:
    """Ordered, punctuation-free, singularized tokens — used for contiguity tests."""
    return [_singular(t) for t in re.findall(r"[a-z0-9]+", _norm(s))]


def _is_contiguous(kw_toks: list[str], seed_toks: list[str]) -> bool:
    """True when the seed's tokens appear back-to-back inside the keyword."""
    n = len(seed_toks)
    if not n or n > len(kw_toks):
        return False
    return any(kw_toks[i : i + n] == seed_toks for i in range(len(kw_toks) - n + 1))


# Endpoint groups — only used to break the phrase/related tie for one-word seeds,
# where "contiguous" and "contains every token" are the same test.
_PHRASE_SOURCES = {"matching-terms-phrase", "dataforseo_keyword_suggestions"}
_BROAD_SOURCES = {"related-terms", "dataforseo_keyword_ideas"}


def classify_expansion(keyword: str, seed: str, *, source_hint: str | None = None) -> str:
    """Classify a keyword against its seed by the ACTUAL relationship.

    Provider feeds are unreliable labels — Ahrefs' ``terms`` feed happily returns
    contiguous phrase matches, so trusting the endpoint collapsed everything into
    ``related``. The relationship decides instead:

      exact   — the seed itself (punctuation / plural insensitive)
      phrase  — seed tokens appear contiguously inside the keyword
      related — every seed token is present, but not contiguously
      broad   — partial or purely topical overlap

    One-word seeds are the exception: "contiguous" and "every token present" are
    identical there, so the endpoint that returned the row decides phrase vs
    related vs broad (each endpoint is purpose-built for one of those).
    """
    kw_toks = _token_list(keyword)
    seed_toks = _token_list(seed)
    if not kw_toks or not seed_toks:
        return "broad"
    if kw_toks == seed_toks:
        return "exact"

    contiguous = _is_contiguous(kw_toks, seed_toks)
    seed_set = {t for t in seed_toks if t not in _STOP and len(t) > 1}
    kw_set = {t for t in kw_toks if t not in _STOP and len(t) > 1}
    covers_all = bool(seed_set) and seed_set.issubset(kw_set)

    if len(seed_toks) == 1:
        if not (contiguous or covers_all):
            return "broad"
        if source_hint in _BROAD_SOURCES:
            return "broad"
        if source_hint in _PHRASE_SOURCES or source_hint is None:
            return "phrase"
        return "related"

    if contiguous:
        return "phrase"
    if covers_all:
        return "related"
    return "broad"


# Back-compat aliases — both provider paths now share one truthful classifier.
def classify_dataforseo_expansion(keyword: str, seed: str) -> str:
    return classify_expansion(keyword, seed)


def classify_vs_seed(keyword: str, seed: str, *, source_hint: str | None = None) -> str:
    _ = source_hint
    return classify_expansion(keyword, seed)


def volume_qualifies(volume: Any, *, min_volume: int = 10) -> bool:
    """True only for an integer DataForSEO volume greater than 9.

    A missing volume stays null and does not pass. A caller that asks for a
    higher minimum (``min_volume`` of 50 means volume >= 50) still applies,
    and that minimum can never drop the floor to 9 or below.
    """
    if volume is None:
        return False
    try:
        value = int(volume)
    except (TypeError, ValueError):
        return False
    if value <= SEARCH_VOLUME_ABOVE:
        return False
    floor = int(min_volume)
    if floor > SEARCH_VOLUME_ABOVE and value < floor:
        return False
    return True


def _passes_volume(row: dict[str, Any], min_volume: int) -> bool:
    return volume_qualifies(row.get("volume"), min_volume=min_volume)


# How a row's search volume is known. The minimum-volume threshold applies only to
# MEASURED volume above zero; a row with no volume (or zero, which is no
# measurement of demand) is kept and flagged unverified instead of being dropped.
VOLUME_MEASURED = "measured"
VOLUME_ESTIMATED = "estimated"
VOLUME_UNVERIFIED = "unverified"


def volume_status(row: dict[str, Any]) -> str:
    if row.get("provider_fallback") or row.get("volume_estimated"):
        return VOLUME_ESTIMATED
    try:
        value = int(row.get("volume"))
    except (TypeError, ValueError):
        return VOLUME_UNVERIFIED
    return VOLUME_MEASURED if value > 0 else VOLUME_UNVERIFIED


def keep_for_volume(row: dict[str, Any], min_volume: int) -> bool:
    """Drop only a measured volume that is below the floor; keep everything else."""
    if volume_status(row) != VOLUME_MEASURED:
        return True
    return volume_qualifies(row.get("volume"), min_volume=min_volume)


def flag_volume(row: dict[str, Any]) -> dict[str, Any]:
    status = volume_status(row)
    row["volume_status"] = status
    row["volume_unverified"] = status == VOLUME_UNVERIFIED
    return row


def volume_coverage(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many rows have measured, estimated or unverified volume."""
    counts = {VOLUME_MEASURED: 0, VOLUME_ESTIMATED: 0, VOLUME_UNVERIFIED: 0}
    for row in rows:
        if isinstance(row, dict):
            counts[volume_status(row)] += 1
    counts["total"] = sum(counts.values())
    return counts


# Funnel coverage: the informational (awareness, TOFU) share of a seed's keywords
# below which the seed gets an informational-only top-up from keyword ideas.
INFORMATIONAL_FUNNEL = "TOFU"
INFORMATIONAL_SHARE_FLOOR = 0.15


def funnel_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"TOFU": 0, "MOFU": 0, "BOFU": 0}
    for row in rows:
        stage = str(row.get("funnel") or "")
        if stage in counts:
            counts[stage] += 1
    return counts


def _merge_keyword_row(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Keep one keyword record. A missing metric must not erase a measured one."""
    merged = dict(current)
    sources = list(current.get("sources") or [])
    for item in list(incoming.get("sources") or []):
        if item and item not in sources:
            sources.append(item)
    endpoint = incoming.get("dataforseo_endpoint") or incoming.get("ahrefs_endpoint")
    if endpoint and endpoint not in sources:
        sources.append(str(endpoint))
    for field in _METRIC_FIELDS:
        value = incoming.get(field)
        if value is None:
            continue
        existing = merged.get(field)
        if existing is None:
            merged[field] = value
        elif (
            field == "volume"
            and isinstance(existing, (int, float))
            and isinstance(value, (int, float))
            and int(value) > int(existing)
        ):
            merged[field] = int(value)
    for field in ("source", "seed", "parent_topic", "match_class", "funnel", "intent_basis", "provider_intent"):
        if merged.get(field) in (None, "") and incoming.get(field) not in (None, ""):
            merged[field] = incoming.get(field)
    merged["sources"] = sources
    return merged


def _mock_expansions(seed: str, *, min_volume: int) -> list[dict[str, Any]]:
    """Deterministic mock expansions covering all four match classes."""
    s = seed.strip()
    toks = _token_list(s)
    head, tail = (toks[0], toks[-1]) if toks else (s, s)
    _P, _T, _B = "matching-terms-phrase", "matching-terms", "related-terms"
    base = [
        # exact
        {"keyword": s, "volume": max(min_volume + 90, 100), "difficulty": 28, "ep": _P},
        # phrase — seed appears contiguously
        {"keyword": f"{s} pricing", "volume": max(min_volume + 40, 55), "difficulty": 32, "ep": _P},
        {"keyword": f"best {s}", "volume": max(min_volume + 25, 40), "difficulty": 35, "ep": _P},
        {"keyword": f"{s} near me", "volume": max(min_volume + 15, 30), "difficulty": 22, "ep": _P},
        # related — every seed token present, split apart
        {
            "keyword": f"{head} and {tail} agency" if len(toks) > 1 else f"{s} agency",
            "volume": max(min_volume + 12, 28),
            "difficulty": 30,
            "ep": _T,
        },
        # broad — topical, does not carry the whole seed
        {"keyword": f"{tail} strategy", "volume": max(min_volume + 8, 22), "difficulty": 40, "ep": _B},
        {"keyword": f"{head} tips", "volume": max(min_volume + 5, 18), "difficulty": 18, "ep": _B},
    ]
    out: list[dict[str, Any]] = []
    for row in base:
        if int(row["volume"]) <= min_volume:
            continue
        kw = str(row["keyword"])
        endpoint = str(row.pop("ep"))
        kw_intent = detect_intent(kw)
        out.append(
            {
                **row,
                "cpc": 2.5,
                "traffic_potential": int(row["volume"]) * 2,
                "source": "mock",
                "volume_estimated": True,
                "ahrefs_endpoint": endpoint,
                "seed": s,
                "intent": kw_intent,
                "funnel": detect_funnel(kw, kw_intent),
                "parent_topic": s,
                "match_class": classify_expansion(kw, s, source_hint=endpoint),
            }
        )
    return out




# Each match class has a dedicated DataForSEO source:
#   phrase  -> keyword_suggestions (full-text: queries containing the seed)
#   related -> related_keywords    (Google "searches related to")
#   broad   -> keyword_ideas       (category-based; need not contain the seed)
_DFS_SOURCE_FOR_CLASS = {
    "phrase": "keyword_suggestions",
    "related": "related_keywords",
    "broad": "keyword_ideas",
}

# One DataForSEO page per buyer stage. The intent filter is what the Labs
# field is called; the funnel name is what the report shows.
_FUNNEL_INTENTS = (
    ("TOFU", "informational"),
    ("MOFU", "commercial"),
    ("BOFU", "transactional"),
)


_FUNNEL_INTENT_VALUES = [intent for _stage, intent in _FUNNEL_INTENTS]


def _intent_field(endpoint: str) -> str:
    """The Labs path for search intent. Related keywords nest it under keyword_data."""
    return (
        "keyword_data.search_intent_info.main_intent"
        if endpoint == "related_keywords"
        else "search_intent_info.main_intent"
    )


def _intent_filter(endpoint: str, intent: str) -> list[Any]:
    return [_intent_field(endpoint), "=", intent]


def _volume_field(endpoint: str) -> str:
    return (
        "keyword_data.keyword_info.search_volume"
        if endpoint == "related_keywords"
        else "keyword_info.search_volume"
    )


def _capped_followups(
    rows: list[dict[str, Any]], *, endpoint: str, page: int, provider_min: int
) -> list[tuple[str, int, list[list[Any]]]]:
    """What each funnel intent still needs after a full all-intents page.

    Labs sorts by volume, highest first, with null volumes last (checked live
    2026-10-18). So a full page holds each intent's top rows, and the rest of an
    intent's own page lies at or below the page's lowest volume, or among the
    nulls once the page reached them. Each follow-up asks only for that remainder,
    so together they return what one page per intent would have.
    Returns ``(intent, limit, filters)`` per intent that is still short.
    """
    field = _volume_field(endpoint)
    measured = [r["volume"] for r in rows if r.get("volume") is not None]
    reached_nulls = len(measured) < len(rows)
    out: list[tuple[str, int, list[list[Any]]]] = []
    for intent in _FUNNEL_INTENT_VALUES:
        mine = [r for r in rows if r.get("intent") == intent]
        if len(mine) >= page:
            continue
        if reached_nulls or not measured:
            # Every measured row is already here; only nulls remain.
            mine_measured = sum(1 for r in mine if r.get("volume") is not None)
            out.append((intent, page - mine_measured, [_intent_filter(endpoint, intent), [field, "=", None]]))
            continue
        cut = min(measured)
        # Rows at exactly the cut may come back again; they are de-duplicated.
        ties = sum(1 for r in mine if r.get("volume") == cut)
        band = [[[field, ">=", provider_min], "and", [field, "<=", cut]], "or", [field, "=", None]]
        out.append((intent, min(page, page - len(mine) + ties), [band, _intent_filter(endpoint, intent)]))
    return out


async def expand_seed_dataforseo(
    seed: str,
    *,
    location_code: int,
    min_volume: int = 10,
    limit: int = 60,
    classes: tuple[str, ...] | None = None,
    language_code: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand a seed via DataForSEO, pulling a dedicated source per match class.

    ``classes`` limits the fetch to the classes still missing, so we only spend
    API calls on the buckets that Ahrefs failed to fill. Rows are classified by
    the real keyword/seed relationship, not by which endpoint returned them."""
    errors: list[str] = []
    seed = (seed or "").strip()
    if not seed:
        return [], errors

    wanted = tuple(classes) if classes else ("phrase", "related", "broad")
    endpoints = {
        _DFS_SOURCE_FOR_CLASS[c] for c in wanted if c in _DFS_SOURCE_FOR_CLASS
    }
    if not endpoints:
        return [], errors

    query = clean_provider_seed(seed)
    fetchers = {
        "keyword_suggestions": dataforseo.keyword_suggestions,
        "related_keywords": dataforseo.related_keywords,
        "keyword_ideas": dataforseo.keyword_ideas,
    }
    ordered = [e for e in ("keyword_suggestions", "related_keywords", "keyword_ideas") if e in endpoints]
    depth = int(get_settings().keyword_related_depth or 2)
    language = language_code or get_settings().dataforseo_language_code
    if not language:
        errors.append("dataforseo_language_missing")
        return [], errors
    # Labs filters are inclusive. Volume must be greater than 9, so ask for
    # at least 10 unless the caller set a higher minimum.
    provider_min = max(int(min_volume), SEARCH_VOLUME_ABOVE + 1)

    page = max(1, min(int(limit), dataforseo.MAX_LABS_LIMIT))

    async def _fetch(endpoint: str, filters: list[list[Any]], *, page_limit: int = page, floor: bool = True):
        kwargs: dict[str, Any] = {
            "location_code": location_code,
            "language_code": language,
            "limit": page_limit,
            # Follow-ups carry their own volume band in ``filters``.
            "min_volume": provider_min if floor else None,
            "keep_null_volume": floor,
            # Labs bills per row: do not pay for rows cleaning always rejects.
            "extra_filters": [*filters, *noise_filters(endpoint)],
        }
        if endpoint == "related_keywords":
            kwargs["depth"] = depth
        return await fetchers[endpoint](query, **kwargs)

    called: list[str] = []
    failed_endpoints: list[str] = []

    def _failed(endpoint: str, intent: str, exc: BaseException) -> None:
        errors.append(f"dataforseo_{endpoint}_{intent}_error")
        failed_endpoints.append(f"{endpoint}:{intent}")
        log.warning(
            "keyword_endpoint_failed",
            seed=seed,
            endpoint=endpoint,
            intent=intent,
            error=type(exc).__name__,
        )

    async def _endpoint_rows(endpoint: str) -> list[dict[str, Any]]:
        # One page for all three funnel intents instead of one page each: Labs
        # charges per request, and most pages are not full. A page that is not
        # full holds every matching row, the same rows three pages would return.
        # Only a full page needs follow-ups, for what each intent is still short.
        called.append(f"{endpoint}:all_intents")
        try:
            rows, errs = await _fetch(endpoint, [[_intent_field(endpoint), "in", _FUNNEL_INTENT_VALUES]])
        except Exception as exc:  # noqa: BLE001 — one endpoint failing must not sink the seed
            _failed(endpoint, "all_intents", exc)
            return []
        errors.extend(errs)
        if len(rows) < page or not getattr(get_settings(), "keyword_labs_followups", False):
            return rows
        followups = _capped_followups(rows, endpoint=endpoint, page=page, provider_min=provider_min)
        called.extend(f"{endpoint}:{intent}" for intent, _n, _f in followups)
        results = await asyncio.gather(
            *[_fetch(endpoint, f, page_limit=n, floor=False) for _intent, n, f in followups],
            return_exceptions=True,
        )
        merged = list(rows)
        seen = {str(r.get("keyword") or "").lower() for r in rows}
        for (intent, _n, _f), res in zip(followups, results):
            if isinstance(res, BaseException):
                _failed(endpoint, intent, res)
                continue
            more, errs = res
            errors.extend(errs)
            for r in more:
                key = str(r.get("keyword") or "").lower()
                if key not in seen:
                    seen.add(key)
                    merged.append(r)
        return merged

    per_endpoint = await asyncio.gather(*[_endpoint_rows(endpoint) for endpoint in ordered])

    out: list[dict[str, Any]] = []
    for endpoint, rows in zip(ordered, per_endpoint):
        out.extend(dataforseo_rows(rows, endpoint=endpoint, seed=seed, query=query))
    log.info(
        "keyword_seed_endpoints",
        seed=seed,
        endpoints_called=called,
        endpoints_failed=failed_endpoints,
        keywords_retrieved=len(out),
    )
    return out, errors


def dataforseo_rows(
    rows: list[dict[str, Any]], *, endpoint: str, seed: str, query: str | None = None
) -> list[dict[str, Any]]:
    """Keyword rows from one DataForSEO endpoint, each with intent and funnel stamped.

    Every endpoint (suggestions, related, ideas, and the informational variant and
    top-up calls) goes through here, so no row leaves without both labels.
    """
    query = query or clean_provider_seed(seed)
    seed_aliases = {_norm(seed), _norm(query)}
    out: list[dict[str, Any]] = []
    for r in rows:
        kw = str(r.get("keyword") or "").strip()
        if not kw or is_stale_year_keyword(kw):
            continue
        is_seed = _norm(kw) in seed_aliases
        # Only a label that maps onto the four valid intents counts as provider
        # evidence; anything else is left to the heuristic.
        provider_intent = normalize_provider_intent(r.get("intent") or r.get("search_intent"))
        row_intent = detect_intent(kw, provider_intent)
        out.append(
            {
                "keyword": kw,
                "volume": r.get("volume"),
                "difficulty": r.get("difficulty"),
                "cpc": r.get("cpc"),
                "traffic_potential": None,
                "intent": row_intent,
                # A label the provider gave us is evidence; one inferred
                # from the keyword text is not. Keep them distinguishable.
                "intent_basis": "measured" if provider_intent else "unmeasured",
                "provider_intent": provider_intent,
                # Measured fields the Labs response already carried. Without
                # these the row reached scoring with no demand direction and
                # no commercial value, and had to be re-priced by a second call.
                "trend": r.get("trend"),
                "competition": r.get("competition"),
                "bid_low": r.get("bid_low"),
                "bid_high": r.get("bid_high"),
                "volume_clickstream": r.get("volume_clickstream"),
                "serp_referring_domains": r.get("serp_referring_domains"),
                "serp_domain_rank": r.get("serp_domain_rank"),
                "detected_language": r.get("detected_language"),
                "is_another_language": r.get("is_another_language"),
                "funnel": detect_funnel(kw, row_intent),
                "parent_topic": seed,
                "source": "dataforseo",
                "sources": [endpoint],
                "ahrefs_endpoint": f"dataforseo_{endpoint}",
                "seed": seed,
                "match_class": (
                    "exact"
                    if is_seed
                    else classify_expansion(
                        kw, query, source_hint=f"dataforseo_{endpoint}"
                    )
                ),
            }
        )
    return out


async def expand_seed(
    seed: str,
    *,
    country: str = "us",
    min_volume: int = 10,
    limit_per_mode: int = 40,
    location_code: int | None = None,
    language_code: str | None = None,
    enable_dataforseo_fallback: bool = True,
    min_keywords: int = 20,
    variants: Callable[[], Awaitable[list[str]]] | None = None,
    service: str | None = None,
    report: dict[str, Any] | None = None,
    pool: PoolContext | None = None,
    informational_floor: float = INFORMATIONAL_SHARE_FLOOR,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand one seed into exact / phrase / related / broad via DataForSEO.

    DataForSEO is the sole provider. If it returns too few rows, top up with
    deterministic coverage expansions (>= min_keywords) — those are flagged
    ``provider_fallback`` and ``volume_estimated`` so downstream reports can mark
    them as estimates rather than presenting them as measured demand.

    ``variants`` returns this seed's informational query variants (see
    ``query_variants``). They widen the pull with one extra keyword-ideas call, and
    when the seed's informational (TOFU) share is still under ``informational_floor``
    (15% for a service seed, 10% for an industry seed) they drive a second,
    informational-only call. ``report`` (if given) receives the seed's funnel
    coverage before and after.

    ``pool`` (if given) serves the seed's main provider pull from the shared keyword
    pool when an earlier client already fetched it, and saves a fresh pull for later
    clients. The variant pulls and the informational top-up stay live: they depend on
    this client's own variants and anchor filter. Mock-provider mode returns above and
    never reaches the pool.
    """
    errors: list[str] = []
    settings = get_settings()
    seed = (seed or "").strip()
    if not seed:
        return [], errors

    if settings.use_mock_providers:
        return _mock_expansions(seed, min_volume=min_volume), errors

    by_kw: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    stats = {
        "retrieved": 0,
        "duplicates_removed": 0,
        "rejected_volume": 0,
        "missing_volume": 0,
    }

    def _ingest(rows: list[dict[str, Any]], *, endpoint: str, source: str) -> None:
        for r in rows:
            stats["retrieved"] += 1
            kw = str(r.get("keyword") or "").strip()
            key = _norm(kw)
            if not key or is_stale_year_keyword(kw):
                continue
            hint = str(r.get("ahrefs_endpoint") or endpoint)
            row = {
                **r,
                "seed": seed,
                "source": r.get("source") or source,
                "ahrefs_endpoint": hint,
                "match_class": classify_expansion(kw, seed, source_hint=hint),
            }
            if row.get("volume") is None:
                stats["missing_volume"] += 1
                row["volume"] = None
            incoming = list(row.get("sources") or [])
            if not incoming and row.get("dataforseo_endpoint"):
                incoming = [str(row.get("dataforseo_endpoint"))]
            row["sources"] = incoming
            prev = pending.get(key)
            if prev:
                stats["duplicates_removed"] += 1
                pending[key] = _merge_keyword_row(prev, row)
            else:
                pending[key] = row

    # DataForSEO is the only keyword provider. Each match class has its own
    # dedicated endpoint — suggestions=phrase, related=related, ideas=broad — so
    # a single pass covers every class. Previously SEMrush was primary and
    # DataForSEO only topped up the classes SEMrush left empty; with no SEMrush
    # subscription that left every class empty and fell through to fabricated
    # coverage rows.
    if location_code is None:
        errors.append("dataforseo_location_missing")
    else:
        language = language_code or getattr(settings, "dataforseo_language_code", None)
        variant_limit = max(20, min(100, int(limit_per_mode) // 2))
        anchor = anchor_term(seed, service)
        on_topic = [["keyword", "like", f"%{anchor}%"]] if anchor else []

        async def _variant_queries() -> list[str]:
            if variants is None or not language:
                return []
            try:
                return list(await variants())
            except Exception as exc:  # noqa: BLE001 — variants are optional
                errors.append(f"query_variants_failed:{type(exc).__name__}")
                return []

        async def _ideas(queries: list[str], extra: list[list[Any]], tag: str) -> list[dict[str, Any]]:
            rows, errs = await dataforseo.keyword_ideas(
                seed,
                keywords=queries,
                location_code=location_code,
                language_code=language,
                limit=variant_limit,
                min_volume=max(int(min_volume), SEARCH_VOLUME_ABOVE + 1),
                keep_null_volume=True,
                extra_filters=[*extra, *noise_filters("keyword_ideas")],
            )
            errors.extend(errs)
            out = dataforseo_rows(rows, endpoint="keyword_ideas", seed=seed)
            for row in out:
                row["query_variant"] = True
                row["variant_queries"] = list(queries)
                row["sources"] = [tag]
                row["ahrefs_endpoint"] = f"dataforseo_{tag}"
                if tag == "keyword_ideas_informational_topup":
                    row["informational_topup"] = True
            return out

        async def _variant_pull() -> tuple[list[str], list[dict[str, Any]]]:
            queries = await _variant_queries()
            if not queries:
                return [], []
            try:
                # Research and consideration stage only: keyword ideas is category-based,
                # and without this half of a live pull was local clinic names
                # (navigational) rather than the questions the variants ask.
                research = [*on_topic, ["search_intent_info.main_intent", "in", ["informational", "commercial"]]]
                return queries, await _ideas(queries, research, "keyword_ideas_variants")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"dataforseo_keyword_ideas_variants_error:{type(exc).__name__}")
                return queries, []

        async def _seed_pull() -> tuple[list[dict[str, Any]], list[str]]:
            """The seed's main provider pull, from the shared pool when one is given."""
            classes = ("phrase", "related", "broad")
            if pool is None:
                return await expand_seed_dataforseo(
                    seed,
                    location_code=location_code,
                    min_volume=min_volume,
                    limit=limit_per_mode,
                    classes=classes,
                    language_code=language_code,
                )
            return await pooled_seed_fetch(
                seed,
                pool=pool,
                service=service,
                fetch=expand_seed_dataforseo,
                location_code=location_code,
                # The language the provider is actually asked in, so the pool key matches.
                language_code=language,
                min_volume=min_volume,
                limit=limit_per_mode,
                classes=classes,
            )

        if getattr(settings, "keyword_extra_idea_pulls", False):
            (df_rows, df_errs), (queries, variant_rows) = await asyncio.gather(
                _seed_pull(),
                _variant_pull(),
            )
        else:
            df_rows, df_errs = await _seed_pull()
            queries, variant_rows = [], []
        errors.extend(df_errs)
        _ingest(df_rows, endpoint="dataforseo", source="dataforseo")
        _ingest(variant_rows, endpoint="dataforseo_keyword_ideas_variants", source="dataforseo")

        def _settle() -> None:
            by_kw.clear()
            stats["rejected_volume"] = 0
            for key, row in pending.items():
                if keep_for_volume(row, min_volume):
                    by_kw[key] = flag_volume(row)
                else:
                    stats["rejected_volume"] += 1

        _settle()
        before = funnel_counts(list(by_kw.values()))
        total = sum(before.values())
        share = (before[INFORMATIONAL_FUNNEL] / total) if total else 0.0
        topped_up = 0
        if getattr(settings, "keyword_extra_idea_pulls", False) and total and share < informational_floor and language:
            # Too few awareness keywords: ask keyword ideas for informational ones
            # only, from the variants (or the seed itself when there are none), and
            # still about the seed's subject.
            informational = [["search_intent_info.main_intent", "=", "informational"], *on_topic]
            try:
                topup = await _ideas(queries or [seed], informational, "keyword_ideas_informational_topup")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"dataforseo_informational_topup_error:{type(exc).__name__}")
                topup = []
            new_keys = {_norm(str(r.get("keyword") or "")) for r in topup} - set(pending)
            topped_up = len(new_keys)
            _ingest(topup, endpoint="dataforseo_keyword_ideas_informational_topup", source="dataforseo")
            _settle()
        after = funnel_counts(list(by_kw.values()))
        if report is not None:
            report.update(
                {
                    "seed": seed,
                    "variant_queries": list(queries),
                    "variant_keywords": len(variant_rows),
                    "funnel_before": before,
                    "informational_share_before": round(share, 3),
                    "topped_up": topped_up,
                    "funnel_after": after,
                    "informational_share_after": round(
                        after[INFORMATIONAL_FUNNEL] / max(1, sum(after.values())), 3
                    ),
                }
            )

    # When live providers are plan-blocked or unreachable there is nothing to
    # expand. Say so loudly and return the little we have. Padding the pool with
    # invented keywords and invented volumes made an outage look like a result,
    # and those rows went on to become cluster primaries and page targets.
    live_classes = {
        str(r.get("match_class"))
        for r in by_kw.values()
        if str(r.get("source") or "") == "dataforseo"
        and str(r.get("ahrefs_endpoint") or "") != "seed"
    }
    if not (live_classes & {"phrase", "related", "broad"}):
        errors.append("provider_expansion_unavailable")
        log.warning(
            "keyword_expansion_unavailable",
            seed=seed,
            kept=len(by_kw),
            errors=errors[:5],
        )
    log.info(
        "keyword_seed_fetch",
        seed=seed,
        keywords_retrieved=stats["retrieved"],
        duplicates_removed=stats["duplicates_removed"],
        rejected_volume=stats["rejected_volume"],
        missing_volume=stats["missing_volume"],
        keywords_kept=len(by_kw),
    )

    return list(by_kw.values()), errors


def summarize_keyword_data_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count how much of a keyword pool carries estimated rather than measured volume.

    When the live providers are unreachable, ``build_coverage_expansions``
    supplies deterministic keywords with ESTIMATED volumes so the phase stays
    usable. Those rows are flagged on the way in — this is what makes the flag
    legible to reports, so invented demand is never presented as measured.
    """
    measured = 0
    estimated = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("provider_fallback") or row.get("volume_estimated"):
            estimated += 1
        else:
            measured += 1

    total = measured + estimated
    share = round((estimated / total) * 100, 1) if total else 0.0
    warning = None
    if estimated:
        warning = (
            f"{estimated} of {total} keywords ({share:.0f}%) carry ESTIMATED search "
            "volume, not measured provider data — the live keyword APIs were "
            "unavailable for this run. Treat those volumes as placeholders."
        )
    return {
        "measured_count": measured,
        "estimated_count": estimated,
        "total": total,
        "estimated_share": share,
        "has_estimated_volumes": estimated > 0,
        "all_estimated": bool(total) and estimated == total,
        "warning": warning,
    }


def build_seed_clusters(
    rows: list[dict[str, Any]],
    seeds: list[str],
    *,
    seed_targets: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Group expanded keywords into per-seed clusters by match_class.

    seed_targets maps normalized seed -> {"target": str, "target_type": str} so
    each cluster records the service/page it expands (not just CDD keywords).
    """
    seed_targets = seed_targets or {}
    by_seed: dict[str, dict[str, Any]] = {}
    for s in seeds:
        key = _norm(s)
        if not key:
            continue
        meta = seed_targets.get(key) or {}
        by_seed[key] = {
            "seed": s.strip(),
            "target": meta.get("target") or s.strip(),
            "target_type": meta.get("target_type") or "keyword",
            "exact": [],
            "phrase": [],
            "related": [],
            "broad": [],
            "keyword_count": 0,
            "volume_sum": 0,
        }
        for field in ("page_type", "page_path", "parent_segment", "seed_origin", "synonym_of"):
            if meta.get(field):
                by_seed[key][field] = meta[field]

    orphan_seed = "_unassigned"
    for row in rows:
        seed = str(row.get("seed") or "").strip()
        key = _norm(seed)
        bucket = by_seed.get(key)
        if not bucket:
            # Attach to closest seed by token overlap
            kw_toks = _tokens(str(row.get("keyword") or ""))
            best_key = None
            best_overlap = 0
            for sk, meta in by_seed.items():
                overlap = len(kw_toks & _tokens(meta["seed"]))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_key = sk
            if best_key and best_overlap:
                bucket = by_seed[best_key]
                row = {**row, "seed": bucket["seed"]}
            else:
                continue
        cls = str(row.get("match_class") or "broad")
        if cls not in MATCH_CLASSES:
            cls = "broad"
        entry_kw = str(row.get("keyword") or "")
        entry_intent = detect_intent(entry_kw, row.get("intent"))
        entry = {
            "keyword": row.get("keyword"),
            "volume": row.get("volume"),
            "difficulty": row.get("difficulty"),
            "cpc": row.get("cpc"),
            "intent": entry_intent,
            "funnel": row.get("funnel") or detect_funnel(entry_kw, entry_intent),
            # Where the intent came from, so cluster rows can show it.
            "intent_basis": row.get("intent_basis"),
            "provider_intent": row.get("provider_intent"),
            "parent_topic": row.get("parent_topic"),
            "traffic_potential": row.get("traffic_potential"),
            "match_class": cls,
            "source": row.get("source") or "dataforseo",
            "ahrefs_endpoint": row.get("ahrefs_endpoint"),
            "below_volume_floor": bool(row.get("below_volume_floor")),
            # Relevance verdicts must survive into the cluster rows. Downstream
            # enforcement (select_topics_from_service_clusters, the CDP cluster
            # list, brief members) reads CLUSTER rows, not the flat dataset — so
            # a flag left out of this whitelist is a flag that silently does
            # nothing.
            "relevance_reason": row.get("relevance_reason"),
            "relevance_unverified": bool(row.get("relevance_unverified")),
            "commercial_target_eligible": row.get("commercial_target_eligible"),
            "reroute_reason": row.get("reroute_reason"),
            "gap_flag": bool(row.get("gap_flag")),
            "provider_fallback": bool(row.get("provider_fallback")),
            "volume_estimated": bool(row.get("volume_estimated")),
            "volume_status": row.get("volume_status") or volume_status(row),
            "volume_unverified": bool(row.get("volume_unverified")),
            "query_variant": bool(row.get("query_variant")),
            "informational_topup": bool(row.get("informational_topup")),
        }
        bucket[cls].append(entry)
        bucket["keyword_count"] += 1
        if isinstance(entry.get("volume"), (int, float)):
            bucket["volume_sum"] += int(entry["volume"])

    # Sort each class by volume desc
    out: list[dict[str, Any]] = []
    for s in seeds:
        key = _norm(s)
        bucket = by_seed.get(key)
        if not bucket:
            continue
        # One search worded several ways ("seo agency", "agency seo", "seo
        # agencies") counts once per seed, across classes; the other wordings
        # stay listed on the kept keyword (keyword_variants).
        kept, folded = collapse_close_variants([r for cls in MATCH_CLASSES for r in bucket[cls]])
        if folded:
            for cls in MATCH_CLASSES:
                bucket[cls] = [r for r in kept if r.get("match_class") == cls]
            bucket["keyword_count"] = len(kept)
            bucket["volume_sum"] = sum(
                int(r["volume"]) for r in kept if isinstance(r.get("volume"), (int, float))
            )
        bucket["close_variants_folded"] = len(folded)
        for cls in MATCH_CLASSES:
            bucket[cls] = sorted(
                bucket[cls],
                key=lambda r: (-(r.get("volume") or 0), str(r.get("keyword") or "")),
            )
        out.append(bucket)
    _ = orphan_seed
    return out


def flatten_dataset(seed_clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat multi-keyword dataset: every keyword with seed + match_class."""
    flat: list[dict[str, Any]] = []
    # Preserve the same keyword under every seed it belongs to. Deduplicating only
    # by keyword hid valid seed-to-keyword associations in the exported dataset.
    seen: set[tuple[str, str, str]] = set()
    for cluster in seed_clusters:
        seed = cluster.get("seed")
        target = cluster.get("target")
        target_type = cluster.get("target_type")
        # A service-scoped run stamps its clusters with the service that owns the seed.
        # Carry it onto every keyword row so a keyword never loses its service, and
        # keep the seed's origin so keywords found through a synonym seed stay traceable.
        owner = {
            k: cluster[k] for k in ("service", "service_id", "seed_origin") if cluster.get(k)
        }
        for cls in MATCH_CLASSES:
            for row in cluster.get(cls) or []:
                kw = _norm(str(row.get("keyword") or ""))
                # De-duplicate per (service, seed, keyword): the same seed text under two
                # services is two separate associations and must both survive.
                association = (_norm(str(owner.get("service") or "")), _norm(str(seed or "")), kw)
                if not kw or association in seen:
                    continue
                seen.add(association)
                flat.append(
                    {
                        **row,
                        "seed": seed,
                        "target": target,
                        "target_type": target_type,
                        "match_class": cls,
                        **owner,
                    }
                )
    return flat


def filter_seed_clusters(
    seed_clusters: list[dict[str, Any]],
    allowed_keywords: set[str],
) -> list[dict[str, Any]]:
    """Keep only keywords that survived cleaning / LLM relevance filters.

    Used so the Multi-mode seeding UI shows the exact same keyword set that
    Topic Plan / clustering consume.
    """
    allowed = {_norm(k) for k in allowed_keywords if _norm(k)}
    out: list[dict[str, Any]] = []
    for cluster in seed_clusters:
        if not isinstance(cluster, dict):
            continue
        cleaned = {
            **cluster,
            "exact": [],
            "phrase": [],
            "related": [],
            "broad": [],
            "keyword_count": 0,
            "volume_sum": 0,
            "classes_missing": [],
        }
        for cls in MATCH_CLASSES:
            kept: list[dict[str, Any]] = []
            for row in cluster.get(cls) or []:
                if not isinstance(row, dict):
                    continue
                if _norm(str(row.get("keyword") or "")) not in allowed:
                    continue
                kept.append(row)
            cleaned[cls] = kept
            cleaned["keyword_count"] += len(kept)
            for row in kept:
                if isinstance(row.get("volume"), (int, float)):
                    cleaned["volume_sum"] += int(row["volume"])
            if not kept:
                cleaned["classes_missing"].append(cls)
        if cleaned["keyword_count"] > 0:
            out.append(cleaned)
    return out


async def run_multi_mode_seeding(
    seeds: list[str],
    *,
    country: str = "us",
    min_volume: int = 10,
    max_seeds: int = 30,
    limit_per_mode: int = 35,
    seed_targets: dict[str, dict[str, Any]] | None = None,
    location_code: int | None = None,
    language_code: str | None = None,
    relevance_context: RelevanceContext | None = None,
    query_variants: Any | None = None,
    pool: PoolContext | None = None,
) -> dict[str, Any]:
    """Expand every seed via Ahrefs multi-mode pipeline into a clustered dataset.

    ``query_variants`` (a ``QueryVariantResolver``) supplies each seed's informational
    query variants; without it the informational widening and top-up use the seed
    alone.

    ``pool`` (a ``PoolContext``) lets each seed's main provider pull be served from the
    shared keyword pool; what it reused and fetched is on ``pool.report``.

    seed_targets maps normalized seed -> {"target": str, "target_type": str} so
    each seed root can be a targeted service / page (not only CDD keywords).
    """
    errors: list[str] = []
    seed_targets = seed_targets or {}
    all_rows: list[dict[str, Any]] = []
    used_seeds: list[str] = []
    for seed in seeds[:max_seeds]:
        s = str(seed).strip()
        if not s or is_stale_year_keyword(s):
            continue
        used_seeds.append(s)

    def _is_industry(s: str) -> bool:
        return (seed_targets.get(_norm(s)) or {}).get("seed_origin") == INDUSTRY_ORIGIN

    # One model call for every seed's variants, started now and awaited by each
    # seed only when it needs them, so it overlaps the main keyword fetches. Industry
    # seeds get none: the home page's terms are commercial, and their informational
    # top-up uses the seed itself.
    variant_seeds = [s for s in used_seeds if not _is_industry(s)]
    variants_task = (
        asyncio.ensure_future(query_variants.variants_for(variant_seeds))
        if query_variants is not None and variant_seeds
        else None
    )

    def _variants_for(s: str) -> Callable[[], Awaitable[list[str]]] | None:
        if variants_task is None or _is_industry(s):
            return None

        async def _get() -> list[str]:
            return list((await asyncio.shield(variants_task)).get(s) or [])

        return _get

    funnel_reports: list[dict[str, Any]] = []

    # Expand every seed concurrently (bounded) — each seed itself fans out 3 calls
    sem = asyncio.Semaphore(3)

    # Industry seeds aim at the home page, mostly commercial and transactional, so their
    # informational top-up starts lower (a setting) than a service seed's 15%.
    industry_floor = float(get_settings().keyword_industry_informational_share)

    async def _expand(s: str) -> tuple[list[dict[str, Any]], list[str]]:
        report: dict[str, Any] = {}
        is_industry = _is_industry(s)
        async with sem:
            result = await expand_seed(
                s,
                country=country,
                min_volume=min_volume,
                limit_per_mode=limit_per_mode,
                location_code=location_code,
                language_code=language_code,
                min_keywords=20,
                variants=_variants_for(s),
                service=str((seed_targets.get(_norm(s)) or {}).get("service") or "") or None,
                report=report,
                pool=pool,
                informational_floor=industry_floor if is_industry else INFORMATIONAL_SHARE_FLOOR,
            )
        if report:
            funnel_reports.append(report)
        return result

    results = await asyncio.gather(*[_expand(s) for s in used_seeds])
    for rows, errs in results:
        errors.extend(errs)
        all_rows.extend(rows)

    # Overview metrics for seeds (fill exact volumes)
    if location_code is not None:
        # No slice: search_volume batches at the documented 1000/task, so every
        # seed gets a measured overview rather than the first forty.
        overview, oerrs = await dataforseo.search_volume(
            used_seeds, location_code=location_code
        )
        errors.extend(oerrs)
    else:
        overview, oerrs = [], ["dataforseo_location_missing"]
        errors.extend(oerrs)
    overview_by = {_norm(str(r.get("keyword") or "")): r for r in overview}
    for row in all_rows:
        key = _norm(str(row.get("keyword") or ""))
        ov = overview_by.get(key)
        if not ov:
            continue
        for field in ("volume", "difficulty", "traffic_potential", "cpc", "intent", "parent_topic"):
            value = ov.get(field)
            if value is None:
                continue
            if row.get(field) is None:
                row[field] = value
            elif (
                field == "volume"
                and isinstance(row.get(field), (int, float))
                and isinstance(value, (int, float))
                and int(value) > int(row[field])
            ):
                row[field] = int(value)
        # Only the row under its OWN seed is the exact match. A seed that happens
        # to be another seed's expansion must keep its real relationship class.
        if _norm(str(row.get("seed") or "")) == key:
            row["match_class"] = "exact"
        row["ahrefs_endpoint"] = row.get("ahrefs_endpoint") or "overview"

    filtered = []
    rejected_volume = 0
    missing_volume = 0
    for row in all_rows:
        kw = str(row.get("keyword") or "")
        if is_stale_year_keyword(kw):
            continue
        vol = row.get("volume")
        if vol is None:
            missing_volume += 1
            row["volume"] = None
        # Only a measured volume below the floor is dropped. No volume is kept,
        # flagged volume_unverified, and reported separately.
        if keep_for_volume(row, min_volume):
            filtered.append(flag_volume(row))
        else:
            rejected_volume += 1

    # Stamp search intent + funnel stage on every expansion (Ahrefs intents when
    # present, else heuristic) — the single choke point every seeded keyword
    # passes through regardless of which upstream endpoint produced it, so
    # funnel coverage doesn't depend on remembering to stamp it at each source.
    def _stamp(row: dict[str, Any]) -> dict[str, Any]:
        kw = str(row.get("keyword") or "")
        intent = detect_intent(kw, row.get("intent"))
        return {**row, "intent": intent, "funnel": row.get("funnel") or detect_funnel(kw, intent)}

    filtered = [_stamp(row) for row in filtered]
    unlabelled = [r for r in filtered if not r.get("intent") or not r.get("funnel")]
    if unlabelled:  # detect_intent / detect_funnel always answer; this is a guard
        log.error("keyword_rows_missing_labels", count=len(unlabelled))
        filtered = [r for r in filtered if r.get("intent") and r.get("funnel")]

    cleaning_audit = empty_cleaning_audit()
    excluded_keywords: list[dict[str, Any]] = []
    if relevance_context is not None:
        filtered, excluded_keywords, cleaning_audit = filter_relevant_keywords(
            filtered, relevance_context, source_label="seeding"
        )

    seed_clusters = build_seed_clusters(filtered, used_seeds, seed_targets=seed_targets)
    dataset = flatten_dataset(seed_clusters)

    counts = {"exact": 0, "phrase": 0, "related": 0, "broad": 0}
    for row in dataset:
        cls = str(row.get("match_class") or "broad")
        if cls in counts:
            counts[cls] += 1

    target_type_counts: dict[str, int] = {}
    for cluster in seed_clusters:
        tt = str(cluster.get("target_type") or "keyword")
        target_type_counts[tt] = target_type_counts.get(tt, 0) + 1

    # Per-seed class coverage — makes a missing bucket visible instead of silent
    coverage: list[dict[str, Any]] = []
    full_coverage = 0
    for cluster in seed_clusters:
        missing = [c for c in MATCH_CLASSES if not cluster.get(c)]
        if not missing:
            full_coverage += 1
        cluster["classes_missing"] = missing
        coverage.append(
            {
                "seed": cluster.get("seed"),
                "target_type": cluster.get("target_type"),
                "counts": {c: len(cluster.get(c) or []) for c in MATCH_CLASSES},
                "missing": missing,
            }
        )

    log.info(
        "keyword_fetch_summary",
        seeds_processed=len(used_seeds),
        seeds_configured=len([s for s in seeds if str(s).strip()]),
        keywords_retrieved=len(all_rows),
        rejected_volume=rejected_volume,
        missing_volume=missing_volume,
        keyword_count=len(dataset),
    )
    coverage_of_volume = volume_coverage(dataset)
    log.info("keyword_volume_coverage", **coverage_of_volume)
    dataset_funnel = funnel_counts(dataset)

    source_set = {str(row.get("source") or "").strip() for row in dataset}
    providers = [p for p in ("dataforseo", "fallback") if p in source_set]
    # Nothing is fabricated to cover an outage any more, so the signal the
    # caller needs is "the provider could not expand", not "we padded it".
    expansion_unavailable = any(
        "provider_expansion_unavailable" in str(e) for e in errors
    )

    return {
        "min_volume": min_volume,
        "seeds": used_seeds,
        "seed_clusters": seed_clusters,
        "keyword_dataset": dataset,
        "keyword_count": len(dataset),
        "class_counts": counts,
        "target_type_counts": target_type_counts,
        "class_coverage": coverage,
        "seeds_with_all_classes": full_coverage,
        # How each keyword's volume is known: measured, estimated or unverified
        # (no volume; kept and flagged, not dropped).
        "volume_coverage": coverage_of_volume,
        # Funnel mix of the final dataset, and per seed: the informational share
        # before and after the variant pull and any informational top-up.
        "funnel_counts": dataset_funnel,
        "funnel_coverage": funnel_reports,
        "informational_topups": sum(1 for r in funnel_reports if r.get("topped_up")),
        "provider_errors": sorted(set(errors)),
        "providers": providers,
        "provider_expansion_unavailable": expansion_unavailable,
        # Kept for callers that still branch on the old name; nothing is
        # fabricated now, so this is only ever an outage signal.
        "provider_fallback_used": expansion_unavailable,
        # Handed back so the caller can persist the full rejection record.
        "excluded_keywords": excluded_keywords,
        "keyword_cleaning": compact_cleaning_audit(cleaning_audit) or cleaning_audit,
        "note": (
            f"Multi-mode seeding across {len(seed_clusters)} target root(s) "
            f"(services / pages / keywords): exact + phrase + related + broad, "
            f"classified by keyword-to-seed relationship. Ahrefs primary "
            f"(phrase/terms/related-terms); DataForSEO tops up missing classes "
            f"(suggestions=phrase, related=related, ideas=broad). "
            f"All 4 classes filled for {full_coverage}/{len(seed_clusters)} seeds; "
            f"measured search volume > {SEARCH_VOLUME_ABOVE}; "
            f"{coverage_of_volume[VOLUME_UNVERIFIED]} keyword(s) without measured volume "
            "kept and flagged volume_unverified."
            + (
                " Live keyword APIs were UNAVAILABLE for this run — the pool is "
                "short because expansion failed, not because demand is thin. "
                "Nothing was substituted; re-run once the provider recovers."
                if expansion_unavailable
                else ""
            )
            + (
                f" Cleaned to CDD/services/pages: kept {cleaning_audit.get('kept_count', 0)}"
                f" of {cleaning_audit.get('input_count', 0)}."
                if cleaning_audit.get("input_count")
                else ""
            )
        ),
    }


# Back-compat alias: this used to be the Ahrefs/SEMrush entry point.
expand_seed_ahrefs = expand_seed
