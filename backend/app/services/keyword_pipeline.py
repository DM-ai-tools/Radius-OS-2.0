"""Explicit keyword cleaning pipeline before clustering.

Raw Keywords
     ↓ Normalize
     ↓ Remove Duplicates
     ↓ Remove Noise
     ↓ Identify Brand Terms
     ↓ Check Relevance
     ↓ Classify Intent
     ↓ Extract Entities / Topics
     ↓ Final Clean Keyword Set
     ↓ Cluster (see keyword_clustering.run_keyword_clustering)
"""

from __future__ import annotations

import re
from typing import Any

from app.services.keyword_opportunity import detect_intent, is_noisy_keyword, is_stale_year_keyword
from app.services.keyword_relevance import (
    RelevanceContext,
    evaluate_keyword,
    filter_relevant_keywords,
    is_competitor_brand_term,
    is_url_like,
)
from app.services.keyword_seeding import clean_provider_seed

_STOP = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with"}
_HEAD_DROP = {
    "best",
    "top",
    "how",
    "what",
    "is",
    "are",
    "vs",
    "versus",
    "near",
    "me",
    "to",
    "do",
    "a",
    "an",
    "the",
    "for",
    "tools",
    "software",
    "guide",
    "pricing",
    "price",
    "cost",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokens(kw: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(kw)) if t not in _STOP and len(t) > 1}


def _singularize(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ses") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _stem_key(kw: str) -> str:
    toks = sorted(_singularize(t) for t in _tokens(kw))
    return " ".join(toks)


def _modifier_bucket(kw: str) -> str | None:
    k = _norm(kw)
    if " vs " in k or " versus " in k or k.endswith(" vs") or " alternatives" in k:
        return "comparison"
    if k.startswith("how to ") or " how to " in k:
        return "how-to"
    if k.startswith("best ") or k.startswith("top "):
        return "listicle"
    if " for " in k:
        return "audience"
    if any(x in k for x in (" tools", " software", " platform", " app")):
        return "tools"
    if k.startswith("what is ") or k.startswith("what are "):
        return "what-is"
    return None


def _head_topic(kw: str) -> str:
    head_toks = [
        t for t in re.findall(r"[a-z0-9]+", _norm(kw)) if t not in _HEAD_DROP and t not in _STOP
    ][:3]
    return " ".join(head_toks[:2]) or _norm(kw)


def _best_evidence_phrase(keyword: str, ctx: RelevanceContext | None) -> str | None:
    if ctx is None:
        return None
    kw = _norm(keyword)
    for phrase in sorted(ctx.evidence_phrases, key=len, reverse=True):
        p = _norm(phrase)
        if not p:
            continue
        if p in kw:
            return phrase
        ptoks = _tokens(p)
        if ptoks and ptoks.issubset(_tokens(kw)):
            return phrase
    return None


def extract_entities_and_topics(
    keyword: str,
    ctx: RelevanceContext | None = None,
) -> dict[str, Any]:
    """Attach parent topic, entity tokens, and modifier bucket."""
    kw = _norm(keyword)
    evidence = _best_evidence_phrase(kw, ctx)
    entities: list[str] = []
    if evidence:
        entities.append(evidence)
    if ctx is not None:
        entities.extend(sorted(_tokens(kw) & ctx.distinctive_tokens))
    entities = list(dict.fromkeys(e for e in entities if e))
    parent = evidence or _head_topic(kw)
    modifier = _modifier_bucket(kw)
    return {
        "parent_topic": parent,
        "entities": entities,
        "topic_modifier": modifier,
    }


def _drop(stage: str, row: dict[str, Any], reason: str, excluded: list[dict[str, Any]]) -> None:
    excluded.append(
        {
            "stage": stage,
            "keyword": row.get("keyword"),
            "reason": reason,
            "match_class": row.get("match_class"),
            "seed": row.get("seed"),
            "source": row.get("source"),
        }
    )


def _stage_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"count": len(rows)}


def run_keyword_pipeline(
    rows: list[dict[str, Any]],
    *,
    relevance_context: RelevanceContext | None = None,
    brand_name: str | None = None,
    source_label: str = "pipeline",
) -> dict[str, Any]:
    """Run the full cleaning pipeline and return the final keyword set + audit."""
    raw_count = len([r for r in rows if isinstance(r, dict)])
    excluded: list[dict[str, Any]] = []
    working: list[dict[str, Any]] = []

    # 1–2. Normalize (check URL-likeness on the raw keyword first)
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_kw = str(row.get("keyword") or "").strip()
        if is_url_like(raw_kw):
            _drop("normalize", row, "url_noise", excluded)
            continue
        cleaned = clean_provider_seed(raw_kw)
        kw = _norm(cleaned)
        if not kw or len(kw) < 2:
            _drop("normalize", row, "empty", excluded)
            continue
        working.append({**row, "keyword": kw})

    normalize_count = len(working)

    # 3. Remove duplicates (stem; prefer higher volume)
    by_stem: dict[str, dict[str, Any]] = {}
    for row in working:
        kw = str(row.get("keyword") or "")
        stem = _stem_key(kw) or kw
        prev = by_stem.get(stem)
        if not prev:
            by_stem[stem] = row
            continue
        pv = prev.get("volume")
        cv = row.get("volume")
        if cv is not None and (pv is None or cv > pv):
            _drop("dedupe", prev, "lower_volume_duplicate", excluded)
            by_stem[stem] = row
        else:
            _drop("dedupe", row, "lower_volume_duplicate", excluded)
    working = list(by_stem.values())
    dedupe_count = len(working)

    # 4. Remove noise
    after_noise: list[dict[str, Any]] = []
    for row in working:
        kw = str(row.get("keyword") or "")
        if is_stale_year_keyword(kw):
            _drop("noise", row, "stale_year", excluded)
            continue
        if is_url_like(kw):
            _drop("noise", row, "url_noise", excluded)
            continue
        if is_noisy_keyword(kw, brand_name=brand_name):
            _drop("noise", row, "noisy", excluded)
            continue
        after_noise.append(row)
    working = after_noise
    noise_count = len(working)

    # 5. Identify brand terms (drop competitor / client-brand noise)
    after_brand: list[dict[str, Any]] = []
    ctx = relevance_context
    for row in working:
        kw = str(row.get("keyword") or "")
        if ctx and is_competitor_brand_term(
            kw,
            brand_full=ctx.competitor_brand_full,
            brand_tokens=ctx.competitor_brand_tokens,
        ):
            _drop("brand", {**row, "brand_type": "competitor"}, "competitor_brand", excluded)
            continue
        kw_tokens = _tokens(kw)
        if ctx and ctx.client_brand_tokens and kw_tokens and kw_tokens <= ctx.client_brand_tokens and len(kw_tokens) <= 2:
            _drop("brand", {**row, "brand_type": "client"}, "client_brand_noise", excluded)
            continue
        tagged = {**row, "brand_type": "none"}
        after_brand.append(tagged)
    working = after_brand
    brand_count = len(working)

    # 6. Check relevance
    relevance_audit: dict[str, Any] = {}
    if ctx is not None:
        working, rel_excluded, relevance_audit = filter_relevant_keywords(
            working,
            ctx,
            source_label=source_label,
        )
        for item in rel_excluded:
            excluded.append({**item, "stage": "relevance"})
    else:
        relevance_audit = {"input_count": brand_count, "kept_count": brand_count, "removed_count": 0}
    relevance_count = len(working)

    # 7. Classify intent
    for i, row in enumerate(working):
        kw = str(row.get("keyword") or "")
        working[i] = {**row, "intent": detect_intent(kw, row.get("intent"))}

    # 8. Extract entities / topics
    for i, row in enumerate(working):
        kw = str(row.get("keyword") or "")
        meta = extract_entities_and_topics(kw, ctx)
        working[i] = {**row, **meta}

    final = working

    return {
        "keywords": final,
        "excluded": excluded,
        "pipeline": {
            "raw": {"count": raw_count},
            "normalize": {"count": normalize_count},
            "dedupe": {"count": dedupe_count},
            "noise": {"count": noise_count},
            "brand": {"count": brand_count},
            "relevance": {
                "count": relevance_count,
                **{k: v for k, v in relevance_audit.items() if k != "sample"},
            },
            "intent": {"count": relevance_count},
            "entities": {"count": len(final)},
            "final": {"count": len(final)},
        },
        "relevance_audit": relevance_audit,
    }
