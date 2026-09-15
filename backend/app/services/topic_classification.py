"""Topic classification — compare keyword clusters against the existing site map.

Runs AFTER keyword clustering and BEFORE URL mapping. This is the stage that
decides *what a cluster is* relative to the site that already exists, so URL
mapping never has to invent the topic structure while it picks URLs.

CLUSTERS (+ intent / funnel)          SITE MAP (Phase 3 inventory + live scan)
        \\                                      /
         \\____________  compare  _____________/
                            |
            per-cluster site-map match evidence
                            |
        +-------------------+--------------------+
        |                   |                    |
  supporting-topic    cross-cluster          match-quality
   detection          cannibalization        classification
        |                   |                    |
        +-------------------+--------------------+
                            |
                     TOPIC STATUS  (+ reason, confidence, evidence)
                            |
                      URL MAPPING (Phase 6)

Every decision carries a ``reason`` and a ``confidence``, and every input that
drove it is kept in ``evidence`` so the final SEO map can be audited back to the
original keyword and site-map rows.
"""

from __future__ import annotations

from typing import Any

from app.services.content_pipeline import identify_primary_keyword
from app.services.url_mapping import (
    _MEDIUM_THRESHOLD,
    _norm_kw,
    _overlap_ratio,
    _tokens,
    band_from_score,
    find_existing_match_for_cluster,
)

# --- Topic status vocabulary ------------------------------------------------
EXISTING_TOPIC = "EXISTING_TOPIC"
EXISTING_TOPIC_NEEDS_OPTIMIZATION = "EXISTING_TOPIC_NEEDS_OPTIMIZATION"
EXISTING_TOPIC_NEEDS_CONSOLIDATION = "EXISTING_TOPIC_NEEDS_CONSOLIDATION"
NEW_TOPIC = "NEW_TOPIC"
SUPPORTING_TOPIC = "SUPPORTING_TOPIC"
CANNIBALIZATION_RISK = "CANNIBALIZATION_RISK"
IRRELEVANT = "IRRELEVANT"
UNCERTAIN = "UNCERTAIN"

TOPIC_STATUSES = frozenset(
    {
        EXISTING_TOPIC,
        EXISTING_TOPIC_NEEDS_OPTIMIZATION,
        EXISTING_TOPIC_NEEDS_CONSOLIDATION,
        NEW_TOPIC,
        SUPPORTING_TOPIC,
        CANNIBALIZATION_RISK,
        IRRELEVANT,
        UNCERTAIN,
    }
)

# url_status is the "what URL does this cluster get" answer required of every
# cluster. Exactly one of these five, always.
URL_STATUS_EXISTING = "existing_url"
URL_STATUS_OPTIMIZE = "existing_url_needs_optimization"
URL_STATUS_CONSOLIDATE = "existing_url_needs_consolidation"
URL_STATUS_NEW = "new_proposed_url"
URL_STATUS_NONE = "no_dedicated_url"

# Legacy three-value disposition kept for every downstream reader that predates
# the richer taxonomy (create_topic drafting, phase validation, exports).
_LEGACY_DISPOSITION = {
    EXISTING_TOPIC: "existing_topic",
    EXISTING_TOPIC_NEEDS_OPTIMIZATION: "existing_topic",
    EXISTING_TOPIC_NEEDS_CONSOLIDATION: "existing_review",
    CANNIBALIZATION_RISK: "existing_review",
    NEW_TOPIC: "new_topic",
    SUPPORTING_TOPIC: "supporting_topic",
    IRRELEVANT: "out_of_scope",
}

# Confidence below this and we refuse to assert the status — the provisional
# call is kept but the cluster is surfaced as UNCERTAIN for human review.
_UNCERTAIN_BELOW = 0.40

# Intents that cannot share one page even when the keywords look similar.
_INCOMPATIBLE_INTENTS = frozenset({("informational", "transactional")})

# Relevance verdicts (from keyword_relevance.evaluate_keyword) that mean the
# cluster only survived on weak evidence.
_WEAK_RELEVANCE_REASONS = frozenset({"hygiene_only", "competitor_gap", "unscored", "gap_only"})
_NO_EVIDENCE_REASONS = frozenset({"no_business_evidence", "irrelevant", "out_of_scope"})

# A page that scores well on URL/title overlap but poorly on actual topical
# coverage is an existing page that needs work, not an existing page that is
# already the right answer.
_STRONG_COVERAGE = 14.0  # of keyword_topic_match's 0–25
_STRONG_INTENT_FIT = 12.0  # of intent_match's 0–15
# Two existing pages within this many points of each other both "win" the
# cluster — the site, not the analyst, has the ambiguity.
_TIE_MARGIN = 5.0


def _intent_of(cluster: dict[str, Any]) -> str:
    return str(cluster.get("intent") or "informational").strip().lower()


def _intents_incompatible(a: str, b: str) -> bool:
    pair = tuple(sorted((a, b)))
    return pair in _INCOMPATIBLE_INTENTS or ("transactional", "informational") == pair


def cluster_volume(cluster: dict[str, Any]) -> int:
    """Total search volume across the cluster's keywords (0 when unknown)."""
    total = 0
    for row in cluster.get("keywords") or []:
        if isinstance(row, dict) and isinstance(row.get("volume"), (int, float)):
            total += int(row["volume"])
    if not total and isinstance(cluster.get("est_traffic"), (int, float)):
        total = int(cluster["est_traffic"])
    return total


def _keyword_count(cluster: dict[str, Any]) -> int:
    return len([k for k in (cluster.get("keywords") or []) if isinstance(k, dict)])


def _relevance_reason(cluster: dict[str, Any]) -> str:
    return str((cluster.get("business_relevance") or {}).get("reason") or "unscored").lower()


def mixed_intent_keywords(cluster: dict[str, Any]) -> list[str]:
    """Materially conflicting intents among the cluster's leading keywords.

    Returns the conflicting pair (sorted) or []. Only Primary/Secondary rows
    count — a Supporting long-tail at a different stage is normal and does not
    make the cluster ambiguous.
    """
    intents = {
        str(k.get("intent") or "").strip().lower()
        for k in (cluster.get("keywords") or [])
        if isinstance(k, dict) and str(k.get("role") or "Secondary").lower() in ("primary", "secondary")
    }
    intents.discard("")
    if {"informational", "transactional"} <= intents:
        return ["informational", "transactional"]
    return []


def _priority_key(cluster: dict[str, Any], match: dict[str, Any]) -> tuple:
    """Ordering used to pick the owner when clusters compete for one topic/URL."""
    return (
        float(match.get("match_score") or 0),
        cluster_volume(cluster),
        _keyword_count(cluster),
        float(cluster.get("best_score") or 0),
    )


# --- Stage 1: per-cluster site-map match ------------------------------------


def match_clusters_to_site_map(
    clusters: list[dict[str, Any]],
    *,
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score every cluster against the site map. No decisions taken here."""
    return [
        find_existing_match_for_cluster(cluster, crawled_pages=pages)
        if isinstance(cluster, dict)
        else {}
        for cluster in clusters
    ]


# --- Stage 2: supporting-topic detection ------------------------------------


def detect_supporting_topics(
    clusters: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Find clusters that should strengthen another cluster's page, not own one.

    A cluster is *supporting* when it is a small, long-tail specialisation of a
    materially larger cluster at the same intent — e.g. "crm pricing tiers"
    (2 keywords, 90 searches) under "crm pricing" (14 keywords, 4,300 searches).

    Deliberately conservative: same intent only, the parent's primary keyword
    must be fully contained in the child's, and the parent must be several times
    the size. Semantically-adjacent-but-different needs ("running shoes for flat
    feet" vs "running shoes for beginners") are *not* collapsed by this rule.
    """
    out: dict[int, dict[str, Any]] = {}
    sized = [
        (i, c, _keyword_count(c), cluster_volume(c))
        for i, c in enumerate(clusters)
        if isinstance(c, dict)
    ]
    for i, child, child_kws, child_vol in sized:
        if child_kws > 2:
            continue
        child_primary = identify_primary_keyword(child)
        child_tokens = _tokens(child_primary)
        if not child_tokens:
            continue
        best: tuple[float, int, dict[str, Any]] | None = None
        for j, parent, parent_kws, parent_vol in sized:
            if j == i:
                continue
            if _intent_of(parent) != _intent_of(child):
                continue
            bigger = parent_kws >= max(3, child_kws * 2) or (
                child_vol > 0 and parent_vol >= child_vol * 3
            )
            if not bigger:
                continue
            parent_primary = identify_primary_keyword(parent)
            parent_tokens = _tokens(parent_primary)
            if not parent_tokens or not parent_tokens < child_tokens:
                # Parent must be the strictly broader head of the same phrase.
                continue
            score = _overlap_ratio(parent_primary, child_primary)
            if best is None or score > best[0]:
                best = (score, j, parent)
        if best:
            score, j, parent = best
            out[i] = {
                "parent_index": j,
                "parent_cluster": parent.get("name"),
                "parent_primary_keyword": identify_primary_keyword(parent),
                "overlap": round(score, 2),
            }
    return out


# --- Stage 3: cross-cluster cannibalization / consolidation -----------------


def detect_cluster_cannibalization(
    clusters: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Flag clusters that compete with each other, BEFORE any URL is created.

    Two triggers:
      * two clusters resolve to the same existing URL, or
      * two clusters share the same normalised primary keyword.

    When the competing clusters share a compatible intent the loser is marked
    for consolidation. When their intents are materially different (e.g.
    informational vs transactional) they are flagged but explicitly NOT merged —
    similar wording does not mean one page can satisfy both needs.
    """
    groups: dict[str, list[int]] = {}
    for i, cluster in enumerate(clusters):
        if not isinstance(cluster, dict):
            continue
        match = matches[i] if i < len(matches) else {}
        url = str((match or {}).get("matched_url") or "").strip()
        if url:
            groups.setdefault(f"url:{url}", []).append(i)
        primary = _norm_kw(identify_primary_keyword(cluster))
        if primary:
            groups.setdefault(f"kw:{primary}", []).append(i)

    out: dict[int, dict[str, Any]] = {}
    for key, members in groups.items():
        members = sorted(set(members))
        if len(members) < 2:
            continue
        ranked = sorted(
            members,
            key=lambda i: _priority_key(clusters[i], matches[i] if i < len(matches) else {}),
            reverse=True,
        )
        owner = ranked[0]
        owner_intent = _intent_of(clusters[owner])
        for loser in ranked[1:]:
            loser_intent = _intent_of(clusters[loser])
            different_intent = _intents_incompatible(owner_intent, loser_intent)
            record = {
                "competes_with_index": owner,
                "competes_with_cluster": clusters[owner].get("name"),
                "competes_with_primary_keyword": identify_primary_keyword(clusters[owner]),
                "trigger": "shared_existing_url" if key.startswith("url:") else "duplicate_primary_keyword",
                "shared_url": key[4:] if key.startswith("url:") else None,
                "owner_intent": owner_intent,
                "competing_intent": loser_intent,
                "different_intent": different_intent,
                "recommended_action": (
                    "keep_separate_review_intent" if different_intent else "consolidate_into_owner"
                ),
            }
            prior = out.get(loser)
            # A cluster caught by both triggers keeps the stronger (URL) one.
            if not prior or prior["trigger"] != "shared_existing_url":
                out[loser] = record
    return out


# --- Stage 4: match-quality classification ----------------------------------


def _classify_from_match(
    cluster: dict[str, Any],
    match: dict[str, Any],
    *,
    page_count: int,
    inventory_complete: bool = True,
) -> tuple[str, str, float]:
    """(status, reason, confidence) from site-map match evidence alone."""
    matched = bool(match.get("matched"))
    score = float(match.get("match_score") or 0)
    band = str(match.get("match_band") or band_from_score(score)).upper()
    breakdown = dict(match.get("score_breakdown") or {})
    coverage = float(breakdown.get("keyword_topic_match") or 0)
    intent_fit = float(breakdown.get("intent_match") or 0)
    url = match.get("matched_url")

    if not matched:
        # Nothing on the site covers this cluster.
        if page_count == 0:
            return (
                NEW_TOPIC,
                "No site-map inventory was available to compare against — the "
                "new-topic call is unverified.",
                0.15,
            )
        inventory_factor = 1.0 if page_count >= 5 else 0.7
        if not inventory_complete:
            # Only part of the site was compared against, so "nothing covers
            # this" is a statement about the sample, not about the site.
            inventory_factor *= 0.5
        confidence = round(
            max(0.1, (0.85 - 0.5 * min(1.0, score / _MEDIUM_THRESHOLD)) * inventory_factor), 2
        )
        caveat = (
            ""
            if inventory_complete
            else " The site map is truncated, so pages outside the sample were never checked."
        )
        return (
            NEW_TOPIC,
            f"No existing page covers this topic — best on-site candidate scored "
            f"{score:g}/100 across {page_count} inventoried pages.{caveat}",
            confidence,
        )

    confidence = round(max(0.1, min(0.95, score / 100)), 2)

    # Site-side cannibalization: two live pages chase this one cluster. Pick a
    # winner later — first say that the site has to resolve the overlap.
    runner_up = match.get("runner_up_url")
    runner_up_score = match.get("runner_up_score")
    if runner_up and isinstance(runner_up_score, (int, float)) and score - float(runner_up_score) <= _TIE_MARGIN:
        return (
            EXISTING_TOPIC_NEEDS_CONSOLIDATION,
            f"Two existing pages target this cluster almost equally — {url} "
            f"({score:g}/100) and {runner_up} ({float(runner_up_score):g}/100). Resolve the "
            "overlap (consolidate or differentiate) before mapping or creating anything.",
            round(confidence * 0.8, 2),
        )

    page = dict(match.get("matched_page") or {})
    # The site map already flagged this page as overlapping another page —
    # consolidate that first rather than pointing more keywords at it.
    overlaps = [u for u in (page.get("potential_cannibalization") or []) if u]
    if overlaps:
        return (
            EXISTING_TOPIC_NEEDS_CONSOLIDATION,
            f"{url} matched this cluster (score {score:g}/100) but the site map flags it as "
            f"overlapping {', '.join(overlaps[:3])} — consolidate the existing pages before "
            "assigning this cluster to one of them.",
            round(confidence * 0.85, 2),
        )

    thin = str(page.get("content_quality") or "") in ("thin", "broken")

    if band == "HIGH" and coverage >= _STRONG_COVERAGE and intent_fit >= _STRONG_INTENT_FIT and not thin:
        return (
            EXISTING_TOPIC,
            f"{url} directly satisfies the dominant search intent and semantic topic "
            f"(score {score:g}/100, topical coverage {coverage:g}/25, intent fit {intent_fit:g}/15).",
            confidence,
        )
    if band == "HIGH":
        weak = []
        if coverage < _STRONG_COVERAGE:
            weak.append(f"its topical coverage is thin ({coverage:g}/25)")
        if intent_fit < _STRONG_INTENT_FIT:
            weak.append(
                f"its page type only partly fits {_intent_of(cluster)} intent ({intent_fit:g}/15)"
            )
        if thin:
            wc = page.get("word_count")
            weak.append(
                f"the site map rates it {page.get('content_quality')}"
                + (f" ({int(wc)} words)" if isinstance(wc, (int, float)) else "")
            )
        return (
            EXISTING_TOPIC_NEEDS_OPTIMIZATION,
            f"{url} is the right page for this cluster (score {score:g}/100) but "
            + " and ".join(weak)
            + " — optimize in place rather than creating a new URL.",
            confidence,
        )
    if band == "MEDIUM":
        return (
            EXISTING_TOPIC_NEEDS_CONSOLIDATION,
            f"{url} is a partial/mismatched target for this cluster (score {score:g}/100) — "
            "review whether to retarget, consolidate, or split before creating anything new.",
            confidence,
        )
    return (
        UNCERTAIN,
        f"{url} matched only weakly (score {score:g}/100) — not enough evidence to "
        "call this an existing topic or a new one.",
        confidence,
    )


def classify_cluster(
    cluster: dict[str, Any],
    *,
    match: dict[str, Any],
    page_count: int,
    supporting: dict[str, Any] | None = None,
    cannibalization: dict[str, Any] | None = None,
    inventory_complete: bool = True,
) -> dict[str, Any]:
    """Decide one cluster's topic status with a reason, confidence and evidence.

    Precedence: out-of-scope → supporting → cannibalization → match quality.
    An UNCERTAIN overlay is applied last when confidence is too low to assert
    the call; the provisional status is preserved so the pipeline still flows.
    """
    primary = identify_primary_keyword(cluster)
    relevance = _relevance_reason(cluster)
    status: str
    reason: str
    confidence: float
    parent: dict[str, Any] | None = None

    if relevance in _NO_EVIDENCE_REASONS:
        status = IRRELEVANT
        reason = (
            f"Primary keyword '{primary}' carries no business evidence "
            f"({relevance}) — out of scope until a human confirms otherwise."
        )
        confidence = 0.6
    elif supporting:
        status = SUPPORTING_TOPIC
        reason = (
            f"'{primary}' is a long-tail specialisation of "
            f"'{supporting['parent_primary_keyword']}' at the same intent "
            f"({_intent_of(cluster)}) — strengthen that page instead of creating a URL."
        )
        confidence = round(min(0.9, 0.5 + float(supporting.get("overlap") or 0) * 0.4), 2)
        parent = supporting
    elif cannibalization:
        different = bool(cannibalization.get("different_intent"))
        status = CANNIBALIZATION_RISK if different else EXISTING_TOPIC_NEEDS_CONSOLIDATION
        if different:
            reason = (
                f"Competes with cluster '{cannibalization['competes_with_cluster']}' "
                f"({cannibalization['trigger']}), but intents differ "
                f"({cannibalization['owner_intent']} vs {cannibalization['competing_intent']}) — "
                "flag, do not merge."
            )
        else:
            reason = (
                f"Same topic and intent as '{cannibalization['competes_with_cluster']}' "
                f"({cannibalization['trigger']}) — consolidate into that page rather than "
                "targeting the same query twice."
            )
        confidence = 0.7
    else:
        status, reason, confidence = _classify_from_match(
            cluster, match, page_count=page_count, inventory_complete=inventory_complete
        )

    provisional = status
    # A cluster whose own leading keywords disagree about intent cannot be
    # confidently mapped to one page type, however well it matched a URL.
    mixed = mixed_intent_keywords(cluster)
    if mixed and status in (EXISTING_TOPIC, EXISTING_TOPIC_NEEDS_OPTIMIZATION, NEW_TOPIC):
        status = UNCERTAIN
        reason = (
            f"{reason} Cluster mixes {' and '.join(mixed)} intent across its primary/secondary "
            "keywords — one page cannot be assumed to satisfy both; split or confirm before mapping."
        )
        confidence = round(min(confidence, 0.35), 2)

    if status not in (IRRELEVANT, SUPPORTING_TOPIC) and confidence < _UNCERTAIN_BELOW:
        status = UNCERTAIN
        reason = f"{reason} Confidence {confidence:.2f} is below the {_UNCERTAIN_BELOW:.2f} " \
                 f"threshold — flagged for human review (provisional: {provisional})."

    if relevance in _WEAK_RELEVANCE_REASONS and status == NEW_TOPIC:
        confidence = round(max(0.1, confidence - 0.15), 2)
        reason = f"{reason} Business relevance for this cluster is weak ({relevance})."

    decision = {
        "topic_status": status,
        "provisional_status": provisional,
        "topic_reason": reason,
        "topic_confidence": confidence,
        "needs_human_review": status in (UNCERTAIN, CANNIBALIZATION_RISK, IRRELEVANT),
        "url_status": _URL_STATUS_FOR.get(provisional, URL_STATUS_NONE),
        "topic_disposition": _LEGACY_DISPOSITION.get(
            status, _LEGACY_DISPOSITION.get(provisional, "new_topic")
        ),
        "evidence": {
            "primary_keyword": primary,
            "intent": _intent_of(cluster),
            "funnel": cluster.get("funnel"),
            "keyword_count": _keyword_count(cluster),
            "cluster_volume": cluster_volume(cluster),
            "business_relevance": relevance,
            "site_map_pages_compared": page_count,
            "site_map_complete": inventory_complete,
            "matched_url": match.get("matched_url"),
            "match_score": match.get("match_score"),
            "match_band": match.get("match_band"),
            "score_breakdown": match.get("score_breakdown") or {},
            "matched_page_title": (match.get("matched_page") or {}).get("title"),
        },
    }
    if parent:
        decision["supporting_parent"] = parent
    if cannibalization:
        decision["cannibalization"] = cannibalization
    return decision


_URL_STATUS_FOR = {
    EXISTING_TOPIC: URL_STATUS_EXISTING,
    EXISTING_TOPIC_NEEDS_OPTIMIZATION: URL_STATUS_OPTIMIZE,
    EXISTING_TOPIC_NEEDS_CONSOLIDATION: URL_STATUS_CONSOLIDATE,
    CANNIBALIZATION_RISK: URL_STATUS_CONSOLIDATE,
    NEW_TOPIC: URL_STATUS_NEW,
    SUPPORTING_TOPIC: URL_STATUS_NONE,
    IRRELEVANT: URL_STATUS_NONE,
    UNCERTAIN: URL_STATUS_NONE,
}


# --- Orchestrator -----------------------------------------------------------


def classify_clusters_against_site_map(
    clusters: list[dict[str, Any]],
    *,
    pages: list[dict[str, Any]],
    inventory_complete: bool = True,
) -> dict[str, Any]:
    """Run the full topic-classification stage and annotate each cluster in place.

    Returns a report; each cluster gains ``topic_status``, ``topic_reason``,
    ``topic_confidence``, ``url_status``, ``existing_page_match`` and the legacy
    ``topic_disposition`` / ``sitemap_match`` keys.
    """
    live = [c for c in clusters if isinstance(c, dict)]
    matches = match_clusters_to_site_map(live, pages=pages)
    supporting = detect_supporting_topics(live)
    cannibal = detect_cluster_cannibalization(live, matches)
    page_count = len(pages or [])

    by_status: dict[str, list[dict[str, Any]]] = {s: [] for s in TOPIC_STATUSES}
    for i, cluster in enumerate(live):
        match = matches[i]
        decision = classify_cluster(
            cluster,
            match=match,
            page_count=page_count,
            supporting=supporting.get(i),
            cannibalization=cannibal.get(i),
            inventory_complete=inventory_complete,
        )
        cluster.update(
            {
                "existing_page_match": match,
                "topic_status": decision["topic_status"],
                "provisional_topic_status": decision["provisional_status"],
                "topic_reason": decision["topic_reason"],
                "topic_confidence": decision["topic_confidence"],
                "topic_needs_review": decision["needs_human_review"],
                "url_status": decision["url_status"],
                "topic_disposition": decision["topic_disposition"],
                "topic_evidence": decision["evidence"],
                "sitemap_match": {
                    "disposition": decision["topic_disposition"],
                    "topic_status": decision["topic_status"],
                    "matched_url": match.get("matched_url"),
                    "match_score": match.get("match_score"),
                    "match_band": match.get("match_band"),
                    "reason": decision["topic_reason"],
                    "confidence": decision["topic_confidence"],
                },
            }
        )
        if "supporting_parent" in decision:
            cluster["supporting_parent"] = decision["supporting_parent"]
        else:
            cluster.pop("supporting_parent", None)
        if "cannibalization" in decision:
            cluster["cannibalization"] = decision["cannibalization"]
        else:
            cluster.pop("cannibalization", None)
        by_status[decision["topic_status"]].append(cluster)

    def _rows(status: str) -> list[dict[str, Any]]:
        return [
            {
                "cluster": c.get("name"),
                "primary_keyword": identify_primary_keyword(c),
                "intent": c.get("intent"),
                "funnel": c.get("funnel"),
                "topic_status": c.get("topic_status"),
                "reason": c.get("topic_reason"),
                "confidence": c.get("topic_confidence"),
                "matched_url": (c.get("existing_page_match") or {}).get("matched_url"),
                "match_band": (c.get("existing_page_match") or {}).get("match_band"),
                "supporting_parent": (c.get("supporting_parent") or {}).get("parent_cluster"),
                "competes_with": (c.get("cannibalization") or {}).get("competes_with_cluster"),
            }
            for c in by_status[status]
        ]

    counts = {status: len(by_status[status]) for status in sorted(TOPIC_STATUSES)}
    return {
        "pages_used": page_count,
        "inventory_complete": inventory_complete,
        "clusters_classified": len(live),
        "counts": counts,
        "by_status": {status: _rows(status) for status in sorted(TOPIC_STATUSES)},
        "needs_review": [
            row for status in sorted(TOPIC_STATUSES) for row in _rows(status)
            if status in (UNCERTAIN, CANNIBALIZATION_RISK, IRRELEVANT)
        ],
        # --- legacy three-bucket view (existing readers) --------------------
        "existing_topic_count": counts[EXISTING_TOPIC] + counts[EXISTING_TOPIC_NEEDS_OPTIMIZATION],
        "existing_review_count": counts[EXISTING_TOPIC_NEEDS_CONSOLIDATION] + counts[CANNIBALIZATION_RISK],
        "new_topic_count": len([c for c in live if c.get("topic_disposition") == "new_topic"]),
        "supporting_topic_count": counts[SUPPORTING_TOPIC],
        "uncertain_count": counts[UNCERTAIN],
        "out_of_scope_count": counts[IRRELEVANT],
        "existing_topics": _rows(EXISTING_TOPIC) + _rows(EXISTING_TOPIC_NEEDS_OPTIMIZATION),
        "existing_review": _rows(EXISTING_TOPIC_NEEDS_CONSOLIDATION) + _rows(CANNIBALIZATION_RISK),
        "new_topics": _rows(NEW_TOPIC),
        "supporting_topics": _rows(SUPPORTING_TOPIC),
    }
