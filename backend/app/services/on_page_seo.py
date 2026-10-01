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
            _scrub_schema_terms(json_ld, terms, url=url, prefix="schema_json_ld", record=_record)

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


def _scrub_schema_terms(
    node: Any,
    terms: list[str],
    *,
    url: str,
    prefix: str,
    record: Any,
) -> None:
    """Drop schema strings that name a competitor. Nested @graph values count."""
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str):
                _cleaned, hits = _strip_terms(value, terms)
                if hits:
                    node.pop(key, None)
                    record(url, f"{prefix}.{key}", hits)
            elif isinstance(value, (dict, list)):
                _scrub_schema_terms(value, terms, url=url, prefix=f"{prefix}.{key}", record=record)
    elif isinstance(node, list):
        for item in node:
            _scrub_schema_terms(item, terms, url=url, prefix=prefix, record=record)


def _display_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("after", "text", "title", "name", "before"):
            text = value.get(key)
            if text:
                return str(text).strip()
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _absolute_url(url: str, primary_url: str) -> str:
    raw = str(url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    base = str(primary_url or "").strip().rstrip("/")
    if base and not base.startswith("http"):
        base = "https://" + base
    path = _path(raw)
    if not base:
        return path
    return base + ("/" if path == "/" else path)


def _preflight(page: dict[str, Any]) -> dict[str, Any]:
    pre = page.get("preflight")
    return pre if isinstance(pre, dict) else {}


def _named_author(page: dict[str, Any], client_name: str) -> str | None:
    pre = _preflight(page)
    who = page.get("who_how_why") if isinstance(page.get("who_how_why"), dict) else {}
    person = who.get("who") if isinstance(who.get("who"), dict) else {}
    name = str(person.get("author") or pre.get("author") or page.get("author") or "").strip()
    if not name or name.lower() == client_name.strip().lower():
        return None
    return name


def _offer_node(page: dict[str, Any]) -> dict[str, Any] | None:
    raw = page.get("offers") if isinstance(page.get("offers"), dict) else None
    if raw is None and isinstance(page.get("offer"), dict):
        raw = page["offer"]
    price = (raw or {}).get("price") if raw else page.get("price")
    if price in (None, ""):
        return None
    currency = "USD"
    if raw:
        currency = str(raw.get("priceCurrency") or raw.get("currency") or currency)
    elif page.get("priceCurrency"):
        currency = str(page["priceCurrency"])
    node: dict[str, Any] = {"@type": "Offer", "price": str(price), "priceCurrency": currency}
    availability = (raw or {}).get("availability") if raw else page.get("availability")
    if availability:
        node["availability"] = str(availability)
    return node


def _rating_node(page: dict[str, Any]) -> dict[str, Any] | None:
    raw = page.get("aggregateRating") or page.get("aggregate_rating")
    if not isinstance(raw, dict) or raw.get("ratingValue") in (None, ""):
        return None
    node: dict[str, Any] = {"@type": "AggregateRating", "ratingValue": raw["ratingValue"]}
    if raw.get("reviewCount") not in (None, ""):
        node["reviewCount"] = raw["reviewCount"]
    return node


def _faq_entities(page: dict[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for item in page.get("faq") or []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("q") or item.get("name") or "").strip()
        answer = item.get("a") or item.get("acceptedAnswer") or item.get("answer")
        if isinstance(answer, dict):
            answer = answer.get("text")
        answer_text = str(answer or "").strip()
        if question and answer_text:
            entities.append(
                {
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {"@type": "Answer", "text": answer_text},
                }
            )
    return entities


def _howto_steps(page: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in page.get("steps") or []:
        if isinstance(item, str) and item.strip():
            steps.append({"@type": "HowToStep", "text": item.strip()})
        elif isinstance(item, dict) and (item.get("text") or item.get("name")):
            steps.append(
                {
                    "@type": "HowToStep",
                    "name": str(item.get("name") or item.get("text") or ""),
                    "text": str(item.get("text") or item.get("name") or ""),
                }
            )
    return steps


def _faq_rich_result_ok(page: dict[str, Any]) -> bool:
    blob = " ".join(
        str(page.get(key) or "")
        for key in ("industry", "site_type", "vertical", "organization_type")
    ).lower()
    return any(token in blob for token in ("government", "public health", "hospital"))


_SERVICE_PAGE_TYPES = {"service", "subservice", "sub_service", "landing", "location"}
_ARTICLE_PAGE_TYPES = {"blog", "guide", "article", "listicle", "spoke"}
_HOWTO_PAGE_TYPES = {"howto", "how_to", "tutorial"}


def schema_for_page(page: dict[str, Any], client_name: str, primary_url: str) -> dict[str, Any]:
    """JSON-LD from page type. Schema.org validity and Google eligibility stay separate.

    Ratings, prices, and authors are omitted unless the brief already has them.
    FAQ and HowTo markup does not claim a rich result for a normal commercial client.
    """
    from app.services.create_content import resolve_page_type

    pre = _preflight(page)
    abs_url = _absolute_url(str(page.get("url") or page.get("page_url") or primary_url), primary_url)
    name = _display_text(page.get("title")) or str(
        page.get("keyword") or page.get("target_keyword") or client_name
    )
    description = _display_text(page.get("meta_description") or page.get("meta")) or f"{name} — {client_name}"
    declared = str(page.get("page_type") or pre.get("page_type") or "").strip().lower()
    page_type = resolve_page_type(
        {
            "url": abs_url,
            "path": page.get("path") or pre.get("url"),
            "keyword": page.get("keyword") or page.get("target_keyword"),
            "title": name,
            "page_type": page.get("page_type") or pre.get("page_type"),
            "content_type": page.get("content_type"),
            "search_intent": page.get("search_intent") or page.get("intent"),
            "preflight": pre,
        }
    )
    if declared in _HOWTO_PAGE_TYPES:
        page_type = declared
    hint = str(page.get("schema_type") or "").strip().lower()
    parent = str(page.get("parent") or pre.get("parent") or "").strip()
    author = _named_author(page, client_name)
    offer = _offer_node(page)
    rating = _rating_node(page)
    omitted: list[str] = []
    nodes: list[dict[str, Any]] = []
    reasons: list[str] = []

    if page_type == "product" or hint == "product":
        product: dict[str, Any] = {"@type": "Product", "name": name, "description": description, "url": abs_url}
        if offer:
            product["offers"] = offer
        else:
            omitted.append("offers")
            reasons.append("Product rich results need a real price; none was supplied.")
        if rating:
            product["aggregateRating"] = rating
        else:
            omitted.append("aggregateRating")
        nodes.append(product)
    elif page_type in _SERVICE_PAGE_TYPES or hint == "service":
        nodes.append(
            {
                "@type": "Service",
                "name": name,
                "description": description,
                "url": abs_url,
                "provider": {"@type": "Organization", "name": client_name, "url": _absolute_url(primary_url, primary_url)},
            }
        )
        if not rating:
            omitted.append("aggregateRating")
        reasons.append("Service markup is valid Schema.org. Google does not show a Service rich result.")
    elif page_type == "comparison":
        kind = "Article" if hint == "article" else "WebPage"
        node = {"@type": kind, "name": name, "headline": name, "description": description, "url": abs_url}
        if kind == "Article" and author:
            node["author"] = {"@type": "Person", "name": author}
        elif kind == "Article":
            omitted.append("author")
        nodes.append(node)
        if hint == "review":
            omitted.append("review")
        reasons.append("Comparison pages do not emit a Review of a rival.")
    elif page_type in _HOWTO_PAGE_TYPES or hint == "howto":
        steps = _howto_steps(page)
        if steps:
            nodes.append({"@type": "HowTo", "name": name, "description": description, "step": steps})
            reasons.append("HowTo rich results are deprecated. Markup is for Schema.org only.")
        else:
            omitted.append("HowTo")
            nodes.append({"@type": "WebPage", "name": name, "description": description, "url": abs_url})
    elif page_type == "faq" or hint == "faqpage":
        entities = _faq_entities(page)
        if entities:
            nodes.append({"@type": "FAQPage", "mainEntity": entities})
            if not _faq_rich_result_ok(page):
                reasons.append(
                    "FAQ rich results are limited to well-known government and health sites."
                )
        else:
            omitted.append("FAQPage")
            nodes.append({"@type": "WebPage", "name": name, "description": description, "url": abs_url})
            reasons.append("FAQPage needs real questions and answers on the page.")
    elif page_type in _ARTICLE_PAGE_TYPES or hint == "article":
        article: dict[str, Any] = {
            "@type": "Article",
            "headline": name,
            "description": description,
            "mainEntityOfPage": abs_url,
        }
        if author:
            article["author"] = {"@type": "Person", "name": author}
        else:
            omitted.append("author")
        image = page.get("image") or page.get("hero_image")
        if image:
            article["image"] = str(image)
        else:
            omitted.append("image")
        published = page.get("datePublished") or page.get("date_published")
        if published:
            article["datePublished"] = str(published)
        else:
            omitted.append("datePublished")
        nodes.append(article)
        if "author" in omitted or "image" in omitted or "datePublished" in omitted:
            reasons.append("Article is missing properties Google requires for an article rich result.")
    else:
        nodes.append(
            {
                "@type": "WebPage",
                "name": name,
                "description": description,
                "url": abs_url,
                "isPartOf": {
                    "@type": "WebSite",
                    "name": client_name,
                    "url": _absolute_url(primary_url, primary_url),
                },
            }
        )

    faq_entities = _faq_entities(page)
    already_faq = any(node.get("@type") == "FAQPage" for node in nodes)
    if faq_entities and not already_faq and (page_type == "faq" or hint == "faqpage"):
        nodes.append({"@type": "FAQPage", "mainEntity": faq_entities})

    if parent:
        parent_url = _absolute_url(parent, primary_url)
        home = _absolute_url(primary_url, primary_url)
        nodes.append(
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": home},
                    {"@type": "ListItem", "position": 2, "name": parent.strip("/").replace("-", " ").title() or "Parent", "item": parent_url},
                    {"@type": "ListItem", "position": 3, "name": name, "item": abs_url},
                ],
            }
        )

    types = [str(node.get("@type")) for node in nodes if node.get("@type")]
    rich = "eligible"
    if "HowTo" in types:
        rich = "not_eligible"
    if "FAQPage" in types and not _faq_rich_result_ok(page):
        rich = "not_eligible"
    if "Product" in types and "offers" in omitted:
        rich = "not_eligible"
    if "Service" in types or "WebPage" in types:
        rich = "not_eligible"
    if "Article" in types and any(field in omitted for field in ("author", "image", "datePublished")):
        rich = "not_eligible"
    if not reasons and rich == "not_eligible":
        reasons.append("Valid Schema.org does not by itself earn a Google rich result.")

    return {
        "types": types,
        "json_ld": {"@context": "https://schema.org", "@graph": nodes},
        "omitted": omitted,
        "eligibility": {
            "schema_org": "valid",
            "google_rich_result": rich,
            "reason": " ".join(reasons),
        },
    }


def _schema_for(page: dict[str, Any], client_name: str, primary_url: str) -> dict[str, Any]:
    return schema_for_page(page, client_name, primary_url)


def build_eeat_checklist(page: dict[str, Any]) -> dict[str, Any]:
    """Pass/gap checklist from the brief. This is not a Quality Rater score."""
    pre = _preflight(page)
    who_how_why = page.get("who_how_why") if isinstance(page.get("who_how_why"), dict) else {}
    who = who_how_why.get("who") if isinstance(who_how_why.get("who"), dict) else {}
    gates = page.get("people_first_gates") if isinstance(page.get("people_first_gates"), dict) else {}
    standing = str(who.get("standing") or pre.get("author_standing") or "").strip()
    ymyl = bool(who.get("ymyl") or pre.get("ymyl"))
    author = _named_author(page, str(page.get("client_name") or ""))
    differentiation = str(page.get("differentiation") or "")
    experience_ok = bool(gates.get("has_differentiation_beyond_consensus")) or len(differentiation) >= 24
    if standing:
        expertise_status = "pass"
        expertise_note = "Author standing is on the brief."
    elif ymyl:
        expertise_status = "gap"
        expertise_note = "YMYL topic without author standing."
    else:
        expertise_status = "gap"
        expertise_note = "Author standing is missing."
    citations = page.get("external_links")
    coverage = page.get("required_coverage") if isinstance(page.get("required_coverage"), dict) else {}
    trust_blob = differentiation.lower()
    trust_ok = bool(coverage.get("out_of_scope")) or any(
        token in trust_blob for token in ("limit", "trade-off", "tradeoff", "depends")
    )
    return {
        "who_how_why": who_how_why or None,
        "people_first_gates": gates or None,
        "checklist": {
            "experience": {
                "status": "pass" if experience_ok else "gap",
                "note": "First-hand detail is required."
                if not experience_ok
                else "Brief carries a specific angle.",
            },
            "expertise": {
                "status": expertise_status,
                "author": author,
                "standing": standing or None,
                "ymyl": ymyl,
                "note": expertise_note,
            },
            "authoritativeness": {
                "status": "pass" if isinstance(citations, list) else "gap",
                "note": "Citations stay limited to sources the brief supplied.",
            },
            "trust": {
                "status": "pass" if trust_ok else "gap",
                "note": "Limits and trade-offs are noted."
                if trust_ok
                else "Limits and trade-offs are not noted.",
            },
        },
    }


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

        schema = schema_for_page({**row, **opt, "url": abs_url}, client_name, primary_url)
        eeat = build_eeat_checklist({**row, "client_name": client_name})
        images = opt.get("images") if isinstance(opt.get("images"), list) else []
        images_note = opt.get("images_note")
        if not images and not images_note:
            images_note = "Page was not fetched — image checks were not run."
        page = {
            "url": abs_url,
            "path": path,
            "keyword": kw or opt.get("target_keyword"),
            "funnel": row.get("funnel"),
            "action": row.get("action"),
            "work_label": row.get("work_label"),
            "title": opt.get("title"),
            "meta_description": opt.get("meta_description"),
            "headings": opt.get("headings"),
            "h1": (opt.get("headings") or {}).get("h1") if isinstance(opt.get("headings"), dict) else None,
            "schema_types": schema["types"],
            "schema_json_ld": schema["json_ld"],
            "schema_omitted": schema["omitted"],
            "schema_eligibility": schema["eligibility"],
            "eeat": eeat,
            "images": images,
            "images_note": images_note,
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
            {
                "url": p.get("url"),
                "types": p.get("schema_types"),
                "json_ld": p.get("schema_json_ld"),
                "omitted": p.get("schema_omitted"),
                "eligibility": p.get("schema_eligibility"),
            }
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
