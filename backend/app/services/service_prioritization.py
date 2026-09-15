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


def is_prioritization_confirmed(prioritization: dict[str, Any] | None) -> bool:
    p = dict(prioritization or {})
    return bool(p.get("confirmed_at")) and bool(p.get("selected_service_ids"))


def build_client_service_catalog(
    *,
    services: list[str],
    website: dict[str, Any] | None = None,
    page_seeds: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Client service tree from CDD products + Phase 3 page hierarchy."""
    catalog: dict[str, dict[str, Any]] = {}
    page_seeds = page_seeds or []

    for idx, svc in enumerate(services):
        name = str(svc or "").strip()
        if not name:
            continue
        sid = _norm_key(name)
        catalog[sid] = {
            "id": sid,
            "name": name,
            "source": "cdd",
            "priority_tier": "primary" if idx < max(1, len(services) // 5) else "secondary",
            "selected": True,
            "subservices": [],
        }

    for seed in page_seeds:
        ptype = str(seed.get("page_type") or "")
        if ptype not in ("sub_service", "service"):
            continue
        term = str(seed.get("term") or "").strip()
        if not term:
            continue
        parent_seg = str(seed.get("parent_segment") or "").strip()
        parent_name = _slug_label(parent_seg) if parent_seg else None
        parent_id = _norm_key(parent_name) if parent_name else None

        if ptype == "service" and _norm_key(term) not in catalog:
            catalog[_norm_key(term)] = {
                "id": _norm_key(term),
                "name": term,
                "source": "website",
                "priority_tier": "secondary",
                "selected": True,
                "subservices": [],
                "path": seed.get("path"),
            }
            continue

        if ptype != "sub_service":
            continue

        # Attach sub-service under parent service when possible
        parent_entry = None
        if parent_id and parent_id in catalog:
            parent_entry = catalog[parent_id]
        else:
            for entry in catalog.values():
                if parent_name and _norm_key(entry["name"]) == _norm_key(parent_name):
                    parent_entry = entry
                    break
        if not parent_entry and catalog:
            parent_entry = next(iter(catalog.values()))
        if not parent_entry:
            parent_entry = {
                "id": parent_id or "services",
                "name": parent_name or "Services",
                "source": "website",
                "priority_tier": "secondary",
                "selected": True,
                "subservices": [],
            }
            catalog[parent_entry["id"]] = parent_entry

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

    return list(catalog.values())


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


async def discover_competitor_service_trees(
    competitors: list[dict[str, Any]],
    *,
    max_competitors: int | None = None,
    max_urls_each: int = 35,
) -> list[dict[str, Any]]:
    """Lightweight sitemap discovery — paths only, no HTML fetch.

    Runs one discovery per competitor concurrently instead of sequentially —
    latency used to be the sum of every competitor's crawl instead of the max.
    """

    async def _discover_one(comp: dict[str, Any]) -> dict[str, Any] | None:
        domain = comp.get("domain") or extract_domain(comp.get("url") or "")
        if not domain:
            return None
        start = comp.get("url") or f"https://{domain}"
        try:
            urls = await discover_site_urls(start, max_pages=max_urls_each)
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

    results = await asyncio.gather(
        *[
            _discover_one(comp)
            for comp in (
                competitors
                if max_competitors is None
                else competitors[:max_competitors]
            )
        ]
    )
    return [r for r in results if r is not None]


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

    threshold = max(2, int(candidate_min_competitors or 2))
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
        catalog = merge_competitor_subservices(
            catalog,
            competitor_tree=None,
            competitor_trees=trees,
            adopt_service_ids=list(existing.get("adopt_competitor_service_ids") or []),
        )
    defaults = default_selection(catalog)
    return {
        "title": f"Service prioritization: {client_name}",
        "client": client_name,
        "principle": "80/20 — select primary services first, then expand into sub-services.",
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
    """Filter CDD services and seed targets to user selection."""
    sel_services = {_norm_key(x) for x in (prioritization.get("selected_service_ids") or [])}
    sel_subs = {_norm_key(x) for x in (prioritization.get("selected_subservice_ids") or [])}
    if not sel_services:
        return services, seed_targets

    filtered_services = [s for s in services if _norm_key(s) in sel_services]
    if not filtered_services:
        filtered_services = services

    filtered_targets: dict[str, dict[str, Any]] = {}
    for key, meta in seed_targets.items():
        ttype = str(meta.get("target_type") or "keyword")
        target = str(meta.get("target") or key)
        if ttype == "service":
            if _norm_key(target) in sel_services:
                filtered_targets[key] = meta
            continue
        if ttype == "sub_service":
            parent_seg = str(meta.get("parent_segment") or "")
            parent_match = parent_seg and _norm_key(_slug_label(parent_seg)) in sel_services
            if _norm_key(target) in sel_subs or (not sel_subs and parent_match):
                filtered_targets[key] = meta
            continue
        if ttype in ("page", "keyword"):
            filtered_targets[key] = meta

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
    if out.get("competitor_trees"):
        out["competitor_trees"] = list(out.get("competitor_trees") or [])
    return out
