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
from app.integrations import ahrefs, dataforseo
from app.services.keyword_opportunity import detect_funnel, detect_intent, is_stale_year_keyword
from app.services.keyword_relevance import (
    RelevanceContext,
    compact_cleaning_audit,
    empty_cleaning_audit,
    filter_relevant_keywords,
)

MATCH_CLASSES = ("exact", "phrase", "related", "broad")
_CLASS_RANK = {"exact": 0, "phrase": 1, "related": 2, "broad": 3}
_STOP = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def clean_provider_seed(seed: str) -> str:
    """Strip characters DataForSEO rejects (``&``, ``/``, parentheses) so compound
    service names like 'Website & Conversion Optimisation (CRO)' still expand.

    Parenthetical acronyms are dropped (they rarely help expansion) and separators
    become spaces. Falls back to the original seed if cleaning empties it."""
    q = re.sub(r"\([^)]*\)", " ", seed or "")
    q = q.replace("&", " ").replace("/", " ")
    q = re.sub(r"[^\w\s-]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q or (seed or "").strip()


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
                "source": "ahrefs",
                "ahrefs_endpoint": endpoint,
                "seed": s,
                "intent": kw_intent,
                "funnel": detect_funnel(kw, kw_intent),
                "parent_topic": s,
                "match_class": classify_expansion(kw, s, source_hint=endpoint),
            }
        )
    return out


# Commercial modifiers used when Ahrefs / DataForSEO cannot return expansions.
_PHRASE_SUFFIXES = (
    "services",
    "agency",
    "company",
    "pricing",
    "cost",
    "near me",
    "consultants",
    "experts",
    "packages",
    "solutions",
)
_PHRASE_PREFIXES = (
    "best",
    "hire",
    "affordable",
    "top",
    "professional",
    "local",
)
_RELATED_TEMPLATES = (
    "{head} strategy for {tail}",
    "{tail} audit for {head}",
    "{head} management and {tail}",
    "improve {head} with {tail}",
    "{tail} checklist for {head}",
    "{head} specialist in {tail}",
    "{tail} consultant for {head}",
    "best practices for {head} and {tail}",
    "{head} implementation with {tail}",
    "{tail} roadmap for {head}",
)
_BROAD_TEMPLATES = (
    "{head} tips",
    "{tail} tools",
    "{tail} software",
    "digital marketing {tail}",
    "{head} campaign ideas",
    "{tail} roi",
    "{head} funnel",
    "{tail} case study",
    "grow with {tail}",
    "{head} trends",
)


def build_coverage_expansions(
    seed: str,
    *,
    min_count: int = 20,
    min_volume: int = 10,
) -> list[dict[str, Any]]:
    """Build ≥min_count phrase/related/broad keywords when live providers fail.

    Used when Ahrefs Keywords Explorer / DataForSEO Labs are unavailable
    (plan limits, payment required). Rows are marked ``provider_fallback``.
    """
    s = (seed or "").strip()
    if not s:
        return []
    toks = _token_list(s)
    head = toks[0] if toks else s
    tail = toks[-1] if toks else s
    candidates: list[tuple[str, str]] = [(s, "exact")]

    # Interleave classes so the first 20 rows are not all phrase matches.
    phrase_opts = [f"{prefix} {s}" for prefix in _PHRASE_PREFIXES] + [
        f"{s} {suffix}" for suffix in _PHRASE_SUFFIXES
    ]
    related_opts = [
        tmpl.format(seed=s, head=head, tail=tail) for tmpl in _RELATED_TEMPLATES
    ]
    # Prefer broad variants that do NOT contain the full seed contiguously,
    # otherwise classify_expansion collapses them back into phrase.
    broad_opts = [
        tmpl.format(seed=s, head=head, tail=tail) for tmpl in _BROAD_TEMPLATES
    ]
    if len(toks) > 1:
        broad_opts = [
            f"{head} tips",
            f"{tail} tools",
            f"{tail} software",
            f"digital marketing {tail}",
            f"{head} campaign ideas",
            f"{tail} roi",
            f"{head} funnel",
            f"{tail} case study",
            f"grow with {tail}",
            f"{head} trends",
            f"{tail} playbook",
            f"{head} benchmarks",
        ] + broad_opts

    max_len = max(len(phrase_opts), len(related_opts), len(broad_opts))
    for i in range(max_len):
        if i < len(phrase_opts):
            candidates.append((phrase_opts[i], "phrase"))
        if i < len(related_opts):
            candidates.append((related_opts[i], "related"))
        if i < len(broad_opts):
            candidates.append((broad_opts[i], "broad"))

    filler_i = 1
    while len({_norm(c[0]) for c in candidates}) < max(min_count, 20):
        candidates.append((f"{s} service package {filler_i}", "phrase"))
        # Split seed tokens so related stays related, not phrase.
        if len(toks) > 1:
            candidates.append((f"{head} and {tail} implementation {filler_i}", "related"))
        else:
            candidates.append((f"{s} implementation guide {filler_i}", "related"))
        candidates.append((f"{tail} growth playbook {filler_i}", "broad"))
        filler_i += 1
        if filler_i > 30:
            break

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    vol_by_class = {
        "exact": max(min_volume + 40, 50),
        "phrase": max(min_volume + 25, 35),
        "related": max(min_volume + 15, 25),
        "broad": max(min_volume + 5, 15),
    }
    # First pass: keep classify_expansion result. Second pass can force the
    # intended class only when classification still lands on exact.
    for kw, intended_cls in candidates:
        text = clean_provider_seed(kw) or kw.strip()
        key = _norm(text)
        if not key or key in seen or is_stale_year_keyword(text):
            continue
        if key == _norm(s) and intended_cls != "exact":
            continue
        seen.add(key)
        if key == _norm(s):
            final_cls = "exact"
        else:
            hint = {
                "phrase": "matching-terms-phrase",
                "related": "matching-terms",
                "broad": "related-terms",
            }.get(intended_cls)
            final_cls = classify_expansion(text, s, source_hint=hint)
            # One-word seeds collapse phrase/related/broad without a hint; keep
            # the intended class for coverage diversity.
            if final_cls == "exact" and intended_cls != "exact":
                final_cls = intended_cls
            if len(_token_list(s)) == 1 and intended_cls in ("phrase", "related", "broad"):
                final_cls = intended_cls
        intent = detect_intent(text)
        out.append(
            {
                "keyword": text,
                "volume": vol_by_class.get(final_cls, min_volume + 5),
                "difficulty": None,
                "cpc": None,
                "traffic_potential": None,
                "intent": intent,
                "funnel": detect_funnel(text, intent),
                "parent_topic": s,
                "source": "fallback",
                "ahrefs_endpoint": "provider_fallback",
                "seed": s,
                "match_class": final_cls,
                "provider_fallback": True,
                "volume_estimated": True,
            }
        )
        if len(out) >= max(min_count, 20):
            break
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
    results = await asyncio.gather(
        *[fetchers[e](query, location_code=location_code, limit=limit) for e in ordered],
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


async def expand_seed_ahrefs(
    seed: str,
    *,
    country: str = "us",
    min_volume: int = 10,
    limit_per_mode: int = 40,
    location_code: int | None = None,
    enable_dataforseo_fallback: bool = True,
    min_keywords: int = 20,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch exact (phrase) + related (terms) + broad (related-terms) for one seed.

    When Ahrefs returns no usable expansions (quota exhausted / rate-limited) and a
    ``location_code`` is provided, fall back to DataForSEO related-keywords so each
    seed still yields a related/broad keyword set. If both providers fail or return
    too few rows, top up with deterministic coverage expansions (≥ min_keywords).
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
            if not prev or (row.get("volume") or 0) > (prev.get("volume") or 0):
                target[key] = row

    if settings.ahrefs_api_key:
        # Three Ahrefs feeds concurrently. The endpoint is provenance only —
        # classification comes from the keyword/seed relationship.
        phrase_res, terms_res, broad_res = await asyncio.gather(
            ahrefs.matching_terms(
                seed,
                country=country,
                limit=limit_per_mode,
                match_mode="phrase",
                min_volume=min_volume,
            ),
            ahrefs.matching_terms(
                seed,
                country=country,
                limit=limit_per_mode,
                match_mode="terms",
                min_volume=min_volume,
            ),
            ahrefs.related_terms(
                seed,
                country=country,
                limit=limit_per_mode,
                terms="all",
                min_volume=min_volume,
            ),
        )
        for (rows, errs), endpoint in (
            (phrase_res, "matching-terms-phrase"),
            (terms_res, "matching-terms"),
            (broad_res, "related-terms"),
        ):
            errors.extend(errs)
            _ingest(rows, endpoint=endpoint, source="ahrefs")
    else:
        errors.append("ahrefs_unavailable")

    # Top up only the classes Ahrefs left empty, each from its dedicated
    # DataForSEO source, so every seed can report all four classes.
    if enable_dataforseo_fallback and location_code is not None:
        present = {str(r.get("match_class")) for r in by_kw.values()}
        missing = tuple(c for c in ("phrase", "related", "broad") if c not in present)
        if missing:
            df_rows, df_errs = await expand_seed_dataforseo(
                seed,
                location_code=location_code,
                min_volume=min_volume,
                classes=missing,
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
            "source": "ahrefs" if settings.ahrefs_api_key else "dataforseo",
            "ahrefs_endpoint": "seed",
            "seed": seed,
            "match_class": "exact",
        }

    # When live providers are plan-blocked / unpaid (no usable expansions beyond
    # the seed placeholder), top up to min_keywords so each service seed still
    # surfaces a usable keyword set in Phase 5.
    live_classes = {
        str(r.get("match_class"))
        for r in by_kw.values()
        if str(r.get("source") or "") in ("ahrefs", "dataforseo")
        and str(r.get("ahrefs_endpoint") or "") != "seed"
    }
    if not (live_classes & {"phrase", "related", "broad"}):
        errors.append("provider_coverage_fallback")
        for row in build_coverage_expansions(
            seed, min_count=max(20, int(min_keywords or 20)), min_volume=min_volume
        ):
            key = _norm(str(row.get("keyword") or ""))
            if not key:
                continue
            prev = by_kw.get(key)
            if not prev or prev.get("volume") is None:
                by_kw[key] = row

    return list(by_kw.values()), errors


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
            "source": row.get("source") or "ahrefs",
            "ahrefs_endpoint": row.get("ahrefs_endpoint"),
            "below_volume_floor": bool(row.get("below_volume_floor")),
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
    sem = asyncio.Semaphore(5)

    async def _expand(s: str) -> tuple[list[dict[str, Any]], list[str]]:
        async with sem:
            return await expand_seed_ahrefs(
                s,
                country=country,
                min_volume=min_volume,
                limit_per_mode=limit_per_mode,
                location_code=location_code,
                min_keywords=20,
            )

    results = await asyncio.gather(*[_expand(s) for s in used_seeds])
    for rows, errs in results:
        errors.extend(errs)
        all_rows.extend(rows)

    # Overview metrics for seeds (fill exact volumes)
    overview, oerrs = await ahrefs.keyword_overview(used_seeds[:12], country=country)
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
    if relevance_context is not None:
        filtered, _excluded, cleaning_audit = filter_relevant_keywords(
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
    providers = [p for p in ("ahrefs", "dataforseo", "fallback") if p in source_set]
    used_fallback = "fallback" in source_set or any(
        "provider_coverage_fallback" in str(e) for e in errors
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
        "provider_fallback_used": used_fallback,
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
                " Live keyword APIs were unavailable — coverage fallback supplied "
                "≥20 keywords per seed so Phase 5 remains usable."
                if used_fallback
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
