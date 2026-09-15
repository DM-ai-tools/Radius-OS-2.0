"""Phase 3 — client website sitemap / URL inventory from crawl + SEO audit."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# Soft UI/memory cap — full URL list still kept separately for downstream phases.
DEFAULT_MAX_ENTRIES = 500

# "acme.com", "www.acme.com/services" — a host, optionally with a path.
_HOSTISH = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?::\d+)?(?:/|$)", re.IGNORECASE)

# Corporate / utility pages. Without this every depth-1 URL fell through the
# depth heuristic and was labelled a "service hub", so /about, /contact and
# /privacy-policy all read as commercial hubs to downstream intent matching.
_UTILITY_SLUGS = frozenset(
    {
        "about", "about-us", "aboutus", "our-story", "who-we-are", "team", "our-team",
        "contact", "contact-us", "contactus", "get-in-touch",
        "careers", "career", "jobs", "join-us",
        "privacy", "privacy-policy", "terms", "terms-of-service", "terms-and-conditions",
        "cookie-policy", "cookies", "legal", "disclaimer", "accessibility",
        "sitemap", "search", "thank-you", "thanks", "404", "not-found",
        "login", "signin", "sign-in", "register", "account",
    }
)

_CLUSTER_ORDER = (
    "home",
    "service_hub",
    "service",
    "sub_service",
    "location",
    "guide",
    "blog",
    "other",
)


def _norm_url(raw: str | None) -> str:
    u = str(raw or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif not u.startswith(("http://", "https://")):
        # A schemeless host ("acme.com") parses with an empty netloc and the
        # host sitting in .path, which used to mint a phantom "/acme.com" page.
        # Only promote something that actually looks like a host, so a bare
        # path ("/services/seo") is still treated as a path.
        if u.startswith("/"):
            return u.rstrip("/") or "/"
        if _HOSTISH.match(u):
            u = "https://" + u
    return u.rstrip("/") or u


def _path_of(url: str) -> str:
    try:
        path = urlparse(url).path or "/"
    except Exception:  # noqa: BLE001
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _depth(path: str) -> int:
    parts = [p for p in path.split("/") if p]
    return len(parts)


_BLOG_SEGMENTS = frozenset({"blog", "blogs", "news", "article", "articles", "insights", "post", "posts"})
_GUIDE_SEGMENTS = frozenset({"guide", "guides", "resource", "resources", "learn", "faq", "faqs", "help"})
_LOCATION_SEGMENTS = frozenset({"location", "locations", "area", "areas", "suburb", "suburbs", "city", "cities"})


def _guess_cluster(path: str, *, is_home: bool = False) -> tuple[str, str]:
    if is_home or path in ("/", ""):
        return "home", "Home"
    segments = [s for s in path.split("/") if s]
    lowered = {s.lower() for s in segments}
    # Segment match, not substring: "/news" used to match inside
    # "/the-smashing-newsletter", typing a product page as a blog.
    if lowered & _BLOG_SEGMENTS:
        return "blog", "Blog / articles"
    if lowered & _GUIDE_SEGMENTS:
        return "guide", "Guides"
    if lowered & _LOCATION_SEGMENTS:
        return "location", "Locations"
    if segments and segments[-1].lower() in _UTILITY_SLUGS:
        return "other", "Other pages"
    # Shallow commercial paths look like hubs; deeper like services.
    depth = _depth(path)
    if depth <= 1:
        return "service_hub", "Service hubs"
    if depth == 2:
        return "service", "Service pages"
    if depth >= 3:
        return "sub_service", "Sub-service pages"
    return "other", "Other pages"


def _parent_path(path: str) -> str | None:
    """Parent path from the URL hierarchy ('/' for top level, None for home)."""
    p = str(path or "/").rstrip("/")
    if not p or p == "":
        return None
    parts = [s for s in p.split("/") if s]
    if len(parts) <= 1:
        return "/"
    return "/" + "/".join(parts[:-1])


def _existing_keywords(page: dict[str, Any]) -> list[str]:
    """Keywords a page is already known to target, from whatever the audit had."""
    out: list[str] = []
    for key in ("keyword", "primary_keyword", "target_keyword"):
        val = page.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
    for key in ("keywords", "target_keywords", "queries"):
        val = page.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    kw = item.get("keyword") or item.get("query")
                    if kw:
                        out.append(str(kw))
                elif item:
                    out.append(str(item))
    seen: set[str] = set()
    return [k for k in out if not (k.lower() in seen or seen.add(k.lower()))][:12]


def _entry_from_seo_page(page: dict[str, Any]) -> dict[str, Any] | None:
    url = _norm_url(page.get("url"))
    if not url:
        return None
    path = str(page.get("path") or _path_of(url))
    cluster = str(page.get("cluster") or "")
    label = str(page.get("cluster_label") or "")
    if not cluster:
        cluster, label = _guess_cluster(path, is_home=path in ("/", ""))
    return {
        "url": url,
        "path": path,
        "title": page.get("title") or path,
        "status": page.get("status"),
        "cluster": cluster,
        "cluster_label": label or cluster,
        # page_type is the site-map entity's own field; cluster stays as the
        # coarser category so existing readers keep working.
        "page_type": str(page.get("page_type") or cluster),
        "category": label or cluster,
        "depth": int(page.get("hierarchy_level") or _depth(path)),
        "parent_url": _parent_path(path),
        "canonical_url": _norm_url(page.get("canonical_url") or page.get("canonical")) or None,
        "existing_keywords": _existing_keywords(page),
        "word_count": page.get("word_count"),
        "cdd_focus": bool(page.get("cdd_focus")),
        "source": "seo_audit",
    }


def _entry_from_url(
    url: str,
    *,
    title: str | None = None,
    status: Any = None,
    source: str = "crawl",
) -> dict[str, Any] | None:
    u = _norm_url(url)
    if not u:
        return None
    path = _path_of(u)
    cluster, label = _guess_cluster(path, is_home=path in ("/", ""))
    return {
        "url": u,
        "path": path,
        "title": title or path,
        "status": status,
        "cluster": cluster,
        "cluster_label": label,
        "page_type": cluster,
        "category": label,
        "depth": _depth(path),
        "parent_url": _parent_path(path),
        "canonical_url": None,
        "existing_keywords": [],
        "word_count": None,
        "cdd_focus": False,
        "source": source,
    }


_TOPIC_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "our", "your",
    "home", "page", "index", "services", "service",
}


def _is_ancestor(maybe_parent: str, path: str) -> bool:
    """True when ``path`` sits under ``maybe_parent`` in the URL hierarchy."""
    a = (maybe_parent or "").rstrip("/")
    b = (path or "").rstrip("/")
    if not a or not b or a == b:
        return False
    if a == "":
        return True
    return b.startswith(a + "/")


def _tokenize(text: str) -> set[str]:
    # Two-character tokens are kept: "ai", "ux" and "hr" are whole topics, and
    # dropping them left sibling pages with nothing but their shared section
    # name to compare on.
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if t not in _TOPIC_STOP and len(t) > 1
    }


def _topic_tokens(entry: dict[str, Any]) -> set[str]:
    blob = f"{entry.get('title') or ''} {str(entry.get('path') or '').replace('-', ' ').replace('/', ' ')}"
    return _tokenize(blob)


def annotate_sitemap_pages(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the structural judgements the site map is supposed to be the source
    of truth for: overlapping topics, orphans, thin pages, and notes.

    Everything here is derived from inventory the crawl actually produced — no
    page is invented and no quality claim is made without a signal behind it.
    """
    known_paths = {str(e.get("path") or "") for e in entries}
    token_map = {str(e.get("path") or ""): _topic_tokens(e) for e in entries}
    title_map: dict[str, list[str]] = {}
    # A parent shared by several pages is a section without an index page
    # (/blog/* with no /blog), not a set of orphans. Counting those as orphans
    # flagged most blogs on most sites.
    parent_counts: dict[str, int] = {}
    for entry in entries:
        title = str(entry.get("title") or "").strip().lower()
        if title:
            title_map.setdefault(title, []).append(str(entry.get("path") or ""))
        parent = entry.get("parent_url")
        if parent:
            parent_counts[parent] = parent_counts.get(parent, 0) + 1

    for entry in entries:
        path = str(entry.get("path") or "")
        notes: list[str] = []

        parent = entry.get("parent_url")
        missing_parent = bool(parent and parent != "/" and parent not in known_paths)
        section_root = missing_parent and parent_counts.get(parent, 0) > 1
        orphaned = missing_parent and not section_root
        if section_root:
            notes.append(f"Section {parent} has no index page in the inventory.")
        elif orphaned:
            notes.append(f"Parent {parent} is not in the inventory (possible orphan).")

        # Pages that appear to target the same or overlapping topic.
        mine = token_map.get(path) or set()
        overlapping: list[str] = []
        if mine:
            for other in entries:
                other_path = str(other.get("path") or "")
                if other_path == path or not other_path:
                    continue
                # A hub and its own children are a hierarchy, not a conflict.
                # /community and /community/awards are supposed to share tokens.
                if _is_ancestor(path, other_path) or _is_ancestor(other_path, path):
                    continue
                theirs = token_map.get(other_path) or set()
                if not theirs:
                    continue
                a, b = mine, theirs
                if entry.get("parent_url") and entry["parent_url"] == other.get("parent_url"):
                    # Siblings inherit their section's name. Compare only what
                    # distinguishes them, or /category/ai and /category/ux read
                    # as the same topic because both are "category".
                    inherited = _tokenize(str(entry["parent_url"]).replace("-", " ").replace("/", " "))
                    a, b = a - inherited, b - inherited
                    if not a or not b:
                        continue
                # Symmetric overlap. Dividing by the smaller set (as this did)
                # scores a one-token page 1.0 against anything containing that
                # token, so every section root "cannibalized" its whole section.
                shared = len(a & b) / len(a | b)
                if shared >= 0.7:
                    overlapping.append(other_path)
        duplicate_titles = [
            p for p in title_map.get(str(entry.get("title") or "").strip().lower(), []) if p != path
        ]
        if duplicate_titles:
            notes.append(f"Duplicate title with {', '.join(duplicate_titles[:3])}.")
        if overlapping:
            notes.append(f"Overlapping topic with {', '.join(sorted(overlapping)[:3])}.")

        word_count = entry.get("word_count")
        quality: str | None = None
        if isinstance(word_count, (int, float)):
            quality = "thin" if word_count < 300 else "adequate" if word_count < 900 else "in_depth"
            if quality == "thin":
                notes.append(f"Thin content ({int(word_count)} words).")
        status = entry.get("status")
        if isinstance(status, (int, float)) and int(status) >= 400:
            quality = "broken"
            notes.append(f"Returns HTTP {int(status)}.")

        entry["orphaned"] = orphaned
        entry["section_root"] = section_root
        entry["potential_cannibalization"] = sorted(set(overlapping + duplicate_titles))[:5]
        entry["content_quality"] = quality
        entry["notes"] = " ".join(notes) or None
    return entries


def build_client_sitemap(
    *,
    primary_url: str,
    seo_audit: dict[str, Any] | None = None,
    crawl: dict[str, Any] | None = None,
    page_inventory: dict[str, Any] | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> dict[str, Any]:
    """Build a current-site sitemap inventory for Phase 3.

    Prefers SEO-audit pages (title/cluster/status), then site-page-inventory
    (Perplexity), then crawl discovered URLs / status samples. Does not invent URLs.
    """
    seo = dict(seo_audit or {})
    crawl_d = dict(crawl or {})
    inventory = dict(page_inventory or {})
    # Keyed by PATH, not URL. A site reached as both acme.com and www.acme.com
    # (or http and https) otherwise lands in the inventory twice, and the
    # duplicates then read as two real pages competing for one topic.
    by_url: dict[str, dict[str, Any]] = {}
    sources: list[str] = []

    def _key(entry: dict[str, Any]) -> str:
        return str(entry.get("path") or _path_of(entry.get("url") or "")) or "/"

    def _merge(entry: dict[str, Any]) -> None:
        """Keep the richer of two rows for the same path."""
        key = _key(entry)
        prior = by_url.get(key)
        if not prior:
            by_url[key] = entry
            return
        for field, value in entry.items():
            if value in (None, "", [], {}):
                continue
            current = prior.get(field)
            if current in (None, "", [], {}) or (
                field == "title" and current == prior.get("path")
            ):
                prior[field] = value

    seo_pages = [p for p in (seo.get("pages") or []) if isinstance(p, dict)]
    if seo_pages:
        sources.append("seo_audit")
        for page in seo_pages:
            entry = _entry_from_seo_page(page)
            if entry:
                _merge(entry)

    inv_pages = [p for p in (inventory.get("pages") or []) if isinstance(p, dict)]
    if inv_pages:
        sources.append("perplexity_inventory")
        for page in inv_pages:
            # Prefer inventory-shaped SEO page entry when title/type present.
            shaped = {
                **page,
                "cluster": page.get("cluster")
                or ("blog" if page.get("page_type") == "blog_article" else page.get("page_type")),
                "canonical_url": page.get("canonical") or page.get("canonical_url"),
            }
            entry = _entry_from_seo_page(shaped) or _entry_from_url(
                str(page.get("url") or ""),
                title=str(page.get("title") or "") or None,
                status=page.get("status") or page.get("status_code"),
                source="perplexity_inventory",
            )
            if entry:
                if page.get("orphan") is not None:
                    entry["orphan"] = bool(page.get("orphan"))
                if page.get("in_xml_sitemap") is not None:
                    entry["in_xml_sitemap"] = bool(page.get("in_xml_sitemap"))
                if page.get("issues"):
                    entry["issues"] = list(page.get("issues") or [])
                _merge(entry)

    if crawl_d:
        if "crawl" not in sources and (
            crawl_d.get("discovered_urls") or crawl_d.get("status_samples") or crawl_d.get("pages_found")
        ):
            sources.append("crawl")
        for sample in crawl_d.get("status_samples") or []:
            if not isinstance(sample, dict):
                continue
            entry = _entry_from_url(
                str(sample.get("url") or ""),
                title=str(sample.get("title") or "") or None,
                status=sample.get("status"),
                source="crawl",
            )
            if not entry:
                continue
            _merge(entry)
        for raw in crawl_d.get("discovered_urls") or []:
            entry = _entry_from_url(str(raw or ""), source="crawl")
            if entry:
                _merge(entry)

    # Ensure primary / home is present when we have any inventory.
    home_entry = _entry_from_url(primary_url, title="Home", status=200, source="primary_url")
    if home_entry and by_url and _key(home_entry) not in by_url:
        _merge(home_entry)
        if "primary_url" not in sources:
            sources.append("primary_url")

    entries = list(by_url.values())
    order_index = {k: i for i, k in enumerate(_CLUSTER_ORDER)}
    entries.sort(
        key=lambda e: (
            order_index.get(str(e.get("cluster") or "other"), 99),
            int(e.get("depth") or 0),
            str(e.get("path") or ""),
        )
    )

    capped = annotate_sitemap_pages(
        entries[: max(1, min(int(max_entries or DEFAULT_MAX_ENTRIES), 2000))]
    )
    sections: list[dict[str, Any]] = []
    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for e in capped:
        key = str(e.get("cluster") or "other")
        by_cluster.setdefault(key, []).append(e)
    for key in list(_CLUSTER_ORDER) + [k for k in by_cluster if k not in _CLUSTER_ORDER]:
        rows = by_cluster.get(key) or []
        if not rows:
            continue
        sections.append(
            {
                "cluster": key,
                "label": rows[0].get("cluster_label") or key,
                "count": len(rows),
                "pages": rows,
            }
        )

    return {
        "url_count": len(entries),
        "shown_count": len(capped),
        "truncated": len(entries) > len(capped),
        "sources": sources,
        "primary_url": _norm_url(primary_url) or primary_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
        "urls": [e["url"] for e in capped],
        "pages": capped,
        "page_inventory_stats": inventory.get("stats") if inventory else None,
        "page_inventory_findings": inventory.get("findings") if inventory else None,
        "page_inventory_method": inventory.get("method_note") if inventory else None,
        "page_inventory_csv": inventory.get("csv") if inventory else None,
    }


def extract_site_sitemap(website: dict[str, Any] | None) -> dict[str, Any] | None:
    """Read the Phase 3 process-wide sitemap from a website_situation pack."""
    pack = dict(website or {})
    raw = pack.get("site_sitemap")
    if isinstance(raw, dict) and (raw.get("pages") or raw.get("urls") or raw.get("sections")):
        return raw
    crawl = pack.get("crawl_technical") or pack.get("crawl") or {}
    if isinstance(crawl, dict):
        summary = crawl.get("summary") if isinstance(crawl.get("summary"), dict) else crawl
        nested = summary.get("site_sitemap") if isinstance(summary, dict) else None
        if isinstance(nested, dict) and (nested.get("pages") or nested.get("urls")):
            return nested
    tech = pack.get("tabs") if isinstance(pack.get("tabs"), dict) else {}
    if isinstance(tech, dict):
        technical = tech.get("technical") if isinstance(tech.get("technical"), dict) else {}
        nested = technical.get("site_sitemap") if isinstance(technical, dict) else None
        if isinstance(nested, dict) and (nested.get("pages") or nested.get("urls")):
            return nested
    return None


def sitemap_pages(website: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalized page rows from the Phase 3 sitemap (preferred inventory)."""
    sm = extract_site_sitemap(website)
    if not sm:
        return []
    pages = [p for p in (sm.get("pages") or []) if isinstance(p, dict) and (p.get("url") or p.get("path"))]
    if pages:
        return pages
    out: list[dict[str, Any]] = []
    for raw in sm.get("urls") or []:
        entry = _entry_from_url(str(raw or ""), source="site_sitemap")
        if entry:
            out.append(entry)
    return out


def sitemap_is_truncated(website: dict[str, Any] | None) -> bool:
    """True when the inventory holds fewer pages than the site actually has.

    Matters downstream: "no existing page covers this cluster" is only a safe
    conclusion when the whole site was compared against.
    """
    sm = extract_site_sitemap(website) or {}
    if sm.get("truncated"):
        return True
    try:
        return int(sm.get("url_count") or 0) > len(sm.get("pages") or [])
    except (TypeError, ValueError):
        return False


def sitemap_urls(website: dict[str, Any] | None, *, limit: int | None = None) -> list[str]:
    """URL strings from the Phase 3 sitemap for seeding later-phase crawls."""
    pages = sitemap_pages(website)
    urls: list[str] = []
    seen: set[str] = set()
    for page in pages:
        u = _norm_url(page.get("url") or page.get("path"))
        if not u or u in seen:
            continue
        seen.add(u)
        urls.append(u)
        if limit is not None and len(urls) >= max(1, int(limit)):
            break
    if urls:
        return urls
    sm = extract_site_sitemap(website) or {}
    for raw in sm.get("urls") or []:
        u = _norm_url(str(raw or ""))
        if not u or u in seen:
            continue
        seen.add(u)
        urls.append(u)
        if limit is not None and len(urls) >= max(1, int(limit)):
            break
    return urls


def ensure_website_sitemap(
    website: dict[str, Any] | None,
    *,
    primary_url: str | None = None,
) -> dict[str, Any]:
    """Return website pack with ``site_sitemap`` present when rebuildable."""
    pack = dict(website or {})
    if extract_site_sitemap(pack):
        if "site_sitemap" not in pack:
            pack["site_sitemap"] = extract_site_sitemap(pack)
        return pack
    rebuilt = build_client_sitemap(
        primary_url=str(primary_url or pack.get("primary_url") or ""),
        seo_audit=pack if pack.get("pages") else None,
        crawl=(pack.get("crawl_technical") or {}).get("summary")
        if isinstance(pack.get("crawl_technical"), dict)
        else pack.get("crawl") or pack,
    )
    if rebuilt.get("url_count"):
        pack["site_sitemap"] = rebuilt
        pack["sitemap_url_count"] = rebuilt.get("url_count")
    return pack
