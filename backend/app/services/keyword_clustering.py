"""Keyword Clustering skill — clean, cluster, map to content pages."""

from __future__ import annotations

import re
from typing import Any

from app.logging_config import get_logger
from app.services.keyword_cluster_roles import (
    RoleContext,
    conflicting_intents,
    finalize_cluster,
    finalize_clusters,
    label_keyword,
    sync_roadmap,
)
from app.services.keyword_cluster_structure import (
    SPLIT_THRESHOLD,
    assign_pillars,
    cluster_modifier_pattern,
    split_name,
    stable_cluster_id,
    split_oversized,
)
from app.services.keyword_opportunity import (
    detect_funnel,
    detect_intent,
)
from app.services.keyword_pipeline import run_keyword_pipeline
from app.services.topic_naming import specific_cluster_name

log = get_logger("keyword_clustering")

_STOP = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with"}
# Shared agency heads — never enough alone to nest a seed under a service
# (otherwise "Google Ads" lands under "Meta Ads" via the token "ads").
_GENERIC_SERVICE_GROUP_TOKENS = frozenset(
    {
        "ads",
        "ad",
        "advertising",
        "advert",
        "ppc",
        "sem",
        "cpc",
        "paid",
        "search",
        "social",
        "media",
        "marketing",
        "digital",
        "online",
        "agency",
        "agencies",
        "service",
        "services",
        "management",
        "company",
        "campaign",
        "campaigns",
        # "Google Business Profile optimization" is not an AEO service just
        # because the service name ends in "optimization".
        "optimization",
        "optimisation",
    }
)
_PLATFORM_TOKEN_ALIASES: dict[str, frozenset[str]] = {
    "meta": frozenset({"meta", "facebook", "fb", "instagram", "ig"}),
    "google": frozenset({"google", "adwords", "adword", "gads", "gad", "gadwords", "gadword"}),
    "linkedin": frozenset({"linkedin"}),
    "tiktok": frozenset({"tiktok"}),
    "youtube": frozenset({"youtube"}),
}
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
# Keyword rows carried on a cluster. Members beyond this are still counted in
# keyword_count and reported via keywords_truncated — never silently dropped.
_MAX_CLUSTER_KEYWORDS = 50

# Cluster fields written by keyword_cluster_roles.finalize_cluster.
_ROLE_FIELDS = (
    "intent_distribution",
    "funnel_distribution",
    "role_counts",
    "label_confidence",
    "primary_selection",
)
_ORPHAN_LABEL_FIELDS = (
    "intent",
    "intent_source",
    "intent_confidence",
    "funnel",
    "funnel_source",
    "funnel_confidence",
    "label_disagreement",
)


def _row_labels(row: dict[str, Any], ctx: RoleContext) -> tuple[str, str]:
    labels = label_keyword(row, brand_tokens=ctx.brand_tokens)
    return labels["intent"], labels["funnel"]


def _det_name(key: str, pkw: str, intent: str) -> str:
    """Rule-path cluster name from its primary keyword and grouping key."""
    head = _head_topic(pkw)
    if key.startswith("cmp:"):
        return specific_cluster_name(pkw, intent=intent, head=head, mod="comparison")
    if key.startswith("loc:"):
        # The grouping key matches locations as substrings ("us" in "australia");
        # the name uses a whole-word location, and never repeats one the
        # keyword already says ("local seo sydney", not "... Sydney in Sydney").
        from app.services.keyword_cluster_structure import pattern_of

        pattern, loc = pattern_of(pkw)
        if pattern != "location" or not loc or loc == "near me" or loc in _norm(pkw).split() or loc in _norm(pkw):
            return specific_cluster_name(pkw, intent=intent, head=head)
        return specific_cluster_name(pkw, intent=intent, head=head, mod=f"location:{loc}")
    if key.startswith("brand:"):
        return specific_cluster_name(pkw, intent="navigational", head=head)
    return specific_cluster_name(pkw, intent=intent, head=head, mod=_modifier_bucket(pkw))


def _member_row(r: dict[str, Any]) -> dict[str, Any]:
    """A cluster member: the row's facts. Roles and labels are added by the decider."""
    member = {
        "keyword": r.get("keyword"),
        "volume": r.get("volume"),
        "difficulty": r.get("difficulty"),
        "cpc": r.get("cpc"),
        "opportunity_score": r.get("opportunity_score"),
        "gap_flag": r.get("gap_flag"),
        "specificity": r.get("specificity"),
        # Carried from keyword_relevance.py's evaluate_keyword() so the
        # cluster contract keeps *why* each keyword survived cleaning,
        # not just that it did.
        "relevance_reason": r.get("relevance_reason"),
        "relevance_evidence": r.get("relevance_evidence") or None,
        "volume_estimated": r.get("volume_estimated"),
        "close_variants": r.get("close_variants") or None,
    }
    # Which seed layer found the keyword ("industry" for the home page's terms). Phase 6
    # reads it to tell industry clusters from service clusters; rows without a tag stay as they were.
    if r.get("seed_origin"):
        member["seed_origin"] = r["seed_origin"]
    return member


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _norm(text)).strip("-")
    return s[:70] or "cluster"


def _tokens(kw: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(kw)) if t not in _STOP and len(t) > 1}


def _distinctive_service_tokens(kw: str) -> set[str]:
    tokens = {t for t in _tokens(kw) if t not in _GENERIC_SERVICE_GROUP_TOKENS}
    expanded = set(tokens)
    for tok in tokens:
        for family in _PLATFORM_TOKEN_ALIASES.values():
            if tok in family:
                expanded.update(family)
    return expanded


# Words that say a query is about paid advertising, not just about a platform.
_PAID_HEADS = frozenset(
    {"ads", "ad", "advertising", "advert", "ppc", "sem", "cpc", "paid"}
    | _PLATFORM_TOKEN_ALIASES["google"] - {"google"}
)


def _family_tokens(tokens: set[str]) -> set[str]:
    """Collapse platform aliases to one token per family.

    ``_distinctive_service_tokens`` expands "google" into adwords/gads/… so a
    service and a seed that use different aliases still meet. Counting that
    expansion as overlap lets one shared word score seven times; collapse it so a
    shared platform is a single match.
    """
    out: set[str] = set()
    for tok in tokens:
        family = next((name for name, al in _PLATFORM_TOKEN_ALIASES.items() if tok in al), None)
        out.add(f"@{family}" if family else tok)
    return out


def _platform_family_key(kw: str) -> str:
    """Return google/meta/… when the keyword names a paid-media platform."""
    raw = _tokens(kw)
    for family, aliases in _PLATFORM_TOKEN_ALIASES.items():
        if raw & aliases:
            return family
    return ""


def _norm_service_key(value: str) -> str:
    """Normalize service labels so slash/punctuation variants still match."""
    return re.sub(r"[^a-z0-9]+", " ", _norm(value)).strip()


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


def _nest_subservices(group: dict[str, Any]) -> None:
    """Keep every keyword theme as a seed of the parent service.

    Local SEO and Technical SEO are different seeds under SEO. They are not
    turned into sub-services.
    """
    group["subservices"] = []
    group["subservice_count"] = 0


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
        seed_key = _norm_service_key(seed)
        target_key = _norm_service_key(target)
        # Seeds generated per service already carry the service that owns them. That
        # assignment is authoritative: it is not re-guessed from token overlap, which
        # is how a seed used to end up under the wrong service.
        assigned = str(cluster.get("service") or "").strip()
        if assigned:
            if str(cluster.get("service_id") or "") == "_other":
                return other_key, "assigned_other", 0
            assigned_key = _norm_service_key(assigned)
            owner = next((s for s in clean_services if _norm_service_key(s) == assigned_key), None)
            if owner:
                return _norm(owner), "assigned_service", 100
        parent_seg = str(cluster.get("parent_segment") or "").strip()
        if parent_seg:
            parent_key = _norm_service_key(parent_seg)
            parent = next(
                (s for s in clean_services if _norm_service_key(s) == parent_key),
                None,
            )
            if parent:
                return _norm(parent), "parent_service_seed", 100
        if str(cluster.get("target_type") or "").lower() in {"service", "seed"}:
            explicit = next(
                (
                    s
                    for s in clean_services
                    if _norm_service_key(s) in {target_key, seed_key}
                ),
                None,
            )
            if explicit:
                return _norm(explicit), "explicit_service_target", 100

        source_text = f"{seed} {target}"
        source_tokens = _family_tokens(_distinctive_service_tokens(source_text))
        if not source_tokens:
            return other_key, "no_service_overlap", 0
        source_is_paid = bool(_tokens(source_text) & _PAID_HEADS)
        best_key = ""
        best_score = 0
        for service in clean_services:
            service_tokens = _family_tokens(_distinctive_service_tokens(service))
            if not service_tokens:
                continue
            overlap = source_tokens & service_tokens
            if not overlap:
                continue
            # "Google Business Profile" shares the platform word with "Google Ads
            # Management" but is not about ads. A platform alone only proves
            # a paid-ads service when the seed also talks about paid advertising.
            if (
                all(tok.startswith("@") for tok in overlap)
                and _tokens(service) & _PAID_HEADS
                and not source_is_paid
            ):
                continue
            score = len(overlap) * 10
            if service_tokens.issubset(source_tokens):
                score += 20
            elif source_tokens.issubset(service_tokens):
                score += 10
            # Expanded keywords can break ties — only distinctive tokens count.
            for cls in ("exact", "phrase", "related", "broad"):
                for row in (cluster.get(cls) or [])[:10]:
                    if not isinstance(row, dict):
                        continue
                    kw_tokens = _family_tokens(
                        _distinctive_service_tokens(str(row.get("keyword") or ""))
                    )
                    score += min(3, len(kw_tokens & service_tokens))
            if score > best_score:
                best_key = _norm(service)
                best_score = score
        # Require at least one distinctive token match (score 10+).
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
                        # Carried so select_topics_from_service_clusters can
                        # actually honour them — this projection is what it reads.
                        "relevance_reason": row.get("relevance_reason"),
                        "relevance_unverified": bool(row.get("relevance_unverified")),
                        "commercial_target_eligible": row.get("commercial_target_eligible"),
                        "gap_flag": bool(row.get("gap_flag")),
                        "role": row.get("role"),
                        # Other wordings of this same search (keyword_variants).
                        "close_variants": row.get("close_variants") or [],
                    }
                )
        keywords.sort(
            key=lambda row: (
                0 if str(row.get("role") or "") == "Primary" else 1,
                {"exact": 0, "phrase": 1, "related": 2, "broad": 3}.get(
                    str(row.get("match_class") or ""), 4
                ),
                -int(row.get("volume") or 0) if isinstance(row.get("volume"), (int, float)) else 0,
            )
        )

        entry = {
            "seed": cluster.get("seed"),
            "primary_keyword": cluster.get("primary_keyword") or cluster.get("seed"),
            "target": cluster.get("target") or cluster.get("seed"),
            "target_type": cluster.get("target_type") or "keyword",
            "page_type": cluster.get("page_type"),
            "page_path": cluster.get("page_path"),
            "parent_segment": cluster.get("parent_segment"),
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

    out: list[dict[str, Any]] = []
    for group in groups.values():
        # Drop the catch-all only when empty — selected services stay visible
        # even with zero seeds so the multi-mode report mirrors selection.
        if group["service_key"] == other_key and not group["seeds"]:
            continue
        _nest_subservices(group)
        out.append(group)
    for group in out:
        group["seeds"].sort(
            key=lambda row: (
                {"service": 0, "sub_service": 1, "page": 2, "keyword": 3}.get(
                    str(row.get("target_type") or "keyword"), 4
                ),
                -int(row.get("keyword_count") or 0),
                str(row.get("seed") or "").lower(),
            )
        )
        # Number seeds within the service (Seed 1, Seed 2, …). Empty selected
        # services are kept so the multi-mode report still lists them.
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


def _legacy_text_intent(keyword: str) -> str:
    """The keyword-text intent rule as it stood before 2026-10-19. Grouping only."""
    k = _norm(keyword)
    geo = ("melbourne", "sydney", "brisbane", "perth", "adelaide", "gold coast", "near me",
           "australia", "auckland", "wellington")
    service = ("seo", "sem", "ppc", "ads", "adwords", "marketing", "agency", "web design",
               "web development", "website", "aeo", "geo")
    educational = ("what is", "what are", "how to", "guide", "meaning", "definition")
    buying = ("buy", "pricing", "price", "cost", "hire", "quote")
    if any(g in k for g in geo) and any(x in k for x in service):
        if any(x in k for x in educational):
            return "informational"
        return "transactional" if any(x in k for x in buying) else "commercial"
    if any(x in k for x in (*buying, "near me", "agency", "services", "company", "consultant")):
        if any(x in k for x in ("what is", "what are", "guide", "how to", "meaning")):
            return "informational"
        return "transactional" if any(x in k for x in buying) else "commercial"
    if any(x in k for x in ("best", "top", "vs", "versus", "alternative", "review", "compare")):
        return "commercial"
    if any(x in k for x in ("how to", "what is", "what are", "guide", "tips", "examples")):
        return "informational"
    if re.search(r"\b(login|sign in|signin|portal|dashboard|my account|customer portal)\b", k):
        return "navigational"
    if re.search(r"\b(official site|official website|contact number|phone number)\b", k):
        return "navigational"
    return "informational"


def _grouping_intent(kw: str, row: dict[str, Any] | None = None) -> str:
    """Intent used ONLY inside the grouping key. Never a label.

    Grouping is frozen as it stood before 2026-10-19: the better labels from
    ``keyword_opportunity.detect_intent`` must not move keywords between
    clusters. So this reproduces the label the old pipeline held for the row
    (the provider's label, the validation model's label, or the old text rule)
    and then applies the old simplified rule. Labels come from
    ``keyword_cluster_roles.label_keyword``.
    """
    row = row or {}
    existing = row.get("intent")
    if row.get("intent_basis") != "llm" and "provider_intent" in row:
        existing = row.get("provider_intent") or _legacy_text_intent(kw)
    if existing and existing not in ("mixed", ""):
        return str(existing).lower()
    k = _norm(kw)
    if any(x in k for x in ("buy", "pricing", "price", "cost", "hire", "quote", "near me")):
        return "transactional"
    if any(x in k for x in ("best", "top", "vs", "versus", "alternative", "review", "compare")):
        return "commercial"
    return "informational"


_GROUP_INTENTS = frozenset({"informational", "commercial", "transactional", "navigational"})
_GROUP_FUNNELS = frozenset({"TOFU", "MOFU", "BOFU"})


def _grouping_funnel(kw: str, row: dict[str, Any] | None, intent: str) -> str:
    """Funnel used only in the grouping key, so one cluster is one buyer stage."""
    stored = str((row or {}).get("funnel") or "").strip().upper()
    if stored in _GROUP_FUNNELS:
        return stored
    return detect_funnel(kw, intent)


def _key_focus(key: str) -> tuple[str, str]:
    """Intent and funnel encoded at the end of a grouping key."""
    body = key.split("|plat:")[0]
    parts = body.split("|")
    funnel = parts[-1] if parts and parts[-1] in _GROUP_FUNNELS else ""
    if funnel:
        parts = parts[:-1]
    intent = parts[-1] if parts and parts[-1] in _GROUP_INTENTS else ""
    return intent, funnel




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


def _normalize_topic_text(kw: str) -> str:
    """One spelling of a topic, so variants share a page.

    "4ps of marketing" and "4 ps of marketing" are the same query. So are
    British and American spellings of the same word. The first two content
    words of this text are the grouping head.
    """
    text = _norm(kw)
    for src, dst in (
        ("optimisation", "optimization"),
        ("optimise", "optimize"),
        ("organisation", "organization"),
        ("organise", "organize"),
        ("behaviour", "behavior"),
        ("colour", "color"),
        ("favour", "favor"),
        ("centre", "center"),
    ):
        text = text.replace(src, dst)
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return re.sub(r"\s+", " ", text).strip()


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
    head_toks: list[str] = []
    for token in re.findall(r"[a-z0-9]+", _normalize_topic_text(kw)):
        if token in drop or token in _STOP:
            continue
        if not token.isdigit() and len(token) <= 1:
            continue
        head_toks.append(token if token.isdigit() else _singularize(token))
        if len(head_toks) == 2:
            break
    return " ".join(head_toks) or _norm(kw)


def _cluster_key(row: dict[str, Any], brand_tokens: set[str]) -> tuple[str, str]:
    """Return (group_key, display_name).

    A cluster is the keyword, not its parent topic. Comparisons, branded
    queries, and location queries stay separate so they do not cannibalize.
    """
    kw = _norm(str(row.get("keyword") or ""))
    toks = _tokens(kw)
    if brand_tokens and toks & brand_tokens:
        brand = next(iter(toks & brand_tokens))
        intent = _grouping_intent(kw, row)
        funnel = _grouping_funnel(kw, row, intent)
        return f"brand:{brand}|{intent}|{funnel}", f"{brand.title()} (Branded)"

    mod = _modifier_bucket(kw)
    head = _head_topic(kw)
    intent = _grouping_intent(kw, row)
    funnel = _grouping_funnel(kw, row, intent)
    # Intent and funnel both stay in the key. A how-to and a pricing query
    # do not share a page, and neither do a best-of list and a hire query
    # that happen to share an intent label.
    focus = f"{intent}|{funnel}"

    if mod == "comparison":
        return f"cmp:{_stem_key(kw)}|{focus}", f"{head.title()} Comparison"
    if mod and mod.startswith("location:"):
        loc = mod.split(":", 1)[1]
        return f"loc:{head}|{loc}|{focus}", f"{head.title()} - {loc.title()}"
    # Same intent and funnel are only a candidate pool. The cluster itself is
    # chosen later: one primary, plus the keywords that are its secondaries.
    label = head.title()
    if mod == "how-to":
        label = f"How to {head.title()}"
    elif mod == "listicle":
        label = f"Best {head.title()}"
    elif mod == "what-is":
        label = f"What Is {head.title()}?"
    elif mod == "tools":
        label = f"{head.title()} Tools"
    return f"topic:{focus}", label


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


def clusters_one_search_each(
    clusters: list[dict[str, Any]],
    ctx: RoleContext | None = None,
) -> list[dict[str, Any]]:
    """Split a cluster whose members are not one search.

    A city query stays with that city. The national wording of the same
    service becomes its own cluster instead of a secondary of the city.
    """
    from app.services.keyword_cluster_roles import finalize_cluster
    from app.services.topic_naming import phrase_title

    ctx = ctx or RoleContext()
    split: list[dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        keywords = [row for row in (cluster.get("keywords") or []) if isinstance(row, dict) and row.get("keyword")]
        pieces = _primary_with_secondaries(keywords, ctx) if len(keywords) > 1 else [keywords]
        original = _norm(str(cluster.get("primary_keyword") or ""))
        for piece in pieces:
            if not piece:
                continue
            finalized = finalize_cluster({"keywords": [_member_row(row) for row in piece]}, ctx)
            primary = str(finalized.get("primary_keyword") or "")
            if _norm(primary) == original or len(pieces) == 1:
                kept = dict(cluster)
                kept["keywords"] = finalized.get("keywords") or piece
                kept["primary_keyword"] = primary or cluster.get("primary_keyword")
                kept["keyword_count"] = len(kept["keywords"])
                kept["keywords_shown"] = len(kept["keywords"])
                kept["role_counts"] = finalized.get("role_counts")
                split.append(kept)
                continue
            funnel = finalized.get("funnel") or cluster.get("funnel")
            intent = finalized.get("intent") or cluster.get("intent")
            name = phrase_title(primary)
            focus = " · ".join(part for part in (funnel, intent) if part)
            if focus:
                name = f"{name} ({focus})"
            split.append(
                {
                    "name": name,
                    "primary_keyword": primary,
                    "funnel": funnel,
                    "intent": intent,
                    "keywords": finalized.get("keywords") or piece,
                    "keyword_count": len(finalized.get("keywords") or piece),
                    "keywords_shown": len(finalized.get("keywords") or piece),
                    "role_counts": finalized.get("role_counts"),
                    "primary_selection": finalized.get("primary_selection"),
                }
            )
    return split


def _primary_with_secondaries(items: list[dict[str, Any]], ctx: RoleContext) -> list[list[dict[str, Any]]]:
    """Split a candidate pool into clusters of one primary and its secondaries.

    A supporting keyword is a different search, so it becomes its own cluster
    and can pick up secondaries of its own from what remains.
    """
    remaining = [row for row in items if isinstance(row, dict) and row.get("keyword")]
    peeled: list[list[dict[str, Any]]] = []
    while remaining:
        draft = finalize_cluster({"keywords": [_member_row(row) for row in remaining]}, ctx)
        keep = {_norm(str(draft.get("primary_keyword") or ""))}
        for member in draft.get("keywords") or []:
            if isinstance(member, dict) and member.get("role") == "Secondary":
                keep.add(_norm(str(member.get("keyword") or "")))
        kept = [row for row in remaining if _norm(str(row.get("keyword") or "")) in keep]
        if not kept:
            kept = remaining[:1]
        peeled.append(kept)
        kept_ids = {id(row) for row in kept}
        nxt = [row for row in remaining if id(row) not in kept_ids]
        if len(nxt) == len(remaining):
            peeled.append(nxt)
            break
        remaining = nxt
    return peeled


def build_clusters_deterministic(
    rows: list[dict[str, Any]],
    *,
    brand_name: str | None = None,
    domain: str | None = None,
    role_context: RoleContext | None = None,
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
    ctx = role_context or RoleContext.build(rows, brand_name=brand_name, domain=domain)

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

    # Merge tiny topical clusters (<3) into nearest topical cluster by
    # distinctive head overlap. Shared generic heads like "ads" must not merge
    # Google Ads into Meta Ads.
    merged_small = 0
    small = [k for k, v in groups.items() if len(v) < 3]
    large_keys = [k for k, v in groups.items() if len(v) >= 3]
    for sk in small:
        if sk.startswith("cmp:") or sk.startswith("brand:") or sk.startswith("loc:"):
            continue
        stoks = _distinctive_service_tokens(labels.get(sk, sk))
        best_k = None
        best_score = 0
        candidates = large_keys or [k for k in groups if k != sk and k.startswith("topic:")]
        for lk in candidates:
            if lk == sk:
                continue
            ltoks = _distinctive_service_tokens(labels.get(lk, lk))
            # Refuse cross-platform merges (google* vs meta*/facebook*).
            s_plat = {f for f, aliases in _PLATFORM_TOKEN_ALIASES.items() if stoks & aliases}
            l_plat = {f for f, aliases in _PLATFORM_TOKEN_ALIASES.items() if ltoks & aliases}
            if "|plat:" in sk or "|plat:" in lk:
                s_key_plat = sk.split("|plat:")[-1] if "|plat:" in sk else ""
                l_key_plat = lk.split("|plat:")[-1] if "|plat:" in lk else ""
                if s_key_plat != l_key_plat:
                    continue
            if s_plat and l_plat and s_plat.isdisjoint(l_plat):
                continue
            s_intent, s_funnel = _key_focus(sk)
            l_intent, l_funnel = _key_focus(lk)
            if s_intent and l_intent and s_intent != l_intent:
                continue
            if s_funnel and l_funnel and s_funnel != l_funnel:
                continue
            # Platform aliases expand one word ("google") into a whole family.
            # Collapse that back to one token so a shared platform is not a merge.
            score = len(_family_tokens(stoks) & _family_tokens(ltoks))
            if score > best_score:
                best_score = score
                best_k = lk
        # One shared word is not enough to merge two topics.
        if best_k and best_score >= 2:
            groups[best_k].extend(groups.pop(sk))
            labels.pop(sk, None)
            merged_small += 1

    # The candidate pool is every keyword of one intent and funnel. The cluster
    # is the primary plus the keywords that are secondaries of that primary.
    peeled_groups: dict[str, list[dict[str, Any]]] = {}
    peeled_labels: dict[str, str] = {}
    for key, items in groups.items():
        pieces = _primary_with_secondaries(items, ctx)
        for index, piece in enumerate(pieces):
            piece_key = key if index == 0 and len(pieces) == 1 else f"{key}|kw{index}"
            peeled_groups[piece_key] = piece
            peeled_labels[piece_key] = labels.get(key, "")
    groups = peeled_groups
    labels = peeled_labels

    # Size discipline: a group over SPLIT_THRESHOLD keywords is several pages.
    # Split it by modifier pattern, then intent, then funnel stage, and name the
    # parts after the whole so the split is visible (keyword_cluster_structure).
    split_info: dict[str, tuple[str, list[tuple[str, str]], int]] = {}
    split_originals = 0
    oversized_unsplit = 0
    for key in list(groups.keys()):
        items = groups[key]
        if len(items) <= SPLIT_THRESHOLD:
            continue
        parts = split_oversized(items, lambda r: _row_labels(r, ctx))
        if len(parts) < 2:
            oversized_unsplit += 1
            continue
        whole = finalize_cluster({"keywords": [_member_row(r) for r in items]}, ctx)
        base = _det_name(key, str(whole["primary_keyword"] or ""), whole["intent"])
        split_originals += 1
        del groups[key]
        labels.pop(key, None)
        for i, (sub, path) in enumerate(parts):
            nk = f"{key}|part{i}"
            groups[nk] = sub
            split_info[nk] = (base, path, len(items))

    clusters_out: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []

    for key, items in groups.items():
        # Keep single-keyword clusters when they are comparison/brand/location
        # or when opportunity is strong enough to warrant its own page.
        if len(items) == 1 and not key.startswith(("cmp:", "brand:", "loc:")):
            only = items[0]
            measured = only.get("volume")
            stands_alone = isinstance(measured, (int, float)) and int(measured) > 9
            if (
                not stands_alone
                and (only.get("opportunity_score") or 0) < 50
                and (measured or 0) < 200
            ):
                labels = label_keyword(only, brand_tokens=ctx.brand_tokens)
                orphans.append(
                    {
                        "keyword": only.get("keyword"),
                        "notes": "Single low-value keyword - needs more related terms",
                        "volume": only.get("volume"),
                        # An orphan is still a classified keyword — it was
                        # excluded from a cluster, not from the analysis.
                        **{k: labels[k] for k in _ORPHAN_LABEL_FIELDS},
                        "exclusion_reason": "single_low_value_keyword",
                    }
                )
                continue

        # One decider for primary, roles and labels (keyword_cluster_roles). The
        # display cap keeps the primary and the best-scored members; the
        # cluster's true size stays in keyword_count.
        ranked = list(items)
        draft = finalize_cluster({"keywords": [_member_row(r) for r in ranked]}, ctx)
        if len(draft["keywords"]) > _MAX_CLUSTER_KEYWORDS:
            keep = {_norm(str(k.get("keyword") or "")) for k in draft["keywords"][:_MAX_CLUSTER_KEYWORDS]}
            shown = [r for r in ranked if _norm(str(r.get("keyword") or "")) in keep]
            draft = finalize_cluster({"keywords": [_member_row(r) for r in shown]}, ctx)
        kw_rows = draft["keywords"]
        pkw = str(draft["primary_keyword"] or "")
        primary = next((r for r in ranked if _norm(str(r.get("keyword") or "")) == _norm(pkw)), ranked[0])
        intent = draft["intent"]
        funnel = draft["funnel"]
        mod = _modifier_bucket(pkw)
        ctype, rec_label = _content_type(mod, intent)
        split = split_info.get(key)
        cluster_name = split_name(split[0], split[1]) if split else _det_name(key, pkw, intent)
        focus_label = " · ".join(part for part in (funnel, intent) if part)
        if focus_label and focus_label.lower() not in cluster_name.lower():
            cluster_name = f"{cluster_name} ({focus_label})"
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
                # True membership, not the number of rows that fit under the
                # display cap — reporting the capped figure made keywords
                # disappear from the audit trail with nothing recording it.
                "keyword_count": len(ranked),
                "keywords_shown": len(kw_rows),
                "keywords_truncated": max(0, len(ranked) - len(kw_rows)),
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
                **{k: draft[k] for k in _ROLE_FIELDS},
                # Set when this cluster is one part of an oversized group.
                "split_from": split[0] if split else None,
                "split_by": [dimension for dimension, _v in split[1]] if split else None,
                "split_from_size": split[2] if split else None,
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
        # Every orphan; _complete_report caps the displayed list.
        "orphans": orphans,
        "orphans_truncated": 0,
        "content_roadmap": roadmap,
        "source": "deterministic_rules",
        "keyword_accounting": build_keyword_accounting(clusters_out, orphans, input_count=len(rows)),
        "size_discipline": {
            "split_threshold": SPLIT_THRESHOLD,
            "merged_small_clusters": merged_small,
            "split_clusters": split_originals,
            "clusters_from_splits": len(split_info),
            "oversized_unsplit": oversized_unsplit,
        },
    }


def build_keyword_accounting(
    clusters: list[dict[str, Any]],
    orphans: list[dict[str, Any]],
    *,
    input_count: int,
) -> dict[str, Any]:
    """Prove every keyword that entered clustering is accounted for.

    ``balanced`` is the invariant: clustered + orphaned == input. When it is
    False something was dropped without a recorded reason, and the run should
    not be treated as a complete keyword map.
    """
    clustered = sum(
        int(c.get("keyword_count") or len(c.get("keywords") or []))
        for c in clusters
        if isinstance(c, dict)
    )
    orphaned = len(orphans)
    return {
        "input_count": input_count,
        "clustered_count": clustered,
        "orphan_count": orphaned,
        "accounted_count": clustered + orphaned,
        "unaccounted_count": max(0, input_count - clustered - orphaned),
        "balanced": clustered + orphaned == input_count,
        "orphan_reasons": {
            reason: sum(1 for o in orphans if o.get("exclusion_reason") == reason)
            for reason in sorted(
                {str(o.get("exclusion_reason") or "unspecified") for o in orphans}
            )
        },
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
        conflict = conflicting_intents(cluster)
        if conflict:
            issues.append(
                {
                    "cluster": name,
                    "issue": "mixed_intent_cluster",
                    "severity": "warn",
                    "intents": conflict,
                    "intent_distribution": (cluster.get("intent_distribution") or {}).get("weighted_share"),
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


ORPHAN_DISPLAY_CAP = 100
ROADMAP_SIZE = 20


def _cluster_service(cluster: dict[str, Any], ctx: RoleContext) -> str | None:
    """The service most of the cluster's keywords were seeded for."""
    counts: dict[str, int] = {}
    for m in cluster.get("keywords") or []:
        if not isinstance(m, dict):
            continue
        service = (ctx.rows_by_kw.get(_norm(str(m.get("keyword") or ""))) or {}).get("service")
        if service:
            counts[str(service)] = counts.get(str(service), 0) + 1
    return max(sorted(counts), key=lambda s: counts[s]) if counts else None


def _roadmap(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(clusters, key=lambda c: c.get("best_score") or 0, reverse=True)
    return [
        {
            "priority": i,
            "cluster": c.get("name"),
            "content_type": c.get("recommended_content") or c.get("content_type"),
            "target_keyword": c.get("primary_keyword"),
            "intent": c.get("intent"),
            "funnel": c.get("funnel"),
            "est_traffic": c.get("est_traffic"),
            "modifier_label": c.get("modifier_label"),
            "pillar_name": c.get("pillar_name"),
        }
        for i, c in enumerate(ranked[:ROADMAP_SIZE], start=1)
    ]


def _provenance(report: dict[str, Any]) -> dict[str, Any]:
    """What was verified against real data and what was inferred."""
    clusters = [c for c in report.get("clusters") or [] if isinstance(c, dict)]
    members = [m for c in clusters for m in (c.get("keywords") or []) if isinstance(m, dict)]
    serp = report.get("serp_clustering") or {}
    model = report.get("model_clustering") or {}
    naming = report.get("naming") or {}
    serp_ran = bool(serp) and not serp.get("skipped")
    checked = {_norm(k) for k in (serp.get("checked_keywords") or [])}
    serp_checked = sum(
        1 for c in clusters if any(_norm(str(m.get("keyword") or "")) in checked for m in c.get("keywords") or [])
    )
    serp_merged = sum(1 for c in clusters if c.get("serp_validated"))
    measured = sum(1 for m in members if m.get("volume") is not None and not m.get("volume_estimated"))
    grouping = "model" if report.get("source") == "keyword_clustering_skill" else "rules"
    verified: list[str] = []
    inferred: list[str] = []
    if serp_ran:
        verified.append(
            f"{serp_checked} of {len(clusters)} clusters contain a head term whose live Google results "
            f"were compared; {serp_merged} cluster(s) were merged because their head terms shared results."
        )
        if len(clusters) > serp_checked:
            inferred.append(
                f"{len(clusters) - serp_checked} clusters had no head term checked against Google results; "
                f"their membership is inferred from {'the model' if grouping == 'model' else 'keyword wording rules'}."
            )
    else:
        reason = _SERP_SKIP_TEXT.get(str(serp.get("reason") or ""), str(serp.get("reason") or "not run"))
        inferred.append(
            f"The shared-search-results check did not run ({reason}), so no cluster's membership was "
            f"verified against Google results; all of it is inferred from "
            f"{'the model' if grouping == 'model' else 'keyword wording rules'}."
        )
    if grouping == "model":
        verified.append(
            "Every keyword the model placed was checked against the cleaned keyword list; "
            f"{report.get('llm_invented_dropped_count') or 0} keyword(s) it proposed that were not in the "
            "list were rejected."
        )
    else:
        inferred.append(
            "Clusters were grouped by deterministic wording rules"
            + (f"; model clustering did not run ({model.get('reason')})." if model.get("reason") else ".")
        )
        if model.get("deadline_fallback"):
            inferred.append(
                "This run fell back to the rules because the phase ran out of time; a run with "
                "time left would have used the model, so cluster membership can differ between runs."
            )
    if members:
        verified.append(
            f"{measured} of {len(members)} clustered keywords shown carry a measured DataForSEO volume; "
            "the rest are unmeasured or estimated and are labelled so."
        )
    accounting = report.get("keyword_accounting") or {}
    if accounting:
        (verified if accounting.get("balanced") else inferred).append(
            "Every keyword that entered clustering is accounted for (clustered or orphaned with a reason)."
            if accounting.get("balanced")
            else f"{accounting.get('unaccounted_count')} keyword(s) left clustering with no recorded reason."
        )
    return {
        "grouping": grouping,
        "source_label": (
            ("Model clustering" if grouping == "model" else "Rule-based clustering")
            + (", SERP-checked" if serp_ran else ", SERP check skipped")
        ),
        "serp_check_ran": serp_ran,
        "serp_check_reason": None if serp_ran else serp.get("reason") or "not_run",
        "serp_head_terms_checked": serp.get("head_terms") if serp_ran else 0,
        "serp_checked_clusters": serp_checked,
        "serp_merged_clusters": serp_merged,
        "model_clustering_ran": bool(model.get("ran")),
        "model_clustering_model": model.get("model"),
        "model_clustering_reason": model.get("reason"),
        "engine_requested": model.get("requested"),
        "deadline_fallback": bool(model.get("deadline_fallback")),
        "naming_ran": bool(naming.get("ran")),
        "naming_model": naming.get("model"),
        "naming_reason": naming.get("reason"),
        "invented_keywords_rejected": int(report.get("llm_invented_dropped_count") or 0),
        "cleaning_excluded_count": report.get("pipeline_excluded_count"),
        "cleaning_excluded_by_reason": report.get("pipeline_excluded_by_reason") or {},
        "measured_volume_keywords": measured,
        "clustered_keywords_shown": len(members),
        "verified": verified,
        "inferred": inferred,
    }


_SERP_SKIP_TEXT = {
    "disabled": "turned off in settings",
    "locale_unresolved": "no resolved market (location and language) for this client",
    "no_measured_head_terms": "no keyword had a measured volume to check",
    "not_run": "not run",
    "rule_fallback": "the model clustering failed and the rule-based result was used without the check",
}


def _complete_report(
    report: dict[str, Any],
    ctx: RoleContext,
    *,
    orphans: list[dict[str, Any]],
    model_clustering: dict[str, Any],
    naming: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Final shape for every exit path: roles, page pattern, pillars, roadmap,
    keyword accounting, provenance, validation."""
    report = _finalize_roles(report, ctx)
    report["clustering"] = {
        "ran": True,
        "engine": "model" if model_clustering.get("ran") else "rules",
        "requested_engine": model_clustering.get("requested"),
        "deadline_fallback": bool(model_clustering.get("deadline_fallback")),
        "reason": None if model_clustering.get("ran") else model_clustering.get("reason"),
    }
    clusters = sorted(
        (c for c in report.get("clusters") or [] if isinstance(c, dict)),
        key=lambda c: c.get("best_score") or 0,
        reverse=True,
    )
    for c in clusters:
        c.update(cluster_modifier_pattern(c))
        # Stable across renames; later phases link clusters by this, not by name.
        c.setdefault("cluster_id", stable_cluster_id(c))
    report["clusters"] = clusters
    report["pillars"] = assign_pillars(clusters, service_of=lambda c: _cluster_service(c, ctx))
    report["content_roadmap"] = _roadmap(clusters)
    report["clusters_created"] = len(clusters)
    report["keyword_accounting"] = build_keyword_accounting(
        clusters, orphans, input_count=int(report.get("total_keywords") or 0)
    )
    report["orphan_count"] = len(orphans)
    report["orphans"] = orphans[:ORPHAN_DISPLAY_CAP]
    report["orphans_truncated"] = max(0, len(orphans) - ORPHAN_DISPLAY_CAP)
    report["model_clustering"] = model_clustering
    report["naming"] = naming or {"ran": False, "model": None, "reason": "naming runs only after a SERP check"}
    report["clustering_provenance"] = _provenance(report)
    return _attach_cluster_validation(report)


def _finalize_roles(report: dict[str, Any], ctx: RoleContext) -> dict[str, Any]:
    """Last word on primary, roles and labels, after SERP merges and naming.

    A SERP merge folds one cluster's rows (its Primary included) into another,
    so the decider runs again on every cluster; it is idempotent, so clusters
    nothing touched come out unchanged.
    """
    report["clusters"] = finalize_clusters(list(report.get("clusters") or []), ctx)
    return sync_roadmap(report)


def _cluster_is_unverified(cluster: dict[str, Any]) -> bool:
    """True when the cluster must not become a page target.

    Choke point between clustering and everything downstream — site
    architecture, planning, briefs, production, publishing all read the CDP
    `clusters` list.

    A cluster is unverified when the post-cluster coherence gate rejected it,
    when the cluster itself is flagged, or when every member keyword is an
    unverified shell.
    """
    if cluster.get("cluster_coherence_ok") is False:
        return True
    if cluster.get("cluster_sense_verdict") in ("drop", "review"):
        return True
    if cluster.get("relevance_unverified") and cluster.get("cluster_sense_verdict"):
        return True
    keywords = [k for k in (cluster.get("keywords") or []) if isinstance(k, dict)]
    if not keywords:
        return bool(cluster.get("relevance_unverified"))
    return all(
        k.get("relevance_unverified") or k.get("relevance_reason") == "relevance_unverified"
        for k in keywords
    )


def clusters_for_cdp(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten cluster report into the simpler CDP `clusters` list."""
    out: list[dict[str, Any]] = []
    for c in report.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        if _cluster_is_unverified(c):
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
                "existing_page_match": c.get("existing_page_match"),
                "topic_disposition": c.get("topic_disposition"),
                "sitemap_match": c.get("sitemap_match"),
                # Topic classification verdict + its justification, so the CDP
                # view can show *why* a cluster is existing/new/supporting.
                "topic_status": c.get("topic_status"),
                "topic_reason": c.get("topic_reason"),
                "topic_confidence": c.get("topic_confidence"),
                "topic_needs_review": c.get("topic_needs_review"),
                "url_status": c.get("url_status"),
                "supporting_parent": c.get("supporting_parent"),
                "cannibalization": c.get("cannibalization"),
                "cluster_id": c.get("cluster_id") or stable_cluster_id(c),
                # Page pattern, hub placement and size discipline.
                "modifier_pattern": c.get("modifier_pattern"),
                "modifier_location": c.get("modifier_location"),
                "modifier_label": c.get("modifier_label"),
                "cluster_tier": c.get("cluster_tier"),
                "pillar_id": c.get("pillar_id"),
                "pillar_name": c.get("pillar_name"),
                "split_from": c.get("split_from"),
                "split_by": c.get("split_by"),
                # Why this primary, and the member label mix.
                "primary_selection": c.get("primary_selection"),
                "intent_distribution": c.get("intent_distribution"),
                "funnel_distribution": c.get("funnel_distribution"),
                "role_counts": c.get("role_counts"),
                "label_confidence": c.get("label_confidence"),
                "serp_validated": c.get("serp_validated"),
            }
        )
    return out


async def _serp_name_and_attach(
    report: dict[str, Any],
    cleaned: list[dict[str, Any]],
    *,
    location_code: int | None,
    language_code: str | None,
    role_context: RoleContext,
    model_clustering: dict[str, Any],
) -> dict[str, Any]:
    """SERP-overlap the head terms, then let Claude name each cluster."""
    from app.services.keyword_cluster_naming import name_clusters
    from app.services.keyword_serp_clustering import refine_clusters_with_serp

    clusters = list(report.get("clusters") or [])
    naming: dict[str, Any] = {"ran": False, "model": None, "reason": "naming runs only after a SERP check"}
    report["serp_clustering"] = {"skipped": True, "reason": "fewer than 3 keywords"}
    if len(cleaned) >= 3:
        clusters, serp_meta = await refine_clusters_with_serp(
            clusters,
            cleaned,
            location_code=location_code,
            language_code=language_code,
        )
        report = {
            **report,
            "clusters": clusters,
            "clusters_created": len(clusters),
            "serp_clustering": serp_meta,
        }
        if not serp_meta.get("skipped"):
            # Roles first, so the namer sees each cluster's final primary.
            report = _finalize_roles(report, role_context)
            report["clusters"] = await name_clusters(report["clusters"], audit=naming)
    return _complete_report(
        report,
        role_context,
        orphans=list(report.get("orphans") or []),
        model_clustering=model_clustering,
        naming=naming,
    )


async def run_keyword_clustering(
    rows: list[dict[str, Any]],
    *,
    seeds: list[str] | None = None,
    brand_name: str | None = None,
    domain: str | None = None,
    products: list[str] | None = None,
    within_deadline: bool = True,
    relevance_context: Any | None = None,
    location_code: int | None = None,
    language_code: str | None = None,
    site_strength: float | None = None,
) -> dict[str, Any]:
    """Clean the keywords, then cluster them with the engine the settings choose.

    ``keyword_clustering_enabled`` switches clustering off entirely: the cleaned
    keywords are still audited, the report records that clustering did not run and
    why, and it carries no ``clusters`` key. ``keyword_clustering_use_model`` picks
    the engine (model or rules). ``within_deadline`` is only a guard: when the phase
    is out of time the rules run instead of the model, and the report says so
    (``clustering.deadline_fallback``) rather than silently differing between runs.
    """
    from app.config import get_settings

    settings = get_settings()
    pipeline_result = run_keyword_pipeline(
        [r for r in rows if isinstance(r, dict) and r.get("keyword")],
        relevance_context=relevance_context,
        brand_name=brand_name,
        source_label="clustering",
    )
    cleaned = pipeline_result["keywords"]
    if not getattr(settings, "keyword_clustering_enabled", True):
        excluded_all = pipeline_result["excluded"]
        return {
            "clustering": {
                "ran": False,
                "engine": None,
                "requested_engine": None,
                "deadline_fallback": False,
                "reason": "turned off in settings (KEYWORD_CLUSTERING_ENABLED=false)",
            },
            "total_keywords": len(cleaned),
            "clusters_created": 0,
            "keyword_pipeline": pipeline_result["pipeline"],
            "pipeline_excluded": excluded_all[:24],
            "pipeline_excluded_count": len(excluded_all),
            "keyword_cleaning": pipeline_result.get("relevance_audit") or {},
            "source": "clustering_disabled",
        }
    requested = "model" if getattr(settings, "keyword_clustering_use_model", False) else "rules"
    role_context = RoleContext.build(
        cleaned,
        brand_name=brand_name,
        domain=domain,
        relevance_context=relevance_context,
        site_strength=site_strength,
    )
    report = build_clusters_deterministic(
        cleaned, brand_name=brand_name, domain=domain, role_context=role_context
    )
    report["keyword_pipeline"] = pipeline_result["pipeline"]
    report["pipeline_excluded"] = pipeline_result["excluded"][:24]
    # The excluded *sample* is capped for payload size; the counts must not be,
    # or the cleaning stage silently loses keywords from the audit trail.
    excluded_all = pipeline_result["excluded"]
    report["pipeline_excluded_count"] = len(excluded_all)
    report["pipeline_excluded_by_reason"] = {
        reason: sum(1 for e in excluded_all if e.get("reason") == reason)
        for reason in sorted({str(e.get("reason") or "unspecified") for e in excluded_all})
    }
    report["keyword_cleaning"] = pipeline_result.get("relevance_audit") or {}

    engine = {"requested": requested, "deadline_fallback": False}
    skip_reason = None
    if requested == "rules":
        skip_reason = "the rules were chosen in settings (KEYWORD_CLUSTERING_USE_MODEL=false)"
    elif not within_deadline:
        skip_reason = "the Phase 5 time budget ran out before clustering, so the rules ran instead"
        engine["deadline_fallback"] = True
    elif len(cleaned) < 3:
        skip_reason = "fewer than 3 keywords"
    if skip_reason:
        return await _serp_name_and_attach(
            report,
            cleaned,
            location_code=location_code,
            language_code=language_code,
            role_context=role_context,
            model_clustering={"ran": False, "model": None, "reason": skip_reason, **engine},
        )

    from app.services.keyword_llm_clustering import llm_cluster_keywords

    llm_result = await llm_cluster_keywords(
        cleaned,
        brand_name=brand_name,
        domain=domain,
        products=products,
        seeds=seeds,
        relevance_context=relevance_context,
        role_context=role_context,
    )

    if llm_result.get("skipped") or not llm_result.get("clusters"):
        why = str(llm_result.get("skipped") or llm_result.get("error") or "empty")
        log.warning(
            "keyword_clustering_llm_unavailable",
            reason=why,
            kept_clusters=len(report.get("clusters") or []),
        )
        report["serp_clustering"] = {"skipped": True, "reason": "rule_fallback"}
        return _complete_report(
            report,
            role_context,
            orphans=list(report.get("orphans") or []),
            model_clustering={
                "ran": False,
                "model": llm_result.get("model"),
                "reason": {
                    "mock_or_no_key": "no model key configured",
                    "empty": "the model returned no clusters",
                }.get(why, why),
                **engine,
            },
        )

    clusters = llm_result["clusters"]
    orphans = llm_result.get("orphans") or []
    roadmap = llm_result.get("content_roadmap") or []

    # Head terms get a real SERP check: keywords returning the same pages are
    # one query however differently they are worded. Priced per keyword, so it
    # covers the head only and is skipped entirely without a resolved locale.
    from app.services.keyword_serp_clustering import refine_clusters_with_serp

    clusters, serp_meta = await refine_clusters_with_serp(
        clusters,
        cleaned,
        location_code=location_code,
        language_code=language_code,
    )
    from app.services.keyword_cluster_naming import name_clusters

    # The decider runs before naming (so names fit the final primary) and again
    # in _finalize_roles below; naming itself cannot change the primary.
    clusters = finalize_clusters(clusters, role_context)
    naming: dict[str, Any] = {}
    clusters = await name_clusters(clusters, audit=naming)

    clusters.sort(key=lambda c: c.get("best_score") or 0, reverse=True)
    return _complete_report(
        {
            "total_keywords": len(cleaned),
            "clusters_created": len(clusters),
            "serp_clustering": serp_meta,
            "clusters": clusters,
            "orphans": orphans,
            "content_roadmap": roadmap[:20],
            "source": "keyword_clustering_skill",
            "size_discipline": llm_result.get("size_discipline") or {},
            "llm_cluster_audit": llm_result.get("llm_cluster_audit") or {},
            "llm_invented_dropped_count": llm_result.get("llm_invented_dropped_count") or 0,
            "keyword_pipeline": report.get("keyword_pipeline"),
            "pipeline_excluded": report.get("pipeline_excluded"),
            "pipeline_excluded_count": report.get("pipeline_excluded_count"),
            "pipeline_excluded_by_reason": report.get("pipeline_excluded_by_reason"),
            "keyword_cleaning": report.get("keyword_cleaning"),
            "llm_invented_dropped": llm_result.get("llm_invented_dropped") or [],
        },
        role_context,
        orphans=orphans,
        model_clustering={
            "ran": True,
            "model": (llm_result.get("llm_cluster_audit") or {}).get("model"),
            "reason": None,
            **engine,
        },
        naming=naming,
    )
