"""Keyword Clustering skill — clean, cluster, map to content pages."""

from __future__ import annotations

import re
from typing import Any

from app.services.keyword_opportunity import (
    detect_funnel as _detect_funnel,
    detect_intent,
    is_stale_year_keyword,
)
from app.services.keyword_pipeline import run_keyword_pipeline
from app.services.keyword_relevance import evaluate_keyword
from app.services.topic_naming import specific_cluster_name

_STOP = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with"}
_LOCATIONS = {
    "near me",
    "usa",
    "uk",
    "us",
    "australia",
    "canada",
    "india",
    "singapore",
    "london",
    "new york",
    "sydney",
    "toronto",
    "mumbai",
    "delhi",
}
_BRAND_HINTS = ("brand", "official", "login", "pricing", "cost")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _norm(text)).strip("-")
    return s[:70] or "cluster"


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


def build_service_seed_clusters(
    seed_clusters: list[dict[str, Any]],
    services: list[str],
) -> list[dict[str, Any]]:
    """Group every seed and its extracted keywords under the closest service.

    Explicit ``target_type=service`` associations win. Page/keyword seeds are
    assigned by token overlap against the CDD service names. Seeds with no
    defensible overlap remain visible under ``Other website topics`` rather
    than being forced into an unrelated service.
    """
    clean_services: list[str] = []
    seen_services: set[str] = set()
    for service in services:
        label = str(service or "").strip()
        key = _norm(label)
        if not key or key in seen_services:
            continue
        seen_services.add(key)
        clean_services.append(label)

    groups: dict[str, dict[str, Any]] = {
        _norm(service): {
            "service": service,
            "service_key": _norm(service),
            "seed_count": 0,
            "keyword_count": 0,
            "total_volume": 0,
            "seeds": [],
        }
        for service in clean_services
    }
    other_key = "_other"

    def _best_service(cluster: dict[str, Any]) -> tuple[str, str, int]:
        seed = str(cluster.get("seed") or "").strip()
        target = str(cluster.get("target") or seed).strip()
        if str(cluster.get("target_type") or "").lower() == "service":
            explicit = next(
                (s for s in clean_services if _norm(s) in {_norm(target), _norm(seed)}),
                None,
            )
            if explicit:
                return _norm(explicit), "explicit_service_target", 100

        source_tokens = _tokens(f"{seed} {target}")
        best_key = ""
        best_score = 0
        for service in clean_services:
            service_tokens = _tokens(service)
            if not service_tokens:
                continue
            overlap = len(source_tokens & service_tokens)
            score = overlap * 10
            if service_tokens.issubset(source_tokens):
                score += 20
            elif source_tokens and source_tokens.issubset(service_tokens):
                score += 10
            # Expanded keywords can disambiguate a generic page/keyword seed.
            for cls in ("exact", "phrase", "related", "broad"):
                for row in (cluster.get(cls) or [])[:10]:
                    if not isinstance(row, dict):
                        continue
                    kw_tokens = _tokens(str(row.get("keyword") or ""))
                    score += min(3, len(kw_tokens & service_tokens))
            if score > best_score:
                best_key = _norm(service)
                best_score = score
        if best_key and best_score >= 10:
            return best_key, "service_token_overlap", best_score
        return other_key, "no_service_overlap", 0

    for cluster in seed_clusters:
        if not isinstance(cluster, dict) or not cluster.get("seed"):
            continue
        group_key, reason, score = _best_service(cluster)
        if group_key not in groups:
            groups[group_key] = {
                "service": "Other website topics",
                "service_key": other_key,
                "seed_count": 0,
                "keyword_count": 0,
                "total_volume": 0,
                "seeds": [],
            }

        class_counts: dict[str, int] = {}
        keywords: list[dict[str, Any]] = []
        seen_keywords: set[tuple[str, str]] = set()
        for cls in ("exact", "phrase", "related", "broad"):
            rows = [r for r in (cluster.get(cls) or []) if isinstance(r, dict)]
            class_counts[cls] = len(rows)
            for row in rows:
                keyword = str(row.get("keyword") or "").strip()
                association = (cls, _norm(keyword))
                if not keyword or association in seen_keywords:
                    continue
                seen_keywords.add(association)
                keywords.append(
                    {
                        "keyword": keyword,
                        "match_class": cls,
                        "volume": row.get("volume"),
                        "difficulty": row.get("difficulty"),
                        "intent": detect_intent(keyword, row.get("intent")),
                        "source": row.get("source"),
                        "below_volume_floor": bool(row.get("below_volume_floor")),
                    }
                )

        entry = {
            "seed": cluster.get("seed"),
            "target": cluster.get("target") or cluster.get("seed"),
            "target_type": cluster.get("target_type") or "keyword",
            "assignment_reason": reason,
            "assignment_score": score,
            "keyword_count": len(keywords),
            "class_counts": class_counts,
            "classes_missing": list(cluster.get("classes_missing") or []),
            "keywords": keywords,
        }
        group = groups[group_key]
        group["seeds"].append(entry)
        group["seed_count"] += 1
        group["keyword_count"] += len(keywords)
        group["total_volume"] += sum(
            int(row.get("volume") or 0)
            for row in keywords
            if isinstance(row, dict) and isinstance(row.get("volume"), (int, float))
        )

    out = [group for group in groups.values() if group["seeds"]]
    for group in out:
        group["seeds"].sort(
            key=lambda row: (
                {"service": 0, "page": 1, "keyword": 2}.get(
                    str(row.get("target_type") or "keyword"), 3
                ),
                -int(row.get("keyword_count") or 0),
                str(row.get("seed") or "").lower(),
            )
        )
        # Number seeds within the service (Seed 1, Seed 2, …). Every service
        # cluster has at least one seed by construction (empty groups dropped).
        for index, seed in enumerate(group["seeds"], start=1):
            seed["seed_index"] = index
            seed["seed_label"] = f"Seed {index}"
    out.sort(key=lambda group: (group["service_key"] == other_key, str(group["service"]).lower()))
    return out


def clean_and_dedupe(
    rows: list[dict[str, Any]],
    *,
    seeds: list[str] | None = None,
    products: list[str] | None = None,
    relevance_context: Any | None = None,
    brand_name: str | None = None,
) -> list[dict[str, Any]]:
    """Run the standard keyword cleaning pipeline; return final keyword rows only."""
    result = run_keyword_pipeline(
        [r for r in rows if isinstance(r, dict)],
        relevance_context=relevance_context,
        brand_name=brand_name,
        source_label="clustering",
    )
    return result["keywords"]


def _detect_intent(kw: str, existing: str | None = None) -> str:
    if existing and existing not in ("mixed", ""):
        return str(existing).lower()
    k = _norm(kw)
    if any(x in k for x in ("buy", "pricing", "price", "cost", "hire", "quote", "near me")):
        return "transactional"
    if any(x in k for x in ("best", "top", "vs", "versus", "alternative", "review", "compare")):
        return "commercial"
    return "informational"




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
    for loc in _LOCATIONS:
        if loc in k:
            return f"location:{loc}"
    return None


def _content_type(modifier: str | None, intent: str) -> tuple[str, str]:
    """Return (content_type, recommended_content label)."""
    if modifier == "comparison":
        return "comparison", "Comparison Page"
    if modifier == "listicle":
        return "listicle", "Listicle / Best-of Post"
    if modifier == "how-to":
        return "guide", "How-to Tutorial"
    if modifier == "tools":
        return "listicle", "Tools / Product Review Roundup"
    if modifier and modifier.startswith("location:"):
        return "landing", "Local Landing Page"
    if intent == "transactional":
        return "landing", "Service / Landing Page"
    if intent == "commercial":
        return "guide", "Consideration Guide"
    return "guide", "Comprehensive Guide"


def _head_topic(kw: str) -> str:
    drop = {
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
    }
    head_toks = [t for t in re.findall(r"[a-z0-9]+", _norm(kw)) if t not in drop and t not in _STOP][:3]
    return " ".join(head_toks[:2]) or _norm(kw)


def _cluster_key(row: dict[str, Any], brand_tokens: set[str]) -> tuple[str, str]:
    """Return (group_key, display_name).

    Level-1 groups by head topic (one page family). Comparisons / branded /
    strong location queries stay separate to avoid cannibalization.
    """
    kw = _norm(str(row.get("keyword") or ""))
    toks = _tokens(kw)
    if brand_tokens and toks & brand_tokens:
        brand = next(iter(toks & brand_tokens))
        return f"brand:{brand}", f"{brand.title()} (Branded)"

    mod = _modifier_bucket(kw)
    head = _head_topic(kw)
    intent = _detect_intent(kw, row.get("intent"))

    if mod == "comparison":
        return f"cmp:{_stem_key(kw)}", f"{head.title()} Comparison"
    if mod and mod.startswith("location:"):
        loc = mod.split(":", 1)[1]
        return f"loc:{head}|{loc}", f"{head.title()} - {loc.title()}"
    parent = str(row.get("parent_topic") or "").strip()
    if parent:
        return f"topic:{_norm(parent)}", parent.title()
    # Modifier informs the label later; key stays topical so how-to/listicle
    # variants of the same head can share a page when SERP-overlap is likely.
    label = head.title()
    if mod == "how-to":
        label = f"How to {head.title()}"
    elif mod == "listicle":
        label = f"Best {head.title()}"
    elif mod == "what-is":
        label = f"What Is {head.title()}?"
    elif mod == "tools":
        label = f"{head.title()} Tools"
    return f"topic:{head}|{intent}", label


_GENERIC_BRAND_STOP = {
    "seo",
    "marketing",
    "digital",
    "agency",
    "services",
    "service",
    "solutions",
    "group",
    "company",
    "inc",
    "llc",
    "ltd",
    "the",
}


def build_clusters_deterministic(
    rows: list[dict[str, Any]],
    *,
    brand_name: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    brand_tokens: set[str] = set()
    if brand_name:
        brand_tokens |= {
            t for t in _tokens(brand_name) if t not in _GENERIC_BRAND_STOP and len(t) > 2
        }
    if domain:
        root = domain.split(".")[0].lower()
        if root not in _GENERIC_BRAND_STOP and len(root) > 2:
            brand_tokens.add(root)

    groups: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        key, label = _cluster_key(row, brand_tokens)
        groups.setdefault(key, []).append(row)
        # Prefer broader topical label over first-seen modifier label
        if key not in labels or key.startswith("topic:"):
            labels[key] = label if key not in labels else labels[key]
            if key.startswith("topic:") and " (" not in labels[key]:
                labels[key] = label.split("(")[0].strip() if "(" in label else labels.get(key, label)

    # Merge tiny topical clusters (<3) into nearest topical cluster by head overlap
    small = [k for k, v in groups.items() if len(v) < 3]
    large_keys = [k for k, v in groups.items() if len(v) >= 3]
    for sk in small:
        if sk.startswith("cmp:") or sk.startswith("brand:") or sk.startswith("loc:"):
            continue
        stoks = _tokens(labels.get(sk, sk))
        best_k = None
        best_score = 0
        candidates = large_keys or [k for k in groups if k != sk and k.startswith("topic:")]
        for lk in candidates:
            if lk == sk:
                continue
            score = len(stoks & _tokens(labels.get(lk, lk)))
            if score > best_score:
                best_score = score
                best_k = lk
        if best_k and best_score > 0:
            groups[best_k].extend(groups.pop(sk))
            labels.pop(sk, None)

    # Split oversized clusters (>15) by modifier
    for key in list(groups.keys()):
        items = groups[key]
        if len(items) <= 15:
            continue
        subgroups: dict[str, list[dict[str, Any]]] = {}
        for row in items:
            mod = _modifier_bucket(str(row.get("keyword") or "")) or "core"
            subgroups.setdefault(mod, []).append(row)
        if len(subgroups) > 1:
            del groups[key]
            base = labels.pop(key, key)
            for mod, sub in subgroups.items():
                nk = f"{key}|{mod}"
                groups[nk] = sub
                labels[nk] = f"{base} - {mod}"

    clusters_out: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []

    for key, items in groups.items():
        # Keep single-keyword clusters when they are comparison/brand/location
        # or when opportunity is strong enough to warrant its own page.
        if len(items) == 1 and not key.startswith(("cmp:", "brand:", "loc:")):
            only = items[0]
            if (only.get("opportunity_score") or 0) < 50 and (only.get("volume") or 0) < 200:
                orphans.append(
                    {
                        "keyword": only.get("keyword"),
                        "notes": "Single low-value keyword - needs more related terms",
                        "volume": only.get("volume"),
                    }
                )
                continue

        # Primary = opportunity + specificity; prefer long-tail over bare head terms
        ranked = sorted(
            items,
            key=lambda r: (
                float(r.get("opportunity_score") or 0),
                float(r.get("specificity") or 0),
                float(r.get("volume") or 0),
                len(str(r.get("keyword") or "").split()),
            ),
            reverse=True,
        )
        primary = ranked[0]
        if len(str(primary.get("keyword") or "").split()) == 1:
            alt = next(
                (
                    r
                    for r in ranked
                    if len(str(r.get("keyword") or "").split()) >= 3
                    and (r.get("opportunity_score") or 0)
                    >= (primary.get("opportunity_score") or 0) * 0.7
                ),
                None,
            )
            if alt:
                primary = alt
        pkw = str(primary.get("keyword") or "")
        intent = _detect_intent(pkw, primary.get("intent"))
        funnel = _detect_funnel(pkw, intent)
        mod = _modifier_bucket(pkw)
        ctype, rec_label = _content_type(mod, intent)
        # Name from full primary keyword (not truncated generic head)
        head = _head_topic(pkw)
        if key.startswith("cmp:"):
            cluster_name = specific_cluster_name(pkw, intent=intent, head=head, mod="comparison")
        elif key.startswith("loc:"):
            loc = key.split("|")[-1] if "|" in key else "local"
            cluster_name = specific_cluster_name(
                pkw, intent=intent, head=head, mod=f"location:{loc}"
            )
        elif key.startswith("brand:"):
            cluster_name = specific_cluster_name(pkw, intent="navigational", head=head)
        else:
            cluster_name = specific_cluster_name(pkw, intent=intent, head=head, mod=mod)
        kw_rows = []
        for i, r in enumerate(ranked[:15]):
            is_primary = _norm(str(r.get("keyword") or "")) == _norm(pkw)
            if is_primary:
                role = "Primary"
            elif i < 4:
                role = "Secondary"
            else:
                role = "Supporting"
            row_kw = str(r.get("keyword") or "")
            row_intent = _detect_intent(row_kw, r.get("intent"))
            kw_rows.append(
                {
                    "keyword": r.get("keyword"),
                    "volume": r.get("volume"),
                    "difficulty": r.get("difficulty"),
                    "cpc": r.get("cpc"),
                    "intent": row_intent,
                    # Per-keyword stage, not just the cluster's — a Secondary/
                    # Supporting keyword can sit at a different funnel stage than
                    # the Primary even inside one cluster (e.g. "what is seo"
                    # (TOFU) supporting a "seo services" (BOFU) primary).
                    "funnel": _detect_funnel(row_kw, row_intent),
                    "role": role,
                    "opportunity_score": r.get("opportunity_score"),
                    "gap_flag": r.get("gap_flag"),
                    "specificity": r.get("specificity"),
                    # Carried from keyword_relevance.py's evaluate_keyword() so the
                    # cluster contract keeps *why* each keyword survived cleaning,
                    # not just that it did.
                    "relevance_reason": r.get("relevance_reason"),
                    "relevance_evidence": r.get("relevance_evidence") or None,
                }
            )
        # Ensure exactly one Primary
        if not any(k.get("role") == "Primary" for k in kw_rows) and kw_rows:
            kw_rows[0]["role"] = "Primary"
        # Move primary to front
        kw_rows.sort(key=lambda k: 0 if k.get("role") == "Primary" else 1)
        vols = [
            int(r.get("volume"))
            for r in ranked
            if isinstance(r, dict) and r.get("volume") is not None
        ]
        est_traffic = sum(vols) if vols else None
        clusters_out.append(
            {
                "name": cluster_name,
                "intent": intent,
                "funnel": funnel,
                "recommended_content": rec_label,
                "content_type": ctype,
                "recommended_url": f"/blog/{_slug(pkw)}",
                "primary_keyword": pkw,
                "est_traffic": est_traffic,
                "keyword_count": len(kw_rows),
                "avg_difficulty": (
                    round(
                        sum(float(r["difficulty"]) for r in ranked if r.get("difficulty") is not None)
                        / max(
                            1,
                            sum(1 for r in ranked if r.get("difficulty") is not None),
                        ),
                        1,
                    )
                    if any(r.get("difficulty") is not None for r in ranked)
                    else None
                ),
                "best_score": float(primary.get("opportunity_score") or 0),
                "competitor_domains": sorted(
                    {
                        d
                        for r in ranked
                        for d in (r.get("competitor_domains") or [])
                        if d
                    }
                )[:5],
                # Entities computed by keyword_pipeline's extract_entities_and_topics
                # (step 8) were previously discarded before reaching the cluster
                # contract — aggregate them here so Phase 6/9 can see what the
                # cluster is actually about beyond the raw keyword strings.
                "entities": sorted(
                    {e for r in ranked for e in (r.get("entities") or []) if e}
                )[:12],
                # Cluster-level business-relevance rationale, traced back to the
                # primary keyword's evaluate_keyword() verdict rather than a new
                # invented summary — "no_business_evidence"/"hygiene_only" primaries
                # surface here so a reviewer can see the cluster wasn't strongly
                # evidenced, not just that it exists.
                "business_relevance": {
                    "reason": primary.get("relevance_reason") or "unscored",
                    "evidence": primary.get("relevance_evidence") or None,
                },
                "keywords": kw_rows,
            }
        )

    clusters_out.sort(key=lambda c: c.get("best_score") or 0, reverse=True)

    roadmap = []
    for i, c in enumerate(clusters_out[:20], start=1):
        roadmap.append(
            {
                "priority": i,
                "cluster": c["name"],
                "content_type": c["content_type"],
                "target_keyword": c["primary_keyword"],
                "est_traffic": c.get("est_traffic"),
            }
        )

    return {
        "total_keywords": len(rows),
        "clusters_created": len(clusters_out),
        "orphan_count": len(orphans),
        "clusters": clusters_out,
        "orphans": orphans[:30],
        "content_roadmap": roadmap,
        "source": "deterministic_rules",
    }


def validate_clusters(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """Post-cluster QA: one page per cluster, roles, intent, and uniqueness."""
    issues: list[dict[str, Any]] = []
    seen_primary: set[str] = set()
    valid_count = 0
    for i, cluster in enumerate(clusters or []):
        if not isinstance(cluster, dict):
            issues.append({"cluster_index": i, "issue": "invalid_cluster_shape"})
            continue
        name = str(cluster.get("name") or f"cluster_{i}").strip()
        kws = [k for k in (cluster.get("keywords") or []) if isinstance(k, dict)]
        primary = str(cluster.get("primary_keyword") or "").strip().lower()
        if not primary and kws:
            primary = str(kws[0].get("keyword") or "").strip().lower()
        if not primary:
            issues.append({"cluster": name, "issue": "missing_primary_keyword"})
            continue
        if primary in seen_primary:
            issues.append({"cluster": name, "issue": "duplicate_primary_keyword", "keyword": primary})
        seen_primary.add(primary)
        primaries = [k for k in kws if str(k.get("role") or "").lower() == "primary"]
        if len(primaries) > 1:
            issues.append({"cluster": name, "issue": "multiple_primary_roles"})
        if not cluster.get("intent"):
            issues.append({"cluster": name, "issue": "missing_intent"})
        if not (cluster.get("content_type") or cluster.get("recommended_content")):
            issues.append({"cluster": name, "issue": "missing_content_type"})
        # Intent-conflict check: the dominant clustering key (parent_topic) is
        # intent-blind, so a cluster can silently mix e.g. informational and
        # transactional keywords. Flag rather than auto-split — one cluster may
        # still be the right call if the reviewer confirms a single page can
        # satisfy both, but it must not ship unflagged.
        kw_intents = {
            str(k.get("intent") or "").strip().lower()
            for k in kws
            if str(k.get("role") or "").lower() in ("primary", "secondary") and k.get("intent")
        }
        if {"informational", "transactional"} <= kw_intents:
            issues.append(
                {
                    "cluster": name,
                    "issue": "mixed_intent_cluster",
                    "severity": "warn",
                    "intents": sorted(kw_intents),
                }
            )
        if len(kws) < 1:
            issues.append({"cluster": name, "issue": "empty_keyword_list"})
        elif len(kws) == 1:
            issues.append({"cluster": name, "issue": "single_keyword_cluster", "severity": "warn"})
        else:
            valid_count += 1
    blocking = [x for x in issues if x.get("severity") != "warn" and x.get("issue") != "single_keyword_cluster"]
    return {
        "valid": not blocking,
        "cluster_count": len(clusters or []),
        "valid_cluster_count": valid_count,
        "issues": issues,
    }


def _attach_cluster_validation(report: dict[str, Any]) -> dict[str, Any]:
    report["cluster_validation"] = validate_clusters(report.get("clusters") or [])
    return report


def clusters_for_cdp(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten cluster report into the simpler CDP `clusters` list."""
    out: list[dict[str, Any]] = []
    for c in report.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        out.append(
            {
                "name": c.get("name"),
                "intent": c.get("intent"),
                "funnel": c.get("funnel"),
                "keyword_count": c.get("keyword_count") or len(c.get("keywords") or []),
                "keywords": c.get("keywords") or [],
                "total_volume": c.get("est_traffic"),
                "avg_difficulty": c.get("avg_difficulty"),
                "best_score": c.get("best_score"),
                "competitor_domains": c.get("competitor_domains") or [],
                "recommended_content": c.get("recommended_content"),
                "recommended_url": c.get("recommended_url"),
                "primary_keyword": c.get("primary_keyword"),
                "content_type": c.get("content_type"),
                "entities": c.get("entities") or [],
                "business_relevance": c.get("business_relevance"),
            }
        )
    return out


async def run_keyword_clustering(
    rows: list[dict[str, Any]],
    *,
    seeds: list[str] | None = None,
    brand_name: str | None = None,
    domain: str | None = None,
    products: list[str] | None = None,
    use_llm: bool = True,
    relevance_context: Any | None = None,
) -> dict[str, Any]:
    pipeline_result = run_keyword_pipeline(
        [r for r in rows if isinstance(r, dict) and r.get("keyword")],
        relevance_context=relevance_context,
        brand_name=brand_name,
        source_label="clustering",
    )
    cleaned = pipeline_result["keywords"]
    report = build_clusters_deterministic(
        cleaned, brand_name=brand_name, domain=domain
    )
    report["keyword_pipeline"] = pipeline_result["pipeline"]
    report["pipeline_excluded"] = pipeline_result["excluded"][:24]
    report["keyword_cleaning"] = pipeline_result.get("relevance_audit") or {}

    if not use_llm or len(cleaned) < 3:
        return _attach_cluster_validation(report)

    from app.config import get_settings

    if not get_settings().keyword_cluster_use_llm:
        return _attach_cluster_validation(report)

    from app.services.keyword_llm_clustering import llm_cluster_keywords

    llm_result = await llm_cluster_keywords(
        cleaned,
        brand_name=brand_name,
        domain=domain,
        products=products,
        seeds=seeds,
        relevance_context=relevance_context,
    )

    if llm_result.get("skipped") or not llm_result.get("clusters"):
        return _attach_cluster_validation(report)

    clusters = llm_result["clusters"]
    orphans = llm_result.get("orphans") or []
    roadmap = llm_result.get("content_roadmap") or []

    clusters.sort(key=lambda c: c.get("best_score") or 0, reverse=True)
    return _attach_cluster_validation(
        {
            "total_keywords": len(cleaned),
            "clusters_created": len(clusters),
            "orphan_count": len(orphans),
            "clusters": clusters,
            "orphans": orphans[:30],
            "content_roadmap": roadmap[:20],
            "source": "keyword_clustering_skill",
            "keyword_pipeline": report.get("keyword_pipeline"),
            "pipeline_excluded": report.get("pipeline_excluded"),
            "keyword_cleaning": report.get("keyword_cleaning"),
            "llm_invented_dropped": llm_result.get("llm_invented_dropped") or [],
        }
    )
