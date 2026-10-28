"""Split a one-seed service expansion into multiple seeds and regroup keywords.

Phase 5 expands each selected service as a single DataForSEO root, so the
multi-mode report often shows one seed per service. This step asks the model
to name the distinct search themes inside that keyword set and assigns every
keyword to one of those seeds. When the model is unavailable or returns a
single seed, keywords are grouped by the themes already present in the list.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from typing import Any

from app.config import get_settings
from app.integrations.llm import keyword_analysis_chat
from app.logging_config import get_logger
from app.services.keyword_seeding import MATCH_CLASSES

log = get_logger("keyword_seed_identification")

MIN_KEYWORDS_TO_SPLIT = 8
DOMINANT_SHARE = 0.7
MAX_SEEDS = 6
MIN_SEED_KEYWORDS = 2
PROMPT_KEYWORD_LIMIT = 100

_BOILERPLATE = frozenset(
    {
        "a",
        "an",
        "and",
        "agency",
        "agencies",
        "are",
        "best",
        "cheap",
        "company",
        "companies",
        "consultant",
        "consultants",
        "cost",
        "costs",
        "expert",
        "experts",
        "firm",
        "firms",
        "for",
        "guide",
        "hire",
        "how",
        "in",
        "is",
        "me",
        "near",
        "of",
        "on",
        "online",
        "or",
        "pricing",
        "price",
        "prices",
        "professional",
        "professionals",
        "provider",
        "providers",
        "service",
        "services",
        "specialist",
        "specialists",
        "the",
        "to",
        "top",
        "versus",
        "vs",
        "what",
        "with",
    }
)

_SYSTEM = """\
You are an SEO seed-keyword analyst. A service was expanded as one seed, so its
keywords are piled into a single group. Split them into multiple seeds.

A seed is a short search theme (usually 2–4 words) that several keywords share.
Examples under an SEO service: "local seo", "technical seo", "ecommerce seo",
"seo agency". Location modifiers of the same theme stay together. Different
services-of-the-service (technical vs ecommerce vs local) are different seeds.

Return valid JSON only:
{
  "seeds": [
    {"seed": "exact phrase", "keywords": ["keyword from the input", "..."]}
  ]
}

Rules:
- Return at least 2 seeds, and at most 6, whenever the list contains more than one theme.
- Never return only the service name.
- Every seed must contain the service's own distinguishing words (for example "content" for Content Marketing, "seo" for SEO, "social media" for Social Media Marketing). A neighboring service's phrase is not a seed.
- Every seed must be the primary keyword of its theme: the short head term a page would target, copied from the input. Prefer an exact or phrase head (usually 2–4 words) over a long-tail, a question, or a checklist.
- A long-tail variant such as "local seo melbourne checklist" belongs under the primary "local seo". It is not its own seed when the head term is in the list.
- Use ONLY keywords from the input. Never invent a keyword.
- Assign every input keyword to exactly one seed. The seed name is the primary; the other keywords support it.
- Do not return volume, difficulty, or any other metric.
"""


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _service_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _norm(value)).strip()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(value).replace("-", " "))


def _identity_tokens(value: str) -> set[str]:
    """Words that identify a service or a seed, without agency/service filler."""
    return {token for token in _tokens(value) if token not in _BOILERPLATE and len(token) > 2}


# Words that show up in many service names and do not identify one service.
_GENERIC_HEADS = frozenset(
    {"marketing", "digital", "online", "management", "solution", "solutions"}
)
# Platforms that stand in for a service when the service name itself is absent.
_PLATFORM_FOR_CORE = {
    "social": frozenset(
        {"instagram", "facebook", "tiktok", "linkedin", "youtube", "pinterest", "twitter"}
    ),
    "media": frozenset(
        {"instagram", "facebook", "tiktok", "linkedin", "youtube", "pinterest", "twitter"}
    ),
    "ads": frozenset({"adwords", "ppc"}),
    "google": frozenset({"adwords", "ppc"}),
}


def _other_services(service: str, services: list[str]) -> list[str]:
    own = _service_key(service)
    return [item for item in services if _service_key(item) and _service_key(item) != own]


def names_another_service(text: str, service: str, services: list[str]) -> bool:
    """True when ``text`` is another selected service, not this one.

    "seo agency" and "google ads agency" name SEO and Google Ads. They are not
    seeds of Content Marketing. "content marketing strategy" still is.
    """
    label_tokens = _identity_tokens(text)
    if not label_tokens:
        return False
    own = _identity_tokens(service)
    label = _norm(text)
    for other in _other_services(service, services):
        other_tokens = _identity_tokens(other)
        if not other_tokens:
            continue
        other_phrase = " ".join(token for token in _tokens(other) if token not in _BOILERPLATE)
        own_phrase = " ".join(token for token in _tokens(service) if token not in _BOILERPLATE)
        if (
            other_phrase
            and len(other_phrase) >= 3
            and other_phrase in label
            and (not own_phrase or own_phrase not in label)
        ):
            return True
        if other_tokens <= label_tokens and not (own and own <= label_tokens):
            return True
    return False


def _core_tokens(service: str, services: list[str]) -> set[str]:
    """The words that make this service different from the others selected."""
    own = _identity_tokens(service)
    shared = set(_GENERIC_HEADS)
    for other in _other_services(service, services):
        shared |= _identity_tokens(other)
    core = own - shared
    return core or own


def seed_fits_service(text: str, service: str, services: list[str]) -> bool:
    """A seed is accurate when it carries this service and not a sibling service.

    "content marketing strategy" fits Content Marketing. "seo agency" does not.
    "local seo" fits SEO. "instagram marketing" fits Social Media Marketing.
    """
    if names_another_service(text, service, services):
        return False
    cores = _core_tokens(service, services)
    tokens = set(_tokens(text))
    if not cores:
        return True
    if cores <= tokens:
        return True
    aliases: set[str] = set()
    for core in cores:
        aliases |= _PLATFORM_FOR_CORE.get(core, frozenset())
    if not (tokens & aliases):
        return False
    for other in _other_services(service, services):
        other_core = _core_tokens(other, services)
        if other_core and other_core <= tokens:
            return False
    return True


def _accurate_seed_groups(
    groups: dict[str, list[str]],
    keywords: list[str],
    service: str,
    services: list[str],
) -> dict[str, list[str]]:
    """Keep seed names that describe this service. Other keywords stay on the service seed."""
    kept: dict[str, list[str]] = {}
    for label, members in groups.items():
        if not seed_fits_service(label, service, services):
            continue
        fitting = [
            keyword
            for keyword in members
            if seed_fits_service(keyword, service, services)
        ]
        if len(fitting) >= MIN_SEED_KEYWORDS:
            kept[label] = fitting
    assigned = {keyword for members in kept.values() for keyword in members}
    parent = _norm(service)
    leftovers = [
        keyword
        for keyword in keywords
        if keyword not in assigned and not names_another_service(keyword, service, services)
    ]
    if leftovers:
        host = next(
            (
                label
                for label in kept
                if _norm(label) == parent or parent in _norm(label)
            ),
            None,
        )
        if host:
            kept[host].extend(leftovers)
        else:
            kept[parent] = leftovers
    return kept


def _strip_cross_service_keywords(
    clusters: list[dict[str, Any]],
    services: list[str],
) -> list[dict[str, Any]]:
    """Remove keywords that name a sibling service before they can become seeds."""
    if len([service for service in services if _service_key(service)]) < 2:
        return clusters
    cleaned: list[dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        service = _parent_service(cluster, services) or str(cluster.get("service") or "")
        if not service:
            cleaned.append(cluster)
            continue
        copy = dict(cluster)
        for cls in MATCH_CLASSES:
            rows = []
            for row in copy.get(cls) or []:
                if not isinstance(row, dict):
                    continue
                keyword = str(row.get("keyword") or "")
                if names_another_service(keyword, service, services):
                    continue
                rows.append(row)
            copy[cls] = rows
        keyword_count = sum(len(copy.get(cls) or []) for cls in MATCH_CLASSES)
        if keyword_count == 0:
            continue
        copy["keyword_count"] = keyword_count
        cleaned.append(copy)
    return cleaned


def _parse_json(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _keyword_rows(clusters: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    """Return (normalized keyword, row, match class) for every keyword row."""
    rows: list[tuple[str, dict[str, Any], str]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        for cls in MATCH_CLASSES:
            for row in cluster.get(cls) or []:
                if not isinstance(row, dict):
                    continue
                keyword = _norm(row.get("keyword"))
                if keyword:
                    rows.append((keyword, row, cls))
    return rows


def _volume(row: dict[str, Any]) -> int:
    volume = row.get("volume")
    return int(volume) if isinstance(volume, (int, float)) else 0


def _parent_service(
    cluster: dict[str, Any], services: list[str]
) -> str | None:
    """Service this cluster should be split under, if it is a service seed."""
    by_key = {_service_key(service): service for service in services if _service_key(service)}
    target_type = str(cluster.get("target_type") or "").lower()
    target = str(cluster.get("target") or "").strip()
    seed = str(cluster.get("seed") or "").strip()
    if target_type != "service" and _service_key(seed) not in by_key and _service_key(target) not in by_key:
        return None
    if by_key:
        match = by_key.get(_service_key(target)) or by_key.get(_service_key(seed))
        if match:
            return match
    if target_type == "service":
        return target or seed or None
    return None


def theme_seed_groups(
    keywords: list[tuple[str, int]],
    *,
    service: str,
    services: list[str] | None = None,
    classes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Group keywords into themes using tokens that are not shared boilerplate.

    Returns an empty dict when the list is one theme. Seed labels are keywords
    from the list, so nothing is invented.
    """
    if len(keywords) < MIN_SEED_KEYWORDS:
        return {}
    unique: dict[str, int] = {}
    for keyword, volume in keywords:
        key = _norm(keyword)
        if not key:
            continue
        unique[key] = max(unique.get(key, 0), int(volume or 0))
    if len(unique) < MIN_KEYWORDS_TO_SPLIT:
        return {}

    service_tokens = set(_tokens(service))
    token_freq: Counter[str] = Counter()
    for keyword in unique:
        token_freq.update(set(_tokens(keyword)))
    common = {
        token
        for token, count in token_freq.items()
        if count >= max(2, int(len(unique) * 0.75))
    }
    common |= service_tokens

    def distinctive(keyword: str) -> list[str]:
        return [
            token
            for token in _tokens(keyword)
            if token not in _BOILERPLATE and token not in common and len(token) > 2
        ]

    grouped: dict[str, list[str]] = defaultdict(list)
    general: list[str] = []
    for keyword in unique:
        heads = distinctive(keyword)
        if not heads:
            general.append(keyword)
            continue
        grouped[heads[0]].append(keyword)

    themes: dict[str, list[str]] = {}
    for members in grouped.values():
        if len(members) < MIN_SEED_KEYWORDS:
            general.extend(members)
            continue
        label = _label_from_members(
            members, unique, service=service, services=services, classes=classes
        )
        themes.setdefault(label, []).extend(members)

    if len(general) >= MIN_SEED_KEYWORDS and themes:
        themes[
            _label_from_members(
                general, unique, service=service, services=services, classes=classes
            )
        ] = list(general)
    elif general and themes:
        largest = max(themes, key=lambda label: len(themes[label]))
        themes[largest].extend(general)
    if len(themes) < 2:
        return {}
    return _cap_seed_map(themes, unique)


_QUESTION_STARTS = frozenset({"how", "what", "why", "when", "where", "which", "who"})
_SUPPORTING_TAILS = frozenset(
    {
        "checklist",
        "guide",
        "template",
        "templates",
        "example",
        "examples",
        "tips",
        "ideas",
        "review",
        "reviews",
        "versus",
        "vs",
    }
)
_CLASS_RANK = {"exact": 0, "phrase": 1, "related": 2, "broad": 3}


def is_primary_head(keyword: str) -> bool:
    """A primary keyword is a short head term, not a question or a supporting tail."""
    tokens = _tokens(keyword)
    if not tokens or len(tokens) > 4:
        return False
    if tokens[0] in _QUESTION_STARTS:
        return False
    if set(tokens) & _SUPPORTING_TAILS:
        return False
    return True


def choose_primary_keyword(
    members: list[str],
    volumes: dict[str, int],
    classes: dict[str, str] | None = None,
) -> str:
    """Pick the primary keyword: a head term, exact or phrase, then highest volume."""

    def rank(keyword: str) -> tuple:
        tokens = _tokens(keyword)
        class_rank = _CLASS_RANK.get(str((classes or {}).get(keyword) or ""), 1)
        return (
            0 if is_primary_head(keyword) else 1,
            class_rank,
            0 if 1 <= len(tokens) <= 3 else 1,
            -int(volumes.get(keyword) or 0),
            len(tokens),
            keyword,
        )

    return min(members, key=rank)


def _label_from_members(
    members: list[str],
    volumes: dict[str, int],
    *,
    service: str | None = None,
    services: list[str] | None = None,
    classes: dict[str, str] | None = None,
) -> str:
    pool = members
    if service:
        fitting = [
            keyword
            for keyword in members
            if seed_fits_service(keyword, service, services or [service])
        ]
        if fitting:
            pool = fitting
    return choose_primary_keyword(pool, volumes, classes)


def _name_seeds_as_primaries(
    groups: dict[str, list[str]],
    volumes: dict[str, int],
    classes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Rename each seed to the primary keyword inside it."""
    named: dict[str, list[str]] = {}
    for members in groups.values():
        if not members:
            continue
        primary = choose_primary_keyword(members, volumes, classes)
        bucket = named.setdefault(primary, [])
        for keyword in members:
            if keyword not in bucket:
                bucket.append(keyword)
    return named


def _cap_seed_map(groups: dict[str, list[str]], volumes: dict[str, int]) -> dict[str, list[str]]:
    if len(groups) <= MAX_SEEDS:
        return groups
    ranked = sorted(groups, key=lambda label: (-sum(volumes.get(kw, 0) for kw in groups[label]), label))
    kept = ranked[:MAX_SEEDS]
    dropped = ranked[MAX_SEEDS:]
    out = {label: list(groups[label]) for label in kept}
    largest = kept[0]
    for label in dropped:
        out[largest].extend(groups[label])
    return out


def _seed_label_grounded(label: str, keywords: set[str]) -> bool:
    normalized = _norm(label)
    if not normalized or normalized in _BOILERPLATE:
        return False
    if normalized in keywords:
        return True
    if len(normalized) < 3:
        return False
    return sum(1 for keyword in keywords if normalized in keyword) >= MIN_SEED_KEYWORDS


def assignments_from_model_payload(
    payload: dict[str, Any] | None,
    keywords: list[str],
) -> dict[str, list[str]] | None:
    """Validate a model payload into seed -> keywords. Invented keywords are dropped."""
    if not isinstance(payload, dict):
        return None
    known = {_norm(keyword) for keyword in keywords if _norm(keyword)}
    if not known:
        return None
    assigned: set[str] = set()
    groups: dict[str, list[str]] = {}
    for entry in payload.get("seeds") or []:
        if not isinstance(entry, dict):
            continue
        label = _norm(entry.get("seed"))
        if not _seed_label_grounded(label, known):
            continue
        members: list[str] = []
        for raw in entry.get("keywords") or []:
            keyword = _norm(raw)
            if keyword not in known or keyword in assigned:
                continue
            members.append(keyword)
            assigned.add(keyword)
        if label not in groups:
            groups[label] = []
        groups[label].extend(members)
    groups = {label: members for label, members in groups.items() if members}
    if len(groups) < 2:
        return None
    volumes = {keyword: 0 for keyword in known}
    groups = _assign_remaining(known - assigned, groups)
    groups = _merge_tiny_seeds(groups, volumes)
    if len(groups) < 2:
        return None
    return _cap_seed_map(groups, volumes)


def _assign_remaining(remaining: set[str], groups: dict[str, list[str]]) -> dict[str, list[str]]:
    if not remaining:
        return groups
    out = {label: list(members) for label, members in groups.items()}
    leftover: list[str] = []
    for keyword in sorted(remaining):
        seed = _closest_seed(keyword, list(out))
        if seed:
            out[seed].append(keyword)
        else:
            leftover.append(keyword)
    if len(leftover) >= MIN_SEED_KEYWORDS:
        label = _label_from_members(leftover, {keyword: 0 for keyword in leftover})
        if label not in out:
            out[label] = leftover
        else:
            out[label].extend(leftover)
    elif leftover and out:
        largest = max(out, key=lambda label: len(out[label]))
        out[largest].extend(leftover)
    return out


def _closest_seed(keyword: str, seeds: list[str]) -> str | None:
    keyword_tokens = set(_tokens(keyword))
    best: str | None = None
    best_score = 0
    for seed in seeds:
        score = len(set(_tokens(seed)) & keyword_tokens)
        if score > best_score:
            best_score = score
            best = seed
    if best_score:
        return best
    normalized = _norm(keyword)
    for seed in seeds:
        if _norm(seed) and _norm(seed) in normalized:
            return seed
    return None


def _merge_tiny_seeds(
    groups: dict[str, list[str]], volumes: dict[str, int]
) -> dict[str, list[str]]:
    large = {label: members for label, members in groups.items() if len(members) >= MIN_SEED_KEYWORDS}
    tiny = {label: members for label, members in groups.items() if len(members) < MIN_SEED_KEYWORDS}
    if len(large) < 2:
        return {}
    for members in tiny.values():
        for keyword in members:
            seed = _closest_seed(keyword, list(large)) or max(
                large, key=lambda label: sum(volumes.get(item, 0) for item in large[label])
            )
            large[seed].append(keyword)
    return large


def _rebuild_clusters(
    source: list[dict[str, Any]],
    *,
    service: str,
    groups: dict[str, list[str]],
    origin: str,
) -> list[dict[str, Any]]:
    by_keyword: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for keyword, row, cls in _keyword_rows(source):
        by_keyword[keyword].append((row, cls))
    clusters: list[dict[str, Any]] = []
    for seed, members in groups.items():
        buckets: dict[str, list[dict[str, Any]]] = {cls: [] for cls in MATCH_CLASSES}
        seen: set[tuple[str, str]] = set()
        for keyword in members:
            for row, cls in by_keyword.get(_norm(keyword), []):
                marker = (cls, _norm(keyword))
                if marker in seen:
                    continue
                seen.add(marker)
                buckets[cls].append({**row, "seed": seed, "parent_topic": seed})
        keyword_count = sum(len(buckets[cls]) for cls in MATCH_CLASSES)
        if keyword_count == 0:
            continue
        for cls in MATCH_CLASSES:
            buckets[cls].sort(
                key=lambda row: (
                    0 if _norm(row.get("keyword")) == _norm(seed) else 1,
                    -_volume(row),
                    _norm(row.get("keyword")),
                )
            )
            for row in buckets[cls]:
                row["role"] = (
                    "Primary" if _norm(row.get("keyword")) == _norm(seed) else "Secondary"
                )
        clusters.append(
            {
                "seed": seed,
                "primary_keyword": seed,
                "target": service,
                "target_type": "service",
                "exact": buckets["exact"],
                "phrase": buckets["phrase"],
                "related": buckets["related"],
                "broad": buckets["broad"],
                "keyword_count": keyword_count,
                "volume_sum": sum(_volume(row) for cls in MATCH_CLASSES for row in buckets[cls]),
                "classes_missing": [cls for cls in MATCH_CLASSES if not buckets[cls]],
                "seed_origin": origin,
            }
        )
    clusters.sort(key=lambda cluster: (-int(cluster.get("volume_sum") or 0), str(cluster.get("seed"))))
    return clusters


def _prompt_keywords(rows: list[tuple[str, dict[str, Any], str]], themes: dict[str, list[str]]) -> list[str]:
    ranked = sorted({keyword for keyword, _, _ in rows}, key=lambda keyword: keyword)
    volumes: dict[str, int] = {}
    for keyword, row, _ in rows:
        volumes[keyword] = max(volumes.get(keyword, 0), _volume(row))
    ranked.sort(key=lambda keyword: (-volumes.get(keyword, 0), keyword))
    chosen = ranked[:PROMPT_KEYWORD_LIMIT]
    seen = set(chosen)
    for members in themes.values():
        for keyword in members[:3]:
            if keyword not in seen and len(chosen) < PROMPT_KEYWORD_LIMIT:
                chosen.append(keyword)
                seen.add(keyword)
    return chosen


def _user_message(
    *,
    service: str,
    keywords: list[str],
    volumes: dict[str, int],
    brand_name: str | None,
    industry: str | None,
    geography: str | None,
    other_services: list[str] | None = None,
) -> str:
    siblings = [item for item in (other_services or []) if _service_key(item) != _service_key(service)]
    lines = [
        f"Service: {service}",
        f"Other selected services (do not use these as seeds): {', '.join(siblings) or '(none)'}",
        f"Brand: {brand_name or '(not provided)'}",
        f"Industry: {industry or '(not provided)'}",
        f"Geography: {geography or '(not provided)'}",
        "",
        "Keywords:",
    ]
    for keyword in keywords:
        volume = volumes.get(keyword)
        if volume:
            lines.append(f"{keyword} | vol={volume}")
        else:
            lines.append(keyword)
    return "\n".join(lines)


async def _model_groups(
    rows: list[tuple[str, dict[str, Any], str]],
    *,
    service: str,
    themes: dict[str, list[str]],
    brand_name: str | None,
    industry: str | None,
    geography: str | None,
    other_services: list[str] | None = None,
) -> dict[str, list[str]] | None:
    settings = get_settings()
    if getattr(settings, "use_mock_llm", False):
        return None
    if not (
        str(getattr(settings, "anthropic_api_key", "") or "").strip()
        or str(getattr(settings, "openrouter_api_key", "") or "").strip()
    ):
        return None
    volumes: dict[str, int] = {}
    for keyword, row, _ in rows:
        volumes[keyword] = max(volumes.get(keyword, 0), _volume(row))
    shown = _prompt_keywords(rows, themes)
    try:
        raw = await asyncio.wait_for(
            keyword_analysis_chat(
                system=_SYSTEM,
                user=_user_message(
                    service=service,
                    keywords=shown,
                    volumes=volumes,
                    brand_name=brand_name,
                    industry=industry,
                    geography=geography,
                    other_services=other_services,
                ),
                model=settings.skill_model,
                max_tokens=4096,
            ),
            timeout=50,
        )
    except asyncio.TimeoutError:
        log.warning("seed_identification_timeout", service=service)
        return None
    except Exception as exc:
        log.warning("seed_identification_failed", service=service, error=str(exc)[:200])
        return None
    payload = _parse_json(raw or "")
    if not payload:
        log.warning("seed_identification_unparsed", service=service)
        return None
    groups = assignments_from_model_payload(payload, list(volumes))
    if not groups:
        log.info("seed_identification_rejected", service=service, reason="fewer_than_two_seeds")
    return groups


def _should_split(clusters: list[dict[str, Any]]) -> bool:
    rows = _keyword_rows(clusters)
    if len({keyword for keyword, _, _ in rows}) < MIN_KEYWORDS_TO_SPLIT:
        return False
    if len(clusters) <= 1:
        return True
    counts = [sum(1 for _k, _r, _c in _keyword_rows([cluster])) for cluster in clusters]
    total = sum(counts) or 1
    return max(counts) / total >= DOMINANT_SHARE


async def identify_multiple_seed_clusters(
    seed_clusters: list[dict[str, Any]],
    *,
    services: list[str],
    brand_name: str | None = None,
    industry: str | None = None,
    geography: str | None = None,
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep each fetched service seed. Do not rename it to a theme inside the list.

    A service such as SEO stays the seed. City variants, page titles, and
    phrases like "get seo service" are keywords under that seed, not new seeds.
    Keywords that name a different selected service are removed.
    """
    seed_clusters = _strip_cross_service_keywords(seed_clusters, services)
    _ = (brand_name, industry, geography, deadline)
    return seed_clusters, {
        "source": "unchanged",
        "seed_count_before": len(seed_clusters),
        "seed_count_after": len(seed_clusters),
        "services": [],
    }

