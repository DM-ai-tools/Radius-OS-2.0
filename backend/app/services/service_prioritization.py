"""Service prioritization — 80/20 selection + competitor-informed sub-service trees.

Runs before Phase 5 keyword expansion so users pick which services (and
sub-services) to target before Ahrefs/DataForSEO spend.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.integrations.llm import extract_domain
from app.integrations.web_fetch import discover_site_urls
from app.services.cache import cache_get, cache_set

_SERVICE_ROOTS = frozenset(
    {"service", "services", "solution", "solutions", "products", "product", "offerings", "what-we-do"}
)
_SERVICE_PATH_RE = re.compile(
    r"^/(?:services|service|solutions|products|what-we-do)(?:/([^/]+))?(?:/([^/]+))?",
    re.IGNORECASE,
)


def _slug_label(segment: str) -> str:
    return segment.replace("-", " ").replace("_", " ").strip().title()


def _norm_key(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _loose_key(value: str) -> str:
    """Punctuation-insensitive key so 'Google Ads/PPC' and 'Google Ads PPC' match."""
    return re.sub(r"[^a-z0-9]+", " ", _norm_key(value)).strip()


def _selection_keys(*values: str) -> set[str]:
    keys: set[str] = set()
    for value in values:
        n = _norm_key(value)
        if not n:
            continue
        keys.add(n)
        loose = _loose_key(n)
        if loose:
            keys.add(loose)
    return keys


# Tokens that appear on almost every agency offering — not enough alone to
# attach a keyword/page seed to a selected service.
_GENERIC_SERVICE_TOKENS = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "near",
        "me",
        "our",
        "your",
        "agency",
        "agencies",
        "service",
        "services",
        "management",
        "company",
        "companies",
        "marketing",
        "digital",
        "online",
        "web",
        "website",
        "optimisation",
        "optimization",
        "melbourne",
        "sydney",
        "australia",
        "au",
        # Shared paid-media heads — "ads" alone must not map Google Ads → Meta Ads.
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
        "campaign",
        "campaigns",
    }
)

# Distinctive platform families so facebook/instagram still attach to Meta Ads,
# and adwords still attaches to Google Ads, after "ads" is treated as generic.
_PLATFORM_TOKEN_ALIASES: dict[str, frozenset[str]] = {
    "meta": frozenset({"meta", "facebook", "fb", "instagram", "ig"}),
    "google": frozenset({"google", "adwords", "adword", "gads", "gad", "gadwords", "gadword"}),
    "linkedin": frozenset({"linkedin"}),
    "tiktok": frozenset({"tiktok"}),
    "youtube": frozenset({"youtube"}),
}


def _distinctive_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in re.split(r"[^a-z0-9]+", _norm_key(text))
        if len(tok) >= 2 and tok not in _GENERIC_SERVICE_TOKENS
    }


def _expand_platform_tokens(tokens: set[str]) -> set[str]:
    out = set(tokens)
    for tok in tokens:
        for family in _PLATFORM_TOKEN_ALIASES.values():
            if tok in family:
                out.update(family)
    return out


def term_matches_selected_services(term: str, services: list[str]) -> bool:
    """True when ``term`` clearly belongs to one of the selected services.

    Used after the selection gate so CDD keywords, website themes, and page
    seeds for *unselected* offerings (e.g. SEO when only Meta Ads was checked,
    or Google Ads when only Meta Ads was checked) do not expand into the pool.

    Shared heads like "ads" / "ppc" / "marketing" are ignored — a match needs a
    distinctive token (or platform alias such as facebook → meta).
    """
    t_tokens = _expand_platform_tokens(_distinctive_tokens(term))
    if not t_tokens or not services:
        return False
    for svc in services:
        s_tokens = _expand_platform_tokens(_distinctive_tokens(str(svc or "")))
        if not s_tokens:
            continue
        # Service identity fully covered by the term ("meta ads melbourne" → Meta Ads).
        if s_tokens <= t_tokens:
            return True
        overlap = t_tokens & s_tokens
        if not overlap:
            continue
        if len(overlap) >= 2:
            return True
        sole = next(iter(overlap))
        # Single distinctive token (meta, google, cro, aeo, klaviyo, …)
        if len(sole) >= 3:
            return True
    return False


def is_prioritization_confirmed(prioritization: dict[str, Any] | None) -> bool:
    p = dict(prioritization or {})
    return bool(p.get("confirmed_at")) and bool(p.get("selected_service_ids"))


def compact_selected_services(
    prioritization: dict[str, Any] | None,
    *,
    service_catalog: list[dict[str, Any]] | None = None,
    ia_nodes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Confirmed selection as id, name, canonical URL, and sub-services.

    Empty when the user has not confirmed a service selection. Later phases use
    this list to optimize only those service pages and to brief blogs per service.
    """
    prio = dict(prioritization or {})
    if not is_prioritization_confirmed(prio):
        return []
    ordered_ids: list[str] = []
    seen_ids: set[str] = set()
    for raw in prio.get("selected_service_ids") or []:
        sid = _norm_key(str(raw))
        if sid and sid not in seen_ids:
            seen_ids.add(sid)
            ordered_ids.append(sid)
    sub_filter = {_norm_key(str(x)) for x in (prio.get("selected_subservice_ids") or [])}
    sub_filter.discard("")
    catalog = [
        row
        for row in (service_catalog or prio.get("service_catalog") or [])
        if isinstance(row, dict)
    ]
    by_id: dict[str, dict[str, Any]] = {}
    for raw in catalog:
        sid = _norm_key(str(raw.get("id") or raw.get("name") or ""))
        if sid not in seen_ids:
            continue
        subs: list[dict[str, Any]] = []
        for sub in raw.get("subservices") or []:
            if not isinstance(sub, dict):
                continue
            sub_id = _norm_key(str(sub.get("id") or sub.get("name") or ""))
            if not sub_id or (sub_filter and sub_id not in sub_filter):
                continue
            subs.append({"id": sub_id, "name": str(sub.get("name") or sub_id)})
        by_id[sid] = {
            "id": sid,
            "name": str(raw.get("name") or sid),
            "url": "",
            "subservices": subs,
        }
    for node in ia_nodes or []:
        if not isinstance(node, dict):
            continue
        sid = _norm_key(str(node.get("service_id") or ""))
        if sid not in seen_ids:
            continue
        entry = by_id.setdefault(
            sid,
            {"id": sid, "name": sid, "url": "", "subservices": []},
        )
        page_type = str(node.get("page_type") or node.get("type") or "").lower()
        url = str(node.get("url") or node.get("path") or "")
        if page_type == "service" and not node.get("subservice_id"):
            if node.get("title"):
                entry["name"] = str(node.get("title"))
            if url:
                entry["url"] = url
            continue
        sub_id = _norm_key(str(node.get("subservice_id") or ""))
        if not sub_id or (sub_filter and sub_id not in sub_filter):
            continue
        matched = False
        for sub in entry["subservices"]:
            if sub["id"] != sub_id:
                continue
            if url:
                sub["url"] = url
            if node.get("title"):
                sub["name"] = str(node.get("title"))
            matched = True
            break
        if not matched:
            sub_row: dict[str, Any] = {
                "id": sub_id,
                "name": str(node.get("title") or sub_id),
            }
            if url:
                sub_row["url"] = url
            entry["subservices"].append(sub_row)
    out: list[dict[str, Any]] = []
    for sid in ordered_ids:
        entry = by_id.get(sid) or {
            "id": sid,
            "name": sid,
            "url": "",
            "subservices": [],
        }
        row: dict[str, Any] = {
            "id": entry["id"],
            "name": entry["name"],
            "subservices": entry["subservices"],
        }
        if entry.get("url"):
            row["url"] = entry["url"]
        out.append(row)
    return out


_STAMP_RE = re.compile(
    r"^(?:\d{1,2}[/.\\-]\d{1,2}[/.\\-]\d{2,4})(?:\s*\((?:draft|final|v\d+)\))?$",
    re.IGNORECASE,
)


def _is_document_stamp(name: str) -> bool:
    """CDD header lines such as '21/09/2026 (draft)' are not offerings."""
    text = " ".join(str(name or "").split()).strip()
    if not text or text.lower() in {"draft", "cdd", "untitled"}:
        return True
    return bool(_STAMP_RE.match(text))


# A city or country stuck on the end of a URL is a location page for a service,
# not a separate offering. "local" stays — it is part of the service name.
_PLACE_TOKENS = frozenset(
    {
        "adelaide",
        "australia",
        "australian",
        "brisbane",
        "canberra",
        "darwin",
        "geelong",
        "hobart",
        "malvern",
        "melbourne",
        "perth",
        "sydney",
        "usa",
        "uk",
    }
)
_LEADING_FLUFF = frozenset({"best", "professional", "top", "award", "winning", "our"})
_ACRONYMS = frozenset(
    {"seo", "ppc", "ai", "cms", "cro", "llm", "roi", "gbp", "aeo", "geo", "sms", "api", "crm", "erp", "b2b", "ui", "ux"}
)
_BRAND_CASE = {
    "woocommerce": "WooCommerce",
    "wordpress": "WordPress",
    "shopify": "Shopify",
    "magento": "Magento",
    "facebook": "Facebook",
    "google": "Google",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "youtube": "YouTube",
    "tiktok": "TikTok",
}
_ARTICLE_SLUG = re.compile(
    r"^(?:what|how|why|when|where|which|who)\b|\b(?:advantages|benefits|guide|tips)\b",
    re.IGNORECASE,
)


def _last_slug(path: str, term: str) -> str:
    segments = [part for part in str(path or "").replace("//", "/").strip("/").split("/") if part]
    raw = segments[-1] if segments else str(term or "")
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def _offering_slug(path: str, term: str) -> str | None:
    """Turn a service URL into the offering it actually sells.

    Page titles are marketing lines ("SEO Services Melbourne | Award-Winning…").
    The path is the offering. Location copies, headline articles, and draft
    duplicates collapse onto that offering instead of becoming their own rows.
    """
    slug = _last_slug(path, term)
    if not slug or _ARTICLE_SLUG.search(slug.replace("-", " ")):
        return None
    parts = [part for part in slug.split("-") if part]
    if len(parts) > 7:
        return None
    if parts and parts[-1].isdigit():
        parts = parts[:-1]
    if parts and parts[-1] in _PLACE_TOKENS:
        parts = parts[:-1]
    if parts and parts[-1] in {"service", "services"}:
        parts = parts[:-1]
    if parts and parts[0] in {"service", "services"}:
        parts = parts[1:]
    while parts and parts[0] in _LEADING_FLUFF:
        parts = parts[1:]
    if not parts or parts in (["service"], ["services"]):
        return None
    return "-".join(parts)


def _offering_key(slug: str) -> str:
    words = [word.replace("isation", "ization").replace("ising", "izing") for word in slug.split("-")]
    return " ".join(words)


def _offering_label(slug: str) -> str:
    label: list[str] = []
    for word in slug.split("-"):
        if word in _ACRONYMS:
            label.append(word.upper())
        elif word in _BRAND_CASE:
            label.append(_BRAND_CASE[word])
        else:
            label.append(word.capitalize())
    return " ".join(label)


def _catalog_kind(page_type: str) -> str:
    kind = str(page_type or "").strip().lower().replace("-", "_")
    if kind in {"sub_service", "subservice"}:
        return "sub_service"
    if kind in {"service", "services", "core_service"}:
        return "service"
    return kind


def build_client_service_catalog(
    *,
    services: list[str],
    website: dict[str, Any] | None = None,
    page_seeds: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Client service tree from website service pages.

    Top-level rows are service pages on the client's site. Nested pages become
    sub-services under that parent. ``services`` (CDD product names) is accepted
    so existing callers keep working and is not used as a row source.
    """
    del services, website
    catalog: dict[str, dict[str, Any]] = {}
    page_seeds = page_seeds or []
    service_seeds: list[dict[str, Any]] = []
    sub_seeds: list[dict[str, Any]] = []
    for seed in page_seeds:
        if not isinstance(seed, dict):
            continue
        kind = _catalog_kind(str(seed.get("page_type") or ""))
        if kind == "service":
            service_seeds.append(seed)
        elif kind == "sub_service":
            sub_seeds.append(seed)

    def _add_service(name: str, path: str | None) -> dict[str, Any] | None:
        slug = _offering_slug(path or "", name)
        if not slug:
            return None
        label = _offering_label(slug)
        if not label or _is_document_stamp(label):
            return None
        sid = _offering_key(slug)
        entry = catalog.get(sid)
        if entry is None:
            entry = {
                "id": sid,
                "name": label,
                "source": "website",
                "priority_tier": "secondary",
                "selected": True,
                "subservices": [],
            }
            if path:
                entry["path"] = path
            catalog[sid] = entry
        elif path and (
            not entry.get("path")
            or len(_last_slug(path, "")) < len(_last_slug(str(entry.get("path") or ""), ""))
        ):
            # Keep the shortest URL when city and draft copies collapse together.
            entry["path"] = path
            entry["name"] = label
        return entry

    for seed in service_seeds:
        _add_service(str(seed.get("term") or ""), str(seed.get("path") or "") or None)

    for seed in sub_seeds:
        sub_slug = _offering_slug(str(seed.get("path") or ""), str(seed.get("term") or ""))
        if not sub_slug:
            continue
        term = _offering_label(sub_slug)
        if not term or _is_document_stamp(term):
            continue
        parent_seg = str(seed.get("parent_segment") or "").strip()
        parent_slug = _offering_slug(parent_seg, parent_seg) if parent_seg else None
        parent_name = _offering_label(parent_slug) if parent_slug else ""
        parent_id = _offering_key(parent_slug) if parent_slug else ""
        parent_entry = catalog.get(parent_id) if parent_id else None
        if parent_entry is None and parent_name:
            for entry in catalog.values():
                if _norm_key(entry["name"]) == _norm_key(parent_name):
                    parent_entry = entry
                    break
        if parent_entry is None and parent_seg and parent_seg.lower() not in _SERVICE_ROOTS:
            parent_entry = _add_service(parent_name, None)
        if parent_entry is None:
            continue
        sub_id = _norm_key(term)
        subs = parent_entry.setdefault("subservices", [])
        if not any(s.get("id") == sub_id for s in subs):
            subs.append(
                {
                    "id": sub_id,
                    "name": term,
                    "source": "website",
                    "selected": True,
                    "path": seed.get("path"),
                    "parent_id": parent_entry["id"],
                }
            )

    rows = list(catalog.values())
    n_primary = max(1, len(rows) // 5) if rows else 0
    for idx, row in enumerate(rows):
        row["priority_tier"] = "primary" if idx < n_primary else "secondary"
    return rows


def _paths_to_service_nodes(urls: list[str]) -> list[dict[str, Any]]:
    """Extract service / sub-service nodes from URL paths."""
    services: dict[str, dict[str, Any]] = {}
    for raw in urls:
        path = urlparse(str(raw)).path or str(raw)
        m = _SERVICE_PATH_RE.match(path.rstrip("/") or "/")
        if not m:
            continue
        l1 = (m.group(1) or "").strip()
        l2 = (m.group(2) or "").strip()
        if not l1:
            continue
        svc_id = _norm_key(l1)
        if svc_id not in services:
            services[svc_id] = {
                "id": svc_id,
                "name": _slug_label(l1),
                "subservices": [],
            }
        if l2:
            sub_id = _norm_key(l2)
            subs = services[svc_id]["subservices"]
            if not any(s.get("id") == sub_id for s in subs):
                subs.append({"id": sub_id, "name": _slug_label(l2), "path": path})
    return list(services.values())


# Competitor site structure changes slowly; a day is plenty and keeps the
# checkpoint card responsive.
_SERVICE_TREE_TTL = 24 * 3600


def _service_tree_cache_key(
    client_id: str, competitors: list[dict[str, Any]], max_urls_each: int
) -> str:
    """Key bound to the exact competitor set, so a change invalidates it."""
    import hashlib

    parts = sorted(
        _norm_key(str(c.get("domain") or extract_domain(str(c.get("url") or "")) or ""))
        for c in competitors
    )
    fp = hashlib.sha1(
        ("|".join(parts) + f"#{max_urls_each}").encode("utf-8")
    ).hexdigest()[:16]
    return f"searchfit:svc_tree:v1:{client_id}:{fp}"


async def discover_competitor_service_trees(
    competitors: list[dict[str, Any]],
    *,
    max_competitors: int | None = None,
    max_urls_each: int = 35,
    client_id: str | None = None,
) -> list[dict[str, Any]]:
    """Lightweight sitemap discovery — paths only, no HTML fetch.

    Runs one discovery per competitor concurrently instead of sequentially —
    latency used to be the sum of every competitor's crawl instead of the max.

    Results are cached per client and competitor set. This is called from a GET
    the UI opens as a checkpoint card; uncached it re-crawled every competitor
    on each view and sat at the per-competitor timeout, ~21s in practice. The
    key includes a fingerprint of the competitor set so adding or removing a
    competitor invalidates it, and a fully failed crawl is never cached — that
    would hide a transient outage for the whole TTL.
    """
    selected = (
        competitors if max_competitors is None else competitors[:max_competitors]
    )
    cache_key = None
    if client_id:
        cache_key = _service_tree_cache_key(client_id, selected, max_urls_each)
        cached = await cache_get(cache_key)
        if isinstance(cached, list):
            return cached

    async def _discover_one(comp: dict[str, Any]) -> dict[str, Any] | None:
        domain = comp.get("domain") or extract_domain(comp.get("url") or "")
        if not domain:
            return None
        start = comp.get("url") or f"https://{domain}"
        try:
            urls = await asyncio.wait_for(
                discover_site_urls(start, max_pages=max_urls_each),
                timeout=20,
            )
        except Exception:  # noqa: BLE001
            urls = []
        nodes = _paths_to_service_nodes(urls)
        return {
            "competitor_id": _norm_key(comp.get("name") or domain),
            "name": comp.get("name") or domain,
            "domain": domain,
            "url_count": len(urls),
            "services": nodes,
        }

    results = await asyncio.gather(*[_discover_one(comp) for comp in selected])
    trees = [r for r in results if r is not None]
    # Only cache a scan that actually saw something. An all-empty result means
    # every competitor timed out or failed, and freezing that for the TTL would
    # turn a blip into a day of empty cards.
    if cache_key and any(t.get("url_count") for t in trees):
        await cache_set(cache_key, trees, _SERVICE_TREE_TTL)
    return trees


def merge_competitor_subservices(
    catalog: list[dict[str, Any]],
    *,
    competitor_tree: dict[str, Any] | None,
    competitor_trees: list[dict[str, Any]] | None = None,
    adopt_service_ids: list[str] | None = None,
    candidate_min_competitors: int = 2,
) -> list[dict[str, Any]]:
    """Merge competitor sub-services and consensus top-level service gaps.

    Existing client services receive competitor sub-services immediately.
    Unmatched top-level services remain opt-in candidates unless at least the
    requested number of distinct competitors expose the same service.
    """
    trees = list(competitor_trees or ([] if not competitor_tree else [competitor_tree]))
    if not trees:
        return catalog
    adopt = {_norm_key(x) for x in (adopt_service_ids or [])}
    by_name = {_norm_key(s["name"]): s for s in catalog}
    unmatched: dict[str, dict[str, Any]] = {}

    def _service_is_adopted(service: dict[str, Any]) -> bool:
        return bool(
            {
                _norm_key(service.get("id") or ""),
                _norm_key(service.get("name") or ""),
            }
            & adopt
        )

    # Promote previously accepted candidates before attaching current evidence.
    for service in catalog:
        if _service_is_adopted(service) and (
            service.get("candidate_new_service")
            or str(service.get("source") or "").lower() in {"competitor", "adopted"}
        ):
            prior_source = str(service.get("source") or "")
            service["source"] = "adopted"
            service["selected"] = True
            service["priority_tier"] = service.get("priority_tier") or "secondary"
            service["candidate_new_service"] = False
            if prior_source == "competitor":
                service["adopted_from"] = "competitor_consensus"

    for tree in trees:
        competitor_name = str(
            tree.get("name") or tree.get("domain") or tree.get("competitor_id") or "competitor"
        ).strip()
        competitor_key = _norm_key(
            str(tree.get("competitor_id") or tree.get("domain") or competitor_name)
        )
        for comp_svc in tree.get("services") or []:
            cid = _norm_key(comp_svc.get("name") or "")
            target = by_name.get(cid)
            if not target:
                if not cid:
                    continue
                bucket = unmatched.setdefault(
                    cid,
                    {
                        "id": comp_svc.get("id") or cid,
                        "name": comp_svc.get("name") or cid,
                        "competitors": {},
                        "subservices": {},
                    },
                )
                bucket["competitors"][competitor_key] = competitor_name
                for sub in comp_svc.get("subservices") or []:
                    sub_key = _norm_key(sub.get("id") or sub.get("name") or "")
                    if not sub_key:
                        continue
                    merged_sub = bucket["subservices"].setdefault(
                        sub_key,
                        {
                            **sub,
                            "id": sub.get("id") or sub_key,
                            "name": sub.get("name") or sub_key,
                            "source": "competitor",
                            "selected": False,
                            "competitors": [],
                        },
                    )
                    if competitor_name and competitor_name not in merged_sub["competitors"]:
                        merged_sub["competitors"].append(competitor_name)
                continue
            existing = {s.get("id"): s for s in target.get("subservices") or []}
            for sub in comp_svc.get("subservices") or []:
                sid = sub.get("id")
                if not sid:
                    continue
                if sid in existing:
                    prior = existing[sid]
                    competitors = list(prior.get("competitors") or [])
                    name = tree.get("name")
                    if name and name not in competitors:
                        competitors.append(name)
                    prior["competitors"] = competitors
                    continue
                target.setdefault("subservices", []).append(
                    {
                        **sub,
                        "source": "competitor",
                        "selected": False,
                        "competitor": tree.get("name"),
                        "competitors": [tree.get("name")] if tree.get("name") else [],
                    }
                )

    threshold = max(1, int(candidate_min_competitors or 2))
    for candidate in unmatched.values():
        competitor_names = [
            name for name in candidate["competitors"].values() if name
        ]
        if len(candidate["competitors"]) < threshold:
            continue
        service_id = candidate.get("id") or _norm_key(candidate.get("name") or "")
        adopted = bool(
            {
                _norm_key(service_id),
                _norm_key(candidate.get("name") or ""),
            }
            & adopt
        )
        catalog.append(
            {
                "id": service_id,
                "name": candidate.get("name"),
                "source": "adopted" if adopted else "competitor",
                "selected": adopted,
                "priority_tier": "secondary" if adopted else None,
                "candidate_new_service": not adopted,
                "adopted_from": "competitor_consensus" if adopted else None,
                "competitor_count": len(candidate["competitors"]),
                "competitors": competitor_names,
                "subservices": list(candidate["subservices"].values()),
            }
        )
    return catalog


def append_saved_manual_services(
    catalog: list[dict[str, Any]],
    existing: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Keep services an operator typed in, across reloads of this card."""
    seen = {
        key
        for row in catalog
        for key in (_norm_key(row.get("id") or ""), _norm_key(row.get("name") or ""))
        if key
    }
    for row in (existing or {}).get("service_catalog") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "").lower() != "manual":
            continue
        name = str(row.get("name") or "").strip()
        if not name or _is_document_stamp(name):
            continue
        sid = _norm_key(row.get("id") or name)
        if not sid or sid in seen or _norm_key(name) in seen:
            continue
        seen.add(sid)
        seen.add(_norm_key(name))
        catalog.append(
            {
                "id": sid,
                "name": name,
                "source": "manual",
                "selected": True,
                "priority_tier": "secondary",
                "candidate_new_service": False,
                "subservices": [
                    sub
                    for sub in (row.get("subservices") or [])
                    if isinstance(sub, dict)
                ],
            }
        )
    return catalog


def default_selection(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """80/20 default — client/adopted services on; new candidates opt-in."""
    active = [
        svc
        for svc in catalog
        if not bool(svc.get("candidate_new_service"))
        and str(svc.get("source") or "").lower() != "competitor"
    ]
    n_primary = max(1, len(active) // 5) if active else 0
    service_ids: list[str] = []
    sub_ids: list[str] = []
    active_index = 0
    for svc in catalog:
        is_candidate = bool(svc.get("candidate_new_service")) or (
            str(svc.get("source") or "").lower() == "competitor"
        )
        svc["selected"] = not is_candidate
        svc["priority_tier"] = (
            None
            if is_candidate
            else "primary" if active_index < n_primary else "secondary"
        )
        if is_candidate:
            for sub in svc.get("subservices") or []:
                sub["selected"] = False
            continue
        service_ids.append(svc["id"])
        for sub in svc.get("subservices") or []:
            sub["selected"] = True
            sub_ids.append(sub["id"])
        active_index += 1
    return {
        "selected_service_ids": service_ids,
        "selected_subservice_ids": sub_ids,
        "primary_service_ids": [s["id"] for s in active[:n_primary]],
    }


def build_prioritization_pack(
    *,
    client_name: str,
    services: list[str],
    website: dict[str, Any] | None = None,
    page_seeds: list[dict[str, Any]] | None = None,
    competitors: list[dict[str, Any]] | None = None,
    competitor_trees: list[dict[str, Any]] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = build_client_service_catalog(
        services=services,
        website=website,
        page_seeds=page_seeds,
    )
    trees = list(competitor_trees or [])
    existing = dict(existing or {})
    if trees:
        # One competitor is enough. The industry list is every service those
        # sites offer, not only the ones that show up twice.
        catalog = merge_competitor_subservices(
            catalog,
            competitor_tree=None,
            competitor_trees=trees,
            adopt_service_ids=list(existing.get("adopt_competitor_service_ids") or []),
            candidate_min_competitors=1,
        )
    catalog = append_saved_manual_services(catalog, existing)
    defaults = default_selection(catalog)
    return {
        "title": f"Service prioritization: {client_name}",
        "client": client_name,
        "principle": (
            "80/20 — choose among every service available in this industry, "
            "including ones not on the client's website yet."
        ),
        "service_catalog": catalog,
        "competitor_trees": trees,
        "competitors": [
            {"name": c.get("name"), "domain": c.get("domain") or extract_domain(c.get("url") or "")}
            for c in (competitors or [])[:8]
        ],
        "defaults": defaults,
        "existing_selection": {
            k: existing.get(k)
            for k in (
                "selected_service_ids",
                "selected_subservice_ids",
                "primary_service_ids",
                "competitor_tree_id",
                "adopt_competitor_service_ids",
                "confirmed_at",
            )
            if existing.get(k) is not None
        },
    }


def apply_service_selection(
    services: list[str],
    seed_targets: dict[str, dict[str, Any]],
    prioritization: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Filter CDD services and seed targets to user selection.

    Service / sub-service roots must be explicitly selected. Keyword and page
    roots are kept only when they match a selected service — otherwise SEO /
    email / other unselected themes leak into expansion after the gate.

    Never falls back to the full unselected service list when a confirmation
    is present — an ID mismatch must be resolved via the catalog, not ignored.
    """
    selected_ids = [
        str(x).strip()
        for x in (prioritization.get("selected_service_ids") or [])
        if str(x).strip()
    ]
    sel_services = _selection_keys(*selected_ids)
    sel_subs = _selection_keys(
        *[str(x) for x in (prioritization.get("selected_subservice_ids") or [])]
    )
    if not sel_services:
        return services, seed_targets

    def _is_selected_service(value: str) -> bool:
        return bool(_selection_keys(value) & sel_services)

    # Resolve display names for selected IDs from the catalog the operator saw.
    catalog_names: list[str] = []
    seen_catalog: set[str] = set()
    for row in prioritization.get("service_catalog") or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        if _is_selected_service(sid) or _is_selected_service(name):
            key = _loose_key(name)
            if key and key not in seen_catalog:
                seen_catalog.add(key)
                catalog_names.append(name)

    filtered_services = [s for s in services if _is_selected_service(s)]
    if not filtered_services and catalog_names:
        # CDD product list naming differs from catalog ids — use catalog names.
        filtered_services = list(catalog_names)
    elif catalog_names:
        # Ensure every selected catalog service is present even if products[]
        # used a slightly different label.
        have = {_loose_key(s) for s in filtered_services}
        for name in catalog_names:
            key = _loose_key(name)
            if key and key not in have:
                filtered_services.append(name)
                have.add(key)

    if not filtered_services:
        # Last resort: map selected ids back through token match, but never
        # silently expand to every CDD service (that ignores the operator).
        filtered_services = [
            s
            for s in services
            if any(term_matches_selected_services(s, [sid]) for sid in selected_ids)
        ]

    filtered_targets: dict[str, dict[str, Any]] = {}
    for key, meta in seed_targets.items():
        ttype = str(meta.get("target_type") or "keyword")
        target = str(meta.get("target") or key)
        if ttype == "service":
            if _is_selected_service(target) or _is_selected_service(key):
                filtered_targets[key] = meta
                continue
            if filtered_services and term_matches_selected_services(
                target, filtered_services
            ):
                filtered_targets[key] = meta
            continue
        if ttype == "sub_service":
            parent_seg = str(meta.get("parent_segment") or "")
            parent_match = parent_seg and (
                _is_selected_service(_slug_label(parent_seg))
                or (
                    filtered_services
                    and term_matches_selected_services(parent_seg, filtered_services)
                )
            )
            if _selection_keys(target) & sel_subs or (not sel_subs and parent_match):
                filtered_targets[key] = meta
            continue
        if ttype in ("page", "keyword"):
            if filtered_services and term_matches_selected_services(
                target, filtered_services
            ):
                filtered_targets[key] = meta
            continue

    return filtered_services, filtered_targets


def expand_vertical_subservice_seeds(
    seed_roots: list[str],
    seed_targets: dict[str, dict[str, Any]],
    *,
    prioritization: dict[str, Any],
    service_catalog: list[dict[str, Any]] | None = None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Add Ahrefs roots for selected sub-services not already in the seed map."""
    catalog = list(service_catalog or [])
    if not catalog or not prioritization.get("confirmed_at"):
        return seed_roots, seed_targets
    sel_services = {_norm_key(x) for x in (prioritization.get("selected_service_ids") or [])}
    sel_subs = {_norm_key(x) for x in (prioritization.get("selected_subservice_ids") or [])}
    if not sel_services:
        return seed_roots, seed_targets

    roots = list(seed_roots)
    targets = dict(seed_targets)
    for svc in catalog:
        sid = _norm_key(str(svc.get("id") or svc.get("name") or ""))
        if sid not in sel_services:
            continue
        for sub in svc.get("subservices") or []:
            sub_id = _norm_key(str(sub.get("id") or sub.get("name") or ""))
            if sel_subs and sub_id not in sel_subs:
                continue
            term = str(sub.get("name") or sub.get("path") or sub.get("id") or "").strip()
            if not term or len(term) < 3:
                continue
            key = term.lower()
            if key in targets:
                continue
            targets[key] = {
                "target": term,
                "target_type": "sub_service",
                "parent_segment": svc.get("name"),
                "page_path": sub.get("path"),
                "source": sub.get("source") or "vertical_expansion",
            }
            roots.append(term)
    return roots, targets


def competitor_tree_for_architecture(
    commercial: dict[str, Any] | None,
    demand: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the combined Phase 4 competitor org chart for Phase 6 IA."""
    prio = dict((commercial or {}).get("service_prioritization") or {})
    if not prio.get("competitor_tree_id"):
        prio = dict((demand or {}).get("service_prioritization") or prio)
    trees = list(
        prio.get("competitor_trees")
        or (demand or {}).get("competitor_trees")
        or []
    )
    if not trees:
        return None
    # Keep legacy saved selections readable, but new flows always use every
    # competitor tree because the UI no longer offers a single-competitor
    # selector.
    tree_id = prio.get("competitor_tree_id")
    if tree_id:
        selected = next((t for t in trees if t.get("competitor_id") == tree_id), None)
        if selected:
            return selected

    services: dict[str, dict[str, Any]] = {}
    for tree in trees:
        for service in tree.get("services") or []:
            key = _norm_key(service.get("name") or service.get("id") or "")
            if not key:
                continue
            merged = services.setdefault(
                key,
                {
                    "id": service.get("id") or key,
                    "name": service.get("name") or key,
                    "subservices": [],
                },
            )
            existing = {s.get("id"): s for s in merged["subservices"]}
            for sub in service.get("subservices") or []:
                sid = sub.get("id")
                if not sid:
                    continue
                if sid not in existing:
                    merged["subservices"].append(
                        {
                            **sub,
                            "competitor": tree.get("name"),
                        }
                    )
    return {
        "competitor_id": "all",
        "name": "All Phase 4 competitors",
        "services": list(services.values()),
    }


def ia_nodes_from_competitor_tree(
    tree: dict[str, Any] | None,
    *,
    domain: str,
    selected_service_ids: list[str] | None = None,
    selected_subservice_ids: list[str] | None = None,
    adopt_service_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build canonical service/sub-service IA nodes from a service catalog tree.

    The tree may be client-, website-, or competitor-sourced.  All sources use
    the same top-level URL convention so merging them cannot create a parallel
    ``/services/`` taxonomy.
    """
    if not tree:
        return []
    sel = {_norm_key(x) for x in (selected_service_ids or [])}
    sel_subs = {_norm_key(x) for x in (selected_subservice_ids or [])}
    adopt = {_norm_key(x) for x in (adopt_service_ids or [])}
    base = f"https://{domain}".rstrip("/")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(
        path: str,
        *,
        title: str,
        page_type: str,
        parent: str,
        depth: int,
        service_id: str,
        subservice_id: str | None = None,
    ) -> None:
        loc = path if path.startswith("/") else f"/{path}"
        loc = loc.lower().rstrip("/") + ("" if loc == "/" else "")
        if not loc or loc in seen:
            return
        seen.add(loc)
        nodes.append(
            {
                "url": loc,
                "path": loc if loc.endswith("/") else f"{loc}/",
                "title": title,
                "page_type": page_type,
                "type": page_type,
                "parent": parent,
                "depth": depth,
                "service_id": service_id,
                "subservice_id": subservice_id,
                "parent_service_id": service_id if subservice_id else None,
                "absolute_url": f"{base}{loc}",
                "from_competitor_ia": True,
                "competitor_source": tree.get("name") or tree.get("domain"),
            }
        )

    for svc in tree.get("services") or []:
        name = str(svc.get("name") or "").strip()
        sid = _norm_key(str(svc.get("id") or name))
        if sel and sid not in sel:
            continue
        if (
            adopt
            and sid not in adopt
            and sel
            and str(svc.get("source") or "").lower() == "competitor"
        ):
            continue
        svc_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"
        svc_path = f"/{svc_slug}/"
        _add(
            svc_path,
            title=name,
            page_type="service",
            parent="/",
            depth=1,
            service_id=sid,
        )
        for sub in svc.get("subservices") or []:
            sub_name = str(sub.get("name") or "").strip()
            sub_id = _norm_key(str(sub.get("id") or sub_name))
            if sel_subs and sub_id not in sel_subs:
                continue
            sub_slug = re.sub(r"[^a-z0-9]+", "-", sub_name.lower()).strip("-")
            if not sub_slug:
                continue
            sub_path = f"/{svc_slug}/{sub_slug}/"
            _add(
                sub_path,
                title=sub_name,
                page_type="subservice",
                parent=svc_path,
                depth=2,
                service_id=sid,
                subservice_id=sub_id,
            )
    return nodes


def top_priority_service_id(prioritization: dict[str, Any] | None) -> str | None:
    """The single #1 service chosen at the selection gate, if any."""
    raw = dict(prioritization or {}).get("top_priority_service_id")
    value = str(raw or "").strip()
    return value or None


def prioritize_top_service_roots(
    seed_roots: list[str],
    seed_targets: dict[str, dict[str, Any]],
    prioritization: dict[str, Any] | None,
) -> list[str]:
    """Move the #1 service's seed roots to the front of the expansion queue.

    Seed roots are capped, so order decides what actually gets expanded. The
    service the client cares most about should never be the one that falls off
    the end of the list.
    """
    top = top_priority_service_id(prioritization)
    if not top:
        return seed_roots
    wanted = _norm_key(top)

    def _is_top(root: str) -> bool:
        meta = seed_targets.get(str(root).lower()) or {}
        return wanted in {
            _norm_key(str(root)),
            _norm_key(str(meta.get("target") or "")),
            _norm_key(str(meta.get("parent_segment") or "")),
        }

    leading = [r for r in seed_roots if _is_top(r)]
    if not leading:
        return seed_roots
    return leading + [r for r in seed_roots if r not in leading]


def cap_seed_roots_preserving_selected_services(
    seed_roots: list[str],
    seed_targets: dict[str, dict[str, Any]],
    *,
    selected_services: list[str],
    max_seed_roots: int,
) -> list[str]:
    """Keep every selected service root; spend the remaining budget on extras.

    Subservice / page / keyword roots can still be truncated by
    ``max_seed_roots``, but a confirmed service selection must never lose a
    service to that soft cap — otherwise the multi-mode report silently omits
    services the operator just picked.
    """
    selected = [str(s).strip() for s in selected_services if str(s).strip()]
    selected_keys = {_norm_key(s) for s in selected}
    budget = max(0, int(max_seed_roots))
    if not selected_keys:
        return list(dict.fromkeys(str(r).strip() for r in seed_roots if str(r).strip()))[:budget]

    def _is_selected_service_root(root: str) -> bool:
        meta = seed_targets.get(str(root).lower()) or {}
        ttype = str(meta.get("target_type") or "").lower()
        keys = {
            _norm_key(root),
            _norm_key(str(meta.get("target") or "")),
        }
        if not (keys & selected_keys):
            return False
        # Sub-service roots under a selected parent are extras, not the
        # service shell the multi-mode report groups by.
        if ttype == "sub_service":
            return False
        return ttype in {"", "service", "keyword"} or _norm_key(root) in selected_keys

    service_roots: list[str] = []
    other_roots: list[str] = []
    seen: set[str] = set()
    for raw in seed_roots:
        root = str(raw or "").strip()
        key = root.lower()
        if not root or key in seen:
            continue
        seen.add(key)
        if _is_selected_service_root(root):
            service_roots.append(root)
        else:
            other_roots.append(root)

    have = {
        _norm_key(r)
        for r in service_roots
    } | {
        _norm_key(str((seed_targets.get(str(r).lower()) or {}).get("target") or ""))
        for r in service_roots
    }
    for svc in selected:
        nk = _norm_key(svc)
        if nk and nk not in have:
            service_roots.append(svc)
            have.add(nk)

    # Selected services always survive. Fill leftover budget with other roots.
    if len(service_roots) >= budget:
        return service_roots
    remaining = budget - len(service_roots)
    return service_roots + other_roots[:remaining]


def confirm_prioritization(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize and stamp confirmation on a prioritization payload."""
    out = dict(payload or {})
    out["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    out["selected_service_ids"] = list(out.get("selected_service_ids") or [])
    out["selected_subservice_ids"] = list(out.get("selected_subservice_ids") or [])
    out["primary_service_ids"] = list(out.get("primary_service_ids") or [])
    selected = {_norm_key(x) for x in out["selected_service_ids"]}
    requested_adoptions = {
        _norm_key(x) for x in (out.get("adopt_competitor_service_ids") or [])
    }
    adopted_ids: list[str] = []
    promoted_catalog: list[dict[str, Any]] = []
    for raw_service in out.get("service_catalog") or []:
        if not isinstance(raw_service, dict):
            continue
        service = dict(raw_service)
        service_id = str(service.get("id") or service.get("name") or "").strip()
        service_keys = {
            _norm_key(service_id),
            _norm_key(service.get("name") or ""),
        }
        should_adopt = bool(
            service_keys & requested_adoptions
            and service_keys & selected
            and (
                service.get("candidate_new_service")
                or str(service.get("source") or "").lower()
                in {"competitor", "adopted"}
            )
        )
        if should_adopt:
            service["source"] = "adopted"
            service["candidate_new_service"] = False
            service["adopted_from"] = "competitor_consensus"
            service["selected"] = True
            service["priority_tier"] = (
                "primary"
                if service_keys
                & {_norm_key(x) for x in out["primary_service_ids"]}
                else "secondary"
            )
            adopted_ids.append(service_id)
        promoted_catalog.append(service)
    out["service_catalog"] = promoted_catalog
    out["adopt_competitor_service_ids"] = adopted_ids

    # The #1 service only means something if it is actually in scope; keeping an
    # unselected one would steer seeding toward a service nobody asked for.
    top = top_priority_service_id(out)
    if top and _norm_key(top) in selected:
        out["top_priority_service_id"] = top
        if not any(_norm_key(x) == _norm_key(top) for x in out["primary_service_ids"]):
            out["primary_service_ids"] = [top, *out["primary_service_ids"]]
    else:
        out["top_priority_service_id"] = None
    if out.get("competitor_trees"):
        out["competitor_trees"] = list(out.get("competitor_trees") or [])
    return out
