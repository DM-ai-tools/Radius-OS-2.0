"""Multi-mode keyword seeding from Ahrefs (exact / related / broad).

Phase 5 seeding:
  - Pull expansions per CDD seed from Ahrefs matching-terms + related-terms
  - Keep only volume > min_volume (default 10)
  - Classify each keyword: exact | related | broad
  - Emit a multi-keyword dataset keyed by seed with nested clusters
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.config import get_settings
from app.integrations import dataforseo
from app.logging_config import get_logger
from app.services.keyword_opportunity import (
    detect_funnel,
    detect_intent,
    is_stale_year_keyword,
)
from app.services.keyword_relevance import (
    RelevanceContext,
    compact_cleaning_audit,
    empty_cleaning_audit,
    filter_relevant_keywords,
)

log = get_logger("keyword_seeding")

MATCH_CLASSES = ("exact", "phrase", "related", "broad")
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


def _passes_volume(row: dict[str, Any], min_volume: int) -> bool:
    vol = row.get("volume")
    if vol is None:
        # Keep seed overview rows without inventing volume — drop expansions lacking volume
        return False
    try:
        return int(vol) > int(min_volume)
    except (TypeError, ValueError):
        return False


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

    async def _fetch(endpoint: str):
        kwargs: dict[str, Any] = {
            "location_code": location_code,
            "language_code": language,
            "limit": limit,
            "min_volume": min_volume,
        }
        if endpoint == "related_keywords":
            kwargs["depth"] = depth
        return await fetchers[endpoint](query, **kwargs)

    results = await asyncio.gather(
        *[_fetch(e) for e in ordered],
        return_exceptions=True,
    )

    seed_aliases = {_norm(seed), _norm(query)}
    out: list[dict[str, Any]] = []
    for endpoint, res in zip(ordered, results):
        if isinstance(res, BaseException):
            errors.append(f"dataforseo_{endpoint}_error")
            continue
        rows, errs = res
        errors.extend(errs)
        for r in rows:
            kw = str(r.get("keyword") or "").strip()
            if not kw or is_stale_year_keyword(kw):
                continue
            is_seed = _norm(kw) in seed_aliases
            row_intent = detect_intent(kw, r.get("intent") or r.get("search_intent"))
            out.append(
                {
                    "keyword": kw,
                    "volume": r.get("volume"),
                    "difficulty": r.get("difficulty"),
                    "cpc": r.get("cpc"),
                    "traffic_potential": None,
                    "intent": row_intent,
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
    return out, errors


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
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expand one seed into exact / phrase / related / broad via DataForSEO.

    DataForSEO is the sole provider. If it returns too few rows, top up with
    deterministic coverage expansions (>= min_keywords) — those are flagged
    ``provider_fallback`` and ``volume_estimated`` so downstream reports can mark
    them as estimates rather than presenting them as measured demand.
    """
    errors: list[str] = []
    settings = get_settings()
    seed = (seed or "").strip()
    if not seed:
        return [], errors

    if settings.use_mock_providers:
        return _mock_expansions(seed, min_volume=min_volume), errors

    # Keywords that pass the volume floor, plus a reservoir of real but
    # sub-threshold keywords used only to fill a class that would be empty.
    by_kw: dict[str, dict[str, Any]] = {}
    reservoir: dict[str, dict[str, Any]] = {}

    def _ingest(rows: list[dict[str, Any]], *, endpoint: str, source: str) -> None:
        for r in rows:
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
            target = by_kw if (_passes_volume(row, min_volume) or key == _norm(seed)) else reservoir
            if row.get("volume") is None and target is reservoir:
                continue
            prev = target.get(key)
            incoming = list(row.get("sources") or [])
            if not incoming and row.get("dataforseo_endpoint"):
                incoming = [str(row.get("dataforseo_endpoint"))]
            if prev:
                sources = list(prev.get("sources") or [])
                for item in incoming:
                    if item and item not in sources:
                        sources.append(item)
                if not prev or (row.get("volume") or 0) > (prev.get("volume") or 0):
                    target[key] = {**row, "sources": sources}
                else:
                    prev["sources"] = sources
            else:
                target[key] = {**row, "sources": incoming}

    # DataForSEO is the only keyword provider. Each match class has its own
    # dedicated endpoint — suggestions=phrase, related=related, ideas=broad — so
    # a single pass covers every class. Previously SEMrush was primary and
    # DataForSEO only topped up the classes SEMrush left empty; with no SEMrush
    # subscription that left every class empty and fell through to fabricated
    # coverage rows.
    if location_code is None:
        errors.append("dataforseo_location_missing")
    else:
        df_rows, df_errs = await expand_seed_dataforseo(
            seed,
            location_code=location_code,
            min_volume=min_volume,
            limit=limit_per_mode,
            classes=("phrase", "related", "broad"),
            language_code=language_code,
        )
        errors.extend(df_errs)
        _ingest(df_rows, endpoint="dataforseo", source="dataforseo")

    # Last resort: fill a still-empty class from real sub-threshold keywords
    # rather than leaving the bucket blank. Never invents a keyword.
    present = {str(r.get("match_class")) for r in by_kw.values()}
    for cls in MATCH_CLASSES:
        if cls in present:
            continue
        spares = sorted(
            (r for k, r in reservoir.items() if r.get("match_class") == cls and k not in by_kw),
            key=lambda r: -(r.get("volume") or 0),
        )[:5]
        for row in spares:
            by_kw[_norm(str(row.get("keyword") or ""))] = {**row, "below_volume_floor": True}

    # Ensure the seed itself is present (overview fills its volume later)
    if _norm(seed) not in by_kw:
        by_kw[_norm(seed)] = {
            "keyword": seed,
            "volume": None,
            "difficulty": None,
            "traffic_potential": None,
            "cpc": None,
            "intent": "",
            "funnel": detect_funnel(seed, ""),
            "parent_topic": seed,
            "source": "dataforseo",
            "ahrefs_endpoint": "seed",
            "seed": seed,
            "match_class": "exact",
        }

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
        for field in ("page_type", "page_path", "parent_segment"):
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
    seen: set[tuple[str, str]] = set()
    for cluster in seed_clusters:
        seed = cluster.get("seed")
        target = cluster.get("target")
        target_type = cluster.get("target_type")
        for cls in MATCH_CLASSES:
            for row in cluster.get(cls) or []:
                kw = _norm(str(row.get("keyword") or ""))
                association = (_norm(str(seed or "")), kw)
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
) -> dict[str, Any]:
    """Expand every seed via Ahrefs multi-mode pipeline into a clustered dataset.

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

    # Expand every seed concurrently (bounded) — each seed itself fans out 3 calls
    sem = asyncio.Semaphore(3)

    async def _expand(s: str) -> tuple[list[dict[str, Any]], list[str]]:
        async with sem:
            return await expand_seed(
                s,
                country=country,
                min_volume=min_volume,
                limit_per_mode=limit_per_mode,
                location_code=location_code,
                language_code=language_code,
                min_keywords=20,
            )

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
            if ov.get(field) is not None and (row.get(field) is None or field == "volume"):
                row[field] = ov.get(field)
        # Only the row under its OWN seed is the exact match. A seed that happens
        # to be another seed's expansion must keep its real relationship class.
        if _norm(str(row.get("seed") or "")) == key:
            row["match_class"] = "exact"
        row["ahrefs_endpoint"] = row.get("ahrefs_endpoint") or "overview"

    # Drop expansions still below volume (except we already filtered)
    filtered = []
    for row in all_rows:
        kw = str(row.get("keyword") or "")
        if is_stale_year_keyword(kw):
            continue
        vol = row.get("volume")
        if vol is None and _norm(kw) in {_norm(s) for s in used_seeds}:
            # Seed with unknown volume — keep as exact placeholder only if overview missing
            filtered.append(row)
            continue
        # Deliberate sub-threshold fills keep an otherwise-empty class populated
        if row.get("below_volume_floor") or row.get("provider_fallback"):
            filtered.append(row)
            continue
        if vol is not None and int(vol) > min_volume:
            filtered.append(row)

    # Stamp search intent + funnel stage on every expansion (Ahrefs intents when
    # present, else heuristic) — the single choke point every seeded keyword
    # passes through regardless of which upstream endpoint produced it, so
    # funnel coverage doesn't depend on remembering to stamp it at each source.
    def _stamp(row: dict[str, Any]) -> dict[str, Any]:
        kw = str(row.get("keyword") or "")
        intent = detect_intent(kw, row.get("intent"))
        return {**row, "intent": intent, "funnel": row.get("funnel") or detect_funnel(kw, intent)}

    filtered = [_stamp(row) for row in filtered]

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
            f"volume > {min_volume}."
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
