"""Live HTTP fetch helpers for SEO agents (no mock data)."""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx

from app.logging_config import get_logger

log = get_logger("web_fetch")

DEFAULT_HEADERS = {
    # Browser-like UA — many agency CDNs/WAF block custom bots with empty pages / 403
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.hrefs: list[str] = []
        self.h1s: list[str] = []
        self.h2s: list[str] = []
        self._capture: str | None = None
        self._buf = ""
        self.imgs: list[dict[str, str]] = []
        self.scripts: list[str] = []
        self.json_ld: list[str] = []
        self._in_ld = False
        self.text_bits: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
            self._buf = ""
        elif tag == "meta":
            name = (ad.get("name") or ad.get("property") or "").lower()
            if name and ad.get("content"):
                self.meta[name] = ad["content"]
        elif tag == "link" and ad.get("rel", "").lower() == "canonical":
            self.canonical = ad.get("href", "")
        elif tag == "a" and ad.get("href"):
            self.hrefs.append(ad["href"])
        elif tag in ("h1", "h2"):
            self._capture = tag
            self._buf = ""
        elif tag == "img":
            self.imgs.append({"src": ad.get("src", ""), "alt": ad.get("alt", "")})
        elif tag == "script":
            src = ad.get("src", "")
            if src:
                self.scripts.append(src)
            if "ld+json" in ad.get("type", ""):
                self._in_ld = True
                self._buf = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = self._buf.strip()
        elif tag == self._capture:
            text = re.sub(r"\s+", " ", self._buf).strip()
            if tag == "h1":
                self.h1s.append(text)
            else:
                self.h2s.append(text)
            self._capture = None
        elif tag == "script" and self._in_ld:
            self.json_ld.append(self._buf.strip())
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title or self._capture or self._in_ld:
            self._buf += data
        elif data and data.strip():
            self.text_bits.append(data.strip())


_PARKING_HOST_MARKERS = (
    "myclickfunnels.com",
    "clickfunnels.com",
    "godaddy.com",
    "sedoparking.com",
    "parkingcrew.net",
    "hugedomains.com",
    "afternic.com",
)


def _host_bare(host: str) -> str:
    return (host or "").lower().split(":")[0].removeprefix("www.")


def _url_host_variants(url: str) -> list[str]:
    """Try apex, www, and common country-TLD aliases (e.g. .com ↔ .com.au)."""
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not host:
        return [url]
    bare = _host_bare(host)
    hosts: list[str] = [host]
    if host.startswith("www."):
        hosts.append(bare)
    else:
        hosts.append(f"www.{bare}")
    # AU businesses sometimes type .com when the live site is .com.au
    alts: list[str] = []
    if bare.endswith(".com") and not bare.endswith(".com.au"):
        alts.append(bare[:-4] + ".com.au")
    # Do not auto-fallback .com.au → .com (often SSL mismatch / different site)
    elif bare.endswith(".co.uk"):
        alts.append(bare[:-6] + ".com")
    for alt in alts:
        hosts.extend([alt, f"www.{alt}"])

    variants: list[str] = []
    seen: set[str] = set()
    for h in hosts:
        candidate = url.replace(parsed.netloc, h, 1)
        if candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)
    return variants


def _looks_parked_or_placeholder(result: dict[str, Any]) -> bool:
    final_host = _host_bare(urlparse(str(result.get("url") or "")).netloc)
    if any(m in final_host for m in _PARKING_HOST_MARKERS):
        return True
    text = (result.get("text") or "")[:4000].lower()
    title_m = re.search(r"<title[^>]*>([^<]+)</title>", text, re.IGNORECASE)
    title = (title_m.group(1) if title_m else "").lower()
    if "hero theme" in title or "parked" in title or "domain for sale" in title:
        return True
    return False


def _looks_bot_challenge(result: dict[str, Any]) -> bool:
    """Detect SiteGround/Cloudflare/MalCare/captcha interstitial pages (not real HTML)."""
    text = (result.get("text") or "").lower()
    status = int(result.get("status_code") or 0)
    if not text:
        return False
    markers = (
        "sgcaptcha",
        "/.well-known/sgcaptcha",
        "cf-browser-verification",
        "cf-challenge",
        "attention required! | cloudflare",
        "just a moment...",
        "enable javascript and cookies to continue",
        "checking your browser before accessing",
        "captcha-delivery.com",
        "hcaptcha.com",
        "malcare",
        "blocked because of malicious activities",
        "robot challenge screen",
    )
    if any(m in text for m in markers):
        return True
    # Tiny meta-refresh shells (common SiteGround challenge)
    if status in (202, 403, 503) and len(text) < 800 and "meta http-equiv=\"refresh\"" in text:
        return True
    if len(text) < 500 and "meta http-equiv=\"refresh\"" in text and "well-known" in text:
        return True
    return False


def _is_public_host(hostname: str | None) -> bool | None:
    """Return True if host has a public IP, False if only private, None if DNS fails."""
    if not hostname:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None
    if not infos:
        return None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            continue
        # Prefer is_global when available; otherwise accept non-private IPv4/IPv6
        if getattr(ip, "is_global", None) is True:
            return True
        if ip.version == 4 and not ip.is_private:
            return True
        if ip.version == 6 and not ip.is_private and not ip.is_reserved:
            return True
    return False


def assert_safe_url(url: str) -> tuple[bool, str]:
    """SSRF pre-check for any ad-hoc httpx call that bypasses fetch_url().

    Returns (True, normalized_url) if the host resolves to a public IP, or
    (False, reason) otherwise. Callers that need redirect-time re-validation
    too should use fetch_url() directly instead of a raw httpx client.
    """
    target = url if "://" in url else f"https://{url}"
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https"):
        return False, "unsupported_scheme"
    ok = _is_public_host(parsed.hostname)
    if ok is False:
        return False, "blocked_unsafe_host"
    if ok is None:
        return False, "dns_resolution_failed"
    return True, target


async def fetch_url(url: str, *, timeout: float = 20.0, follow: bool = True) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    requested = url if url.startswith("http") else "https://" + url
    for attempt in _url_host_variants(url):
        try:
            host_ok = _is_public_host(urlparse(attempt).hostname)
            if host_ok is False:
                log.warning("fetch_blocked_unsafe_host", url=attempt)
                last = {
                    "url": attempt,
                    "status_code": 0,
                    "headers": {},
                    "text": "",
                    "history": [],
                    "error": "blocked_unsafe_host",
                }
                continue
            if host_ok is None:
                log.warning("fetch_dns_failed", url=attempt)
                last = {
                    "url": attempt,
                    "status_code": 0,
                    "headers": {},
                    "text": "",
                    "history": [],
                    "error": "dns_resolution_failed",
                }
                continue
            async with httpx.AsyncClient(
                follow_redirects=follow,
                timeout=timeout,
                headers=DEFAULT_HEADERS,
            ) as client:
                resp = await client.get(attempt)
                # Re-check final host after redirects (SSRF)
                final_host = urlparse(str(resp.url)).hostname
                final_ok = _is_public_host(final_host)
                if final_ok is False:
                    log.warning("fetch_blocked_unsafe_redirect", url=str(resp.url))
                    last = {
                        "url": str(resp.url),
                        "status_code": 0,
                        "headers": {},
                        "text": "",
                        "history": [r.status_code for r in resp.history],
                        "error": "blocked_unsafe_host",
                    }
                    continue
                result = {
                    "url": str(resp.url),
                    "status_code": resp.status_code,
                    "headers": {k.lower(): v for k, v in resp.headers.items()},
                    "text": resp.text or "",
                    "history": [r.status_code for r in resp.history],
                    "error": None,
                }
                if resp.status_code >= 400 or not resp.text:
                    last = result
                    continue
                if _looks_bot_challenge(result):
                    log.warning("fetch_bot_challenge", url=str(resp.url), status=resp.status_code)
                    last = {**result, "text": "", "error": "bot_challenge_blocked"}
                    continue
                # Soft-block pages (short 403 body served as 200, or tiny challenge pages)
                if len(resp.text) < 400 and (
                    "forbidden" in resp.text.lower() or "access denied" in resp.text.lower()
                ):
                    last = {**result, "error": "soft_forbidden"}
                    continue
                if _looks_parked_or_placeholder(result):
                    log.info(
                        "fetch_parking_skip",
                        requested=url,
                        resolved=str(resp.url),
                    )
                    last = result
                    continue
                # Reject near-empty shells that aren't a real page
                if len(resp.text) < 250 and "<a " not in resp.text.lower() and "<title" not in resp.text.lower():
                    last = {**result, "error": "empty_or_shell_page"}
                    continue
                if attempt.rstrip("/") != requested.rstrip("/"):
                    log.info("fetch_host_fallback", requested=url, resolved=str(resp.url))
                return result
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch_failed", url=attempt, error=str(exc))
            last = {
                "url": attempt,
                "status_code": 0,
                "headers": {},
                "text": "",
                "history": [],
                "error": str(exc),
            }
    return last or {
        "url": requested,
        "status_code": 0,
        "headers": {},
        "text": "",
        "history": [],
        "error": "fetch_failed",
    }


async def fetch_page_html(
    url: str,
    *,
    follow: bool = True,
    timeout: float = 15,
) -> tuple[str, dict[str, Any]]:
    """Fetch rendered HTML with host fallback and Firecrawl when bot protection blocks."""
    fetched = await fetch_url(url, follow=follow, timeout=timeout)
    html = str(fetched.get("text") or "")
    meta: dict[str, Any] = {
        "status_code": fetched.get("status_code"),
        "final_url": fetched.get("url") or url,
        "source": "fetch_url",
        "error": fetched.get("error"),
    }
    blocked = fetched.get("error") == "bot_challenge_blocked" or (
        not html and int(fetched.get("status_code") or 0) in (202, 403, 503)
    )
    if blocked or len(html) < 800:
        from app.integrations.firecrawl import scrape_page

        scrape = await scrape_page(meta["final_url"] or url)
        if scrape.get("available"):
            html = str(scrape.get("html") or "")
            meta["source"] = "firecrawl"
            meta["firecrawl_title"] = (scrape.get("metadata") or {}).get("title")
            meta["status_code"] = (scrape.get("metadata") or {}).get("status_code") or meta["status_code"]
            meta["error"] = None if html else meta.get("error")
    return html, meta


def parse_html(html: str) -> PageParser:
    parser = PageParser()
    try:
        parser.feed(html or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("html_parse_failed", error=str(exc))
    return parser


def absolute_links(base_url: str, hrefs: list[str], *, same_host_only: bool = False) -> list[str]:
    base = urlparse(base_url)
    base_bare = _host_bare(base.netloc)
    out: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if same_host_only and _host_bare(parsed.netloc) != base_bare:
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
        if clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def page_text_excerpt(parser: PageParser, limit: int = 4000) -> str:
    bits: list[str] = []
    for bit in parser.text_bits:
        s = bit.strip()
        if not s or len(s) < 3:
            continue
        # Skip CSS/JS blobs accidentally captured from inline blocks
        low = s.lower()
        if low.startswith("@font-face") or "font-family:" in low[:80]:
            continue
        if low.startswith("function ") or low.startswith("const ") or low.startswith("var "):
            continue
        if s.count("{") > 2 and s.count(";") > 3:
            continue
        bits.append(s)
    blob = " ".join(bits)
    blob = re.sub(r"\s+", " ", blob).strip()
    return blob[:limit]


def detect_tracking_snippets(html: str, scripts: list[str]) -> dict[str, Any]:
    blob = (html or "") + "\n" + "\n".join(scripts)
    ga4_ids = sorted(set(re.findall(r"\bG-[A-Z0-9]{6,}\b", blob)))
    gtm_ids = sorted(set(re.findall(r"\bGTM-[A-Z0-9]+\b", blob)))
    has_gtag = "gtag(" in blob or "googletagmanager.com/gtag" in blob
    has_gtm = "googletagmanager.com/gtm.js" in blob or bool(gtm_ids)
    has_linker = "linker" in blob.lower() and "domains" in blob.lower()
    return {
        "ga4_ids": ga4_ids,
        "gtm_ids": gtm_ids,
        "has_gtag": has_gtag,
        "has_gtm": has_gtm,
        "has_linker": has_linker,
    }


_ASSET_EXT = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".css",
    ".js",
    ".xml",
    ".json",
    ".mp4",
    ".mp3",
    ".ico",
    ".woff",
    ".woff2",
    ".txt",
)

# Utility / archive / pagination paths — not unique content pages.
_SKIP_PATH = re.compile(
    r"(?ix)"
    r"(?:"
    r"/wp-(?:admin|login|json|cron)(?:/|$)"
    r"|/xmlrpc\.php"
    r"|/(?:feed|rss|atom)(?:/|\.xml|$)"
    r"|/comments/feed"
    r"|/(?:cart|basket|checkout|wishlist|my-account)(?:/|$)"
    r"|/cdn-cgi/"
    r"|/(?:tag|tags|author|authors)(?:/|$)"
    r"|/page/\d+(?:/|$)"
    r")"
)

_DROP_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "_ga",
    "ref",
    "source",
    "s",
    "paged",
    "orderby",
    "order",
    "add-to-cart",
    "replytocom",
    "share",
    "filter",
}


def _normalize_page_url(
    url: str,
    *,
    prefer_netloc: str | None = None,
    prefer_scheme: str | None = None,
) -> str:
    parsed = urlparse(url if url.startswith("http") else "https://" + url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    scheme = (prefer_scheme or parsed.scheme or "https").lower()
    netloc = (prefer_netloc or parsed.netloc or "").lower()
    if prefer_netloc and _host_bare(parsed.netloc) == _host_bare(prefer_netloc):
        netloc = prefer_netloc.lower()
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in _DROP_QUERY_KEYS
    ]
    query = urlencode(kept) if kept else ""
    return f"{scheme}://{netloc}{path}{('?' + query) if query else ''}"


def normalize_primary_url(raw: str | None) -> str:
    """Canonical form for a client's primary URL: scheme present, no trailing slash.

    Stored verbatim before, so a client entered as "acme.com" produced a
    schemeless URL whose ``urlparse().path`` is the host itself — which minted a
    phantom "/acme.com" page in the site map and broke same-host comparisons.
    """
    url = str(raw or "").strip()
    if not url:
        return url
    if url.startswith("//"):
        url = "https:" + url
    elif not url.lower().startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    parsed = urlparse(url)
    if not parsed.netloc:
        return str(raw or "").strip()
    path = (parsed.path or "").rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def is_indexable_html_url(url: str) -> bool:
    """True for unique content pages; false for assets, tags, feeds, pagination."""
    raw = (url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if raw.startswith("http") else "https://" + raw)
    if parsed.scheme not in ("http", "https"):
        return False
    path = parsed.path or "/"
    path_l = path.lower()
    if any(path_l.endswith(ext) for ext in _ASSET_EXT):
        return False
    if _SKIP_PATH.search(path_l):
        return False
    return True


def extract_sitemap_locs(xml_text: str) -> list[str]:
    """Pull <loc> URLs from a sitemap or sitemap index document."""
    locs = re.findall(r"<loc[^>]*>\s*([^<\s]+)\s*</loc>", xml_text or "", flags=re.IGNORECASE)
    out: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        clean = loc.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


# Below this many directly-discovered pages, treat discovery as blocked and
# fall back to third-party indexes (Google `site:`, then Perplexity).
_SERP_FALLBACK_BELOW = 8


async def discover_site_urls(start_url: str, *, max_pages: int = 0) -> list[str]:
    """Discover unique indexable HTML pages via homepage crawl + sitemap.

    Filters tag/author/feed/pagination/asset URLs so the count matches real pages,
    not every loc in a bloated sitemap.
    """
    if max_pages <= 0:
        max_pages = 2000
    if not start_url.startswith("http"):
        start_url = "https://" + start_url
    start_url = _normalize_page_url(start_url)
    host = urlparse(start_url).netloc.lower()
    bare = _host_bare(host)

    ordered: list[str] = []
    seen: set[str] = set()
    skipped = 0
    prefer_netloc: str | None = None
    prefer_scheme: str | None = None

    def _add(u: str) -> None:
        nonlocal skipped
        if len(ordered) >= max_pages:
            return
        if not is_indexable_html_url(u):
            skipped += 1
            return
        try:
            nu = _normalize_page_url(
                u, prefer_netloc=prefer_netloc, prefer_scheme=prefer_scheme
            )
        except Exception:  # noqa: BLE001
            return
        p = urlparse(nu)
        if p.scheme not in ("http", "https"):
            return
        h = p.netloc.lower()
        if _host_bare(h) != bare:
            return
        if nu in seen:
            return
        seen.add(nu)
        ordered.append(nu)

    # Do NOT add start_url yet. prefer_netloc/prefer_scheme are still unknown,
    # so a typed alias (acme.com) would be normalized against itself and kept
    # alongside the resolved host (www.acme.com) — two homepage rows for one
    # page. Add the resolved home_url below instead.
    home = await fetch_url(start_url)
    home_parsed = urlparse(home.get("url") or start_url)
    prefer_netloc = home_parsed.netloc.lower() or host
    prefer_scheme = (home_parsed.scheme or "https").lower()
    home_url = _normalize_page_url(
        home.get("url") or start_url,
        prefer_netloc=prefer_netloc,
        prefer_scheme=prefer_scheme,
    )
    # Follow redirects: audit the resolved host, not the typed alias
    bare = _host_bare(prefer_netloc)
    _add(home_url)

    # Sitemap candidates from robots.txt + default paths
    sitemap_candidates: list[str] = [
        f"https://{urlparse(home_url).netloc}/sitemap.xml",
        f"https://{urlparse(home_url).netloc}/sitemap_index.xml",
    ]
    try:
        robots = await fetch_url(f"https://{urlparse(home_url).netloc}/robots.txt", timeout=10)
        if robots.get("status_code") == 200 and robots.get("text"):
            for line in robots["text"].splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    if sm:
                        sitemap_candidates.insert(0, sm)
    except Exception:  # noqa: BLE001
        pass

    async def _pull_sitemap(sm_url: str, depth: int = 0) -> None:
        if depth > 2 or len(ordered) >= max_pages:
            return
        res = await fetch_url(sm_url, timeout=15)
        text = res.get("text") or ""
        if res.get("status_code") != 200 or not text:
            return
        locs = extract_sitemap_locs(text)
        child_sitemaps = [
            u
            for u in locs
            if u.lower().endswith(".xml")
            and not re.search(r"(?:tag|author|authors)-sitemap", u, re.IGNORECASE)
        ]
        page_locs = [u for u in locs if not u.lower().endswith(".xml")]
        for u in page_locs:
            _add(u)
            if len(ordered) >= max_pages:
                return
        for child in child_sitemaps[:20]:
            await _pull_sitemap(child, depth + 1)
            if len(ordered) >= max_pages:
                return

    for sm in dict.fromkeys(sitemap_candidates):
        await _pull_sitemap(sm)
        if len(ordered) >= max_pages:
            break

    # BFS crawl from homepage (and a few seed pages) to catch pages missing from sitemap
    queue: list[str] = [home_url]
    crawled: set[str] = set()
    while queue and len(ordered) < max_pages and len(crawled) < max_pages:
        current = queue.pop(0)
        if current in crawled:
            continue
        crawled.add(current)
        page = home if current == home_url else await fetch_url(current, timeout=12)
        if page.get("error") or (page.get("status_code") or 0) >= 400:
            continue
        parser = parse_html(page.get("text") or "")
        links = absolute_links(page.get("url") or current, parser.hrefs, same_host_only=True)
        for link in links:
            before = len(ordered)
            _add(link)
            if len(ordered) > before and link not in crawled and link not in queue:
                queue.append(link)
            if len(ordered) >= max_pages:
                break

    # Prefer homepage first
    if start_url in ordered:
        ordered.remove(start_url)
        ordered.insert(0, start_url)
    elif home_url in ordered:
        ordered.remove(home_url)
        ordered.insert(0, home_url)

    # Google `site:` index — the fallback for when a WAF blocks HTML/sitemaps.
    # It used to run unconditionally, merging unverified index URLs into a site
    # map that direct discovery had already built correctly: stale and removed
    # pages came back as "existing pages", and clusters were then mapped to
    # them. Only reach for it when direct discovery genuinely underperformed.
    if len(ordered) < _SERP_FALLBACK_BELOW:
        try:
            from app.integrations.dataforseo import indexed_site_urls

            for u in await indexed_site_urls(bare, limit=max_pages):
                _add(u)
        except Exception as exc:  # noqa: BLE001
            log.warning("discover_indexed_serp_failed", error=str(exc))

    # Perplexity only fills remaining gaps (do not replace a real index list).
    if len(ordered) < _SERP_FALLBACK_BELOW:
        try:
            from app.integrations.site_research import (
                research_ready,
                research_site_crawl,
            )

            if research_ready():
                research = await research_site_crawl(start_url, max_pages=max_pages)
                for p in research.get("pages") or []:
                    u = p.get("url")
                    if u and int(p.get("status_code") or 0) < 400:
                        _add(u)
        except Exception as exc:  # noqa: BLE001
            log.warning("discover_perplexity_failed", error=str(exc))

    log.info(
        "discover_site_urls_ok",
        start=start_url,
        pages=len(ordered),
        skipped=skipped,
    )
    return ordered[:max_pages]

