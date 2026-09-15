"""Business-relevance keyword filtering — token-efficient OpenRouter calls."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.config import get_settings
from app.integrations.llm import _openrouter_chat
from app.logging_config import get_logger

log = get_logger("keyword_llm_relevance")

_DEFAULT_MODEL = "google/gemini-2.5-flash"

_SYSTEM = """\
Select keywords this company should SEO-target. Return JSON only:
{{"seed":"<seed>","relevant":["..."],"dropped":["..."]}}
Keep at most {max_per_seed} in "relevant". Every input keyword in relevant OR dropped.
Drop: generic industry heads, wrong vertical, competitor brands, jobs/login, off-topic.
Keep: service-specific terms a buyer of THIS business would search.
"dropped" = keyword strings only (no reasons)."""


def _norm_kw(kw: str) -> str:
    return re.sub(r"\s+", " ", (kw or "").strip().lower())


def _compact_biz(
    *,
    company_url: str,
    business_name: str | None,
    industry: str | None,
    services: list[str] | None,
    geography: str | None,
) -> str:
    svc = ",".join(str(s).strip() for s in (services or [])[:10] if str(s).strip())
    parts = [
        business_name or "?",
        company_url or "?",
        industry or "",
        svc,
        geography or "",
    ]
    return "|".join(parts)


def _business_description(
    *,
    company_url: str,
    business_name: str | None,
    industry: str | None,
    services: list[str] | None,
    geography: str | None,
) -> str:
    """Full description for audit payloads."""
    lines = [f"Company: {business_name}" if business_name else "Company: (name not provided)"]
    lines.append(f"Website: {company_url or '(not provided)'}")
    if industry:
        lines.append(f"Industry: {industry}")
    services_clean = [str(s).strip() for s in (services or []) if str(s).strip()]
    if services_clean:
        lines.append(f"Services/products offered: {', '.join(services_clean[:20])}")
    if geography:
        lines.append(f"Primary market/geography: {geography}")
    return "\n".join(lines)


def _seed_context(seed: str, seed_targets: dict[str, dict[str, Any]] | None) -> str:
    if not seed_targets:
        return ""
    meta = seed_targets.get(_norm_kw(seed)) or seed_targets.get(seed.lower()) or {}
    target = str(meta.get("target") or "").strip()
    if not target:
        return ""
    return target


def _seed_target_type(
    seed: str, seed_targets: dict[str, dict[str, Any]] | None
) -> str:
    if not seed_targets:
        return ""
    meta = seed_targets.get(_norm_kw(seed)) or seed_targets.get(seed.lower()) or {}
    return str(meta.get("target_type") or "").strip().lower()


def _safe_service_backfill(row: dict[str, Any]) -> bool:
    """Allow deterministic provider rows to recover from an over-strict LLM."""
    keyword = _norm_kw(str(row.get("keyword") or ""))
    if not keyword or len(keyword) < 3:
        return False
    if any(
        token in keyword.split()
        for token in ("jobs", "careers", "login", "official", "wiki", "wikipedia")
    ):
        return False
    return True


def _cap_seed_rows(rows: list[dict[str, Any]], *, max_per_seed: int) -> list[dict[str, Any]]:
    if len(rows) <= max_per_seed:
        return rows
    return sorted(rows, key=row_volume_key, reverse=True)[:max_per_seed]


def _pretrim_seed_rows(
    rows: list[dict[str, Any]], *, max_input: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Send only top-N by volume to the model; drop the rest without an API call."""
    if len(rows) <= max_input:
        return rows, []
    ranked = sorted(rows, key=row_volume_key, reverse=True)
    return ranked[:max_input], ranked[max_input:]


def row_volume_key(row: dict[str, Any]) -> int:
    try:
        return int(row.get("volume") or 0)
    except (TypeError, ValueError):
        return 0


def _dedupe_across_seeds(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep one row per normalized keyword — highest volume wins."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        kw = _norm_kw(str(row.get("keyword") or ""))
        if not kw:
            continue
        prev = best.get(kw)
        if not prev:
            best[kw] = row
            order.append(kw)
            continue
        if row_volume_key(row) > row_volume_key(prev):
            best[kw] = row
    kept = [best[kw] for kw in order if kw in best]
    dropped: list[dict[str, Any]] = []
    seen_kept = {_norm_kw(str(r.get("keyword") or "")) for r in kept}
    for row in rows:
        kw = _norm_kw(str(row.get("keyword") or ""))
        if not kw or kw not in seen_kept:
            continue
        if row is not best[kw]:
            dropped.append({**row, "relevance_drop_reason": "cross_seed_dedupe"})
    return kept, dropped


async def _evaluate_seed_batch(
    seed: str,
    keywords: list[str],
    *,
    biz_line: str,
    seed_target: str,
    model: str,
    max_per_seed: int,
    max_tokens: int,
) -> dict[str, Any]:
    if not keywords:
        return {"seed": seed, "relevant": [], "dropped": []}

    user_msg = (
        f"{biz_line}\n"
        f"Seed:{seed}"
        + (f"|Target:{seed_target}" if seed_target else "")
        + f"|Keep≤{max_per_seed}|KWS:{json.dumps(keywords, ensure_ascii=False)}"
    )
    system = _SYSTEM.format(max_per_seed=max_per_seed)

    try:
        raw = await _openrouter_chat(
            system=system,
            user=user_msg,
            model=model,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        log.warning("keyword_relevance_failed", seed=seed, error=str(exc))
        return {"seed": seed, "relevant": [], "dropped": [], "error": str(exc)}

    if not raw:
        return {"seed": seed, "relevant": [], "dropped": [], "error": "empty_response"}

    try:
        text = raw.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        result = json.loads(text)
        dropped_raw = result.get("dropped") or []
        dropped: list[str] = []
        for item in dropped_raw:
            if isinstance(item, str):
                dropped.append(item)
            elif isinstance(item, dict) and item.get("keyword"):
                dropped.append(str(item["keyword"]))
        return {
            "seed": seed,
            "relevant": list(result.get("relevant") or []),
            "dropped": dropped,
        }
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("keyword_relevance_parse_failed", seed=seed, error=str(exc))
        return {"seed": seed, "relevant": [], "dropped": [], "error": f"parse: {exc}"}


async def llm_filter_keywords_by_seed(
    rows: list[dict[str, Any]],
    *,
    company_url: str,
    competitor_urls: list[str],
    business_name: str | None = None,
    industry: str | None = None,
    services: list[str] | None = None,
    geography: str | None = None,
    seed_targets: dict[str, dict[str, Any]] | None = None,
    max_per_seed: int | None = None,
    max_input_per_seed: int | None = None,
    min_per_service: int | None = 20,
    max_tokens: int | None = None,
    max_concurrent: int = 3,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Filter keyword rows by business relevance, grouped by seed."""
    settings = get_settings()
    use_model = model or getattr(settings, "keyword_cluster_model", None) or _DEFAULT_MODEL
    cap = max_per_seed if max_per_seed is not None else getattr(
        settings, "keyword_relevance_max_per_seed", 25
    )
    max_in = max_input_per_seed if max_input_per_seed is not None else getattr(
        settings, "keyword_relevance_max_input_per_seed", 40
    )
    min_service = max(
        0,
        int(
            min_per_service
            if min_per_service is not None
            else getattr(settings, "keyword_relevance_min_per_service", 20)
        ),
    )
    tok = max_tokens if max_tokens is not None else getattr(
        settings, "keyword_relevance_max_tokens", 2048
    )
    biz_line = _compact_biz(
        company_url=company_url,
        business_name=business_name,
        industry=industry,
        services=services,
        geography=geography,
    )
    business_description = _business_description(
        company_url=company_url,
        business_name=business_name,
        industry=industry,
        services=services,
        geography=geography,
    )

    by_seed: dict[str, list[dict[str, Any]]] = {}
    no_seed: list[dict[str, Any]] = []
    for row in rows:
        seed = str(row.get("seed") or "").strip()
        if not seed:
            no_seed.append(row)
            continue
        by_seed.setdefault(seed, []).append(row)

    api_calls = 0

    if settings.use_mock_llm or not settings.openrouter_api_key:
        kept = []
        dropped = []
        for seed_rows in by_seed.values():
            capped = _cap_seed_rows(seed_rows, max_per_seed=cap)
            kept.extend(capped)
            for row in seed_rows:
                if row not in capped:
                    dropped.append({**row, "relevance_drop_reason": "cap_per_seed"})
        kept.extend(no_seed)
        return kept, dropped, {
            "seeds_evaluated": len(by_seed),
            "input_count": len(rows),
            "kept": len(kept),
            "dropped": len(dropped),
            "api_calls": 0,
            "skipped": "mock_or_no_key",
        }

    sem = asyncio.Semaphore(max_concurrent)
    results: dict[str, dict[str, Any]] = {}

    async def _process_seed(seed: str, seed_rows: list[dict[str, Any]]) -> None:
        nonlocal api_calls
        llm_rows, volume_trimmed = _pretrim_seed_rows(seed_rows, max_input=max_in)

        if not llm_rows:
            results[seed] = {
                "relevant_set": set(),
                "volume_trimmed": volume_trimmed,
                "skipped": "empty_after_pretrim",
            }
            return

        # Small seeds: rule-filtered already — skip API, keep top by volume
        if len(llm_rows) <= cap:
            results[seed] = {
                "relevant_set": {_norm_kw(str(r.get("keyword") or "")) for r in llm_rows},
                "volume_trimmed": volume_trimmed,
                "skipped": "under_cap",
            }
            return

        keywords = [str(r.get("keyword") or "").strip() for r in llm_rows if str(r.get("keyword") or "").strip()]
        target = _seed_context(seed, seed_targets)

        async with sem:
            api_calls += 1
            result = await _evaluate_seed_batch(
                seed,
                keywords,
                biz_line=biz_line,
                seed_target=target,
                model=use_model,
                max_per_seed=cap,
                max_tokens=tok,
            )

        relevant_set = {_norm_kw(str(kw)) for kw in (result.get("relevant") or [])}
        entry: dict[str, Any] = {
            "relevant_set": relevant_set,
            "dropped": result.get("dropped") or [],
            "volume_trimmed": volume_trimmed,
        }
        if result.get("error"):
            entry["error"] = str(result["error"])
            entry["fallback_rows"] = _cap_seed_rows(llm_rows, max_per_seed=cap)
        results[seed] = entry

    await asyncio.gather(*[_process_seed(seed, seed_rows) for seed, seed_rows in by_seed.items()])

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    kept_count = 0
    dropped_count = 0
    tier2_kept = 0
    seeds_skipped_api = 0

    for seed, seed_rows in by_seed.items():
        llm_rows, _ = _pretrim_seed_rows(seed_rows, max_input=max_in)
        result = results.get(seed) or {}
        volume_trimmed = list(result.get("volume_trimmed") or [])

        def _append_tier2(seed_kept: list[dict[str, Any]], dropped_keys: set[str]) -> list[dict[str, Any]]:
            nonlocal tier2_kept
            kept_keys = {_norm_kw(str(r.get("keyword") or "")) for r in seed_kept}
            for row in volume_trimmed:
                kw = _norm_kw(str(row.get("keyword") or ""))
                if kw in kept_keys or kw in dropped_keys:
                    continue
                seed_kept.append(row)
                kept_keys.add(kw)
                tier2_kept += 1
            return seed_kept

        if not result:
            capped = _cap_seed_rows(llm_rows, max_per_seed=cap)
            capped = _append_tier2(capped, set())
            kept.extend(capped)
            kept_count += len(capped)
            continue

        if result.get("skipped") == "under_cap":
            seeds_skipped_api += 1
            capped = _cap_seed_rows(llm_rows, max_per_seed=cap)
            capped = _append_tier2(capped, set())
            kept.extend(capped)
            kept_count += len(capped)
            continue

        if result.get("fallback_rows") is not None:
            fallback = result["fallback_rows"]
            fallback = _append_tier2(list(fallback), set())
            kept.extend(fallback)
            kept_count += len(fallback)
            dropped_keys = {_norm_kw(str(r.get("keyword") or "")) for r in llm_rows if r not in fallback}
            for row in llm_rows:
                if row not in fallback:
                    dropped.append({**row, "relevance_drop_reason": "provider_fallback_cap"})
                    dropped_count += 1
            continue

        relevant_set = result.get("relevant_set") or set()
        target_type = _seed_target_type(seed, seed_targets)
        # Never drop deterministic provider-fallback coverage rows — they exist
        # because live keyword APIs failed and must keep the service usable.
        for row in [*llm_rows, *volume_trimmed]:
            if row.get("provider_fallback"):
                key = _norm_kw(str(row.get("keyword") or ""))
                if key:
                    relevant_set.add(key)
        backfill_keys: set[str] = set()
        if target_type in ("service", "sub_service") and min_service:
            required = min(min_service, len(llm_rows) + len(volume_trimmed))
            relevant_rows = sum(
                1
                for row in [*llm_rows, *volume_trimmed]
                if _norm_kw(str(row.get("keyword") or "")) in relevant_set
            )
            if relevant_rows < required:
                candidates = sorted(
                    [*llm_rows, *volume_trimmed],
                    key=lambda row: (
                        0
                        if str(row.get("match_class") or "").lower()
                        in ("exact", "phrase", "related")
                        else 1,
                        -row_volume_key(row),
                    ),
                )
                for row in candidates:
                    key = _norm_kw(str(row.get("keyword") or ""))
                    if (
                        key
                        and key not in relevant_set
                        and key not in backfill_keys
                        and _safe_service_backfill(row)
                    ):
                        backfill_keys.add(key)
                    if relevant_rows + len(backfill_keys) >= required:
                        break
        seed_kept: list[dict[str, Any]] = []
        dropped_keys: set[str] = set()
        for row in llm_rows:
            kw = _norm_kw(str(row.get("keyword") or ""))
            if kw in relevant_set or kw in backfill_keys:
                seed_kept.append(
                    {**row, "relevance_rescued": kw in backfill_keys and kw not in relevant_set}
                )
            else:
                dropped.append({**row, "relevance_drop_reason": "not_relevant"})
                dropped_keys.add(kw)
                dropped_count += 1

        seed_kept = _cap_seed_rows(seed_kept, max_per_seed=cap)
        kept_keys = {_norm_kw(str(r.get("keyword") or "")) for r in seed_kept}
        for row in llm_rows:
            kw = _norm_kw(str(row.get("keyword") or ""))
            if kw not in kept_keys and kw not in dropped_keys:
                dropped.append({**row, "relevance_drop_reason": "cap_per_seed"})
                dropped_count += 1
        seed_kept = _append_tier2(seed_kept, dropped_keys)
        kept.extend(seed_kept)
        kept_count += len(seed_kept)

    kept.extend(no_seed)

    kept, cross_seed_dropped = _dedupe_across_seeds(kept)
    dropped.extend(cross_seed_dropped)
    dropped_count += len(cross_seed_dropped)

    audit = {
        "seeds_evaluated": len(by_seed),
        "input_count": len(rows),
        "kept": len(kept) - len(no_seed),
        "dropped": dropped_count,
        "api_calls": api_calls,
        "seeds_skipped_api": seeds_skipped_api,
        "pre_volume_tier2_kept": tier2_kept,
        "cross_seed_deduped": len(cross_seed_dropped),
        "max_per_seed": cap,
        "max_input_per_seed": max_in,
        "no_seed_passthrough": len(no_seed),
        "business_description": business_description,
        "errors": [seed for seed, r in results.items() if isinstance(r, dict) and r.get("error")],
    }
    return kept, dropped, audit
