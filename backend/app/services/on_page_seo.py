"""Phase 11 — On-page SEO package service."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.integrations.providers import optimize_on_page

# Terms shorter than this are too collision-prone to strip safely ("ai", "go").
_MIN_TRADEMARK_TERM = 3


def _path(url: str) -> str:
    if not url:
        return "/"
    if "://" in url:
        return urlparse(url).path or "/"
    return url if str(url).startswith("/") else f"/{url}"


def competitor_terms(
    competitive: dict[str, Any] | None,
    *,
    client_name: str = "",
    extra_denylist: list[str] | None = None,
) -> list[str]:
    """Competitor brand terms that must never appear in client on-page content.

    Architecture v1.9 guardrail — "Competitor brand terms must never enter client
    content, regardless of how well they score as keywords. The skill won't know this
    on its own." Sourced from the Phase 4 competitive landscape plus a per-client
    denylist, with the client's own brand excluded so we never strip their name.
    """
    pack = dict(competitive or {})
    raw: list[str] = []
    raw.extend(str(n) for n in (pack.get("competitor_names") or []))
    for row in pack.get("competitors") or []:
        if isinstance(row, dict):
            raw.append(str(row.get("name") or ""))
            raw.append(str(row.get("domain") or row.get("url") or ""))
        elif row:
            raw.append(str(row))
    raw.extend(str(d) for d in (pack.get("competitor_domains") or []))
    raw.extend(str(t) for t in (extra_denylist or []))

    own = {w for w in re.split(r"\W+", client_name.lower()) if len(w) >= _MIN_TRADEMARK_TERM}
    own.add(client_name.strip().lower())

    terms: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = item.strip().lower()
        if not text:
            continue
        # A domain contributes both the full host and its brand label (rival-a.com → rival-a)
        candidates = [text]
        if "." in text:
            host = text.split("://")[-1].split("/")[0].removeprefix("www.")
            label = host.split(".")[0]
            candidates.extend([host, label])
        for cand in candidates:
            cand = cand.strip(" -/")
            if len(cand) < _MIN_TRADEMARK_TERM or cand in seen or cand in own:
                continue
            seen.add(cand)
            terms.append(cand)
    return terms


def _strip_terms(text: str, terms: list[str]) -> tuple[str, list[str]]:
    """Remove competitor terms on word boundaries; return cleaned text and what was hit."""
    if not text or not terms:
        return text, []
    cleaned = text
    hits: list[str] = []
    for term in terms:
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        if pattern.search(cleaned):
            hits.append(term)
            cleaned = pattern.sub("", cleaned)
    if not hits:
        return text, []
    # Tidy the punctuation/whitespace the removal left behind
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s*([|·–—-])\s*\1+", r" \1 ", cleaned)
    cleaned = cleaned.strip(" |·–—-,:")
    return cleaned, hits


REDACTION = "[competitor]"


def apply_trademark_block(
    pages: list[dict[str, Any]],
    internal_links: list[dict[str, Any]],
    *,
    terms: list[str],
    client_name: str = "",
) -> list[dict[str, Any]]:
    """Remove competitor brands from the on-page package. Returns the blocked log.

    Mutates pages/links in place. Excising a brand mid-sentence leaves broken copy
    ("Better than  for SEO."), so the replacement depends on what the field is:

    * ``before`` — an observed value from the live page. Redact the term so the record
      stays accurate ("this page currently name-drops a rival") without shipping it.
    * ``after`` / ``h1`` / anchors — values we generate. Regenerate from a safe template
      rather than publishing surgery residue.
    * ``h2s`` — drop the affected heading; a partial heading is worth less than none.
    """
    blocked: list[dict[str, Any]] = []
    if not terms:
        return blocked

    def _record(url: str, field: str, hits: list[str]) -> None:
        for term in hits:
            blocked.append({"url": url, "field": field, "term": term})

    def _redact(text: str) -> tuple[str, list[str]]:
        hits: list[str] = []
        out = text
        for term in terms:
            pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
            if pattern.search(out):
                hits.append(term)
                out = pattern.sub(REDACTION, out)
        return out, hits

    for page in pages:
        url = str(page.get("url") or "")
        kw = str(page.get("keyword") or "").strip()
        safe_title = f"{kw.title()} | {client_name}".strip(" |") if kw else client_name
        safe_meta = (
            f"{kw.title()} from {client_name}. Learn more and get started.".strip()
            if kw
            else f"Learn more about {client_name}."
        )[:160]

        for field, safe_value in (("title", safe_title), ("meta_description", safe_meta)):
            block = page.get(field)
            if not isinstance(block, dict):
                continue
            redacted, hits = _redact(str(block.get("before") or ""))
            if hits:
                block["before"] = redacted
                _record(url, f"{field}.before", hits)
            _cleaned, hits = _strip_terms(str(block.get("after") or ""), terms)
            if hits:
                block["after"] = safe_value
                _record(url, f"{field}.after", hits)
            if isinstance(block.get("after"), str):
                block["length_after"] = len(block["after"])

        headings = page.get("headings")
        if isinstance(headings, dict):
            _cleaned, hits = _strip_terms(str(headings.get("h1") or ""), terms)
            if hits:
                headings["h1"] = kw.title() if kw else client_name
                page["h1"] = headings["h1"]
                _record(url, "headings.h1", hits)
            h2s = headings.get("h2s")
            if isinstance(h2s, list):
                kept: list[str] = []
                for h2 in h2s:
                    _cleaned, hits = _strip_terms(str(h2 or ""), terms)
                    if hits:
                        _record(url, "headings.h2s", hits)
                        continue
                    kept.append(str(h2))
                headings["h2s"] = kept

        json_ld = page.get("schema_json_ld")
        if isinstance(json_ld, dict):
            for key, value in list(json_ld.items()):
                if not isinstance(value, str):
                    continue
                _cleaned, hits = _strip_terms(value, terms)
                if hits:
                    # Schema values are machine-read; an invented substitute would be a
                    # structured-data lie, so drop the property instead.
                    json_ld.pop(key, None)
                    _record(url, f"schema_json_ld.{key}", hits)

    for link in internal_links:
        _cleaned, hits = _strip_terms(str(link.get("anchor") or ""), terms)
        if hits:
            target = str(link.get("to") or "").strip("/").split("/")[-1].replace("-", " ")
            link["anchor"] = target.title() if target else "Related"
            _record(str(link.get("from") or ""), "internal_links.anchor", hits)

    return blocked


def build_internal_linking_plan(
    *,
    page_path: str,
    page_keyword: str | None = None,
    parent: str | None = None,
    site_architecture: dict[str, Any] | None = None,
    content_planning: dict[str, Any] | None = None,
    brief_links: list[dict[str, Any]] | None = None,
    page_type: str | None = None,
) -> dict[str, Any]:
    """Phase 11 internal-linking skill contract — hub/spoke + orphans + planned targets.

    Only links to URLs present in the IA tree, crawl orphans list, or locked roadmap.
    Never invents destinations.
    """
    ia = dict(site_architecture or {})
    planning = dict(content_planning or {})
    path = _path(page_path)
    parent_path = _path(str(parent)) if parent else None
    kw = str(page_keyword or "").strip()
    anchor_base = kw.title() if kw else (path.strip("/").split("/")[-1] or "related").replace("-", " ").title()

    tree: list[dict[str, Any]] = [
        n for n in (ia.get("target_url_tree") or []) if isinstance(n, dict)
    ]
    tree_by_path: dict[str, dict[str, Any]] = {}
    for n in tree:
        p = _path(str(n.get("url") or n.get("path") or ""))
        if p:
            tree_by_path[p] = n

    roadmap_paths: dict[str, dict[str, Any]] = {}
    for r in (planning.get("pages") or planning.get("roadmap") or []):
        if not isinstance(r, dict):
            continue
        p = _path(str(r.get("url") or r.get("path") or ""))
        if p and p != "/":
            roadmap_paths[p] = r

    known = set(tree_by_path) | set(roadmap_paths) | {path}
    if parent_path:
        known.add(parent_path)
    known.add("/")

    def _status(target: str) -> str:
        t = _path(target)
        if t in tree_by_path or t == "/":
            node = tree_by_path.get(t) or {}
            # Nodes without action are treated as live/planned in IA
            action = str(node.get("action") or roadmap_paths.get(t, {}).get("action") or "").lower()
            if action in ("create", "planned"):
                return "planned"
            if t in roadmap_paths and t not in tree_by_path:
                return "planned"
            return "live"
        if t in roadmap_paths:
            return "planned"
        return "unknown"

    def _label(target: str) -> str:
        t = _path(target)
        node = tree_by_path.get(t) or roadmap_paths.get(t) or {}
        return str(
            node.get("keyword")
            or node.get("primary_keyword")
            or node.get("title")
            or t.strip("/").split("/")[-1]
            or "home"
        ).replace("-", " ").strip()

    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        frm: str,
        to: str,
        anchor: str,
        reason: str,
        *,
        bidirectional: bool = False,
    ) -> None:
        f, t = _path(frm), _path(to)
        if not f or not t or f == t:
            return
        if t not in known and f not in known:
            return
        if t not in known:
            return
        key = (f, t)
        if key in seen:
            return
        seen.add(key)
        links.append(
            {
                "from": f,
                "to": t,
                "anchor": (anchor or _label(t))[:80],
                "reason": reason,
                "target_status": _status(t),
            }
        )
        if bidirectional:
            add(t, f, anchor_base if f == path else _label(f), reason + "_return")

    # 1) Brief-specified links
    for ln in brief_links or []:
        if not isinstance(ln, dict):
            continue
        to = ln.get("to") or ln.get("target") or ln.get("url")
        if not to:
            continue
        add(
            path,
            str(to),
            str(ln.get("anchor") or _label(str(to))),
            str(ln.get("reason") or ln.get("why") or "brief_target"),
        )

    # 2) Parent hub ↔ this page
    if parent_path and parent_path != path:
        add(path, parent_path, _label(parent_path) or "Back to hub", "parent_hub")
        add(parent_path, path, anchor_base, "hub_to_spoke")

    # 3) Homepage shortcut when depth suggests burial
    node = tree_by_path.get(path) or {}
    depth = node.get("depth")
    try:
        depth_i = int(depth) if depth is not None else None
    except (TypeError, ValueError):
        depth_i = None
    if path != "/" and (depth_i is None or depth_i >= 2):
        add("/", path, anchor_base, "homepage_shortcut")

    # 4) Cluster ownership — link to the owner of this cluster + siblings
    ownership = [
        o for o in (ia.get("cluster_ownership") or []) if isinstance(o, dict)
    ]
    my_cluster = None
    for o in ownership:
        owner = _path(str(o.get("owner_url") or o.get("url") or ""))
        if owner == path:
            my_cluster = str(o.get("cluster") or o.get("name") or "")
            break
    if not my_cluster:
        my_kw = str(node.get("keyword") or kw or "").lower()
        for o in ownership:
            cname = str(o.get("cluster") or o.get("name") or "").lower()
            if cname and my_kw and (cname in my_kw or my_kw in cname):
                my_cluster = str(o.get("cluster") or o.get("name") or "")
                owner = _path(str(o.get("owner_url") or o.get("url") or ""))
                if owner and owner != path:
                    add(path, owner, _label(owner), "cluster_owner")
                break

    siblings = [
        p
        for p, n in tree_by_path.items()
        if p != path and _path(str(n.get("parent") or "")) == (parent_path or _path(str(node.get("parent") or "")))
    ]
    for sib in siblings[:4]:
        add(path, sib, _label(sib), "sibling_cluster", bidirectional=False)

    # Same-cluster ownership peers
    if my_cluster:
        for o in ownership:
            if str(o.get("cluster") or o.get("name") or "") != my_cluster:
                continue
            owner = _path(str(o.get("owner_url") or o.get("url") or ""))
            if owner and owner != path:
                add(path, owner, _label(owner), "cluster_peer")

    # 5) Roadmap peers under same parent / related keyword
    for rp, row in list(roadmap_paths.items())[:12]:
        if rp == path:
            continue
        rparent = _path(str(row.get("parent") or ""))
        if parent_path and rparent == parent_path:
            add(path, rp, _label(rp), "roadmap_sibling")
        elif kw and _norm_overlap(kw, str(row.get("keyword") or row.get("title") or "")):
            add(path, rp, _label(rp), "roadmap_related")

    # Commercial CTA from editorial pages
    pt = (page_type or str(node.get("type") or node.get("page_type") or "")).lower()
    if pt in ("article", "blog", "post", "guide", "spoke", "hub") or "/blog/" in path:
        for p, n in tree_by_path.items():
            npt = str(n.get("type") or n.get("page_type") or "").lower()
            if npt in ("service", "landing", "product") or "/services/" in p:
                add(path, p, _label(p), "editorial_to_commercial")
                break

    # Orphans / depth / dead ends from IA current_state
    cs = ia.get("current_state") if isinstance(ia.get("current_state"), dict) else {}
    orphans_raw = cs.get("orphans") if "orphans" in cs else ia.get("orphans")
    if isinstance(orphans_raw, int) or not isinstance(orphans_raw, list):
        orphans_raw = []
    orphans: list[dict[str, Any]] = []
    for o in orphans_raw[:12]:
        if isinstance(o, dict):
            ou = _path(str(o.get("url") or o.get("path") or ""))
        else:
            ou = _path(str(o))
        if not ou or ou == path:
            continue
        # Suggest linking FROM the current page when topical, else from parent/home
        suggested_from = path if _norm_overlap(kw, ou) else (parent_path or "/")
        orphans.append({"url": ou, "suggested_from": suggested_from})
        add(suggested_from, ou, _label(ou), "orphan_rescue")

    depth_4: list[dict[str, Any]] = []
    for n in tree:
        p = _path(str(n.get("url") or n.get("path") or ""))
        try:
            d = int(n.get("depth")) if n.get("depth") is not None else None
        except (TypeError, ValueError):
            d = None
        if p and d is not None and d >= 4:
            depth_4.append({"url": p, "depth": d, "shortcut_from": "/"})
            add("/", p, _label(p), "depth_shortcut")

    # Dead ends: pages in tree with no outgoing recommendation yet
    outgoing = {ln["from"] for ln in links}
    dead_ends: list[dict[str, Any]] = []
    if path not in outgoing or sum(1 for ln in links if ln["from"] == path) < 2:
        # Ensure this page is not a dead end — already adding links above
        pass
    for p in list(tree_by_path)[:20]:
        if p == path:
            continue
        out_count = sum(1 for ln in links if ln["from"] == p)
        if out_count == 0:
            targets = [path, parent_path or "/"]
            dead_ends.append({"url": p, "suggested_to": [t for t in targets if t and t != p][:2]})
            add(p, path, anchor_base, "dead_end_rescue")

    # Cluster map for the report
    cluster_map: list[dict[str, Any]] = []
    hubs: dict[str, list[str]] = {}
    for n in tree:
        p = _path(str(n.get("url") or n.get("path") or ""))
        par = _path(str(n.get("parent") or "")) or "/"
        if not p:
            continue
        hubs.setdefault(par, []).append(p)
    for hub, spokes in list(hubs.items())[:8]:
        cluster_map.append({"hub": hub, "spokes": spokes[:8]})

    # Prefer links involving the selected page; keep supporting graph
    primary = [ln for ln in links if ln["from"] == path or ln["to"] == path]
    supporting = [ln for ln in links if ln not in primary]
    ordered = (primary + supporting)[:40]

    return {
        "links": ordered,
        "orphans": orphans[:10],
        "dead_ends": dead_ends[:10],
        "depth_4_plus": depth_4[:10],
        "cluster_map": cluster_map,
        "pages_analyzed": len(known),
        "link_count": len(ordered),
        "from_page": path,
        "note": (
            "Internal linking plan from Site Architecture + roadmap "
            "(hub↔spoke, orphans, depth shortcuts). Planned targets publish in Phase 12."
            if ordered
            else "No link targets in IA/roadmap yet — lock Site Architecture and Content Planning first."
        ),
    }


def _norm_overlap(a: str, b: str) -> bool:
    ta = {t for t in re.findall(r"[a-z0-9]+", (a or "").lower()) if len(t) > 2}
    tb = {t for t in re.findall(r"[a-z0-9]+", (b or "").lower()) if len(t) > 2}
    if not ta or not tb:
        return False
    return bool(ta & tb)


def production_gate_ok(
    production_status: str | None,
    production: dict[str, Any],
    planning: dict[str, Any],
) -> tuple[bool, str]:
    if (production_status or "") == "complete":
        return True, "content_production"
    if production.get("briefs") or production.get("drafts"):
        return True, "content_production_summary"
    roadmap = planning.get("pages") or planning.get("roadmap") or []
    if isinstance(roadmap, list) and any(
        isinstance(r, dict) and (r.get("url") or r.get("path") or r.get("url_n")) for r in roadmap
    ):
        return True, "content_planning_fallback"
    return False, "blocked"


def selected_on_page_rows(production: dict[str, Any]) -> list[dict[str, Any]]:
    """One row: the drafted / selected topic. Never the full brief list."""
    from app.services.content_production import pick_brief

    briefs = [b for b in (production.get("briefs") or []) if isinstance(b, dict)]
    drafts = [d for d in (production.get("drafts") or []) if isinstance(d, dict)]

    def _merge(draft: dict[str, Any] | None, brief: dict[str, Any] | None) -> dict[str, Any]:
        row = dict(brief or {})
        if draft:
            pre = row.get("preflight") if isinstance(row.get("preflight"), dict) else {}
            row.update(
                {
                    "url": draft.get("url") or row.get("url") or row.get("path"),
                    "path": draft.get("url") or row.get("path") or row.get("url"),
                    "keyword": draft.get("keyword") or row.get("keyword"),
                    "title": draft.get("title") or row.get("title"),
                    "meta_description": draft.get("meta_description") or row.get("meta_description"),
                    "funnel": draft.get("funnel") or row.get("funnel"),
                    "parent": row.get("parent") or pre.get("parent"),
                    "internal_links": row.get("internal_links") or [],
                    "outline": row.get("outline") or [],
                }
            )
        return row

    if drafts:
        draft = drafts[0]
        brief = pick_brief(
            briefs,
            selected_url=str(draft.get("url") or "") or None,
            selected_keyword=str(draft.get("keyword") or "") or None,
        )
        return [_merge(draft, brief)]

    brief = pick_brief(
        briefs,
        selected_url=str(production.get("selected_url") or "") or None,
        selected_keyword=str(production.get("selected_keyword") or "") or None,
    )
    if brief:
        return [_merge(None, brief)]
    return []


def _schema_for(page: dict[str, Any], client_name: str, primary_url: str) -> dict[str, Any]:
    url = str(page.get("url") or page.get("page_url") or primary_url)
    kw = page.get("keyword") or page.get("target_keyword") or client_name
    funnel = page.get("funnel") or ""
    types = ["WebPage"]
    json_ld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": page.get("title") or kw,
        "url": url if url.startswith("http") else primary_url.rstrip("/") + _path(url),
        "description": page.get("meta") or f"{kw} — {client_name}",
        "isPartOf": {"@type": "WebSite", "name": client_name, "url": primary_url},
    }
    if funnel == "ToFu" or "blog" in _path(url):
        types = ["Article", "BreadcrumbList"]
        json_ld = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": page.get("title") or kw,
            "author": {"@type": "Organization", "name": client_name},
            "mainEntityOfPage": url,
        }
    elif funnel == "BoFu":
        types = ["WebPage", "Organization"]
    return {"types": types, "json_ld": json_ld}


async def run_on_page_seo_plan(
    *,
    client_name: str,
    primary_url: str,
    content_production_status: str | None = None,
    content_production: dict[str, Any] | None = None,
    content_planning: dict[str, Any] | None = None,
    site_architecture: dict[str, Any] | None = None,
    competitive_landscape: dict[str, Any] | None = None,
    trademark_denylist: list[str] | None = None,
) -> dict[str, Any]:
    production = dict(content_production or {})
    planning = dict(content_planning or {})
    ia = dict(site_architecture or {})

    ok, gate = production_gate_ok(content_production_status, production, planning)
    if not ok:
        return {
            "blocked": True,
            "reason": (
                "On-Page SEO needs Content Production briefs/drafts "
                "(or a Content Planning roadmap fallback)."
            ),
        }

    queue = selected_on_page_rows(production)
    if not queue:
        return {
            "blocked": True,
            "reason": (
                "On-Page SEO packages the topic you selected in Content Production — "
                "not the full brief queue. Write the draft for that topic, then run On-Page SEO."
            ),
        }

    pages: list[dict[str, Any]] = []
    internal_links: list[dict[str, Any]] = []
    providers_used: list[str] = []
    link_plan: dict[str, Any] = {}

    for row in queue[:1]:
        path = _path(str(row.get("url") or row.get("path") or "/"))
        abs_url = row.get("url") if str(row.get("url") or "").startswith("http") else primary_url.rstrip("/") + path
        kw = row.get("keyword") or row.get("target_keyword")
        opt: dict[str, Any] = {}
        try:
            opt = await optimize_on_page(
                str(abs_url),
                display_name=client_name,
                keyword=str(kw) if kw else None,
            )
            providers_used.append("optimize_on_page")
        except Exception:  # noqa: BLE001
            title_after = f"{str(kw or client_name).title()} | {client_name}"
            meta = f"Learn about {kw or client_name} from {client_name}."[:160]
            opt = {
                "page_url": abs_url,
                "target_keyword": kw,
                "title": {"before": client_name, "after": title_after, "length_after": len(title_after)},
                "meta_description": {"before": "", "after": meta, "length_after": len(meta)},
                "headings": {
                    "h1": str(kw or client_name).title(),
                    "h2s": list(row.get("outline") or [])[:5] or [f"About {kw}", "FAQs"],
                },
                "source": "rule_based",
            }

        schema = _schema_for({**row, **opt, "url": abs_url}, client_name, primary_url)
        page = {
            "url": abs_url,
            "path": path,
            "keyword": kw or opt.get("target_keyword"),
            "funnel": row.get("funnel"),
            "title": opt.get("title"),
            "meta_description": opt.get("meta_description"),
            "headings": opt.get("headings"),
            "h1": (opt.get("headings") or {}).get("h1") if isinstance(opt.get("headings"), dict) else None,
            "schema_types": schema["types"],
            "schema_json_ld": schema["json_ld"],
            "current_score": opt.get("current_score"),
            "optimized_score": opt.get("optimized_score"),
        }
        pages.append(page)

        brief_links = [
            ln
            for ln in (row.get("internal_links") or opt.get("internal_links") or [])
            if isinstance(ln, dict)
        ]
        linking = build_internal_linking_plan(
            page_path=path,
            page_keyword=str(kw) if kw else None,
            parent=(
                str(row.get("parent") or "").strip()
                or str((row.get("preflight") or {}).get("parent") or "").strip()
                or None
            ),
            site_architecture=ia,
            content_planning=planning,
            brief_links=brief_links,
            page_type=(
                str(row.get("page_type") or "").strip()
                or str((row.get("preflight") or {}).get("page_type") or "").strip()
                or None
            ),
        )
        internal_links.extend(linking.get("links") or [])
        link_plan = linking

    # Dedupe links (plan may already be clean; keep stable order)
    seen: set[tuple[str, str]] = set()
    deduped = []
    for ln in internal_links:
        key = (str(ln.get("from")), str(ln.get("to")))
        if key in seen or not ln.get("to"):
            continue
        seen.add(key)
        deduped.append(ln)

    links = deduped[:40]
    if not link_plan:
        link_plan = {
            "links": links,
            "orphans": [],
            "dead_ends": [],
            "depth_4_plus": [],
            "cluster_map": [],
            "pages_analyzed": len(pages),
            "link_count": len(links),
            "note": "No internal linking targets resolved.",
        }
    else:
        link_plan = {**link_plan, "links": links, "link_count": len(links)}

    # v1.9 guardrail — strip competitor brands before the package is reviewable.
    terms = competitor_terms(
        competitive_landscape,
        client_name=client_name,
        extra_denylist=trademark_denylist,
    )
    trademark_blocked = apply_trademark_block(
        pages, links, terms=terms, client_name=client_name
    )

    from app.services.phase_pipeline import enrich_phase11_pack

    return enrich_phase11_pack(
        {
        "client_name": client_name,
        "primary_url": primary_url,
        "pages": pages,
        "queue": pages,
        "internal_links": links,
        "internal_linking": link_plan,
        "orphans": link_plan.get("orphans") or [],
        "dead_ends": link_plan.get("dead_ends") or [],
        "depth_4_plus": link_plan.get("depth_4_plus") or [],
        "cluster_map": link_plan.get("cluster_map") or [],
        "schema_items": [
            {"url": p.get("url"), "types": p.get("schema_types"), "json_ld": p.get("schema_json_ld")}
            for p in pages
        ],
        "trademark_blocked": trademark_blocked,
        "trademark_terms_checked": len(terms),
        "gate": gate,
        "providers_used": list(dict.fromkeys(providers_used)),
        "source": "on_page_seo",
        "selected_url": (queue[0].get("url") if queue else None),
        "selected_keyword": (queue[0].get("keyword") if queue else None),
        "target_keyword": (queue[0].get("keyword") if queue else None),
        "one_page_only": True,
        "skills_used": ["on_page_seo", "schema_markup", "internal_linking"],
        "note": (
            "On-page package for the selected draft only "
            f"({(queue[0].get('keyword') if queue else 'topic')}) — titles, schema, and "
            f"internal linking ({len(links)} link suggestions). Publish in Phase 12."
        ),
        }
    )
