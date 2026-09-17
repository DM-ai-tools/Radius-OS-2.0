"""Site Architecture skill — click-depth audit + IA blueprint from Phase 5 clusters."""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from typing import Any
from urllib.parse import urlparse

from app.agents.prompts import load_skill_file
from app.integrations.llm import synthesize_json
from app.integrations.web_fetch import absolute_links, fetch_url, parse_html
from app.logging_config import get_logger

log = get_logger("site_architecture")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "page"


def _norm_url(url: str) -> str:
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path
    return f"{(p.scheme or 'https').lower()}://{(p.netloc or '').lower()}{path}".rstrip("/") or (
        f"{(p.scheme or 'https').lower()}://{(p.netloc or '').lower()}/"
    )


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _path_n(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    path = urlparse(raw).path if "://" in raw else (raw if raw.startswith("/") else f"/{raw}")
    path = path.lower() or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _parent_of(path: str) -> str:
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) <= 1:
        return "/"
    return "/" + "/".join(parts[:-1])


def _normalize_url_tree(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every IA node has url + depth so validation and planning joins stay stable."""
    out: list[dict[str, Any]] = []
    for node in tree:
        if not isinstance(node, dict):
            continue
        row = dict(node)
        loc = _path_n(str(row.get("url") or row.get("path") or ""))
        if not loc:
            continue
        row["url"] = loc
        row["path"] = row.get("path") or loc
        if row.get("depth") is None:
            row["depth"] = 0 if loc == "/" else max(1, loc.strip("/").count("/") + 1)
        if not row.get("parent"):
            row["parent"] = _parent_of(loc)
        out.append(row)
    return out


def _nodes_from_strategy_queue(seo_strategy: dict[str, Any]) -> list[dict[str, Any]]:
    """Target tree rows from Phase 6 priority_queue — join key for Phase 9."""
    queue = (
        seo_strategy.get("combined_priority_queue")
        or seo_strategy.get("priority_queue")
        or seo_strategy.get("priority_pages")
        or []
    )
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in queue:
        if not isinstance(item, dict):
            continue
        if str(item.get("priority") or item.get("priority_tier") or "").lower() == "avoid":
            continue
        raw = item.get("url") or item.get("suggested_url") or item.get("path")
        kw = item.get("keyword") or item.get("primary_keyword") or item.get("title")
        if not raw and kw:
            raw = f"/blog/{_slug(str(kw))}"
        path = _path_n(str(raw) if raw else "")
        if not path or path in seen:
            continue
        seen.add(path)
        parent = item.get("parent") or item.get("parent_url") or _parent_of(path)
        parent_n = _path_n(str(parent)) if parent else "/"
        try:
            depth = int(item.get("depth")) if item.get("depth") is not None else max(1, path.count("/"))
        except (TypeError, ValueError):
            depth = max(1, path.count("/"))
        ptype = str(item.get("page_type") or item.get("type") or item.get("content_type") or "article").lower()
        if ptype in ("guide", "post", "page", "planned"):
            ptype = "article"
        nodes.append(
            {
                "url": path,
                "path": path,
                "parent": parent_n,
                "depth": depth,
                "page_type": ptype,
                "type": ptype,
                "title": item.get("title"),
                "keyword": kw,
                "primary_keyword": kw,
                "cluster": item.get("cluster") or item.get("pillar"),
                "indexable": True,
                "from_strategy": True,
            }
        )
    return nodes


async def measure_click_depth(
    seed_url: str,
    *,
    page_cap: int = 80,
) -> dict[str, Any]:
    """Lightweight in-app BFS from homepage — click depth, not folder count."""
    seed = seed_url if "://" in seed_url else f"https://{seed_url}"
    host = _host(seed)
    seen: set[str] = set()
    q: deque[tuple[str, int]] = deque([(seed, 0)])
    pages: list[dict[str, Any]] = []
    inbound: dict[str, set[str]] = defaultdict(set)

    while q and len(pages) < page_cap:
        url, depth = q.popleft()
        key = _norm_url(url)
        if key in seen:
            continue
        seen.add(key)
        try:
            fetched = await fetch_url(url)
        except Exception as exc:  # noqa: BLE001
            pages.append({"url": url, "status": 0, "depth": depth, "error": str(exc)[:120]})
            continue
        final = str(fetched.get("url") or url)
        status = int(fetched.get("status_code") or 0)
        html = fetched.get("text") or ""
        parser = parse_html(html)
        links = absolute_links(final, parser.hrefs)
        internal = [
            u
            for u in links
            if _host(u) == host and not u.startswith(("mailto:", "tel:", "javascript:"))
        ]
        pages.append(
            {
                "url": final,
                "status": status,
                "depth": depth,
                "title": parser.title,
                "canonical": parser.canonical,
                "path": urlparse(final).path,
                "out_links": len(internal),
            }
        )
        src = _norm_url(final)
        if status and status < 400:
            for link in internal:
                dst = _norm_url(link.split("#")[0])
                inbound[dst].add(src)
                if dst not in seen and len(seen) + len(q) < page_cap * 2:
                    q.append((link.split("#")[0], depth + 1))

    depths = [int(p.get("depth") or 0) for p in pages]
    orphans = [
        p
        for p in pages
        if int(p.get("depth") or 0) > 0
        and _norm_url(str(p.get("url") or "")) not in inbound
        and int(p.get("status") or 0) == 200
    ]
    depth_4 = [p for p in pages if int(p.get("depth") or 0) >= 4]

    # Phantom path prefixes
    page_paths = {urlparse(str(p.get("url") or "")).path.rstrip("/") or "/" for p in pages}
    phantoms: list[str] = []
    for p in pages:
        parts = [x for x in urlparse(str(p.get("url") or "")).path.strip("/").split("/") if x]
        for i in range(1, len(parts)):
            seg = "/" + "/".join(parts[:i])
            if seg.rstrip("/") not in {x.rstrip("/") for x in page_paths}:
                if seg not in phantoms and not re.search(r"/20\d{2}$", seg):
                    phantoms.append(seg)

    issues = []
    if depth_4:
        issues.append(
            {
                "issue": "pages_beyond_3_clicks",
                "count": len(depth_4),
                "severity": "high" if len(depth_4) > 5 else "medium",
                "note": "Fix with nav/hubs — not by flattening folders (Google doesn't count slashes)",
            }
        )
    if orphans:
        issues.append(
            {
                "issue": "orphan_pages",
                "count": len(orphans),
                "severity": "high",
                "note": "No internal path from crawl seed — hand off remediation to Internal Linking",
            }
        )
    if phantoms:
        issues.append(
            {
                "issue": "phantom_directories",
                "count": len(phantoms),
                "severity": "medium",
                "note": "Breadcrumb/URL hierarchy hole — add hub pages at intermediate paths",
            }
        )

    return {
        "urls_crawled": len(pages),
        "status_200": sum(1 for p in pages if int(p.get("status") or 0) == 200),
        "max_click_depth": max(depths) if depths else 0,
        "within_3_clicks": sum(1 for d in depths if d <= 3),
        "depth_4_plus": len(depth_4),
        "orphans": len(orphans),
        "phantom_dirs": len(phantoms),
        "redirect_chains": 0,
        "canonical_conflicts": sum(
            1
            for p in pages
            if p.get("canonical")
            and _norm_url(str(p.get("canonical"))) != _norm_url(str(p.get("url") or ""))
        ),
        "issues": issues,
        "pages_depth_4_plus": [
            {"url": p.get("url"), "depth": p.get("depth"), "title": p.get("title")}
            for p in depth_4[:25]
        ],
        "orphan_sample": [{"url": p.get("url"), "title": p.get("title")} for p in orphans[:15]],
        "phantom_sample": phantoms[:15],
        "click_depth_distribution": dict(Counter(depths)),
        "pages": pages,
        "method": "in_app_bfs",
        "evidence_notes": [
            "Measured click depth from homepage seed — not folder/slash count",
            "Google does not count URL slashes; do not recommend restructure solely for flat URLs",
            "Prefer hub-and-spoke over strict siloing",
        ],
    }


def _money_keywords(commercial: dict[str, Any], demand: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("products", "products_for_promotion", "business_keywords"):
        raw = commercial.get(key)
        if isinstance(raw, str):
            out.extend([x.strip() for x in raw.replace(";", ",").split(",") if x.strip()])
        elif isinstance(raw, list):
            out.extend([str(x).strip() for x in raw if str(x).strip()])
    for row in (demand.get("best_opportunities") or [])[:8]:
        if isinstance(row, dict) and row.get("keyword"):
            out.append(str(row["keyword"]))
    # dedupe
    seen: set[str] = set()
    clean = []
    for k in out:
        n = k.lower()
        if n not in seen:
            seen.add(n)
            clean.append(k)
    return clean[:12]


def _declared_services(commercial: dict[str, Any], demand: dict[str, Any]) -> list[str]:
    """CDD/Phase-5 service names, excluding generic opportunity keywords."""
    raw_values: list[Any] = [
        demand.get("services"),
        demand.get("products"),
        commercial.get("products"),
        commercial.get("products_for_promotion"),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        values = (
            [x.strip() for x in raw.replace(";", ",").split(",")]
            if isinstance(raw, str)
            else raw if isinstance(raw, list) else []
        )
        for value in values:
            name = str(value or "").strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                out.append(name)
    return out


def build_blueprint(
    *,
    client_name: str,
    domain: str,
    current_state: dict[str, Any],
    demand: dict[str, Any],
    seo_strategy: dict[str, Any],
    commercial: dict[str, Any],
    website: dict[str, Any],
) -> dict[str, Any]:
    cluster_report = dict(demand.get("cluster_report") or {})
    clusters = list(cluster_report.get("clusters") or demand.get("clusters") or [])
    topic_plan = dict(demand.get("topic_plan") or {})
    core_topics = list(seo_strategy.get("core_topics") or [])
    money = _money_keywords(commercial, demand)
    declared_services = _declared_services(commercial, demand)

    base = f"https://{domain}"

    page_type_model = [
        {
            "type": "Home",
            "url_pattern": "/",
            "parent": "—",
            "breadcrumb": "Home",
            "indexable": True,
            "count": 1,
        },
        {
            "type": "Service page",
            "url_pattern": "/services/{service}/",
            "parent": "Home",
            "breadcrumb": "Home > Service",
            "indexable": True,
            "count": len(declared_services) or len(clusters) or 3,
        },
        {
            "type": "Sub-service",
            "url_pattern": "/services/{service}/{subservice}/",
            "parent": "Its service",
            "breadcrumb": "Home > Service > Sub-service",
            "indexable": True,
            "count": "varies",
        },
        {
            "type": "Utility",
            "url_pattern": "/{page}/",
            "parent": "Home",
            "breadcrumb": "Home > Page",
            "indexable": False,
            "count": "few",
        },
    ]

    target_url_tree: list[dict[str, Any]] = []
    cluster_ownership: list[dict[str, Any]] = []
    primary_nav: list[dict[str, Any]] = [{"label": "Home", "url": f"{base}/", "depth": 0}]

    strategy_nodes = _nodes_from_strategy_queue(seo_strategy)
    if strategy_nodes:
        target_url_tree.extend(strategy_nodes)
        for node in strategy_nodes:
            cluster = str(node.get("cluster") or "").strip()
            if cluster and not any(
                str(o.get("cluster") or "").lower() == cluster.lower() for o in cluster_ownership
            ):
                cluster_ownership.append(
                    {
                        "cluster": cluster,
                        "canonical_owner_url": node.get("url") or node.get("path"),
                        "owner_url": node.get("url") or node.get("path"),
                        "competing_urls": [],
                        "disposition": "keep",
                        "note": "Owner taken from first Phase 6 queue URL in this cluster",
                    }
                )
            if len(primary_nav) < 7 and int(node.get("depth") or 9) <= 1:
                primary_nav.append(
                    {
                        "label": str(node.get("title") or node.get("keyword") or node.get("path")),
                        "url": f"{base}{node.get('path')}",
                        "depth": node.get("depth") or 1,
                    }
                )

    # Build hubs from core topics or clusters (skip paths already assigned by strategy)
    hubs: list[dict[str, Any]] = []
    if core_topics:
        for core in core_topics[:6]:
            if not isinstance(core, dict):
                continue
            name = str(core.get("pillar") or core.get("primary_keyword") or "topic")
            hubs.append(
                {
                    "name": name,
                    "slug": _slug(str(core.get("primary_keyword") or name)),
                    "keyword": core.get("primary_keyword"),
                    "children": [
                        {
                            "name": c.get("name"),
                            "slug": _slug(str(c.get("primary_keyword") or c.get("name") or "")),
                            "keyword": c.get("primary_keyword"),
                        }
                        for c in (core.get("clusters") or [])[:6]
                        if isinstance(c, dict)
                    ],
                }
            )
    else:
        for c in clusters[:8]:
            if not isinstance(c, dict):
                continue
            hubs.append(
                {
                    "name": c.get("name"),
                    "slug": _slug(str(c.get("primary_keyword") or c.get("name") or "")),
                    "keyword": c.get("primary_keyword"),
                    "children": [
                        {
                            "name": k.get("keyword"),
                            "slug": _slug(str(k.get("keyword") or "")),
                            "keyword": k.get("keyword"),
                        }
                        for k in (c.get("keywords") or [])[1:5]
                        if isinstance(k, dict)
                    ],
                }
            )

    existing_paths = {_path_n(str(n.get("url") or n.get("path") or "")) for n in target_url_tree}
    for hub in hubs:
        hub_path = f"/{hub['slug']}/"
        if _path_n(hub_path) in existing_paths:
            continue
        existing_paths.add(_path_n(hub_path))
        target_url_tree.append(
            {
                "url": _path_n(hub_path),
                "path": hub_path,
                "type": "hub",
                "page_type": "hub",
                "cluster": hub.get("name"),
                "depth": 1,
                "primary_keyword": hub.get("keyword"),
                "keyword": hub.get("keyword"),
                "parent": "/",
                "absolute_url": f"{base}{hub_path}",
            }
        )
        primary_nav.append(
            {"label": str(hub.get("name") or hub["slug"]).title(), "url": f"{base}{hub_path}", "depth": 1}
        )
        cluster_ownership.append(
            {
                "cluster": hub.get("name"),
                "canonical_owner_url": f"{base}{hub_path}",
                "competing_urls": [],
                "disposition": "keep",
                "note": "Hub owns cluster; spokes nest underneath; cross-cluster links allowed",
            }
        )
        for child in hub.get("children") or []:
            if not child.get("slug"):
                continue
            spoke_path = f"{hub_path}{child['slug']}/"
            if _path_n(spoke_path) in existing_paths:
                continue
            existing_paths.add(_path_n(spoke_path))
            target_url_tree.append(
                {
                    "url": _path_n(spoke_path),
                    "path": spoke_path,
                    "type": "spoke",
                    "page_type": "spoke",
                    "cluster": hub.get("name"),
                    "depth": 2,
                    "primary_keyword": child.get("keyword"),
                    "keyword": child.get("keyword"),
                    "parent": _path_n(hub_path),
                    "absolute_url": f"{base}{spoke_path}",
                }
            )
            cluster_ownership.append(
                {
                    "cluster": f"{hub.get('name')} > {child.get('name')}",
                    "canonical_owner_url": f"{base}{spoke_path}",
                    "competing_urls": [],
                    "disposition": "keep",
                }
            )

    # One service hierarchy for CDD, live-site, and competitor sources.
    from app.services.service_prioritization import (
        build_client_service_catalog,
        competitor_tree_for_architecture,
        ia_nodes_from_competitor_tree,
        merge_competitor_subservices,
    )

    prioritization = dict(
        commercial.get("service_prioritization")
        or demand.get("service_prioritization")
        or {}
    )
    service_catalog = list(
        demand.get("service_catalog")
        or prioritization.get("service_catalog")
        or build_client_service_catalog(services=declared_services)
    )
    competitor_tree = competitor_tree_for_architecture(commercial, demand)
    if competitor_tree:
        service_catalog = merge_competitor_subservices(
            service_catalog,
            competitor_tree=competitor_tree,
        )
    service_nodes = ia_nodes_from_competitor_tree(
        {"name": "Unified client service catalog", "services": service_catalog},
        domain=domain,
        selected_service_ids=list(prioritization.get("selected_service_ids") or []),
        selected_subservice_ids=list(
            prioritization.get("selected_subservice_ids") or []
        ),
        adopt_service_ids=list(
            prioritization.get("adopt_competitor_service_ids") or []
        ),
    )
    for node in service_nodes:
        path = _path_n(str(node.get("url") or node.get("path") or ""))
        if path in existing_paths:
            continue
        existing_paths.add(path)
        target_url_tree.append(node)
    if declared_services:
        for service_name in declared_services[: max(0, 6 - len(primary_nav))]:
            primary_nav.append(
                {
                    "label": service_name,
                    "url": f"{base}/{_slug(service_name)}/",
                    "depth": 1,
                }
            )

    primary_nav.append({"label": "Blog", "url": f"{base}/blog/", "depth": 1})
    # Cap primary nav at 7
    primary_nav = primary_nav[:7]

    # Depth remediation from audit
    depth_remediation = []
    for p in current_state.get("pages_depth_4_plus") or []:
        depth_remediation.append(
            {
                "url": p.get("url"),
                "current_depth": p.get("depth"),
                "action": "Add to hub nav or create intermediate hub — do not flatten folders for slash count",
                "target_depth": 3,
            }
        )

    # Redirect map — only when strategy/IA suggests path moves vs existing website pages
    redirect_map: list[dict[str, Any]] = []
    existing_paths = set()
    for key in ("top_pages", "pages", "crawled_pages"):
        raw = website.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("url"):
                    existing_paths.add(urlparse(str(item["url"])).path)

    for node in target_url_tree:
        # No mass redirects invented — only flag when website summary shows alternate path
        pass

    url_rules = {
        "trailing_slash": "Use trailing slash; 301 the non-slash variant",
        "case": "Lowercase only; redirect mixed case",
        "subdomain_vs_subfolder": "Prefer subfolders (operational: GSC, analytics, linking) — not a ranking rule",
        "pagination": "/page/2/ self-canonical; never canonicalise to page 1",
        "faceted_navigation": "Block non-indexable facets in robots or use fragments; at most one indexable facet",
        "folder_depth": "Do NOT restructure for slash count — Google does not count folders",
        "siloing": "Hub-and-spoke with cross-cluster linking where relevant — no strict silos",
        "references": [
            "skills/site-architecture/references/evidence-base.md",
            "skills/site-architecture/references/url-conventions.md",
            "skills/site-architecture/references/migration-playbook.md",
        ],
    }

    rollout = {
        "sequence": [
            "Approve blueprint (Strategist) + redirect/robots sign-off (Tech SEO)",
            "Add missing hub pages for phantom directories / depth remediation via nav first",
            "Publish new hub/spoke URLs only where pages do not exist yet",
            "If URL moves required: section-by-section 301s, never mass-to-home",
            "Validate crawl at 24h / 7d / 30d",
        ],
        "freeze_window": "No concurrent CMS + redesign + IA change",
        "rollback_trigger": "Money-page organic traffic −25% for 7 days or index coverage collapse",
        "high_risk_flags": [],
    }
    if len(redirect_map) > 0 or (current_state.get("urls_crawled") or 0) > 200:
        rollout["high_risk_flags"].append("Large inventory — move in sections if URLs change")

    handoffs = [
        {"item": "Redirect map, canonical + robots/facet rules", "receiving": "Technical SEO (Phase 7)"},
        {"item": "Page inventory + types", "receiving": "Content Strategy / Content Brief (Phases 8–10)"},
        {"item": "Cluster ownership map", "receiving": "On-Page SEO (Phase 11)"},
        {"item": "Hub-spoke structure, orphan list", "receiving": "Internal Linking (Phase 11)"},
        {"item": "Breadcrumb trails per type", "receiving": "Schema Markup (Phase 11)"},
        {"item": "Publish sequence", "receiving": "Publishing & Indexation (Phase 12)"},
    ]

    # Logical parentage follows topical support even when the publishing URL
    # remains under /blog/.  Prefer the most-specific matching sub-service.
    from app.services.content_planning import (
        _matching_service_target,
        _page_type,
        _service_targets,
    )

    service_targets = _service_targets(target_url_tree)
    for node in target_url_tree:
        if _page_type(node) not in {
            "article",
            "blog",
            "guide",
            "spoke",
            "cluster",
            "supporting",
        }:
            continue
        keyword = str(
            node.get("primary_keyword")
            or node.get("keyword")
            or node.get("title")
            or ""
        )
        target = _matching_service_target(node, keyword=keyword, targets=service_targets)
        if not target:
            continue
        parent = _path_n(str(target.get("url") or target.get("path") or ""))
        node["parent"] = parent
        node["supports_service_id"] = target.get("service_id")
        node["supports_subservice_id"] = target.get("subservice_id")
        node["depth"] = int(target.get("depth") or 0) + 1

    return {
        "client": client_name,
        "current_state": {
            k: current_state.get(k)
            for k in (
                "urls_crawled",
                "status_200",
                "max_click_depth",
                "within_3_clicks",
                "depth_4_plus",
                "orphans",
                "phantom_dirs",
                "redirect_chains",
                "canonical_conflicts",
                "issues",
                "pages_depth_4_plus",
                "orphan_sample",
                "phantom_sample",
                "click_depth_distribution",
                "method",
                "evidence_notes",
            )
        },
        "page_type_model": page_type_model,
        "target_url_tree": _normalize_url_tree(target_url_tree),
        "url_convention_rules": url_rules,
        "navigation": {
            "primary_nav": primary_nav,
            "breadcrumb_pattern": {
                "hub": "Home > {Hub}",
                "spoke": "Home > {Hub} > {Spoke}",
                "service": "Home > {Service}",
                "subservice": "Home > {Service} > {Sub-service}",
                "blog": "Home > {Supporting service, when relevant} > {Post}",
            },
            "depth_remediation": depth_remediation[:20],
            "rule": "Money pages within 3 clicks; indexable within 4; breadcrumbs must match URL hierarchy",
        },
        "cluster_ownership": cluster_ownership,
        "cluster_owners": cluster_ownership,
        "redirect_map": redirect_map,
        "rollout_plan": rollout,
        "handoffs": handoffs,
        "money_pages": money,
        "gate": "No Phase 9 brief until URL, parent, and target depth are fixed for each page",
        "source": "site_architecture_rules",
    }


async def run_site_architecture_plan(
    *,
    client_name: str,
    primary_url: str,
    domain: str,
    demand: dict[str, Any],
    seo_strategy: dict[str, Any],
    commercial: dict[str, Any],
    website: dict[str, Any],
    competitive: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    page_cap: int = 60,
) -> dict[str, Any]:
    competitive = competitive or {}
    marketing = marketing or {}
    cluster_report = dict(demand.get("cluster_report") or {})
    clusters = list(cluster_report.get("clusters") or demand.get("clusters") or [])
    if not clusters and not demand.get("topics") and not seo_strategy.get("core_topics"):
        return {
            "blocked": True,
            "reason": "Cluster map missing — run Phase 5 Keyword Clustering (and ideally Content Strategy) before Site Architecture.",
            "route_to": "search_demand",
        }

    competitors = list(
        demand.get("competitors")
        or seo_strategy.get("competitors")
        or competitive.get("competitors")
        or []
    )
    competitor_names = [
        str(c.get("name") or c.get("domain") or "")
        for c in competitors
        if isinstance(c, dict)
    ] or list(demand.get("competitor_names") or seo_strategy.get("competitor_names") or [])
    competitor_domains = list(
        demand.get("competitor_domains") or seo_strategy.get("competitor_domains") or []
    )
    competitor_sites = list(
        demand.get("competitor_sites") or seo_strategy.get("competitor_sites") or []
    )

    from app.services.service_prioritization import (
        build_client_service_catalog,
        competitor_tree_for_architecture,
        ia_nodes_from_competitor_tree,
        merge_competitor_subservices,
    )

    prioritization = dict(
        commercial.get("service_prioritization") or demand.get("service_prioritization") or {}
    )
    competitor_service_tree = competitor_tree_for_architecture(commercial, demand)
    geo_focus = (
        demand.get("geographic_focus")
        or commercial.get("geographic_focus")
        or (marketing.get("client_intake") or {}).get("geographic_focus")
        or marketing.get("geographic_focus")
        or ""
    )
    location_name = demand.get("location_name") or geo_focus or "United States"

    current = await measure_click_depth(primary_url, page_cap=page_cap)
    # Merge website summary page counts if crawl thin
    if (current.get("urls_crawled") or 0) < 5 and website:
        current["issues"].append(
            {
                "issue": "thin_live_crawl",
                "count": current.get("urls_crawled") or 0,
                "severity": "medium",
                "note": "Live BFS returned few pages — blueprint still uses Phase 5 clusters; re-run architecture_audit.py for fuller evidence",
            }
        )

    # Competitor IA patterns from Phase 5 site snapshots
    competitor_ia: list[dict[str, Any]] = []
    for snap in competitor_sites[:4]:
        if not isinstance(snap, dict):
            continue
        competitor_ia.append(
            {
                "name": snap.get("name"),
                "domain": snap.get("domain"),
                "page_count": snap.get("page_count"),
                "blog_count": snap.get("blog_count"),
                "hub_paths": snap.get("hub_paths") or [],
                "blog_examples": [
                    {"title": b.get("title"), "path": b.get("path")}
                    for b in (snap.get("blogs") or [])[:5]
                    if b.get("title") or b.get("path")
                ],
            }
        )

    blueprint = build_blueprint(
        client_name=client_name,
        domain=domain,
        current_state=current,
        demand=demand,
        seo_strategy=seo_strategy,
        commercial=commercial,
        website=website,
    )

    from app.services.site_sitemap import ensure_website_sitemap, sitemap_pages
    from app.services.url_mapping import (
        apply_url_map_to_architecture,
        build_final_url_map,
    )

    # Phase 3 site sitemap is the process-wide inventory. Phase 5 live scan
    # enriches titles/H1 on those URLs when present; otherwise use the sitemap.
    website_for_mapping = ensure_website_sitemap(dict(website))
    live_scan = demand.get("live_site_scan") or {}
    if live_scan.get("pages"):
        website_for_mapping["pages"] = live_scan["pages"]
    elif sitemap_pages(website_for_mapping):
        website_for_mapping["pages"] = sitemap_pages(website_for_mapping)

    url_map_report = build_final_url_map(
        cluster_report,
        website=website_for_mapping,
        content_audit=seo_strategy.get("content_audit") or demand.get("content_audit"),
        serp_by_keyword=demand.get("serp_by_keyword") or seo_strategy.get("serp_by_keyword") or {},
        commercial=commercial,
        client_domain=domain,
        pillar_structure=seo_strategy.get("core_topics"),
    )
    blueprint = apply_url_map_to_architecture(blueprint, url_map_report)
    blueprint["url_map_report"] = url_map_report
    if isinstance(blueprint.get("target_url_tree"), list):
        blueprint["target_url_tree"] = _normalize_url_tree(blueprint["target_url_tree"])

    # Topics were drafted in Phase 5 for new clusters only; stamp URL map actions.
    from app.services.create_topic import attach_url_map_to_topic_plan, topics_from_plan

    existing_plan = dict(demand.get("topic_plan") or {})
    if existing_plan.get("topic_ideas") or existing_plan.get("existing_on_site"):
        topic_plan = attach_url_map_to_topic_plan(dict(existing_plan), url_map_report)
        blueprint["topic_plan"] = topic_plan
        blueprint["topics"] = topics_from_plan(topic_plan)
    blueprint["sitemap_classification"] = (
        demand.get("sitemap_classification")
        or (demand.get("cluster_report") or {}).get("sitemap_classification")
    )

    # Build one canonical hierarchy for CDD, existing-site, and competitor
    # services.  This replaces the old two-track merge where only competitor
    # sub-services were nested.
    service_catalog = list(
        demand.get("service_catalog")
        or prioritization.get("service_catalog")
        or []
    )
    if not service_catalog:
        service_catalog = build_client_service_catalog(
            services=_declared_services(commercial, demand),
            website=website,
            page_seeds=[],
        )
    if competitor_service_tree:
        service_catalog = merge_competitor_subservices(
            service_catalog,
            competitor_tree=competitor_service_tree,
        )
        blueprint["competitor_service_tree"] = competitor_service_tree

    if service_catalog:
        blueprint["service_catalog"] = service_catalog
        service_tree = {
            "name": "Unified client service catalog",
            "services": service_catalog,
        }
        service_nodes = ia_nodes_from_competitor_tree(
            service_tree,
            domain=domain,
            selected_service_ids=list(prioritization.get("selected_service_ids") or []),
            selected_subservice_ids=list(
                prioritization.get("selected_subservice_ids") or []
            ),
            adopt_service_ids=list(
                prioritization.get("adopt_competitor_service_ids") or []
            ),
        )
        blueprint["service_ia_nodes"] = service_nodes
        if competitor_service_tree:
            blueprint["competitor_ia_nodes"] = [
                node for node in service_nodes if node.get("from_competitor_ia")
            ]
        existing_paths = {
            _path_n(str(n.get("url") or n.get("path") or ""))
            for n in blueprint.get("target_url_tree") or []
        }
        merged_tree = list(blueprint.get("target_url_tree") or [])
        by_path = {
            _path_n(str(n.get("url") or n.get("path") or "")): index
            for index, n in enumerate(merged_tree)
            if isinstance(n, dict)
        }
        for node in service_nodes:
            path = _path_n(str(node.get("url") or node.get("path") or ""))
            if not path:
                continue
            # Canonical service metadata wins over a generic strategy/hub row
            # at the same URL.
            if path in existing_paths:
                merged_tree[by_path[path]] = {**merged_tree[by_path[path]], **node}
            else:
                merged_tree.append(node)
                by_path[path] = len(merged_tree) - 1
                existing_paths.add(path)
        blueprint["target_url_tree"] = _normalize_url_tree(merged_tree)

    if competitor_service_tree:
        # Retained as a separate response field for consumers that display the
        # competitor evidence behind the unified hierarchy.
        blueprint["competitor_service_tree"] = competitor_service_tree

    # Content URLs remain under their publishing path (for example /blog/...),
    # but their logical IA parent is the most relevant service/sub-service.
    # Content Planning applies the same rule to its roadmap rows.
    from app.services.content_planning import (
        _matching_service_target,
        _page_type,
        _service_targets,
    )

    tree_rows = list(blueprint.get("target_url_tree") or [])
    service_targets = _service_targets(tree_rows)
    for node in tree_rows:
        if not isinstance(node, dict) or _page_type(node) not in {
            "article",
            "blog",
            "guide",
            "spoke",
            "cluster",
            "supporting",
        }:
            continue
        keyword = str(
            node.get("primary_keyword")
            or node.get("keyword")
            or node.get("title")
            or ""
        )
        target = _matching_service_target(node, keyword=keyword, targets=service_targets)
        if not target:
            continue
        parent = _path_n(str(target.get("url") or target.get("path") or ""))
        if not parent:
            continue
        node["parent"] = parent
        node["supports_service_id"] = target.get("service_id")
        node["supports_subservice_id"] = target.get("subservice_id")
        try:
            node["depth"] = int(target.get("depth")) + 1
        except (TypeError, ValueError):
            node["depth"] = max(1, parent.strip("/").count("/") + 2)
    blueprint["target_url_tree"] = _normalize_url_tree(tree_rows)

    from app.services.phase_pipeline import enrich_phase6_pack

    blueprint = enrich_phase6_pack(blueprint)

    blueprint["competitors"] = competitors
    blueprint["competitor_names"] = competitor_names
    blueprint["competitor_domains"] = competitor_domains
    blueprint["competitor_ia"] = competitor_ia
    blueprint["geographic_focus"] = geo_focus
    blueprint["location_name"] = location_name

    # Optional LLM polish of navigation labels / ownership notes
    skill = load_skill_file("site-architecture")
    try:
        system = (
            (skill[:6000] if skill else "You are an information architect.")
            + "\nRead evidence: Google does not count slashes; prefer click depth; no strict silos.\n"
            "Return JSON with keys: executive_summary (string), navigation_notes (string), "
            "extra_depth_fixes (array of {url,action}), competitor_ia_notes (string). "
            "Do not invent crawl metrics. Compare only against listed shared-memory competitors. "
            "Do not invent cities outside geographic_focus."
        )
        user = (
            f"Client: {client_name} ({domain})\n"
            f"Geographic focus: {geo_focus or 'not set'} → {location_name}\n"
            f"Shared-memory competitors: {', '.join(competitor_names[:6] or competitor_domains[:5])}\n"
            f"Competitor IA snapshots: {competitor_ia}\n"
            + (
                f"Selected competitor service org chart ({competitor_service_tree.get('name')}): "
                f"{competitor_service_tree.get('services')}\n"
                if competitor_service_tree
                else ""
            )
            + f"Current state: {blueprint['current_state']}\n"
            f"Hubs: {blueprint['target_url_tree'][:8]}\n"
            f"Ownership: {blueprint['cluster_ownership'][:6]}\n"
        )
        parsed = await synthesize_json(system, user)
        if isinstance(parsed, dict):
            if parsed.get("executive_summary"):
                blueprint["executive_summary"] = str(parsed["executive_summary"]).strip()
            if parsed.get("navigation_notes"):
                blueprint["navigation"]["notes"] = parsed["navigation_notes"]
            if parsed.get("competitor_ia_notes"):
                blueprint["competitor_ia_notes"] = str(parsed["competitor_ia_notes"]).strip()
            extras = parsed.get("extra_depth_fixes")
            if isinstance(extras, list):
                blueprint["navigation"]["depth_remediation"].extend(
                    [e for e in extras if isinstance(e, dict)][:10]
                )
            blueprint["source"] = "site_architecture_skill"
    except Exception as exc:  # noqa: BLE001
        log.warning("site_architecture_llm_polish_failed", client=client_name, error=str(exc))

    if not blueprint.get("executive_summary"):
        cs = blueprint["current_state"]
        comps_bit = (
            f" Compared vs {', '.join(competitor_names[:3])}."
            if competitor_names
            else ""
        )
        blueprint["executive_summary"] = (
            f"IA blueprint for {client_name}: {len(blueprint['target_url_tree'])} planned URLs, "
            f"{len(blueprint['cluster_ownership'])} cluster owners. "
            f"Geo: {location_name}. "
            f"Live audit: {cs.get('urls_crawled')} URLs, max click depth {cs.get('max_click_depth')}, "
            f"{cs.get('depth_4_plus')} at depth 4+, {cs.get('orphans')} orphans."
            f"{comps_bit} "
            "Optimise click depth via hubs/nav — do not flatten folders for slash count."
        )
    from app.services.bw_workbook import attach_workbook_to_site_architecture

    return attach_workbook_to_site_architecture(blueprint, client_name=client_name)
