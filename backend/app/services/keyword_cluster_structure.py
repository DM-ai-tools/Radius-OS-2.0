"""Structure on top of the clusters: page pattern, size discipline, pillar hubs.

None of this adds, drops or invents a keyword. It only describes and arranges
the clusters the engine already built:

- ``modifier_pattern``: what kind of page a cluster needs (comparison, how-to,
  best-of list, audience-specific, tools, what-is, location, or core topic),
  read from the members' own wording.
- ``split_oversized``: a cluster over ``SPLIT_THRESHOLD`` keywords is several
  pages, so it is split by modifier pattern, then intent, then funnel stage,
  and every part is named so the split is visible.
- ``assign_pillars``: clusters that share a head topic and together span more
  than one funnel stage become subtopics of one pillar (a content hub); a
  cluster with no siblings stands alone.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable
from typing import Any

SPLIT_THRESHOLD = 15
MIN_PART_SIZE = 3
_FUNNEL_ORDER = {"TOFU": 0, "MOFU": 1, "BOFU": 2}

PATTERN_LABELS = {
    "comparison": "Comparison",
    "how_to": "How-to",
    "best_of": "Best-of list",
    "audience": "Audience-specific",
    "tools": "Tools",
    "what_is": "What-is",
    "location": "Location",
    "core": "Core topic",
}
# How a split part is named, per dimension.
_FACET = {
    "comparison": "comparisons",
    "how_to": "how-to guides",
    "best_of": "best-of lists",
    "audience": "audience-specific",
    "tools": "tools",
    "what_is": "definitions",
    "core": "core",
}


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def stable_cluster_id(cluster: dict[str, Any]) -> str:
    """An id that survives renames: built from the cluster's member keywords.

    Cluster names are written by the model and rewritten later in the
    pipeline, so nothing structural should key on them. Membership does not
    change when a cluster is renamed.
    """
    members = sorted(
        {_norm(m.get("keyword")) for m in cluster.get("keywords") or [] if isinstance(m, dict) and m.get("keyword")}
        | {_norm(m) for m in cluster.get("keywords") or [] if isinstance(m, str) and m.strip()}
    )
    basis = "|".join(members) or _norm(cluster.get("primary_keyword") or cluster.get("name"))
    return "c_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def pattern_of(keyword: str) -> tuple[str, str | None]:
    """(pattern, location) for one keyword, from the engine's modifier rules.

    Locations count only as whole words: the engine's own bucket matches them
    as substrings, so "us" inside "australia" would otherwise read as a place.
    """
    from app.services.keyword_clustering import _LOCATIONS, _modifier_bucket

    k = _norm(keyword)
    bucket = _modifier_bucket(k)
    if bucket and bucket.startswith("location:"):
        bucket = None
    if not bucket:
        found = [loc for loc in _LOCATIONS if re.search(rf"(?<![a-z0-9]){re.escape(loc)}(?![a-z0-9])", k)]
        if found:
            return "location", max(found, key=len)
        return "core", None
    return {"how-to": "how_to", "listicle": "best_of", "what-is": "what_is"}.get(bucket, bucket), None


def cluster_modifier_pattern(cluster: dict[str, Any]) -> dict[str, Any]:
    """The members' prevailing pattern (the primary breaks ties)."""
    members = [m for m in (cluster.get("keywords") or []) if isinstance(m, dict) and m.get("keyword")]
    if not members:
        return {"modifier_pattern": "core", "modifier_location": None, "modifier_label": PATTERN_LABELS["core"]}
    patterns = [pattern_of(m["keyword"]) for m in members]
    counts = Counter(p for p, _loc in patterns)
    top = max(counts.values())
    primary_pattern = pattern_of(str(cluster.get("primary_keyword") or members[0]["keyword"]))[0]
    leaders = [p for p, n in counts.items() if n == top]
    pattern = primary_pattern if primary_pattern in leaders else min(leaders)
    location = None
    if pattern == "location":
        locs = Counter(loc for p, loc in patterns if p == "location" and loc)
        location = locs.most_common(1)[0][0] if locs else None
    label = PATTERN_LABELS[pattern]
    if location == "near me":
        label = "Local (near me)"
    elif location:
        label = f"Location: {location.upper() if len(location) <= 3 else location.title()}"
    return {"modifier_pattern": pattern, "modifier_location": location, "modifier_label": label}


# --------------------------------------------------------------------------- size


def _facet_label(dimension: str, value: str) -> str:
    if dimension == "modifier":
        if value.startswith("location:"):
            return value.split(":", 1)[1].title()
        return _FACET.get(value, value)
    if dimension == "intent":
        return f"{value} intent"
    return f"{value} stage"


def split_oversized(
    rows: list[dict[str, Any]],
    labels: Callable[[dict[str, Any]], tuple[str, str]],
    *,
    threshold: int = SPLIT_THRESHOLD,
) -> list[tuple[list[dict[str, Any]], list[tuple[str, str]]]]:
    """Split a member list larger than ``threshold`` into page-sized parts.

    Tries the members' modifier pattern first, then their intent, then their
    funnel stage (``labels(row) -> (intent, funnel)``), recursing on any part
    still too large. A part smaller than ``MIN_PART_SIZE`` joins the largest
    part rather than becoming a page of its own. Returns ``(rows, path)`` pairs,
    where ``path`` lists the ``(dimension, value)`` steps that made the part;
    an empty path means the list was not split.
    """

    def value(row: dict[str, Any], dimension: str) -> str:
        if dimension == "modifier":
            pattern, loc = pattern_of(str(row.get("keyword") or ""))
            return f"location:{loc}" if pattern == "location" and loc else pattern
        intent, funnel = labels(row)
        return intent if dimension == "intent" else funnel

    def _split(part: list[dict[str, Any]], dims: list[str]) -> list[tuple[list[dict[str, Any]], list[tuple[str, str]]]]:
        if len(part) <= threshold or not dims:
            return [(part, [])]
        dimension = dims[0]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in part:
            groups.setdefault(value(row, dimension), []).append(row)
        big = {k: v for k, v in groups.items() if len(v) >= MIN_PART_SIZE}
        if len(big) < 2:
            return _split(part, dims[1:])
        largest = max(big, key=lambda k: (len(big[k]), k))
        for k, v in groups.items():
            if k not in big:
                big[largest].extend(v)
        out: list[tuple[list[dict[str, Any]], list[tuple[str, str]]]] = []
        for k in sorted(big, key=lambda k: (-len(big[k]), k)):
            for sub, path in _split(big[k], dims[1:]):
                out.append((sub, [(dimension, k), *path]))
        return out

    return _split(list(rows), ["modifier", "intent", "funnel"])


def split_name(base: str, path: list[tuple[str, str]]) -> str:
    return f"{base}: {', '.join(_facet_label(d, v) for d, v in path)}" if path else base


# --------------------------------------------------------------------------- pillars

_HEAD_DROP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "with", "to", "for", "my", "your", "i", "it",
    "how", "what", "why", "when", "where", "which", "who", "is", "are", "does", "do", "can",
    "should", "much", "many", "best", "top", "vs", "versus", "compare", "comparison",
    "alternative", "alternatives", "review", "reviews", "cost", "costs", "price", "prices",
    "pricing", "guide", "tips", "examples", "ideas", "near", "me", "cheap", "affordable", "hire",
    "buy", "quote", "worth", "agency", "agencies", "service", "services", "company", "companies",
    "consultant", "consultants", "specialist", "specialists", "expert", "experts", "firm",
    "provider", "tool", "tools", "software", "platform", "app", "checklist", "template",
    "definition", "meaning", "melbourne", "sydney", "brisbane", "perth", "adelaide", "canberra",
    "hobart", "darwin", "geelong", "australia", "local", "online", "free", "small", "business",
}


def _head_tokens(cluster: dict[str, Any]) -> list[str]:
    text = cluster.get("primary_keyword") or cluster.get("name") or ""
    return [t for t in re.findall(r"[a-z0-9]+", _norm(text)) if t not in _HEAD_DROP and not t.isdigit()]


def _pillar_title(head: str, service: str | None) -> str:
    if service and _norm(service) == head:
        return str(service)
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in head.split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(text)).strip("-")[:60] or "pillar"


def _volume(cluster: dict[str, Any]) -> int | None:
    v = cluster.get("est_traffic")
    if v is None:
        v = cluster.get("total_volume")
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def assign_pillars(
    clusters: list[dict[str, Any]],
    *,
    service_of: Callable[[dict[str, Any]], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Group clusters into pillar hubs; mark each cluster subtopic or standalone.

    A head topic is the primary keyword's leading meaning-bearing words (the
    two-word head when other clusters share it, else the one-word head). Clusters
    only group within the same service. A group becomes a pillar when it has at
    least two clusters and they cover more than one funnel stage; otherwise its
    clusters stand alone, with the reason recorded.
    """
    service_of = service_of or (lambda _c: None)
    heads = []
    for c in clusters:
        tokens = _head_tokens(c)
        heads.append(
            {
                "service": service_of(c),
                "bigram": " ".join(tokens[:2]) if len(tokens) >= 2 else None,
                "unigram": tokens[0] if tokens else None,
            }
        )
    bigrams = Counter((h["service"], h["bigram"]) for h in heads if h["bigram"])
    key_of: list[tuple[str | None, str] | None] = []
    for h in heads:
        if h["bigram"] and bigrams[(h["service"], h["bigram"])] >= 2:
            key_of.append((h["service"], h["bigram"]))
        else:
            key_of.append(None)
    unigrams = Counter((h["service"], h["unigram"]) for h, k in zip(heads, key_of) if k is None and h["unigram"])
    for i, h in enumerate(heads):
        if key_of[i] is None and h["unigram"] and unigrams[(h["service"], h["unigram"])] >= 2:
            key_of[i] = (h["service"], h["unigram"])

    groups: dict[tuple[str | None, str], list[int]] = {}
    for i, key in enumerate(key_of):
        if key is not None:
            groups.setdefault(key, []).append(i)

    pillars: list[dict[str, Any]] = []
    for c in clusters:
        c["cluster_tier"] = "standalone"
        c["pillar_id"] = None
        c["pillar_name"] = None
        c["standalone_reason"] = "no other cluster shares its head topic"
    for (service, head), idx in groups.items():
        members = [clusters[i] for i in idx]
        stages = sorted({str(c.get("funnel") or "") for c in members if c.get("funnel")}, key=lambda s: _FUNNEL_ORDER.get(s, 9))
        if len(stages) < 2:
            for c in members:
                c["standalone_reason"] = (
                    f"shares the head topic \"{head}\" with {len(members) - 1} other cluster(s), "
                    f"but all sit at {stages[0] if stages else 'one'} stage, so they are not a hub"
                )
            continue
        title = _pillar_title(head, service)
        pillar_id = _slug(f"{service or ''} {head}")
        vols = [v for v in (_volume(c) for c in members) if v is not None]
        members.sort(key=lambda c: (_FUNNEL_ORDER.get(str(c.get("funnel")), 9), -(_volume(c) or 0)))
        for c in members:
            c["cluster_tier"] = "subtopic"
            c["pillar_id"] = pillar_id
            c["pillar_name"] = title
            c.pop("standalone_reason", None)
        pillars.append(
            {
                "pillar_id": pillar_id,
                "name": title,
                "head_term": head,
                "service": service,
                "subtopic_count": len(members),
                "total_volume": sum(vols) if vols else None,
                "funnel_stages": stages,
                "intents": sorted({str(c.get("intent")) for c in members if c.get("intent")}),
                "recommended_url": f"/{_slug(head)}/",
                "subtopics": [
                    {
                        "name": c.get("name"),
                        "primary_keyword": c.get("primary_keyword"),
                        "intent": c.get("intent"),
                        "funnel": c.get("funnel"),
                        "modifier_label": c.get("modifier_label"),
                        "volume": _volume(c),
                        "recommended_url": c.get("recommended_url"),
                    }
                    for c in members
                ],
            }
        )
    pillars.sort(key=lambda p: (-(p["total_volume"] or 0), p["name"]))
    return pillars
