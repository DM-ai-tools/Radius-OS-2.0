"""Phase 9 — lock a page roadmap by joining strategy + architecture + audit.

Zero judgement: every field is copied from an upstream pack or derived by catalog.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

PRIORITY_TIERS = frozenset({"quick_win", "big_bet", "fill_in", "avoid"})
# Blog/guide creates that are not tied to a confirmed selected service stay small.
# Phase 5 often overshoots with near-duplicate ideas. A selected service does not
# receive a fixed guide, pricing, FAQ, or tool page. Supporting pages come from
# that service's keyword clusters, and a blog is added only for a real trending keyword.
MAX_BLOG_CREATES = 2
MIN_BLOG_CREATES = 2
BLOGS_PER_SELECTED_SERVICE = 2
_GENERIC_MATCH_TOKENS = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "service",
        "services",
        "agency",
        "agencies",
        "company",
        "management",
        "solutions",
        "solution",
    }
)
SERVICE_PAGE_TYPES = frozenset({"service", "subservice", "sub_service", "product", "commercial"})
BLOGISH_CONTENT = frozenset({"blog", "article", "guide", "listicle", "comparison", "spoke", "supporting"})
PAGE_TYPES = frozenset(
    {
        "home",
        "hub",
        "spoke",
        "pillar",
        "cluster",
        "article",
        "blog",
        "service",
        "subservice",
        "product",
        "location",
        "commercial",
        "supporting",
        "utility",
        "landing",
        "faq",
        "comparison",
        "guide",
        "listicle",
        "tool",
    }
)
TIER_ALIASES = {
    "quick win": "quick_win",
    "quick_win": "quick_win",
    "big bet": "big_bet",
    "big_bet": "big_bet",
    "fill-in": "fill_in",
    "fill in": "fill_in",
    "fill_in": "fill_in",
    "fillin": "fill_in",
    "avoid": "avoid",
}
PAGE_TYPE_ALIASES = {
    "planned": "article",
    "page": "article",
    "post": "article",
    "guide": "guide",
    "landing_page": "landing",
    "faq_page": "faq",
    "sub_service": "subservice",
}
DISPOSITION_ALIASES = {
    "keep": "KEEP",
    "refresh": "REFRESH",
    "optimise": "OPTIMISE",
    "optimize": "OPTIMISE",
    "retitle": "RETITLE",
    "consolidate": "CONSOLIDATE",
    "noindex": "NOINDEX",
    "delete_candidate": "DELETE_CANDIDATE",
    "delete": "DELETE_CANDIDATE",
    "retire": "DELETE_CANDIDATE",
}
REFRESH_DISPOSITIONS = frozenset({"REFRESH", "OPTIMISE", "RETITLE"})
RETIRE_DISPOSITIONS = frozenset({"CONSOLIDATE", "NOINDEX", "DELETE_CANDIDATE"})
TIER_RANK = {"quick_win": 0, "big_bet": 1, "fill_in": 2, "avoid": 3}
ACTION_RANK = {"refresh": 0, "create": 1, "no_action": 2, "retire": 3}
# Within each service family, content is walked in this order.
# 0 pillar page → 1 blogs on that pillar → 2 sub-pillar pages → 3 blogs on those sub-pillars.
HIERARCHY_ROLE = {
    0: "pillar_page",
    1: "pillar_blogs",
    2: "subpillar_page",
    3: "subpillar_blogs",
    4: "other",
}
_PILLAR_PAGE_TYPES = frozenset({"service", "pillar", "hub", "product", "commercial"})
_SUBPILLAR_PAGE_TYPES = frozenset({"subservice", "sub_service"})


def url_n(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        path = urlparse(raw).path or "/"
    else:
        path = raw if raw.startswith("/") else f"/{raw}"
    path = path.lower()
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _tier(row: dict[str, Any]) -> str | None:
    raw = row.get("priority_tier") or row.get("priority") or row.get("priority_label") or row.get("bucket")
    if raw is None or raw == "":
        return None
    key = str(raw).strip().lower()
    return TIER_ALIASES.get(key) or TIER_ALIASES.get(key.replace("_", " ")) or TIER_ALIASES.get(key.replace("-", " "))


def _hierarchy_slot(row: dict[str, Any], subpillar_urls: set[str]) -> int:
    """Where this page sits in the pillar → blogs → sub-pillar → sub-pillar blogs walk."""
    ptype = str(row.get("page_type") or row.get("content_type") or "").strip().lower()
    parent = url_n(str(row.get("parent_url_n") or row.get("parent") or ""))
    on_subpillar = bool(
        row.get("supports_subservice_id")
        or row.get("subservice")
        or row.get("subservice_id")
        or (parent and parent in subpillar_urls)
    )
    if ptype == "home":
        return -1
    if ptype in _PILLAR_PAGE_TYPES:
        return 0
    if ptype in _SUBPILLAR_PAGE_TYPES:
        return 2
    if ptype in BLOGISH_CONTENT or ptype in {"faq", "article", "guide", "blog"}:
        return 3 if on_subpillar else 1
    if on_subpillar:
        return 3
    if row.get("supports_service_id") or row.get("service"):
        return 1
    return 4


def _family_key(row: dict[str, Any]) -> str:
    return _norm_kw(
        row.get("supports_service_id")
        or row.get("service_id")
        or row.get("service")
        or row.get("cluster")
        or ""
    )


def order_pages_by_hierarchy(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order a roadmap by service family, then pillar → blogs → sub-pillars → their blogs.

    Families are ordered by their pillar page (priority tier, then score) so the
    strongest pillar is produced first. Inside a family the hierarchy is fixed;
    tier and score only break ties within the same slot.
    """
    subpillar_urls = {
        url_n(str(row.get("url_n") or row.get("url") or row.get("path") or ""))
        for row in pages
        if str(row.get("page_type") or "").strip().lower() in _SUBPILLAR_PAGE_TYPES
    }
    subpillar_urls.discard("")

    for row in pages:
        slot = _hierarchy_slot(row, subpillar_urls)
        row["hierarchy_slot"] = slot
        row["hierarchy_role"] = HIERARCHY_ROLE.get(slot, "other")

    family_rank: dict[str, tuple] = {}
    for row in pages:
        key = _family_key(row)
        if not key:
            continue
        slot = int(row.get("hierarchy_slot") or 4)
        tier = TIER_RANK.get(str(row.get("priority_tier") or ""), 9)
        try:
            score = float(row.get("priority_score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        # Prefer the pillar page itself when ranking families against each other.
        candidate = (0 if slot == 0 else 1, tier, -score, key)
        current = family_rank.get(key)
        if current is None or candidate < current:
            family_rank[key] = candidate
    ordered_families = sorted(family_rank, key=lambda k: family_rank[k])
    family_index = {key: i for i, key in enumerate(ordered_families)}

    def _sort_key(row: dict[str, Any]) -> tuple:
        key = _family_key(row)
        slot = int(row.get("hierarchy_slot") if row.get("hierarchy_slot") is not None else 4)
        try:
            score = float(row.get("priority_score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        return (
            -1 if slot < 0 else family_index.get(key, len(ordered_families)),
            slot,
            TIER_RANK.get(str(row.get("priority_tier") or ""), 9),
            ACTION_RANK.get(str(row.get("action") or ""), 9),
            -score,
            str(row.get("url_n") or ""),
        )

    pages.sort(key=_sort_key)
    return pages


def _page_type(row: dict[str, Any]) -> str | None:
    raw = row.get("page_type") or row.get("type")
    if not raw:
        return None
    key = str(raw).strip().lower()
    if key in PAGE_TYPES:
        return key
    return PAGE_TYPE_ALIASES.get(key, key)


def _planning_content_type(row: dict[str, Any], *, page_type: str | None, primary: str | None) -> str:
    """Human-facing content type for Phase 9 roadmap rows."""
    raw = row.get("content_type") or row.get("type")
    if raw:
        key = str(raw).strip().lower()
        if key in {"blog", "article", "post", "blog_article"}:
            return "blog"
        if key in {"service", "services", "subservice", "sub_service", "commercial"}:
            return "service page"
        if key in {"landing", "landing_page"}:
            return "landing page"
        if key in {"faq", "faq_page"}:
            return "faq"
        if key in {"guide", "comparison", "listicle", "tool", "location", "product", "hub"}:
            return key
        return key
    ptype = (page_type or "").lower()
    if ptype in {"service", "subservice", "sub_service", "product", "commercial"}:
        return "service page"
    if ptype == "landing":
        return "landing page"
    if ptype in {"faq", "guide", "comparison", "listicle", "tool", "location", "hub"}:
        return ptype
    if ptype in {"article", "blog"}:
        return "blog"
    kw = str(primary or row.get("keyword") or row.get("title") or "").lower()
    intent = str(row.get("intent") or "").lower()
    if "faq" in kw or (kw.startswith("what is ") and "?" in kw):
        return "faq"
    if " vs " in kw or " versus " in kw:
        return "comparison"
    if kw.startswith(("best ", "top ")):
        return "listicle"
    if intent == "transactional" or any(x in kw for x in ("pricing", "hire", "quote")):
        return "landing page"
    if intent == "commercial":
        return "service page"
    if intent == "informational" or kw.startswith(("how to", "what is")):
        return "guide"
    return "page"


def _disposition(row: dict[str, Any] | None) -> str | None:
    if not row:
        return None
    raw = row.get("disposition")
    if raw is None or raw == "":
        return None
    s = str(raw).strip()
    upper = s.upper().replace(" ", "_")
    if upper in DISPOSITION_ALIASES.values() or upper in (
        "KEEP",
        "REFRESH",
        "OPTIMISE",
        "RETITLE",
        "CONSOLIDATE",
        "NOINDEX",
        "DELETE_CANDIDATE",
    ):
        if upper == "OPTIMIZE":
            return "OPTIMISE"
        return DISPOSITION_ALIASES.get(s.lower(), upper)
    return DISPOSITION_ALIASES.get(s.lower())


def _action_from_disposition(disp: str | None) -> str:
    if not disp:
        return "create"
    if disp in REFRESH_DISPOSITIONS:
        return "refresh"
    if disp in RETIRE_DISPOSITIONS:
        return "retire"
    if disp == "KEEP":
        return "no_action"
    return "create"


def _content_action_label(*, action: str, disposition: str | None, page_type: str | None) -> str:
    """Canonical action family used downstream for optimize-vs-create decisions."""
    if action == "create":
        return "NEW_CONTENT"
    if action == "retire":
        if disposition == "CONSOLIDATE":
            return "CONSOLIDATE_CONTENT"
        return "REDIRECT_OR_RETIRE"
    if action == "refresh":
        if page_type in {"pillar", "hub"}:
            return "PILLAR_OPTIMIZATION"
        if page_type in {"cluster", "supporting", "spoke"}:
            return "CLUSTER_CONTENT_OPTIMIZATION"
        if disposition == "RETITLE":
            return "UPDATE_EXISTING"
        if disposition == "OPTIMISE":
            return "OPTIMIZE_EXISTING"
        return "UPDATE_EXISTING"
    return "LEAVE_UNCHANGED"


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1}


def _keyword_overlap(a: str, b: str) -> bool:
    na = " ".join(str(a or "").lower().split())
    nb = " ".join(str(b or "").lower().split())
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    at, bt = _token_set(na), _token_set(nb)
    if len(at) < 2 or len(bt) < 2:
        return False
    return len(at & bt) / max(1, min(len(at), len(bt))) >= 0.7


def _service_targets(architecture_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonical service targets, most-specific (sub-service) first."""
    targets = [
        row
        for row in architecture_rows
        if _page_type(row) in {"service", "subservice"}
        and url_n(str(row.get("url") or row.get("path") or ""))
    ]
    return sorted(
        targets,
        key=lambda row: (
            0 if _page_type(row) == "subservice" else 1,
            -len(_token_set(str(row.get("title") or row.get("keyword") or ""))),
        ),
    )


def _matching_service_target(
    row: dict[str, Any],
    *,
    keyword: str,
    targets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve explicit service IDs first, then defensible topical overlap."""
    explicit_sub = _norm_kw(
        row.get("supports_subservice_id")
        or row.get("subservice_id")
        or row.get("subservice")
        or (
            row.get("target")
            if str(row.get("target_type") or "").lower() in {"subservice", "sub_service"}
            else None
        )
    )
    explicit_service = _norm_kw(
        row.get("supports_service_id")
        or row.get("service_id")
        or row.get("service")
        or row.get("parent_service_id")
        or row.get("parent_segment")
    )
    for target in targets:
        target_type = _page_type(target)
        target_ids = {
            _norm_kw(target.get("subservice_id")),
            _norm_kw(target.get("service_id")),
            _norm_kw(target.get("title")),
            _norm_kw(target.get("keyword")),
        }
        target_ids.discard("")
        if explicit_sub and target_type == "subservice" and explicit_sub in target_ids:
            return target
        if explicit_service and explicit_service in target_ids:
            return target

    keyword_tokens = _token_set(keyword)
    if not keyword_tokens:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for target in targets:
        label = str(
            target.get("title")
            or target.get("primary_keyword")
            or target.get("keyword")
            or ""
        )
        label_tokens = _token_set(label)
        if not label_tokens:
            continue
        overlap = len(keyword_tokens & label_tokens)
        if not overlap:
            continue
        coverage = overlap / len(label_tokens)
        precision = overlap / len(keyword_tokens)
        # A complete service-name match is strong; partial matches require at
        # least two shared tokens to avoid assigning every "... ads" article
        # to an arbitrary paid-media service.
        if coverage < 1.0 and overlap < 2:
            continue
        score = coverage * 2 + precision
        if _page_type(target) == "subservice":
            score += 0.25
        if best is None or score > best[0]:
            best = (score, target)
    return best[1] if best else None


def _low_volume_commercial_service_match(row: dict[str, Any], keyword: str) -> bool:
    intent = str(row.get("intent") or "").strip().lower()
    if intent not in {"commercial", "transactional"}:
        return False
    try:
        volume = float(row.get("volume") or row.get("search_volume") or 0)
    except (TypeError, ValueError):
        volume = 0
    return volume <= 1000 and bool(keyword.strip())


def _keyword_is_service_head_term(keyword: str, service: dict[str, Any]) -> bool:
    """True when keyword is the service name itself, not a geo/modifier variant.

    Commercial routing may collapse a blog/guide URL onto the canonical service
    page only for head terms (e.g. \"social media marketing\" → /social-media-marketing).
    Geo or modifier variants (\"google ads management sydney\") keep their own URL and
    become supporting children of the service instead — otherwise multiple variants
    overwrite the same service url_n and unlock the Phase 9 roadmap.
    """
    label = str(
        service.get("title")
        or service.get("primary_keyword")
        or service.get("keyword")
        or ""
    )
    kt, lt = _token_set(keyword), _token_set(label)
    if not kt or not lt:
        return False
    if kt == lt:
        return True
    if not lt <= kt:
        return False
    filler = {
        "services",
        "service",
        "agency",
        "company",
        "companies",
        "best",
        "top",
        "online",
        "digital",
    }
    return len(kt - lt - filler) == 0


def _audit_keyword_matches(
    audit: dict[str, Any] | None,
    *,
    keyword: str | None,
) -> list[dict[str, Any]]:
    if not audit or not keyword:
        return []
    matches: list[dict[str, Any]] = []
    for row in list(audit.get("inventory") or []) + list(audit.get("refresh_queue") or []):
        if not isinstance(row, dict):
            continue
        probe = str(row.get("keyword") or row.get("primary_keyword") or row.get("title") or "")
        if _keyword_overlap(keyword, probe):
            matches.append(row)
    return matches


def _breadcrumb_list(raw: Any, parent: str | None, path: str) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str) and raw.strip():
        if ">" in raw:
            return [p.strip() for p in raw.split(">") if p.strip()]
        return [raw.strip()]
    bits = ["Home"]
    if parent and url_n(parent) not in ("", "/"):
        bits.append(url_n(parent).strip("/").replace("-", " ").title())
    if path not in ("", "/"):
        bits.append(path.strip("/").split("/")[-1].replace("-", " ").title())
    return bits


def _indexable(row: dict[str, Any]) -> bool | None:
    if "indexable" in row:
        val = row.get("indexable")
        if isinstance(val, bool):
            return val
        if str(val).lower() in ("false", "0", "no"):
            return False
        if str(val).lower() in ("true", "1", "yes"):
            return True
    return None


def ia_gate_ok(site_architecture_status: str | None, site_architecture: dict[str, Any]) -> bool:
    if (site_architecture_status or "") == "complete":
        return True
    tree = site_architecture.get("target_url_tree") or []
    if not isinstance(tree, list) or not tree:
        return False
    return any(isinstance(n, dict) and (n.get("url") or n.get("path")) for n in tree)


def _strategy_row_richness(row: dict[str, Any]) -> tuple[int, ...]:
    """Prefer rows that carry URL + demand signals when deduping queue copies."""
    return (
        1 if _strategy_url(row) else 0,
        1 if row.get("intent") else 0,
        1 if (row.get("volume") or row.get("search_volume")) else 0,
        1 if (row.get("title") or row.get("topic")) else 0,
        1 if (row.get("priority_tier") or row.get("priority")) else 0,
    )


def _strategy_rows(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the Phase 6 strategy spine once.

    Prefer strategy-origin rows on ``content_plan``. Older payloads still expose
    ``priority_queue`` / ``priority_pages``. Audit-origin rows stay out of this spine.
    """
    collected: list[dict[str, Any]] = []
    plan_rows = [row for row in (strategy.get("content_plan") or []) if isinstance(row, dict)]
    if plan_rows and any(row.get("source") for row in plan_rows):
        collected.extend(row for row in plan_rows if row.get("source") == "content-strategy")
    if not collected:
        for key in ("priority_queue", "priority_pages"):
            for row in strategy.get(key) or []:
                if isinstance(row, dict):
                    collected.append(row)
    # Fall back to the merged list only when the strategy spine is empty (legacy packs).
    if not collected:
        for row in strategy.get("combined_priority_queue") or plan_rows:
            if isinstance(row, dict):
                collected.append(row)

    by_key: dict[str, dict[str, Any]] = {}
    no_key: list[dict[str, Any]] = []
    for row in collected:
        nk = _row_keyword(row) or _strategy_url(row)
        if not nk:
            no_key.append(row)
            continue
        prev = by_key.get(nk)
        if prev is None or _strategy_row_richness(row) > _strategy_row_richness(prev):
            by_key[nk] = row
    return list(by_key.values()) + no_key


def _is_service_page(row: dict[str, Any]) -> bool:
    ptype = _page_type(row)
    if ptype in SERVICE_PAGE_TYPES:
        return True
    ctype = str(row.get("content_type") or "").strip().lower()
    return ctype in {"service", "service page", "services", "subservice", "commercial"}


def _is_blogish_create(row: dict[str, Any]) -> bool:
    if str(row.get("action") or "") != "create":
        return False
    if _is_service_page(row):
        return False
    ctype = str(row.get("content_type_label") or row.get("content_type") or "").strip().lower()
    ptype = str(row.get("page_type") or "").strip().lower()
    if ctype in BLOGISH_CONTENT or ptype in BLOGISH_CONTENT:
        return True
    path = str(row.get("url_n") or row.get("url") or row.get("path") or "").lower()
    return path.startswith(("/blog/", "/guides/", "/guide/", "/articles/"))


def _blog_create_rank(row: dict[str, Any]) -> tuple:
    """Prefer Phase 5 spine + higher tiers when capping blog creates."""
    tier = str(row.get("priority_tier") or "fill_in")
    from_p5 = 0 if row.get("from_phase5_topic") else 1
    return (
        from_p5,
        TIER_RANK.get(tier, 9),
        -float(row.get("priority_score") or 0),
        str(row.get("url_n") or ""),
    )


def _norm_service_id(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _distinct_tokens(text: str) -> set[str]:
    tokens = _token_set(text) - _GENERIC_MATCH_TOKENS
    return tokens or _token_set(text)


# A blog is created only when the topic clears this trend/virality score.
# Pillar and sub-pillar pages are not subject to the check.
VIRALITY_PASS_SCORE = 0.35


def assess_topic_virality(
    row: dict[str, Any],
    lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score whether a topic is worth a blog page.

    Uses trend direction, the Phase 5 bucket, volume, and opportunity score.
    Declining, avoided, and stale-year topics fail. A topic with no trend or
    volume evidence also fails — a blog is not invented from the service name.
    """
    from app.services.keyword_opportunity import has_present_year, is_stale_year_keyword

    keyword = str(row.get("keyword") or row.get("primary_keyword") or "").strip()
    extra = (lookup or {}).get(_norm_kw(keyword)) or {}
    src = {**extra, **{k: v for k, v in row.items() if v not in (None, "", [])}}
    trend = str(src.get("trend") or "").strip().lower()
    bucket = str(src.get("bucket") or "").strip().lower()
    try:
        volume = float(src.get("volume") or src.get("search_volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    try:
        opportunity = float(src.get("opportunity_score") or 0)
    except (TypeError, ValueError):
        opportunity = 0.0
    if 0 < opportunity <= 1:
        opportunity *= 100

    stale = bool(keyword) and is_stale_year_keyword(keyword)
    present = bool(keyword) and has_present_year(keyword)
    score = 0.0
    notes: list[str] = []
    if trend == "rising":
        score += 0.45
        notes.append("rising")
    elif trend == "stable":
        score += 0.15
        notes.append("stable")
    elif trend == "declining":
        score -= 0.5
        notes.append("declining")
    if bucket == "trend":
        score += 0.3
        notes.append("trend bucket")
    elif bucket == "avoid":
        score -= 0.4
        notes.append("avoid bucket")
    elif bucket == "best":
        score += 0.1
    if present:
        score += 0.2
        notes.append("current year")
    if stale:
        score -= 0.6
        notes.append("stale year")
    if volume >= 500:
        score += 0.2
        notes.append(f"vol {int(volume)}")
    elif volume >= 50:
        score += 0.1
        notes.append(f"vol {int(volume)}")
    if opportunity >= 55:
        score += 0.15
    elif opportunity >= 40:
        score += 0.05
    score = max(0.0, min(1.0, round(score, 2)))
    hard_fail = trend == "declining" or bucket == "avoid" or stale
    has_signal = bool(trend or bucket or volume >= 50 or present or opportunity >= 40)
    passed = has_signal and not hard_fail and score >= VIRALITY_PASS_SCORE
    if not has_signal:
        reason = "no trend or volume evidence for a blog"
    elif hard_fail:
        reason = "topic is declining, avoided, or stale"
    elif not passed:
        reason = "trend potential below the blog threshold"
    else:
        reason = "trend potential supports a blog"
    return {
        "pass": passed,
        "score": score,
        "reason": reason,
        "signals": ", ".join(notes) or "none",
        "trend": trend or None,
        "keyword": keyword,
    }


def demand_keyword_rows(search_demand: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten Phase 5 keyword rows that can carry a trend signal."""
    demand = dict(search_demand or {})
    rows: list[dict[str, Any]] = []
    for key in ("trend_plays", "best_opportunities", "strong_evergreen", "keyword_table"):
        for row in demand.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    cluster_report = demand.get("cluster_report") if isinstance(demand.get("cluster_report"), dict) else {}
    for cluster in (cluster_report or {}).get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        if cluster.get("primary_keyword") or cluster.get("keyword"):
            rows.append(cluster)
        for member in cluster.get("keywords") or []:
            if isinstance(member, dict):
                rows.append(member)
    return rows


def index_demand_keywords(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _norm_kw(row.get("keyword") or row.get("primary_keyword"))
        if key and key not in index:
            index[key] = row
    return index


def pick_viral_blog_topic(
    service: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Highest-scoring demand keyword for this service that clears the blog gate."""
    name_tokens = _distinct_tokens(str(service.get("name") or service.get("id") or ""))
    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for row in rows:
        keyword = str(row.get("keyword") or row.get("primary_keyword") or "")
        if name_tokens and not (name_tokens & _token_set(keyword)):
            continue
        verdict = assess_topic_virality(row)
        if not verdict["pass"]:
            continue
        if best is None or float(verdict["score"]) > best[0]:
            best = (float(verdict["score"]), row, verdict)
    if not best:
        return None
    return best[1], best[2]


def apply_blog_virality_gate(
    pages: list[dict[str, Any]],
    search_demand: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop blog creates whose topic fails the virality check. Other page types stay."""
    lookup = index_demand_keywords(demand_keyword_rows(search_demand))
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in pages:
        kind = _row_page_kind(row)
        if str(row.get("action") or "") == "create" and kind in {"blog", "article"}:
            verdict = assess_topic_virality(row, lookup)
            row["virality_score"] = verdict["score"]
            row["virality_pass"] = verdict["pass"]
            row["virality_reason"] = verdict["reason"]
            row["virality_signals"] = verdict["signals"]
            # A hard fail (declining, avoided, stale) is dropped. A blog invented
            # for a selected service is dropped unless it clears the score.
            # A blog already chosen by strategy or the URL map stays when the
            # only gap is missing trend fields — those pages were not guessed
            # from a service name.
            synthesized = "selected_service_blog" in set(row.get("flags") or [])
            hard_fail = verdict["reason"] == "topic is declining, avoided, or stale"
            if not verdict["pass"] and (hard_fail or synthesized):
                dropped.append(
                    {
                        "url_n": row.get("url_n"),
                        "reason": f"virality_check — {verdict['reason']} ({verdict['signals']})",
                        "source_pack": "virality_check",
                        "title": row.get("title"),
                        "keyword": row.get("primary_keyword") or row.get("keyword"),
                    }
                )
                continue
        kept.append(row)
    return kept, dropped


def demand_clusters(search_demand: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Keyword clusters from a search-demand pack. Loose keyword rows are not pages."""
    demand = dict(search_demand or {})
    report = demand.get("cluster_report") if isinstance(demand.get("cluster_report"), dict) else {}
    clusters = [row for row in (report.get("clusters") or []) if isinstance(row, dict)]
    if clusters:
        return clusters
    return [row for row in (demand.get("clusters") or []) if isinstance(row, dict)]


def _cluster_blob(row: dict[str, Any]) -> set[str]:
    return _token_set(
        " ".join(
            str(row.get(key) or "")
            for key in ("keyword", "primary_keyword", "name", "title", "parent_topic", "service")
        )
    )


def _service_identity(service: dict[str, Any]) -> set[str]:
    keys = {_norm_kw(service.get("name")), _norm_kw(service.get("id"))}
    for sub in service.get("subservices") or []:
        if isinstance(sub, dict):
            keys.add(_norm_kw(sub.get("name")))
            keys.add(_norm_kw(sub.get("id")))
    keys.discard("")
    return keys


def _cluster_service_labels(row: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for key in ("supports_service_id", "service_id", "parent_service_id", "service"):
        label = _norm_kw(row.get(key))
        if label:
            labels.add(label)
    return labels


def _best_service_for_cluster(
    row: dict[str, Any],
    services: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The selected service this cluster belongs to, most specific name first."""
    labels = _cluster_service_labels(row)
    if labels:
        labeled = [
            service
            for service in services
            if isinstance(service, dict) and labels & _service_identity(service)
        ]
        if not labeled:
            return None
        return max(labeled, key=lambda service: len(_distinct_tokens(str(service.get("name") or service.get("id") or ""))))
    blob = _cluster_blob(row)
    hits = []
    for service in services:
        if not isinstance(service, dict):
            continue
        tokens = _distinct_tokens(str(service.get("name") or service.get("id") or ""))
        if tokens and tokens <= blob:
            hits.append(service)
    if not hits:
        return None
    return max(hits, key=lambda service: len(_distinct_tokens(str(service.get("name") or service.get("id") or ""))))


def clusters_by_service(
    services: list[dict[str, Any]],
    clusters: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """One list of real clusters per selected service. A cluster is not copied onto every service."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[str]] = {}
    usable = [service for service in services if isinstance(service, dict)]
    for row in clusters or []:
        if not isinstance(row, dict):
            continue
        service = _best_service_for_cluster(row, usable)
        if not service:
            continue
        keyword = _norm_kw(row.get("primary_keyword") or row.get("keyword") or row.get("name"))
        if not keyword or keyword in _service_identity(service):
            continue
        key = _norm_service_id(service.get("id") or service.get("name"))
        if not key or keyword in seen.setdefault(key, set()):
            continue
        seen[key].add(keyword)
        buckets.setdefault(key, []).append(row)
    return buckets


def spec_from_keyword(row: dict[str, Any]) -> dict[str, Any]:
    """Page type, path, intent, and funnel taken from the keyword itself."""
    from app.services.create_content import path_for_page_type, resolve_page_type
    from app.services.keyword_opportunity import detect_funnel, detect_intent

    keyword = str(row.get("primary_keyword") or row.get("keyword") or row.get("name") or "").strip()
    raw_intent = str(row.get("intent") or "").strip().lower()
    intent = raw_intent if raw_intent in {"informational", "commercial", "transactional", "navigational"} else detect_intent(keyword, raw_intent)
    page_type = resolve_page_type({"keyword": keyword, "intent": intent})
    funnel = str(row.get("funnel") or "").strip().upper()
    if funnel not in {"TOFU", "MOFU", "BOFU"}:
        funnel = detect_funnel(keyword, intent)
    title = str(row.get("title") or "").strip()
    name = str(row.get("name") or "").strip()
    if not title:
        title = keyword if not name or " · " in name else name
    raw_url = str(row.get("recommended_url") or row.get("suggested_url") or row.get("url") or "")
    path = urlparse(raw_url).path if "://" in raw_url else raw_url
    if not path.startswith("/"):
        path = path_for_page_type(page_type, keyword)
    return {
        "page_type": page_type,
        "keyword": keyword,
        "title": title,
        "intent": intent,
        "funnel": funnel,
        "path": path,
        "work_label": f"Create {page_type} page",
        "volume": row.get("est_traffic") if row.get("est_traffic") is not None else row.get("total_volume", row.get("volume")),
        "opportunity_score": row.get("opportunity_score") if row.get("opportunity_score") is not None else row.get("best_score"),
        "from_keyword_cluster": True,
        "require_virality": False,
    }


def _row_page_kind(row: dict[str, Any]) -> str:
    return str(row.get("page_type") or row.get("content_type_label") or row.get("content_type") or "").strip().lower()


def _work_label_for(row: dict[str, Any]) -> str:
    explicit = str(row.get("work_label") or "").strip()
    if explicit:
        return explicit
    action = str(row.get("action") or "")
    page_type = _row_page_kind(row)
    flags = set(row.get("flags") or [])
    if action == "create" and page_type in PAGE_TYPES and page_type not in SERVICE_PAGE_TYPES:
        return f"Create {page_type} page"
    if action == "create" and (
        page_type in BLOGISH_CONTENT or "selected_service_blog" in flags
    ):
        return "Create blog page"
    if action == "refresh" and (
        _is_service_page(row)
        or "service_optimize" in flags
        or "selected_service_page" in flags
    ):
        return "Optimize service page"
    if action == "create":
        return "Create page"
    if action == "refresh":
        return "Optimize page"
    if action == "retire":
        return "Retire page"
    return action or "Leave unchanged"


def _blog_is_protected(row: dict[str, Any], protected_ids: set[str]) -> bool:
    if not protected_ids:
        return False
    if "selected_service_blog" in (row.get("flags") or []):
        return True
    return _norm_service_id(row.get("supports_service_id")) in protected_ids


def prune_blog_creates(
    pages: list[dict[str, Any]],
    *,
    max_creates: int = MAX_BLOG_CREATES,
    protect_service_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cap blog/guide creates that are not covering a selected service.

    Service refresh rows are untouched. Blog creates whose ``supports_service_id``
    is in ``protect_service_ids`` stay on the roadmap so each selected service can
    keep its floor. Remaining blog creates are capped at ``max_creates``.
    """
    keep_n = max(0, int(max_creates))
    protected_ids = {_norm_service_id(x) for x in (protect_service_ids or set())}
    protected_ids.discard("")
    blogs = [r for r in pages if _is_blogish_create(r)]
    protected = {id(r) for r in blogs if _blog_is_protected(r, protected_ids)}
    unprotected = [r for r in blogs if id(r) not in protected]
    if len(unprotected) <= keep_n:
        return pages, []
    ranked = sorted(unprotected, key=_blog_create_rank)
    keep_set = protected | {id(r) for r in ranked[:keep_n]}
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in pages:
        if _is_blogish_create(row) and id(row) not in keep_set:
            dropped.append(
                {
                    "url_n": row.get("url_n"),
                    "reason": (
                        f"blog_create_cap — kept top {keep_n} blog suggestions; "
                        "extra fill-in blogs were not on the briefable plan"
                    ),
                    "source_pack": "planning_cap",
                    "title": row.get("title"),
                    "keyword": row.get("primary_keyword") or row.get("keyword"),
                }
            )
            continue
        kept.append(row)
    return kept, dropped


def selected_services_for_plan(
    architecture: dict[str, Any] | None,
    search_demand: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Confirmed services for this roadmap, from architecture or search demand."""
    from app.services.service_prioritization import compact_selected_services

    arch = dict(architecture or {})
    demand = dict(search_demand or {})
    for source in (arch, demand):
        stamped = [
            row
            for row in (source.get("selected_services") or [])
            if isinstance(row, dict) and (row.get("id") or row.get("name"))
        ]
        if stamped:
            return stamped
    prio = demand.get("service_prioritization")
    if not isinstance(prio, dict):
        prio = arch.get("service_prioritization") if isinstance(arch.get("service_prioritization"), dict) else {}
    catalog = (
        demand.get("service_catalog")
        or (prio or {}).get("service_catalog")
        or arch.get("service_catalog")
    )
    return compact_selected_services(
        prio if isinstance(prio, dict) else {},
        service_catalog=catalog if isinstance(catalog, list) else None,
        ia_nodes=arch.get("service_ia_nodes") or arch.get("target_url_tree"),
    )


def _selected_identity(selected: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    ids: set[str] = set()
    urls: set[str] = set()
    slugs: set[str] = set()
    for svc in selected:
        if not isinstance(svc, dict):
            continue
        sid = _norm_service_id(svc.get("id"))
        name = str(svc.get("name") or "")
        if sid:
            ids.add(sid)
        name_id = _norm_service_id(name)
        if name_id:
            ids.add(name_id)
        slug = _slug(name) or _slug(sid)
        if slug:
            slugs.add(slug)
        if svc.get("url"):
            urls.add(url_n(str(svc.get("url"))))
        if slug:
            urls.add(f"/{slug}")
        for sub in svc.get("subservices") or []:
            if not isinstance(sub, dict):
                continue
            sub_id = _norm_service_id(sub.get("id"))
            sub_name = str(sub.get("name") or "")
            if sub_id:
                ids.add(sub_id)
            if _norm_service_id(sub_name):
                ids.add(_norm_service_id(sub_name))
            sub_slug = _slug(sub_name) or _slug(sub_id)
            if sub_slug:
                slugs.add(sub_slug)
            if sub.get("url"):
                urls.add(url_n(str(sub.get("url"))))
            if slug and sub_slug:
                urls.add(f"/{slug}/{sub_slug}")
    urls.discard("")
    urls.discard("/")
    slugs.discard("")
    ids.discard("")
    return ids, urls, slugs


def _matches_selected_service(row: dict[str, Any], selected: list[dict[str, Any]]) -> bool:
    """True when a service/sub-service page belongs to the confirmed selection."""
    if not selected:
        return True
    ids, urls, slugs = _selected_identity(selected)
    for key in (
        "service_id",
        "supports_service_id",
        "parent_service_id",
        "subservice_id",
        "supports_subservice_id",
    ):
        if _norm_service_id(row.get(key)) in ids:
            return True
    path = url_n(str(row.get("url") or row.get("path") or row.get("url_n") or ""))
    if path in urls:
        return True
    segments = [
        seg
        for seg in path.strip("/").split("/")
        if seg and seg not in {"services", "service", "solutions", "products", "what-we-do"}
    ]
    if any(seg in slugs for seg in segments):
        return True
    label_tokens = _token_set(
        " ".join(
            str(row.get(key) or "")
            for key in ("title", "keyword", "primary_keyword")
        )
    )
    for svc in selected:
        names = [str(svc.get("name") or "")]
        for sub in svc.get("subservices") or []:
            if isinstance(sub, dict):
                names.append(str(sub.get("name") or ""))
        for name in names:
            tokens = _distinct_tokens(name)
            if tokens and tokens <= label_tokens:
                return True
    return False


def _blog_supports_service(row: dict[str, Any], service: dict[str, Any]) -> bool:
    sid = _norm_service_id(row.get("supports_service_id") or "")
    target = _norm_service_id(service.get("id"))
    if sid:
        return bool(target) and sid == target
    parent = url_n(str(row.get("parent") or row.get("parent_url") or row.get("parent_url_n") or ""))
    svc_url = url_n(str(service.get("url") or ""))
    slug = _slug(str(service.get("name") or service.get("id") or ""))
    if parent and svc_url and parent == svc_url:
        return True
    if parent and slug and parent in {f"/{slug}", f"/services/{slug}"}:
        return True
    name_tokens = _distinct_tokens(str(service.get("name") or ""))
    if not name_tokens:
        return False
    blob = _token_set(
        " ".join(
            str(row.get(key) or "")
            for key in ("title", "keyword", "primary_keyword", "service")
        )
    )
    return name_tokens <= blob


def _service_page_url(service: dict[str, Any]) -> str:
    explicit = url_n(str(service.get("url") or ""))
    if explicit and explicit != "/":
        return explicit
    slug = _slug(str(service.get("name") or service.get("id") or "service")) or "service"
    return f"/{slug}"


def _selected_service_page_row(service: dict[str, Any], url: str) -> dict[str, Any]:
    name = str(service.get("name") or service.get("id") or "Service")
    return {
        "url_n": url,
        "url": url,
        "path": url,
        "action": "refresh",
        "content_action": "OPTIMIZE_EXISTING",
        "disposition": "OPTIMISE",
        "decision_basis": "url_map_optimize_existing",
        "existing_content_check": "matched_existing",
        "title": name,
        "primary_keyword": name,
        "keyword": name,
        "secondary_keywords": [],
        "supporting_keywords": [],
        "keywords": [],
        "intent": "commercial",
        "service": name,
        "content_type": "service page",
        "content_type_label": "service page",
        "page_type": "service",
        "parent": "/",
        "parent_url_n": "/",
        "supports_service_id": _norm_service_id(service.get("id")),
        "depth": 1,
        "indexable": True,
        "priority_tier": "big_bet",
        "priority_score": 0,
        "work_label": "Optimize service page",
        "flags": ["service_optimize", "selected_service_page"],
        "status": "planned",
        "from_selected_service": True,
    }


def _selected_service_supporting_row(
    service: dict[str, Any],
    spec: dict[str, str],
) -> dict[str, Any]:
    name = str(service.get("name") or service.get("id") or "Service")
    parent = _service_page_url(service)
    path = url_n(spec["path"])
    page_type = spec["page_type"]
    keyword = spec["keyword"]
    from_cluster = bool(spec.get("from_keyword_cluster"))
    viral = bool(spec.get("require_virality"))
    flags = ["supports_service", "selected_service_page"]
    if viral:
        flags.append("selected_service_blog")
    if from_cluster:
        flags.append("keyword_cluster")
    return {
        "url_n": path,
        "url": path,
        "path": path,
        "action": "create",
        "content_action": "NEW_CONTENT",
        "disposition": None,
        "decision_basis": "new_content_gap",
        "existing_content_check": "no_match",
        "new_content_justification": (
            f"'{keyword}' is a keyword cluster for {name}. "
            f"The {page_type} page type follows that keyword's intent and funnel."
            if from_cluster
            else f"'{keyword}' is a trending topic for {name}."
        ),
        "title": spec["title"],
        "primary_keyword": spec["keyword"],
        "keyword": spec["keyword"],
        "secondary_keywords": [],
        "supporting_keywords": [],
        "keywords": [],
        "intent": spec["intent"],
        "funnel": spec.get("funnel"),
        "volume": spec.get("volume"),
        "service": name,
        "content_type": page_type,
        "content_type_label": page_type,
        "page_type": page_type,
        "parent": parent,
        "parent_url_n": parent,
        "supports_service_id": _norm_service_id(service.get("id")),
        "depth": 2,
        "indexable": True,
        "priority_tier": "fill_in",
        "priority_score": 0,
        "work_label": spec["work_label"],
        "flags": flags,
        "status": "planned",
        "from_selected_service": True,
        "from_selected_service_blog": viral,
        "from_keyword_cluster": from_cluster,
    }


def ensure_selected_service_coverage(
    pages: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    search_demand: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Optimize each selected service page and add pages for its keyword clusters.

    Nothing is invented from the service name. A blog is added only when a real
    keyword for that service passes the virality check.
    """
    if not selected:
        return pages
    from app.services.create_content import path_for_page_type

    demand_rows = demand_keyword_rows(search_demand)
    grouped = clusters_by_service(selected, demand_clusters(search_demand))
    covered_urls = {
        url_n(str(row.get("url_n") or row.get("url") or row.get("path") or ""))
        for row in pages
        if isinstance(row, dict)
    }
    claimed_ids: set[int] = set()
    claimed_keywords = {
        _norm_kw(row.get("keyword") or row.get("primary_keyword"))
        for row in pages
        if isinstance(row, dict)
    }
    claimed_keywords.discard("")

    def _add_spec(service: dict[str, Any], spec: dict[str, Any], viral: dict[str, Any] | None = None) -> None:
        keyword = _norm_kw(spec.get("keyword"))
        path = url_n(spec["path"])
        if not keyword or keyword in claimed_keywords or path in covered_urls:
            return
        created = _selected_service_supporting_row(service, spec)
        if viral:
            created["trend"] = viral.get("trend")
            created["volume"] = viral.get("volume")
            created["bucket"] = viral.get("bucket")
            created["opportunity_score"] = viral.get("opportunity_score")
            created["virality_score"] = viral.get("score")
            created["virality_pass"] = True
            created["virality_reason"] = viral.get("reason")
            created["virality_signals"] = viral.get("signals")
        pages.append(created)
        covered_urls.add(path)
        claimed_keywords.add(keyword)

    for service in selected:
        if not isinstance(service, dict):
            continue
        has_page = any(
            isinstance(row, dict)
            and str(row.get("action") or "") in {"refresh", "create"}
            and _is_service_page(row)
            and _matches_selected_service(row, [service])
            for row in pages
        )
        if not has_page:
            url = _service_page_url(service)
            if url not in covered_urls:
                pages.append(_selected_service_page_row(service, url))
                covered_urls.add(url)
        for row in pages:
            if not isinstance(row, dict) or id(row) in claimed_ids:
                continue
            if str(row.get("action") or "") != "create" or _is_service_page(row):
                continue
            if not _blog_supports_service(row, service):
                continue
            row["supports_service_id"] = _norm_service_id(service.get("id"))
            row["from_selected_service"] = True
            flags = list(row.get("flags") or [])
            if "selected_service_page" not in flags:
                flags.append("selected_service_page")
            kind = _row_page_kind(row)
            if (
                kind in BLOGISH_CONTENT
                and not row.get("from_keyword_cluster")
                and "selected_service_blog" not in flags
            ):
                flags.append("selected_service_blog")
            row["flags"] = flags
            claimed_ids.add(id(row))
            claimed_keywords.add(_norm_kw(row.get("keyword") or row.get("primary_keyword")))
        service_key = _norm_service_id(service.get("id") or service.get("name"))
        for cluster in grouped.get(service_key, []):
            _add_spec(service, spec_from_keyword(cluster))
        picked = pick_viral_blog_topic(service, demand_rows)
        if not picked:
            continue
        topic, verdict = picked
        keyword = str(topic.get("keyword") or topic.get("primary_keyword") or "").strip()
        if not keyword:
            continue
        _add_spec(
            service,
            {
                "page_type": "blog",
                "keyword": keyword,
                "title": str(topic.get("title") or keyword),
                "intent": str(topic.get("intent") or "informational"),
                "funnel": str(topic.get("funnel") or "TOFU"),
                "path": path_for_page_type("blog", keyword),
                "work_label": "Create blog page",
                "require_virality": True,
            },
            {**topic, **verdict},
        )
    claimed_keywords.discard("")
    return pages


def _live_service_optimize_candidate(
    row: dict[str, Any],
    *,
    audit_row: dict[str, Any] | None,
    map_action: str,
) -> bool:
    """True when an architecture-only service page should enter the roadmap as refresh."""
    if not _is_service_page(row):
        return False
    if map_action in {"OPTIMIZE_EXISTING", "REVIEW_MERGE_REDIRECT"}:
        return True
    if audit_row is not None:
        disp = (_disposition(audit_row) or "").upper()
        if disp in {"RETIRE", "REMOVE", "DELETE", "NOINDEX"}:
            return False
        # Existing service pages always get an optimize brief — KEEP still needs
        # on-page refresh work for the commercial spine.
        return True
    # No audit hit: still admit clear service/subservice nodes that already have a URL
    # in the IA tree (live site taxonomy), so Phase 9 is not create-only blogs.
    path = url_n(str(row.get("url") or row.get("path") or ""))
    return bool(path and path != "/" and not path.startswith(("/blog/", "/guides/")))



def _arch_rows(architecture: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (architecture.get("target_url_tree") or []) if isinstance(r, dict)]


def _audit_by_url(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for key in ("dispositions", "inventory", "refresh_queue"):
        for row in audit.get(key) or []:
            if not isinstance(row, dict):
                continue
            key_n = url_n(str(row.get("url") or row.get("path") or ""))
            if key_n:
                by[key_n] = row
    return by


def _cluster_owners(architecture: dict[str, Any]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for row in architecture.get("cluster_owners") or architecture.get("cluster_ownership") or []:
        if not isinstance(row, dict):
            continue
        cluster = str(row.get("cluster") or "").strip().lower()
        owner = url_n(str(row.get("owner_url") or row.get("canonical_owner_url") or row.get("url") or ""))
        if cluster and owner:
            owners[cluster] = owner
    return owners


def _strategy_url(row: dict[str, Any]) -> str:
    return url_n(str(row.get("url") or row.get("suggested_url") or row.get("path") or ""))


def _norm_kw(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _row_keyword(row: dict[str, Any]) -> str:
    return _norm_kw(row.get("keyword") or row.get("primary_keyword"))


def build_roadmap(
    strategy: dict[str, Any],
    architecture: dict[str, Any],
    audit: dict[str, Any] | None = None,
    *,
    strict: bool = False,
    client_name: str | None = None,
    primary_url: str | None = None,
    search_demand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Join three packs on url_n.

    A confirmed service selection optimizes those service pages and adds a
    page for each keyword cluster that belongs to the service.
    """
    strat_rows = _strategy_rows(strategy)
    arch_rows = _arch_rows(architecture)
    audit_by = _audit_by_url(audit or {})
    owners = _cluster_owners(architecture)

    strat_by: dict[str, dict[str, Any]] = {}
    strat_no_url: list[dict[str, Any]] = []
    for row in strat_rows:
        key = _strategy_url(row)
        if key:
            strat_by.setdefault(key, row)
        else:
            strat_no_url.append(row)

    arch_by: dict[str, dict[str, Any]] = {}
    for row in arch_rows:
        key = url_n(str(row.get("url") or row.get("path") or ""))
        if key:
            arch_by.setdefault(key, row)
    selected_services = selected_services_for_plan(architecture, search_demand)
    service_targets = _service_targets(arch_rows)
    if selected_services:
        service_targets = [
            target
            for target in service_targets
            if _matches_selected_service(target, selected_services)
        ]

    # URL map from site architecture — attach crawled URLs to clusters before join.
    from app.services.url_mapping import lookup_url_map_entry

    from app.services.content_strategy import is_page_plan, url_map_entries

    map_rows = url_map_entries(architecture)
    page_plan = is_page_plan(map_rows)
    for entry in map_rows:
        if not isinstance(entry, dict):
            continue
        # Supporting / out-of-scope / held-for-review clusters deliberately own
        # no URL of their own — they must not seed a planning row for a page
        # that will never exist.
        if entry.get("dedicated_url") is False:
            continue
        action = str(entry.get("action") or "").upper()
        if action == "CREATE" and not page_plan:
            continue
        if action == "CREATE":
            mapped = url_n(
                str(entry.get("proposed_url") or entry.get("selected_url") or entry.get("create_url") or "")
            )
        else:
            mapped = url_n(str(entry.get("current_url") or entry.get("selected_url") or ""))
        if not mapped or mapped == "/":
            continue
        if mapped not in arch_by:
            arch_by[mapped] = {
                "url": mapped,
                "path": mapped,
                "cluster": entry.get("cluster"),
                "keyword": entry.get("primary_keyword"),
                "primary_keyword": entry.get("primary_keyword"),
                "from_url_map": True,
                "url_score": entry.get("url_score"),
                "url_map_action": entry.get("action"),
                "score_band": entry.get("score_band"),
            }

    # 1. Exact URL join.
    merged_keys = sorted(set(strat_by) & set(arch_by))
    pairs: list[tuple[str, dict[str, Any], dict[str, Any], str | None]] = [
        (k, strat_by[k], arch_by[k], None) for k in merged_keys
    ]

    # 2. Reconcile same-topic/different-URL rows before excluding either side.
    #    Phase 6 and Phase 6b routinely slug the same keyword under different parents
    #    (/guides/local-seo-pricing vs /blog/local-seo-pricing). A URL-only join drops
    #    BOTH rows, so a planned page silently disappears and is double-reported as a
    #    strategy_only + architecture_only gap. Match those on keyword and keep the
    #    Site Architecture URL — the url_conflict_winner declared in
    #    content_queue.build_combined_priority_queue.
    arch_leftover_by_kw: dict[str, list[str]] = {}
    for key, row in arch_by.items():
        if key in strat_by:
            continue
        nk = _row_keyword(row)
        if nk:
            arch_leftover_by_kw.setdefault(nk, []).append(key)
    for cands in arch_leftover_by_kw.values():
        cands.sort()

    claimed_arch: set[str] = set()
    # A keyword already planned by the exact-URL join must not get a second page from
    # reconciliation — two URLs on one keyword is the cannibalisation this phase exists
    # to prevent.
    planned_kw: set[str] = {
        nk for nk in (_row_keyword(s) or _row_keyword(a) for _k, s, a, _n in pairs) if nk
    }

    def _claim_arch_for(nk: str) -> str | None:
        if nk in planned_kw:
            return None
        for cand in arch_leftover_by_kw.get(nk, []):
            if cand not in claimed_arch:
                claimed_arch.add(cand)
                planned_kw.add(nk)
                return cand
        return None

    reconciled_strat_urls: set[str] = set()
    reconciled_no_url: set[int] = set()

    for key in sorted(set(strat_by) - set(arch_by)):
        row = strat_by[key]
        nk = _row_keyword(row)
        if not nk:
            continue
        cand = _claim_arch_for(nk)
        if cand:
            pairs.append(
                (cand, row, arch_by[cand], f"url_conflict_resolved: strategy {key} → architecture {cand}")
            )
            reconciled_strat_urls.add(key)

    for idx, row in enumerate(strat_no_url):
        nk = _row_keyword(row)
        if not nk:
            continue
        cand = _claim_arch_for(nk)
        if cand:
            pairs.append((cand, row, arch_by[cand], f"url_assigned_from_architecture: {cand}"))
            reconciled_no_url.add(idx)

    # 3. Whatever is still unmatched is a genuine gap.
    excluded: list[dict[str, Any]] = []
    for idx, row in enumerate(strat_no_url):
        if idx in reconciled_no_url:
            continue
        excluded.append(
            {
                "url_n": None,
                "reason": "strategy_only — topic planned but no URL/parent assigned",
                "source_pack": "strategy",
                "title": row.get("title"),
                "keyword": row.get("keyword") or row.get("primary_keyword"),
            }
        )
    for key, row in strat_by.items():
        if key not in arch_by and key not in reconciled_strat_urls:
            excluded.append(
                {
                    "url_n": key,
                    "reason": "strategy_only — topic planned but no URL/parent assigned",
                    "source_pack": "strategy",
                    "title": row.get("title"),
                    "keyword": row.get("keyword") or row.get("primary_keyword"),
                }
            )
    for key, row in arch_by.items():
        if key not in strat_by and key not in claimed_arch:
            if (
                selected_services
                and _is_service_page(row)
                and not _matches_selected_service(row, selected_services)
            ):
                excluded.append(
                    {
                        "url_n": key,
                        "reason": "unselected_service — outside the confirmed service selection",
                        "source_pack": "architecture",
                        "title": row.get("title"),
                        "keyword": row.get("keyword") or row.get("primary_keyword"),
                    }
                )
                continue
            # Existing pages marked OPTIMIZE / audit refresh must still reach the
            # roadmap — Phase 5 only spines new_topic creates, so without this
            # admission Content Planning (and Production briefs) show creates only.
            map_action = str(row.get("url_map_action") or "").upper()
            if not map_action:
                map_entry = lookup_url_map_entry(
                    {"final_url_map": url_map_entries(architecture)},
                    cluster=str(row.get("cluster") or ""),
                    keyword=str(row.get("keyword") or row.get("primary_keyword") or ""),
                )
                if map_entry and url_n(str(map_entry.get("selected_url") or "")) == key:
                    map_action = str(map_entry.get("action") or "").upper()
                    row = {
                        **row,
                        "url_map_action": map_entry.get("action"),
                        "keyword": row.get("keyword") or map_entry.get("primary_keyword"),
                        "primary_keyword": row.get("primary_keyword")
                        or map_entry.get("primary_keyword"),
                    }
            au = audit_by.get(key)
            disp = _disposition(au)
            is_optimize = map_action in (
                "OPTIMIZE_EXISTING",
                "REVIEW_MERGE_REDIRECT",
            ) or (disp is not None and _action_from_disposition(disp) == "refresh")
            # Live service / subservice pages must reach the roadmap as refresh even
            # when Phase 5 never put them on the create spine (existing_on_site).
            if not is_optimize and _live_service_optimize_candidate(
                row, audit_row=au, map_action=map_action
            ):
                is_optimize = True
                map_action = map_action or "OPTIMIZE_EXISTING"
                row = {**row, "url_map_action": row.get("url_map_action") or "OPTIMIZE_EXISTING"}
            if is_optimize:
                primary = (
                    row.get("keyword")
                    or row.get("primary_keyword")
                    or (au or {}).get("keyword")
                    or key.strip("/").replace("-", " ")
                )
                # Money pages rank ahead of fill-in blogs.
                tier = "big_bet" if _is_service_page(row) else "fill_in"
                synthetic = {
                    "url": key,
                    "path": key,
                    "title": row.get("title") or primary,
                    "keyword": primary,
                    "primary_keyword": primary,
                    "cluster": row.get("cluster"),
                    "priority_tier": tier,
                    "from_url_map_optimize": True,
                    "intent": row.get("intent") or (au or {}).get("intent") or "commercial",
                    "page_type": row.get("page_type") or "service",
                    "content_type": row.get("content_type") or "service page",
                }
                pairs.append(
                    (
                        key,
                        synthetic,
                        row,
                        (
                            "architecture_optimize_admitted: existing service page needs refresh"
                            if _is_service_page(row)
                            else "architecture_optimize_admitted: existing page needs refresh"
                        ),
                    )
                )
                claimed_arch.add(key)
                continue
            excluded.append(
                {
                    "url_n": key,
                    "reason": "architecture_only — URL in taxonomy with no content plan",
                    "source_pack": "architecture",
                    "title": row.get("title"),
                    "keyword": row.get("keyword") or row.get("primary_keyword"),
                }
            )

    pages: list[dict[str, Any]] = []
    for key, s, a, reconcile_note in pairs:
        primary_probe = str(
            s.get("keyword")
            or s.get("primary_keyword")
            or a.get("keyword")
            or a.get("primary_keyword")
            or ""
        ).strip()
        service_target = _matching_service_target(
            {**a, **s},
            keyword=primary_probe,
            targets=service_targets,
        )
        original_page_type = _page_type(a)
        route_to_service = bool(
            service_target
            and _low_volume_commercial_service_match({**a, **s}, primary_probe)
            and _keyword_is_service_head_term(primary_probe, service_target)
        )
        supporting_parent = bool(
            service_target
            and not route_to_service
            and original_page_type
            in {"article", "blog", "guide", "spoke", "cluster", "supporting", "hub"}
        )
        if route_to_service and service_target:
            key = url_n(
                str(service_target.get("url") or service_target.get("path") or "")
            )
            a = {**a, **service_target}
            reconcile_note = (
                f"{reconcile_note}; " if reconcile_note else ""
            ) + "commercial_keyword_routed_to_service"

        au = audit_by.get(key)
        disp = _disposition(au)
        inferred_existing = None
        if not disp:
            kw_probe = str(
                s.get("keyword")
                or s.get("primary_keyword")
                or a.get("keyword")
                or a.get("primary_keyword")
                or ""
            ).strip()
            kw_matches = _audit_keyword_matches(audit, keyword=kw_probe)
            if kw_matches:
                inferred_existing = kw_matches[0]
                disp = _disposition(inferred_existing) or "REFRESH"
        action = _action_from_disposition(disp)
        # Architecture-admitted service optimizes must stay briefable as refresh —
        # audit KEEP would otherwise collapse them to no_action.
        if s.get("from_url_map_optimize") and action in {"create", "no_action"}:
            if (_disposition(au) or "").upper() not in {"RETIRE", "REMOVE", "DELETE", "NOINDEX"}:
                action = "refresh"
                disp = disp if disp and _action_from_disposition(disp) == "refresh" else "OPTIMISE"
        cluster = str(s.get("cluster") or s.get("pillar") or a.get("cluster") or "").strip()
        primary = s.get("keyword") or s.get("primary_keyword") or a.get("keyword") or a.get("primary_keyword")
        url_map_entry = (
            None
            if route_to_service
            else lookup_url_map_entry(
                {"final_url_map": architecture.get("final_url_map") or []},
                cluster=cluster,
                keyword=str(primary or ""),
            )
        )
        if url_map_entry:
            map_action = url_map_entry.get("action")
            mapped_url = url_n(str(url_map_entry.get("selected_url") or ""))
            if map_action == "OPTIMIZE_EXISTING":
                action = "refresh"
                disp = disp or "OPTIMISE"
            elif map_action == "REVIEW_MERGE_REDIRECT":
                action = "refresh"
            if mapped_url and mapped_url != "/":
                key = mapped_url
        # Commercial keywords that collapse onto an existing service URL must
        # optimize that page — never brief a "create" for a live /services path.
        if route_to_service and action == "create":
            action = "refresh"
            disp = disp or "OPTIMISE"
            flags_pending_service_refresh = True
        else:
            flags_pending_service_refresh = False
        strategy_action = str(s.get("action") or "").strip().lower()
        if strategy_action in {"optimize", "optimise", "refresh"} and _is_service_page(
            {**a, **s, "page_type": a.get("page_type") or s.get("page_type")}
        ):
            action = "refresh"
            disp = disp or "OPTIMISE"
            flags_pending_service_refresh = True
        if _is_service_page({**a, **s, "page_type": a.get("page_type") or s.get("page_type")}) and action == "create":
            # Strategy invent sometimes stamps create onto an existing service path.
            if audit_by.get(key) or str(a.get("url_map_action") or "").upper() in {
                "OPTIMIZE_EXISTING",
                "REVIEW_MERGE_REDIRECT",
            }:
                action = "refresh"
                disp = disp or "OPTIMISE"
                flags_pending_service_refresh = True
        owner = owners.get(cluster.lower()) if cluster else None
        cannibal = bool(owner and owner != key)
        depth = a.get("depth")
        try:
            depth_i = int(depth) if depth is not None else (key.count("/") or 1)
        except (TypeError, ValueError):
            depth_i = key.count("/") or 1
        parent = a.get("parent") or a.get("parent_url")
        if supporting_parent and service_target:
            parent = service_target.get("url") or service_target.get("path")
            depth = service_target.get("depth")
            try:
                depth_i = int(depth) + 1
            except (TypeError, ValueError):
                depth_i = max(1, url_n(str(parent)).count("/")) + 1
        raw_crumbs = None if supporting_parent else a.get("breadcrumb")
        crumbs = _breadcrumb_list(raw_crumbs, str(parent) if parent else None, key)
        indexable = _indexable(a)
        tier = _tier(s)
        flags: list[str] = []
        if reconcile_note:
            flags.append("url_reconciled")
        if route_to_service:
            flags.append("commercial_service_route")
        elif supporting_parent:
            flags.append("supports_service")
        if flags_pending_service_refresh:
            flags.append("service_optimize")
        if cannibal:
            flags.append("cannibal_conflict")
        if inferred_existing:
            flags.append("existing_intent_match")
        if url_map_entry:
            flags.append(f"url_map_{str(url_map_entry.get('score_band') or 'mapped').lower()}")
        if tier in ("quick_win", "big_bet") and depth_i > 4:
            flags.append("depth_gt_4")
        if indexable is False and action in ("create", "refresh"):
            flags.append("non_indexable_active")
        if raw_crumbs and crumbs and len(crumbs) != depth_i + 1:
            flags.append("breadcrumb_mismatch")
        kws = (
            s.get("secondary_keywords")
            or s.get("supporting_keywords")
            or s.get("keywords")
            or a.get("secondary_keywords")
            or a.get("supporting_keywords")
            or []
        )
        if isinstance(kws, str):
            kws = [kws]
        primary = s.get("keyword") or s.get("primary_keyword") or a.get("keyword") or a.get("primary_keyword")
        title = s.get("title") or a.get("title") or primary
        ptype = _page_type(a)
        ctype = _planning_content_type({**s, **a}, page_type=ptype, primary=str(primary or "") or None)
        score = s.get("opportunity_score") or s.get("priority_score") or 0
        try:
            score_n = float(score)
        except (TypeError, ValueError):
            score_n = 0.0
        existing_match_url = None
        if inferred_existing:
            existing_match_url = inferred_existing.get("url") or inferred_existing.get("path")
        # Create rows must stamp a create-gap basis (validator rejects audit_disposition on create).
        if action == "create":
            if url_map_entry and url_map_entry.get("action") == "CREATE":
                decision_basis = "url_map_create"
            elif inferred_existing:
                decision_basis = "existing_content_match"
            else:
                decision_basis = "new_content_gap"
        else:
            decision_basis = "audit_disposition" if au else "new_content_gap"
            if url_map_entry:
                if url_map_entry.get("action") == "OPTIMIZE_EXISTING":
                    decision_basis = "url_map_optimize_existing"
                elif url_map_entry.get("action") == "REVIEW_MERGE_REDIRECT":
                    decision_basis = "url_map_review_merge"
                elif url_map_entry.get("action") == "CREATE":
                    decision_basis = "url_map_create"
            if inferred_existing and not str(decision_basis).startswith("url_map"):
                decision_basis = "existing_content_match"
        content_action = _content_action_label(
            action=action,
            disposition=disp,
            page_type=ptype,
        )
        new_content_justification = None
        if action == "create":
            new_content_justification = (
                "No relevant existing page matched by URL/keyword in content-audit inventory."
            )
        if (
            selected_services
            and _is_service_page({**a, **s, "page_type": ptype, "url": key, "path": key, "url_n": key})
            and not _matches_selected_service(
                {**a, **s, "page_type": ptype, "url": key, "path": key, "url_n": key},
                selected_services,
            )
        ):
            excluded.append(
                {
                    "url_n": key,
                    "reason": "unselected_service — outside the confirmed service selection",
                    "source_pack": "strategy",
                    "title": title,
                    "keyword": primary,
                }
            )
            continue
        secondary = [str(k).strip() for k in kws if str(k).strip() and str(k).strip().lower() != str(primary or "").strip().lower()][:8]
        pages.append(
            {
                "url_n": key,
                "url": a.get("url") or s.get("url") or s.get("suggested_url") or key,
                "path": key,
                "action": action,
                "content_action": content_action,
                "disposition": disp,
                "disposition_reason": (au or {}).get("reason") if au else None,
                "decision_basis": decision_basis,
                "existing_content_evidence_url": existing_match_url,
                "existing_content_check": "matched_existing" if inferred_existing else "no_match",
                "new_content_justification": new_content_justification,
                "effort": (au or {}).get("effort") if au else None,
                "title": title,
                "primary_keyword": primary,
                "keyword": primary,
                "secondary_keywords": secondary,
                "supporting_keywords": secondary,
                "keywords": secondary,
                "intent": s.get("intent") or a.get("intent"),
                "funnel": s.get("funnel") or a.get("funnel"),
                "angle": s.get("angle") or a.get("angle"),
                "business_fit": s.get("business_fit") or a.get("business_fit"),
                "service": s.get("service") or a.get("service"),
                "subservice": s.get("subservice") or a.get("subservice"),
                "target": s.get("target") or a.get("target"),
                "target_type": s.get("target_type") or a.get("target_type"),
                "seed": s.get("seed") or a.get("seed"),
                "from_phase5_topic": bool(s.get("from_phase5_topic")),
                "competitor_domains": list(s.get("beat_competitors") or s.get("competitor_domains") or []),
                "content_type": s.get("content_type") or a.get("content_type") or ctype,
                "content_type_label": ctype,
                "image_suggestions": s.get("image_suggestions") or [],
                "cluster": cluster or None,
                "parent": parent,
                "parent_url_n": url_n(str(parent)) if parent else None,
                "supports_service_id": (
                    (service_target.get("service_id") if service_target else None)
                    or s.get("supports_service_id")
                    or a.get("supports_service_id")
                ),
                "work_label": s.get("work_label") or a.get("work_label"),
                "from_selected_service": bool(
                    s.get("from_selected_service") or a.get("from_selected_service")
                ),
                "supports_subservice_id": (
                    service_target.get("subservice_id") if service_target else None
                ),
                "depth": depth_i,
                "breadcrumb": crumbs,
                "page_type": ptype,
                "indexable": indexable,
                "priority_tier": tier,
                "priority_score": score_n,
                "trend": s.get("trend") or a.get("trend"),
                "volume": s.get("volume") or s.get("search_volume") or a.get("volume"),
                "bucket": s.get("bucket") or a.get("bucket"),
                "opportunity_score": s.get("opportunity_score") or a.get("opportunity_score"),
                "cannibal_conflict": cannibal,
                "flags": flags,
                "reconcile_note": reconcile_note,
                "status": "planned",
                "url_map_score": url_map_entry.get("url_score") if url_map_entry else None,
                "url_map_band": url_map_entry.get("score_band") if url_map_entry else None,
                "url_map_action": url_map_entry.get("action") if url_map_entry else (
                    a.get("url_map_action") or s.get("url_map_action")
                ),
            }
        )

    pages = ensure_selected_service_coverage(pages, selected_services, search_demand)
    pages, viral_dropped = apply_blog_virality_gate(pages, search_demand)
    excluded.extend(viral_dropped)
    for row in pages:
        row["work_label"] = _work_label_for(row)
    pages, capped = prune_blog_creates(
        pages,
        max_creates=MAX_BLOG_CREATES,
        protect_service_ids={
            _norm_service_id(svc.get("id"))
            for svc in selected_services
            if isinstance(svc, dict)
        },
    )
    excluded.extend(capped)

    order_pages_by_hierarchy(pages)
    for i, row in enumerate(pages, 1):
        row["priority_rank"] = i
        row["wave"] = 1 if row.get("priority_tier") == "quick_win" else 2 if row.get("priority_tier") == "big_bet" else 3

    lock_problems: list[str] = []
    seen: set[str] = set()
    for row in pages:
        key = str(row.get("url_n") or "")
        if key in seen:
            lock_problems.append(f"duplicate url_n {key}")
        seen.add(key)
        if row.get("action") == "create":
            if not row.get("title"):
                lock_problems.append(f"create {key} missing title")
            if not row.get("primary_keyword"):
                lock_problems.append(f"create {key} missing primary_keyword")
        ptype = row.get("page_type")
        if ptype and ptype not in PAGE_TYPES:
            lock_problems.append(f"{key} unrecognised page_type {ptype}")
        tier = row.get("priority_tier")
        if tier and tier not in PRIORITY_TIERS:
            lock_problems.append(f"{key} unrecognised priority_tier {tier}")

    flagged = sum(1 for r in pages if r.get("flags"))
    if strict:
        if flagged:
            lock_problems.append(f"{flagged} row(s) have structural/cannibal flags")
        if excluded:
            lock_problems.append(f"{len(excluded)} excluded row(s)")

    locked = not lock_problems
    by_action = {
        "create": sum(1 for r in pages if r.get("action") == "create"),
        "refresh": sum(1 for r in pages if r.get("action") == "refresh"),
        "retire": sum(1 for r in pages if r.get("action") == "retire"),
        "no_action": sum(1 for r in pages if r.get("action") == "no_action"),
    }
    briefable = [r for r in pages if r.get("action") in ("create", "refresh")]
    from app.services.phase_pipeline import enrich_phase89_pack

    return enrich_phase89_pack(
        {
        "card_type": "content_planning_report",
        "client_name": client_name,
        "primary_url": primary_url,
        "locked": locked,
        "lock_reason": None if locked else "; ".join(lock_problems[:8]),
        "pages": pages,
        "roadmap": pages,
        "excluded": excluded,
        "selected_services": selected_services,
        "summary": {
            "by_action": by_action,
            "merged": len(pages),
            "excluded": len(excluded),
            "flagged": flagged,
            "briefable": len(briefable),
            "blogs_per_selected_service": {
                str(svc.get("name") or svc.get("id")): sum(
                    1
                    for row in pages
                    if str(row.get("action") or "") == "create"
                    and not _is_service_page(row)
                    and _blog_supports_service(row, svc)
                )
                for svc in selected_services
                if isinstance(svc, dict)
            },
        },
        "planned_count": len(pages),
        "create_count": by_action["create"],
        "refresh_count": by_action["refresh"],
        "retire_count": by_action["retire"],
        "no_action_count": by_action["no_action"],
        "excluded_count": len(excluded),
        "source": "content_planning",
        "note": (
            (
                "Locked merge of strategy + architecture + audit. "
                "Each confirmed selected service page is optimized. "
                "Supporting pages come from that service's keyword clusters. "
                "A blog is added only when a keyword for the service is trending. "
                f"Unrelated blog creates stay capped at {MAX_BLOG_CREATES}. "
                "Phase 10 briefs pages with action create|refresh in priority_rank order. "
                "Re-run this merge if any upstream pack changes."
            )
            if locked and selected_services
            else (
                "Locked merge of strategy + architecture + audit. "
                f"At most {MAX_BLOG_CREATES} blog/guide creates are briefable. "
                "Live service pages enter as refresh/optimize. "
                "Phase 10 briefs pages with action create|refresh in priority_rank order. "
                "Re-run this merge if any upstream pack changes."
            )
            if locked
            else f"Unlocked — {'; '.join(lock_problems[:4])}. Phase 10 must not brief from this report."
        ),
        }
    )


def validate_report(report: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Re-check lock conditions on an existing report."""
    pages = report.get("pages") or report.get("roadmap") or []
    rebuilt = build_roadmap(
        {"priority_queue": pages},
        {
            "target_url_tree": pages,
            "selected_services": list(report.get("selected_services") or []),
            "cluster_owners": [
                {"cluster": p.get("cluster"), "owner_url": p.get("url_n")}
                for p in pages
                if isinstance(p, dict) and p.get("cluster")
            ],
        },
        {
            "dispositions": [
                {
                    "url": p.get("url_n"),
                    "disposition": p.get("disposition"),
                    "reason": p.get("disposition_reason"),
                }
                for p in pages
                if isinstance(p, dict) and p.get("disposition")
            ]
        },
        strict=strict,
        client_name=report.get("client_name"),
        primary_url=report.get("primary_url"),
    )
    # Prefer validating the stored pages in place rather than re-joining
    lock_problems: list[str] = []
    seen: set[str] = set()
    for row in pages:
        if not isinstance(row, dict):
            continue
        key = str(row.get("url_n") or url_n(str(row.get("url") or row.get("path") or "")))
        if key in seen:
            lock_problems.append(f"duplicate url_n {key}")
        seen.add(key)
        if row.get("action") == "create":
            if not (row.get("title") or row.get("primary_keyword")):
                lock_problems.append(f"create {key} missing title/keyword")
            if not row.get("primary_keyword") and not row.get("keyword"):
                lock_problems.append(f"create {key} missing primary_keyword")
        ptype = row.get("page_type")
        if ptype and ptype not in PAGE_TYPES:
            lock_problems.append(f"{key} unrecognised page_type {ptype}")
        tier = row.get("priority_tier")
        if tier and tier not in PRIORITY_TIERS:
            lock_problems.append(f"{key} unrecognised priority_tier {tier}")
    if strict:
        if any(isinstance(r, dict) and r.get("flags") for r in pages):
            lock_problems.append("flags present")
        if report.get("excluded"):
            lock_problems.append("excluded rows present")
    locked = not lock_problems
    return {
        "locked": locked,
        "lock_reason": None if locked else "; ".join(lock_problems[:8]),
        "ok": locked,
        "rebuilt_locked": rebuilt.get("locked"),
    }


async def run_content_planning_plan(
    *,
    client_name: str,
    primary_url: str,
    site_architecture_status: str | None = None,
    site_architecture: dict[str, Any] | None = None,
    seo_strategy: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
    website: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    seo_strategy_status: str | None = None,
    website_status: str | None = None,
    content_audit_status: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    _ = marketing, industry
    ia = dict(site_architecture or {})
    strategy = dict(seo_strategy or {})
    audit = dict(content_audit or {})
    site = dict(website or {})

    if not ia_gate_ok(site_architecture_status, ia):
        return {
            "blocked": True,
            "locked": False,
            "reason": (
                "Content Planning needs an approved Site Architecture blueprint "
                "(target_url_tree with URL + parent + depth). "
                "Run and approve Site Architecture first."
            ),
        }

    queue = _strategy_rows(strategy)
    if (seo_strategy_status or "") != "complete" and not queue:
        return {
            "blocked": True,
            "locked": False,
            "reason": (
                "Content Planning joins approved Content Strategy with architecture. "
                "Approve Phase 6 strategy first — this phase does not invent topics."
            ),
        }

    has_live_site = (website_status or "") == "complete" or bool(
        site.get("sample_urls")
        or (isinstance(site.get("crawl"), dict) and site.get("crawl"))
    )
    has_audit = bool(
        audit.get("dispositions") or audit.get("inventory") or audit.get("refresh_queue")
    )
    if has_live_site and (content_audit_status or "") not in ("complete", "pending_signoff") and not has_audit:
        return {
            "blocked": True,
            "locked": False,
            "reason": (
                "Established site — approve Content Audit (Phase 8) first so existing pages "
                "get dispositions. Without audit, every URL would be treated as create."
            ),
        }

    if has_live_site and has_audit:
        inventory = list(audit.get("inventory") or [])
        with_disposition = sum(
            1
            for row in inventory
            if isinstance(row, dict) and row.get("disposition") not in (None, "")
        )
        if inventory and with_disposition == 0:
            return {
                "blocked": True,
                "locked": False,
                "reason": (
                    "Content Audit inventory exists but has no dispositions yet. "
                    "Classify existing pages first so planning can decide optimize/update/"
                    "consolidate before any new creation."
                ),
            }

    return build_roadmap(
        strategy,
        ia,
        audit,
        strict=strict,
        client_name=client_name,
        primary_url=primary_url,
        search_demand=dict(search_demand or {}),
    )
