"""Phase 7 page-sample checks: one fetch pass over the homepage and a few key pages.

Every audit runs this, with or without Ahrefs. From the same fetched pages it measures
on-page fields, structured data, security headers, URL-structure behaviour,
canonicals, raw-versus-rendered content, and page weight. Each check reports one of
``pass`` / ``issue`` / ``not_measured`` with the evidence; nothing is inferred
without a fetched response.

Fetching uses a strict prober (one request per hop, SSRF-checked, no host
substitution) so redirect chains, www/non-www and trailing-slash behaviour are
seen exactly as a crawler sees them. Many sites block server requests (403 or a
bot challenge); then header-based checks are reported as not measured and the HTML
checks use the Firecrawl copy when available.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from app.integrations.web_fetch import DEFAULT_HEADERS, assert_safe_url
from app.logging_config import get_logger

log = get_logger("technical_seo_sample")

MAX_SAMPLE_PAGES = 6
MAX_HOPS = 6
MAX_HTML_BYTES = 3_000_000
PROBE_TIMEOUT = 15.0
THIN_CONTENT_WORDS = 200
LARGE_HTML_BYTES = 500_000
LARGE_IMAGE_BYTES = 300_000
# 202 is how several WAFs (AWS, Sucuri) serve their JavaScript challenge page.
BLOCKED_STATUS = {202, 401, 403, 429, 503}

# --------------------------------------------------------------------------- fetching


@dataclass
class Probe:
    """One URL as a crawler saw it: every hop, the final response, its headers."""

    url: str
    final_url: str = ""
    status: int = 0
    chain: list[tuple[str, int]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    html: str = ""
    html_bytes: int = 0
    loop: bool = False
    blocked: bool = False
    error: str = ""

    @property
    def hops(self) -> int:
        return max(0, len(self.chain) - 1)


async def probe(url: str, *, client: httpx.AsyncClient, read_body: bool = True) -> Probe:
    """Follow redirects by hand (each hop SSRF-checked), recording the chain."""
    result = Probe(url=url)
    current = url
    seen: set[str] = set()
    for _ in range(MAX_HOPS + 1):
        ok, safe = assert_safe_url(current)
        if not ok:
            result.error = f"unsafe_or_unresolvable_host ({safe})"
            return result
        if safe in seen:
            result.loop = True
            result.final_url = safe
            return result
        seen.add(safe)
        try:
            method = "GET" if read_body else "HEAD"
            resp = await client.request(method, safe, headers=DEFAULT_HEADERS)
        except httpx.HTTPError as exc:
            result.error = type(exc).__name__
            result.final_url = safe
            return result
        result.chain.append((safe, resp.status_code))
        if resp.is_redirect and resp.headers.get("location"):
            current = urljoin(safe, resp.headers["location"])
            continue
        result.final_url = safe
        result.status = resp.status_code
        result.headers = {k.lower(): v for k, v in resp.headers.items()}
        result.blocked = resp.status_code in BLOCKED_STATUS or _looks_challenged(resp)
        # Only a real 200 page is parsed; a challenge or error page would yield false findings.
        if read_body and not result.blocked and resp.status_code == 200:
            body = resp.content[:MAX_HTML_BYTES]
            result.html_bytes = len(resp.content)
            result.html = body.decode(resp.encoding or "utf-8", errors="replace")
        return result
    result.error = "too_many_redirects"
    result.final_url = current
    return result


def _looks_challenged(resp: httpx.Response) -> bool:
    if resp.status_code != 200:
        return False
    head = resp.text[:3000].lower() if resp.headers.get("content-type", "").startswith("text") else ""
    return any(m in head for m in ("cf-challenge", "challenge-platform", "just a moment...", "captcha-delivery"))


# --------------------------------------------------------------------------- parsing

_SKIP_TEXT = {"script", "style", "noscript", "template", "svg"}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.headings: list[tuple[int, str]] = []
        self.images: list[dict[str, Any]] = []
        self.links: list[str] = []
        self.nav_links = 0
        self.scripts: list[dict[str, Any]] = []
        self.inline_scripts = 0
        self.stylesheets: list[dict[str, Any]] = []
        self.json_ld_raw: list[str] = []
        self.itemtypes: list[str] = []
        self.lang = ""
        self.text: list[str] = []
        self.resource_urls: list[str] = []
        self._stack: list[str] = []
        self._in_title = False
        self._in_head = False
        self._heading: tuple[int, list[str]] | None = None
        self._json_ld: list[str] | None = None
        self._nav_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "head":
            self._in_head = True
        if tag == "body":
            self._in_head = False
        if tag in _SKIP_TEXT:
            self._stack.append(tag)
        if tag in ("nav", "header"):
            self._nav_depth += 1
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            href = a.get("href", "")
            if "canonical" in rel and href and not self.canonical:
                self.canonical = href
            if "stylesheet" in rel and href:
                self.stylesheets.append({"href": href, "in_head": self._in_head, "media": a.get("media", "")})
                self.resource_urls.append(href)
            elif "preload" in rel or "icon" in rel:
                self.resource_urls.append(href)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = (int(tag[1]), [])
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            self.images.append({"src": src, "alt": a.get("alt"), "has_alt": "alt" in a})
            if a.get("src"):
                self.resource_urls.append(a["src"])
        elif tag == "a":
            href = a.get("href", "")
            if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                self.links.append(href)
                if self._nav_depth:
                    self.nav_links += 1
        elif tag == "script":
            src = a.get("src", "")
            stype = a.get("type", "").lower()
            if "ld+json" in stype:
                self._json_ld = []
            elif src:
                self.scripts.append(
                    {
                        "src": src,
                        "async": "async" in a,
                        "defer": "defer" in a,
                        "module": stype == "module",
                        "in_head": self._in_head,
                    }
                )
                self.resource_urls.append(src)
            else:
                self.inline_scripts += 1
        elif tag in ("iframe", "source", "video", "audio", "embed") and a.get("src"):
            self.resource_urls.append(a["src"])
        if a.get("itemtype"):
            self.itemtypes.extend(a["itemtype"].split())

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag in ("nav", "header") and self._nav_depth:
            self._nav_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading:
            level, parts = self._heading
            self.headings.append((level, " ".join(" ".join(parts).split())))
            self._heading = None
        if tag == "script" and self._json_ld is not None:
            self.json_ld_raw.append("".join(self._json_ld))
            self._json_ld = None

    def handle_data(self, data: str) -> None:
        if self._json_ld is not None:
            self._json_ld.append(data)
            return
        if self._in_title:
            self.title += data
        if self._heading is not None:
            self._heading[1].append(data)
        if not self._stack:
            self.text.append(data)


def _same_site(url: str, base: str) -> bool:
    a = (urlparse(url).hostname or "").lower().removeprefix("www.")
    b = (urlparse(base).hostname or "").lower().removeprefix("www.")
    return not a or a == b


def parse_page(html: str, url: str) -> dict[str, Any]:
    """Everything the checks need from one page's HTML."""
    p = _PageParser()
    try:
        p.feed(html or "")
        p.close()
    except Exception as exc:  # noqa: BLE001 — broken markup still yields what was parsed
        log.debug("page_parse_partial", url=url, error=str(exc)[:120])
    words = re.findall(r"[A-Za-z0-9À-ɏ']+", " ".join(p.text))
    canonical = urljoin(url, p.canonical.strip()) if p.canonical else ""
    images = p.images
    scripts = [dict(s, src=urljoin(url, s["src"])) for s in p.scripts]
    third_party = sorted({urlparse(s["src"]).hostname or "" for s in scripts if not _same_site(s["src"], url)} - {""})
    blocking_js = [s["src"] for s in scripts if s["in_head"] and not (s["async"] or s["defer"] or s["module"])]
    blocking_css = [
        urljoin(url, s["href"]) for s in p.stylesheets if s["in_head"] and s["media"].lower() not in ("print",)
    ]
    mixed = []
    if url.startswith("https://"):
        mixed = sorted({urljoin(url, r) for r in p.resource_urls if str(r).startswith("http://")})[:20]
    internal_links = [urljoin(url, h) for h in p.links if _same_site(urljoin(url, h), url)]
    structured = parse_structured_data(p.json_ld_raw, p.itemtypes)
    return {
        "url": url,
        "title": " ".join(p.title.split()),
        "meta_description": " ".join((p.meta.get("description") or "").split()),
        "meta_robots": (p.meta.get("robots") or "").lower(),
        "viewport": p.meta.get("viewport", ""),
        "generator": p.meta.get("generator", ""),
        "lang": p.lang,
        "canonical": canonical,
        "h1": [t for lvl, t in p.headings if lvl == 1],
        "headings": p.headings,
        "heading_issues": heading_issues(p.headings),
        "image_count": len(images),
        "images_missing_alt": sum(1 for i in images if not i["has_alt"]),
        "image_urls": [urljoin(url, i["src"]) for i in images if i["src"] and not i["src"].startswith("data:")][:30],
        "word_count": len(words),
        "link_count": len(p.links),
        "internal_links": list(dict.fromkeys(internal_links))[:200],
        "nav_link_count": p.nav_links,
        "script_count": len(scripts),
        "inline_script_count": p.inline_scripts,
        "third_party_hosts": third_party,
        "render_blocking_js": blocking_js,
        "render_blocking_css": blocking_css,
        "mixed_content": mixed,
        "structured_data": structured,
        "app_shell": _looks_like_app_shell(html or "", len(words)),
    }


def heading_issues(headings: list[tuple[int, str]]) -> list[str]:
    """Skipped heading levels and empty headings, in document order."""
    issues: list[str] = []
    previous = 0
    for level, text in headings:
        if not text:
            issues.append(f"Empty H{level}")
        if previous and level > previous + 1:
            issues.append(f"H{level} follows H{previous} (skips a level): “{text[:60]}”")
        if not previous and level > 1:
            issues.append(f"First heading is H{level}, not H1")
        previous = level
    return issues[:10]


def _looks_like_app_shell(html: str, words: int) -> bool:
    """An empty root element plus little text: content arrives by JavaScript."""
    shell = re.search(
        r"<div[^>]+id=[\"'](root|app|__next|__nuxt|___gatsby|svelte)[\"'][^>]*>\s*</div>", html, re.IGNORECASE
    )
    return bool(shell) and words < 150


# --------------------------------------------------------------------------- structured data

# Properties Google needs for the rich result (or the entity to be usable at all).
REQUIRED_PROPERTIES: dict[str, tuple[str, ...]] = {
    "Organization": ("name", "url"),
    "LocalBusiness": ("name", "address"),
    "WebSite": ("name", "url"),
    "WebPage": ("name",),
    "Article": ("headline", "author", "datePublished"),
    "BlogPosting": ("headline", "author", "datePublished"),
    "NewsArticle": ("headline", "author", "datePublished"),
    "Product": ("name",),
    "Offer": ("price", "priceCurrency"),
    "AggregateRating": ("ratingValue", "reviewCount|ratingCount"),
    "Review": ("author", "reviewRating"),
    "FAQPage": ("mainEntity",),
    "BreadcrumbList": ("itemListElement",),
    "Service": ("name",),
    "Event": ("name", "startDate", "location"),
    "Recipe": ("name", "image"),
    "VideoObject": ("name", "thumbnailUrl", "uploadDate"),
    "JobPosting": ("title", "datePosted", "description", "hiringOrganization"),
    "Person": ("name",),
    "HowTo": ("name", "step"),
}
# Local business subtypes count as LocalBusiness.
_LOCAL_SUBTYPES = re.compile(
    r"(Business|Store|Restaurant|Dentist|Physician|Clinic|Agency|Office|Attorney|LegalService|"
    r"Plumber|Electrician|RoofingContractor|HomeAndConstructionBusiness|ProfessionalService|"
    r"FinancialService|RealEstateAgent|AutoRepair|HealthAndBeautyBusiness|LodgingBusiness)$"
)
RICH_RESULTS: dict[str, str] = {
    "BreadcrumbList": "Breadcrumb trail in search results",
    "Product": "Product snippet (price, availability, ratings)",
    "Review": "Review snippet",
    "AggregateRating": "Review stars",
    "Article": "Article rich result (headline, image, date)",
    "BlogPosting": "Article rich result (headline, image, date)",
    "NewsArticle": "Article rich result (headline, image, date)",
    "Event": "Event rich result",
    "Recipe": "Recipe rich result",
    "VideoObject": "Video rich result",
    "JobPosting": "Job posting rich result",
    "Organization": "Organization logo and knowledge panel details",
    "LocalBusiness": "Local business details (address, hours) for the knowledge panel",
    "FAQPage": "FAQ rich result (Google shows these only for well-known government and health sites since 2023)",
    "WebSite": "Site name in search results",
}


def _base_type(name: str) -> str:
    name = name.rsplit("/", 1)[-1]
    if name in REQUIRED_PROPERTIES:
        return name
    if _LOCAL_SUBTYPES.search(name):
        return "LocalBusiness"
    return name


def _walk(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, out)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _walk(node["@graph"], out)
    if node.get("@type"):
        out.append(node)
    for key, value in node.items():
        if key in ("@graph", "@context"):
            continue
        if isinstance(value, (dict, list)):
            _walk(value, out)


def parse_structured_data(json_ld_raw: list[str], itemtypes: list[str]) -> dict[str, Any]:
    """Schema types present, malformed blocks, missing required properties,
    duplicates and rich-result eligibility for one page."""
    nodes: list[dict[str, Any]] = []
    top_level: list[dict[str, Any]] = []
    malformed: list[str] = []
    for i, raw in enumerate(json_ld_raw, start=1):
        text = raw.strip()
        if not text:
            malformed.append(f"JSON-LD block {i} is empty")
            continue
        try:
            data = json.loads(text)
        except ValueError as exc:
            malformed.append(f"JSON-LD block {i} is not valid JSON ({str(exc)[:60]})")
            continue
        top = data if isinstance(data, list) else [data]
        for item in top:
            if isinstance(item, dict) and "@context" not in item and "@graph" not in item:
                malformed.append(f"JSON-LD block {i} has no @context")
            if isinstance(item, dict):
                graph = item.get("@graph")
                entries = graph if isinstance(graph, list) else [item]
                top_level.extend(e for e in entries if isinstance(e, dict) and e.get("@type"))
        _walk(data, nodes)
    types: list[str] = []
    missing: list[str] = []
    eligible: list[str] = []
    for node in nodes:
        raw_types = node.get("@type")
        for t in raw_types if isinstance(raw_types, list) else [raw_types]:
            if not isinstance(t, str):
                continue
            base = _base_type(t)
            types.append(t)
            required = REQUIRED_PROPERTIES.get(base)
            if not required:
                continue
            absent = [
                prop for prop in required if not any(node.get(alt) not in (None, "", [], {}) for alt in prop.split("|"))
            ]
            if absent:
                missing.append(f"{t}: missing {', '.join(p.replace('|', ' or ') for p in absent)}")
            elif base in RICH_RESULTS and RICH_RESULTS[base] not in eligible:
                eligible.append(RICH_RESULTS[base])
    micro = sorted({t.rsplit("/", 1)[-1] for t in itemtypes})
    counts: dict[str, int] = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    duplicates = [f"{t} declared {n} times" for t, n in counts.items() if n > 1 and t in ("Organization", "WebSite", "LocalBusiness", "BreadcrumbList", "FAQPage")]
    ids = {str(n.get("@id")) for n in nodes if n.get("@id")}
    # Separate entities need linking; nested ones (WebSite > SearchAction) already are.
    separate = len(top_level)
    linked = separate < 2 or any(
        isinstance(n.get(k), dict) and str(n[k].get("@id")) in ids
        for n in nodes
        for k in ("publisher", "author", "provider", "isPartOf", "about", "mainEntityOfPage", "parentOrganization")
    )
    return {
        "json_ld_blocks": len(json_ld_raw),
        "types": sorted(set(types)),
        "microdata_types": micro,
        "malformed": malformed,
        "missing_required": missing,
        "duplicates": duplicates,
        "entities_linked": linked,
        "top_level_entities": separate,
        "rich_results_eligible": eligible,
    }


# --------------------------------------------------------------------------- the pass


def _norm(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme.lower(), (p.hostname or "").lower(), p.path.rstrip("/") or "/", "", p.query, ""))


def pick_sample_urls(home: str, *, candidates: list[str], home_links: list[str]) -> list[str]:
    """Homepage plus pages from distinct sections (first path segment)."""
    chosen = [home]
    seen_sections = {""}
    for url in [*candidates, *home_links]:
        if not url or not _same_site(url, home):
            continue
        path = urlparse(url).path.strip("/")
        if not path or re.search(r"\.(xml|pdf|jpe?g|png|gif|webp|svg|css|js|zip)$", path, re.IGNORECASE):
            continue
        section = path.split("/")[0].lower()
        if section in seen_sections or any(_norm(url) == _norm(c) for c in chosen):
            continue
        seen_sections.add(section)
        chosen.append(url)
        if len(chosen) >= MAX_SAMPLE_PAGES:
            break
    return chosen


async def run_page_sample(
    primary_url: str,
    *,
    candidate_urls: list[str] | None = None,
    rendered_fetch: Any | None = None,
    html_fallback: Any | None = None,
) -> dict[str, Any]:
    """Fetch the sample and run every page-level check.

    ``rendered_fetch(url) -> html`` returns a browser-rendered copy (Firecrawl) for
    the JavaScript comparison; ``html_fallback(url) -> html`` returns a copy when
    the site blocks this server. Both are optional and injected for testing.
    """
    seed = primary_url if "://" in primary_url else f"https://{primary_url}"
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=False) as client:
        home = await probe(seed, client=client)
        home_url = home.final_url or seed
        home_html = home.html
        fetched_via = "direct" if home_html else "none"
        if (home.blocked or not home_html) and html_fallback:
            home_html = await html_fallback(home_url) or ""
            fetched_via = "firecrawl" if home_html else "none"
        home_parsed = parse_page(home_html, home_url) if home_html else None
        urls = pick_sample_urls(
            home_url,
            candidates=list(candidate_urls or []),
            home_links=(home_parsed or {}).get("internal_links") or [],
        )
        sem = asyncio.Semaphore(4)

        async def _one(u: str) -> Probe:
            async with sem:
                return await probe(u, client=client)

        others = await asyncio.gather(*[_one(u) for u in urls[1:]])
        probes = [home, *others]

        pages: list[dict[str, Any]] = []
        for pr in probes:
            html = pr.html if pr is not home else home_html
            source = "direct" if pr.html else ("firecrawl" if pr is home and home_html else "")
            if not html and pr is not home and html_fallback and pr.blocked:
                html = await html_fallback(pr.final_url or pr.url) or ""
                source = "firecrawl" if html else ""
            parsed = parse_page(html, pr.final_url or pr.url) if html else None
            pages.append(
                {
                    "url": pr.url,
                    "final_url": pr.final_url,
                    "status": pr.status,
                    "hops": pr.hops,
                    "chain": pr.chain,
                    "loop": pr.loop,
                    "blocked": pr.blocked,
                    "error": pr.error,
                    "headers": pr.headers,
                    "html_bytes": pr.html_bytes if source == "direct" else 0,
                    "html_source": source,
                    "parsed": parsed,
                }
            )

        variants = await _url_variant_probes(client, home_url, [p["final_url"] for p in pages[1:] if p["status"] == 200])
        # Without the homepage HTML the platform is unknown; never conclude "not WordPress".
        wp = await _wordpress_probes(client, home_url, home_html, home.headers) if home_html else {}
        image_sizes = await _image_sizes(client, (home_parsed or {}).get("image_urls") or [], home_url)

    rendered: list[dict[str, Any]] = []
    if rendered_fetch:
        # The comparison needs the raw HTML a crawler receives; a Firecrawl copy is already rendered.
        for page in [p for p in pages if p["html_source"] == "direct"][:2]:
            if not page["parsed"]:
                continue
            try:
                r_html = await rendered_fetch(page["final_url"] or page["url"])
            except Exception as exc:  # noqa: BLE001
                log.info("rendered_fetch_failed", url=page["url"], error=str(exc)[:120])
                r_html = ""
            if r_html:
                rendered.append({"url": page["final_url"], "raw": page["parsed"], "rendered": parse_page(r_html, page["final_url"])})

    return {
        "status": "available" if any(p["parsed"] for p in pages) else "NOT_AVAILABLE",
        "home_url": home_url,
        "fetched_via": fetched_via,
        "headers_measured": not home.blocked and home.status == 200,
        "direct_pages": sum(1 for p in pages if p["html_source"] == "direct"),
        "pages": pages,
        "variants": variants,
        "wordpress": wp,
        "image_sizes": image_sizes,
        "rendered": rendered,
        "rendered_available": bool(rendered),
    }


def _with_path(url: str, path: str, query: str = "") -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, path, "", query, ""))


async def _url_variant_probes(client: httpx.AsyncClient, home_url: str, inner: list[str]) -> dict[str, Any]:
    """How the site answers the other spelling of each URL."""
    p = urlparse(home_url)
    host = p.hostname or ""
    other_host = host[4:] if host.startswith("www.") else f"www.{host}"
    jobs: dict[str, str] = {
        "http": urlunparse(("http", p.netloc, p.path or "/", "", "", "")),
        "other_host": urlunparse((p.scheme, other_host, p.path or "/", "", "", "")),
    }
    sample = next((u for u in inner if urlparse(u).path.strip("/")), "")
    if sample:
        path = urlparse(sample).path
        toggled = path[:-1] if path.endswith("/") and len(path) > 1 else path + "/"
        jobs["slash"] = _with_path(sample, toggled)
        jobs["upper"] = _with_path(sample, path.upper()) if path.upper() != path else ""
        jobs["param"] = _with_path(sample, path, "utm_source=audit&ref=test")
    out: dict[str, Any] = {"sample_url": sample}
    results = await asyncio.gather(
        *[probe(u, client=client, read_body=k in ("upper", "param", "slash")) for k, u in jobs.items() if u]
    )
    for (key, url), res in zip([(k, u) for k, u in jobs.items() if u], results):
        canonical = parse_page(res.html, res.final_url).get("canonical") if res.html else ""
        out[key] = {
            "url": url,
            "final_url": res.final_url,
            "status": res.status,
            "hops": res.hops,
            "chain": res.chain,
            "loop": res.loop,
            "blocked": res.blocked,
            "error": res.error,
            "canonical": canonical,
            "first_status": res.chain[0][1] if res.chain else 0,
        }
    return out


_WP_CONFIG_BACKUPS = ("wp-config.php.bak", "wp-config.php.old", "wp-config.php~", "wp-config.bak", "wp-config.txt")


async def _wordpress_probes(
    client: httpx.AsyncClient, home_url: str, html: str, headers: dict[str, str]
) -> dict[str, Any]:
    """Only on WordPress sites: exposed config backups, XML-RPC, version disclosure.
    Bodies of exposed files are never kept, only whether they answered."""
    low = (html or "").lower()
    is_wp = "wp-content/" in low or "wp-includes/" in low or "wordpress" in (headers.get("x-powered-by", "").lower())
    if not is_wp:
        return {"is_wordpress": False}
    base = urlunparse((urlparse(home_url).scheme, urlparse(home_url).netloc, "/", "", "", ""))
    exposed: list[str] = []
    blocked = 0
    for name in _WP_CONFIG_BACKUPS:
        res = await probe(urljoin(base, name), client=client)
        blocked += int(res.blocked)
        if res.status == 200 and res.hops == 0 and ("db_password" in res.html.lower() or "db_name" in res.html.lower()):
            exposed.append(urljoin(base, name))
    xmlrpc = await probe(urljoin(base, "xmlrpc.php"), client=client, read_body=False)
    # xmlrpc.php answers GET with 405 "XML-RPC server accepts POST requests only".
    xmlrpc_open = xmlrpc.status in (200, 405) and not xmlrpc.blocked
    version = ""
    gen = re.search(r"<meta[^>]+name=[\"']generator[\"'][^>]+content=[\"']WordPress\s*([\d.]+)", html or "", re.IGNORECASE)
    if gen:
        version = gen.group(1)
    elif re.search(r"[?&]ver=\d+\.\d+(\.\d+)?", low) and "wp-includes" in low:
        version = "exposed via ?ver= on core assets"
    readme = await probe(urljoin(base, "readme.html"), client=client)
    return {
        "is_wordpress": True,
        "probes_blocked": blocked == len(_WP_CONFIG_BACKUPS) or xmlrpc.blocked,
        "exposed_files": exposed,
        "files_checked": [urljoin(base, n) for n in _WP_CONFIG_BACKUPS],
        "xmlrpc_enabled": xmlrpc_open,
        "xmlrpc_status": xmlrpc.status,
        "version_exposed": version,
        "readme_exposed": readme.status == 200 and not readme.blocked and "wordpress" in readme.html.lower(),
    }


async def _image_sizes(client: httpx.AsyncClient, urls: list[str], home_url: str) -> list[dict[str, Any]]:
    """Byte size of up to 8 same-site homepage images (HEAD, SSRF-checked)."""
    out: list[dict[str, Any]] = []
    for url in [u for u in urls if _same_site(u, home_url)][:8]:
        res = await probe(url, client=client, read_body=False)
        size = res.headers.get("content-length")
        if res.status == 200 and size and size.isdigit():
            out.append({"url": res.final_url, "bytes": int(size)})
    return out
