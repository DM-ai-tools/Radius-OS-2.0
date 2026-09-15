"""GA4 / GSC / GTM / Ahrefs / Moz adapters with mock mode + fallback."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.config import get_settings
from app.integrations.retry import with_retry
from app.logging_config import get_logger

log = get_logger("providers")


async def pull_backlinks(domain: str) -> tuple[dict[str, Any], str]:
    """Returns (payload, provider_used). Ahrefs primary, Moz fallback, else live site signals."""
    settings = get_settings()

    async def ahrefs() -> dict[str, Any]:
        if settings.use_mock_providers:
            return _mock_backlinks(domain, "ahrefs")
        if not settings.ahrefs_api_key:
            raise RuntimeError("Ahrefs API key not configured")
        # Ahrefs Site Explorer (v3) — backlinks-stats requires target + date
        from datetime import date

        import httpx

        today = date.today().isoformat()
        headers = {"Authorization": f"Bearer {settings.ahrefs_api_key}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://api.ahrefs.com/v3/site-explorer/backlinks-stats",
                params={"target": domain, "mode": "subdomains", "date": today},
                headers=headers,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Ahrefs HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            metrics = data.get("metrics") or data

            authority = 0.0
            try:
                dr_resp = await client.get(
                    "https://api.ahrefs.com/v3/site-explorer/domain-rating",
                    params={"target": domain, "date": today},
                    headers=headers,
                )
                if dr_resp.status_code < 400:
                    dr_data = dr_resp.json()
                    authority = float(
                        (dr_data.get("domain_rating") or {}).get("domain_rating")
                        if isinstance(dr_data.get("domain_rating"), dict)
                        else dr_data.get("domain_rating")
                        or dr_data.get("metrics", {}).get("domain_rating")
                        or 0
                    )
            except Exception:  # noqa: BLE001
                authority = 0.0

            return {
                "referring_domains": int(
                    metrics.get("live_refdomains")
                    or metrics.get("live_referring_domains")
                    or metrics.get("referring_domains")
                    or 0
                ),
                "authority_score": authority,
                "top_anchor_text": metrics.get("top_anchor_text")
                or {"brand": 40, "exact_match": 15, "generic": 30, "naked_url": 15},
                "sample_links": metrics.get("sample_links") or [],
            }

    async def moz() -> dict[str, Any]:
        if settings.use_mock_providers:
            return _mock_backlinks(domain, "moz")
        if not settings.moz_api_key:
            raise RuntimeError("Moz API key not configured")
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://lsapi.seomoz.com/v2/url_metrics",
                params={"targets": [f"https://{domain}"]},
                headers={"Authorization": f"Basic {settings.moz_api_key}"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Moz HTTP {resp.status_code}: {resp.text[:200]}")
            rows = resp.json() if isinstance(resp.json(), list) else resp.json().get("results") or []
            row = rows[0] if rows else {}
            return {
                "referring_domains": int(row.get("root_domains_to_root_domain") or 0),
                "authority_score": float(row.get("domain_authority") or 0),
                "top_anchor_text": {"brand": 40, "exact_match": 15, "generic": 30, "naked_url": 15},
                "sample_links": [],
            }

    async def live_signals() -> dict[str, Any]:
        """Public-page outbound/inbound heuristics when SEO APIs are unavailable."""
        from app.integrations.web_fetch import absolute_links, fetch_url, parse_html

        fetched = await fetch_url(f"https://{domain}")
        parser = parse_html(fetched.get("text") or "")
        external = absolute_links(fetched.get("url") or f"https://{domain}", parser.hrefs)
        external = [u for u in external if domain not in u]
        # Conservative public estimates — not Ahrefs DR
        authority = min(70.0, 15 + len(parser.hrefs) * 0.4 + len(parser.json_ld) * 5)
        return {
            "referring_domains": None,
            "authority_score": round(authority, 2),
            "top_anchor_text": {"outbound_sample": min(40, len(external))},
            "sample_links": [{"from": u, "anchor": "outbound", "spammy": False} for u in external[:8]],
            "note": (
                "Ahrefs/Moz keys not set — authority estimated from live page signals only; "
                "referring_domains unavailable."
            ),
            "live_fetch_status": fetched.get("status_code"),
        }

    if settings.use_mock_providers:
        return _mock_backlinks(domain, "ahrefs"), "ahrefs"

    try:
        if settings.ahrefs_api_key:
            data = await with_retry(ahrefs, attempts=2, label="ahrefs")
            return data, "ahrefs"
    except Exception as exc:  # noqa: BLE001
        log.warning("ahrefs_failed", error=str(exc), domain=domain)

    try:
        if settings.moz_api_key:
            data = await with_retry(moz, attempts=2, label="moz")
            return data, "moz"
    except Exception as exc:  # noqa: BLE001
        log.warning("moz_failed", error=str(exc), domain=domain)

    data = await live_signals()
    return data, "live_site_signals"


def _mock_backlinks(domain: str, provider: str) -> dict[str, Any]:
    seed = sum(ord(c) for c in domain) % 100
    return {
        "referring_domains": 120 + seed,
        "authority_score": round(25 + seed / 2, 2),
        "top_anchor_text": {
            "brand": 40,
            "exact_match": 15,
            "generic": 30,
            "naked_url": 15,
        },
        "sample_links": [
            {"from": f"https://ref{i}.example", "anchor": "learn more", "spammy": i % 7 == 0}
            for i in range(8)
        ],
        "provider_note": provider,
    }


async def validate_tracking(
    domain: str,
    has_credentials: dict[str, bool],
    only_elements: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return per-element tracking checks. Live mode inspects page HTML for tags."""
    settings = get_settings()
    elements = [
        "ga4_base_tag",
        "conversion_event",
        "gtm_container",
        "search_console_access",
        "cross_domain_tracking",
    ]
    if only_elements is not None:
        elements = [el for el in elements if el in only_elements]

    snippets = {"ga4_ids": [], "gtm_ids": [], "has_gtag": False, "has_gtm": False, "has_linker": False}
    fetch_error = None
    if not settings.use_mock_providers:
        from app.integrations.web_fetch import (
            detect_tracking_snippets,
            fetch_url,
            parse_html,
        )

        url = domain if domain.startswith("http") else f"https://{domain}"
        fetched = await fetch_url(url)
        fetch_error = fetched.get("error")
        parser = parse_html(fetched.get("text") or "")
        snippets = detect_tracking_snippets(fetched.get("text") or "", parser.scripts)

    results = []
    for el in elements:
        provider_map = {
            "ga4_base_tag": "ga4",
            "conversion_event": "ga4",
            "gtm_container": "gtm",
            "search_console_access": "search_console",
            "cross_domain_tracking": "ga4",
        }
        cred = has_credentials.get(provider_map[el], False)

        if settings.use_mock_providers:
            if not cred:
                results.append(
                    {
                        "element": el,
                        "check_result": "warning",
                        "detail": {
                            "message": (
                                f"{el} skipped — Google API not connected. "
                                "Tracking continues without this check."
                            ),
                            "fix": None,
                        },
                    }
                )
                continue
            if el == "cross_domain_tracking":
                result, detail = "warning", {
                    "message": "Cross-domain linker parameters missing on checkout subdomain.",
                    "fix": "Enable linker in GA4 config and include checkout domain.",
                }
            elif el == "conversion_event":
                result, detail = "pass", {
                    "message": "purchase event firing; no duplicate hits detected in sample window.",
                    "fix": None,
                }
            else:
                result, detail = "pass", {
                    "message": f"{el} verified against live configuration.",
                    "fix": None,
                }
            results.append({"element": el, "check_result": result, "detail": detail})
            continue

        # —— Live path ——
        if el == "ga4_base_tag":
            if snippets["ga4_ids"] or snippets["has_gtag"]:
                results.append(
                    {
                        "element": el,
                        "check_result": "pass",
                        "detail": {
                            "message": f"GA4 tag detected on live page ({', '.join(snippets['ga4_ids']) or 'gtag'}).",
                            "fix": None,
                            "source": "live_html",
                        },
                    }
                )
            else:
                results.append(
                    {
                        "element": el,
                        "check_result": "fail" if not fetch_error else "unverified",
                        "detail": {
                            "message": (
                                "No GA4/gtag snippet found on live page."
                                + (f" Fetch error: {fetch_error}" if fetch_error else "")
                            ),
                            "fix": "Install GA4 base tag (gtag.js) sitewide.",
                            "source": "live_html",
                        },
                    }
                )
        elif el == "gtm_container":
            if snippets["has_gtm"] or snippets["gtm_ids"]:
                results.append(
                    {
                        "element": el,
                        "check_result": "pass",
                        "detail": {
                            "message": f"GTM container detected ({', '.join(snippets['gtm_ids']) or 'gtm.js'}).",
                            "fix": None,
                            "source": "live_html",
                        },
                    }
                )
            else:
                results.append(
                    {
                        "element": el,
                        "check_result": "warning",
                        "detail": {
                            "message": "No GTM container snippet found on homepage HTML.",
                            "fix": "Publish GTM container or confirm tags are hardcoded.",
                            "source": "live_html",
                        },
                    }
                )
        elif el == "cross_domain_tracking":
            if snippets["has_linker"]:
                results.append(
                    {
                        "element": el,
                        "check_result": "pass",
                        "detail": {"message": "Linker/domains config detected in page scripts.", "fix": None},
                    }
                )
            else:
                results.append(
                    {
                        "element": el,
                        "check_result": "warning",
                        "detail": {
                            "message": "No cross-domain linker config detected in homepage HTML.",
                            "fix": "Enable linker if you use multiple domains/subdomains.",
                        },
                    }
                )
        elif el == "conversion_event":
            if cred:
                results.append(
                    {
                        "element": el,
                        "check_result": "warning",
                        "detail": {
                            "message": (
                                "OAuth credential present but live GA4 Data API pull is not "
                                "wired — conversion firing not verified against event stream."
                            ),
                            "fix": "Connect GA4 Data API or verify conversions in GA4 DebugView.",
                        },
                    }
                )
            else:
                results.append(
                    {
                        "element": el,
                        "check_result": "warning",
                        "detail": {
                            "message": (
                                "Conversion firing not checked — GA4 API skipped. "
                                "HTML tag audit still ran."
                            ),
                            "fix": None,
                        },
                    }
                )
        elif el == "search_console_access":
            if cred:
                results.append(
                    {
                        "element": el,
                        "check_result": "pass",
                        "detail": {
                            "message": "Search Console credential on file (token stored).",
                            "fix": None,
                        },
                    }
                )
            else:
                results.append(
                    {
                        "element": el,
                        "check_result": "warning",
                        "detail": {
                            "message": (
                                "Search Console not connected — optional. "
                                "Pipeline continues without query/index history."
                            ),
                            "fix": None,
                        },
                    }
                )
    return results


def _crawl_from_research_pages(research: dict[str, Any]) -> dict[str, Any]:
    """Map Perplexity research crawl results into the Website Situation crawl shape."""
    pages = [p for p in (research.get("pages") or []) if p.get("url")]
    status_samples = [
        {"url": p["url"], "status": int(p.get("status_code") or 0), "title": p.get("title") or ""}
        for p in pages[:40]
    ]
    indexable = sum(1 for p in pages if int(p.get("status_code") or 0) == 200)
    broken = sum(1 for p in pages if int(p.get("status_code") or 0) >= 400)
    canonical_issues = sum(
        1 for p in pages if int(p.get("status_code") or 0) == 200 and not p.get("canonical")
    )
    pages_found = len(pages) or int(research.get("pages_crawled") or 0)
    broken_links = int(research.get("broken_links") or broken)
    severity = (
        "critical"
        if broken > 5 or pages_found == 0
        else "warning"
        if broken or canonical_issues or pages_found <= 1
        else "info"
    )
    note = research.get("note") or (
        f"Discovered {pages_found} pages via Perplexity research because the site "
        "blocks direct bot access (SiteGround / MalCare / similar)."
    )
    return {
        "pages_found": pages_found,
        "indexable": indexable,
        "redirects": 0,
        "redirect_chains": 0,
        "canonical_issues": canonical_issues,
        "broken_links": broken_links,
        "notable_changes": [],
        "severity": severity,
        "status_samples": status_samples,
        "discovered_urls": [p["url"] for p in pages[:80]],
        "note": note,
        "source": "perplexity_research",
    }


async def crawl_site(url: str, *, max_pages: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    if settings.use_mock_providers:
        return {
            "pages_found": 342,
            "indexable": 298,
            "redirects": 18,
            "redirect_chains": 3,
            "canonical_issues": 5,
            "broken_links": 7,
            "notable_changes": [
                {
                    "date": "2026-06-12",
                    "change": "New redirect chain introduced on /blog/*",
                }
            ],
            "severity": "warning",
        }

    from app.integrations.site_research import research_ready, research_site_crawl
    from app.integrations.web_fetch import discover_site_urls, fetch_url, parse_html

    if not url.startswith("http"):
        url = "https://" + url

    requested_cap = int(max_pages or settings.effective_seo_audit_max_pages)
    max_pages = max(1, min(requested_cap, 2000))


    async def _remote_crawl() -> dict[str, Any] | None:
        if not research_ready():
            return None
        try:
            research = await research_site_crawl(url, max_pages=max_pages)
        except Exception as exc:  # noqa: BLE001
            return {
                "pages_found": 0,
                "indexable": 0,
                "redirects": 0,
                "redirect_chains": 0,
                "canonical_issues": 0,
                "broken_links": 0,
                "notable_changes": [],
                "severity": "critical",
                "error": "perplexity_crawl_failed",
                "note": f"Perplexity site research failed: {exc}",
                "source": "perplexity_research",
            }
        if research.get("pages"):
            return _crawl_from_research_pages(research)
        if research.get("error"):
            return {
                "pages_found": 0,
                "indexable": 0,
                "redirects": 0,
                "redirect_chains": 0,
                "canonical_issues": 0,
                "broken_links": 0,
                "notable_changes": [],
                "severity": "critical",
                "error": research.get("error"),
                "note": f"Perplexity research did not return pages ({research.get('error')}).",
                "source": "perplexity_research",
            }
        return None

    # Probe homepage first. SiteGround/WAF often returns 202 on HTML but we can
    # still build a page list from Google's index (and robots.txt).
    home_probe = await fetch_url(url)
    home_blocked = (home_probe.get("error") or "") == "bot_challenge_blocked"

    if home_blocked:
        page_urls = await discover_site_urls(url, max_pages=max_pages)
        if len(page_urls) >= 2:
            return {
                "pages_found": len(page_urls),
                "indexable": len(page_urls),
                "redirects": 0,
                "redirect_chains": 0,
                "canonical_issues": 0,
                "broken_links": 0,
                "notable_changes": [],
                "severity": "warning",
                "error": "bot_challenge_blocked",
                "note": (
                    f"Live HTML is blocked by a SiteGround / captcha challenge, so tags "
                    f"could not be fetched. Counted {len(page_urls)} unique pages from "
                    f"Google's index (site: search) plus any open robots/sitemap URLs."
                ),
                "status_samples": [{"url": u, "status": 202} for u in page_urls[:20]],
                "discovered_urls": page_urls[:80],
                "source": "indexed_serp",
            }
        remote = await _remote_crawl()
        if remote:
            return remote
        return {
            "pages_found": 0,
            "indexable": 0,
            "redirects": 0,
            "redirect_chains": 0,
            "canonical_issues": 0,
            "broken_links": 0,
            "notable_changes": [],
            "severity": "critical",
            "error": "bot_challenge_blocked",
            "note": (
                "This website is protected by a bot / captcha / WAF challenge "
                "(SiteGround, MalCare, Cloudflare, etc.). Configure OpenRouter + "
                "RESEARCH_MODEL (Perplexity) for AI site research, or ask hosting to "
                "allowlist this server."
            ),
            "source": "live_crawl",
        }

    page_urls = await discover_site_urls(url, max_pages=max_pages)

    if not page_urls:
        if home_probe.get("error") and not home_probe.get("text"):
            err = home_probe.get("error") or "fetch_failed"
            remote = await _remote_crawl()
            if remote:
                return remote
            note = (
                "Could not resolve or reach this domain from the server."
                if err == "dns_resolution_failed"
                else f"Homepage fetch failed: {err}"
            )
            return {
                "pages_found": 0,
                "indexable": 0,
                "redirects": 0,
                "redirect_chains": 0,
                "canonical_issues": 0,
                "broken_links": 0,
                "notable_changes": [],
                "severity": "critical",
                "error": err,
                "note": note,
                "source": "live_crawl",
            }
        page_urls = [home_probe.get("url") or url]

    sample = await fetch_url(page_urls[0], timeout=12)
    sample_blocked = (sample.get("error") or "") == "bot_challenge_blocked" or not (
        sample.get("text") or ""
    )
    if sample_blocked and len(page_urls) > 1:
        remote = await _remote_crawl()
        if remote and int(remote.get("pages_found") or 0) > 1:
            return remote
        return {
            "pages_found": len(page_urls),
            "indexable": len(page_urls),
            "redirects": 0,
            "redirect_chains": 0,
            "canonical_issues": 0,
            "broken_links": 0,
            "notable_changes": [],
            "severity": "info",
            "status_samples": [{"url": u, "status": 200} for u in page_urls[:20]],
            "discovered_urls": page_urls[:80],
            "note": (
                "Page list discovered via Perplexity research; live status checks are "
                "blocked by the site WAF from this server."
            ),
            "source": "perplexity_research",
        }
    if sample_blocked and len(page_urls) <= 1:
        remote = await _remote_crawl()
        if remote:
            return remote

    sem = asyncio.Semaphore(5)
    status_samples: list[dict[str, Any]] = []
    redirects = 0
    broken = 0
    canonical_issues = 0
    indexable = 0

    async def _check(page_url: str) -> None:
        nonlocal redirects, broken, canonical_issues, indexable
        async with sem:
            res = await fetch_url(page_url, timeout=12)
            code = int(res.get("status_code") or 0)
            final = res.get("url") or page_url
            status_samples.append({"url": final, "status": code})
            if code and code >= 400:
                broken += 1
            if code in (301, 302, 307, 308) or len(res.get("history") or []) > 0:
                redirects += len(res.get("history") or []) or (
                    1 if code in (301, 302, 307, 308) else 0
                )
            if code == 200:
                indexable += 1
                p2 = parse_html(res.get("text") or "")
                if not p2.canonical:
                    canonical_issues += 1

    await asyncio.gather(*[_check(u) for u in page_urls])

    failed_all = indexable == 0 and broken == 0 and all(
        int(s.get("status") or 0) in (0, 202) for s in status_samples
    )
    if failed_all and page_urls:
        remote = await _remote_crawl()
        if remote:
            return remote
        return {
            "pages_found": 0,
            "indexable": 0,
            "redirects": redirects,
            "redirect_chains": 0,
            "canonical_issues": 0,
            "broken_links": 0,
            "notable_changes": [],
            "severity": "critical",
            "error": "bot_challenge_blocked",
            "note": (
                "This website returns a bot / captcha challenge to our crawler. "
                "Configure OpenRouter + Perplexity (RESEARCH_MODEL) for AI site research, "
                "or ask hosting to allowlist this server."
            ),
            "status_samples": status_samples[:20],
            "source": "live_crawl",
        }

    pages_found = len(page_urls)
    severity = (
        "critical"
        if broken > 5 or pages_found == 0
        else "warning"
        if broken or canonical_issues or pages_found <= 1
        else "info"
    )
    note = (
        f"Counted {pages_found} unique HTML pages (homepage, nav, posts, products). "
        "Tag, author, feed, pagination, and duplicate URLs are excluded."
    )
    if pages_found <= 1:
        remote = await _remote_crawl()
        if remote and int(remote.get("pages_found") or 0) > 1:
            return remote
        note = (
            "Only the homepage was discovered. The site may be JavaScript-rendered, "
            "block bots, or lack a sitemap / internal HTML links. Confirm the primary "
            "URL and re-run, or provide a sitemap."
        )

    return {
        "pages_found": pages_found,
        "indexable": indexable,
        "redirects": redirects,
        "redirect_chains": 0,
        "canonical_issues": canonical_issues,
        "broken_links": broken,
        "notable_changes": [],
        "severity": severity,
        "status_samples": status_samples[:20],
        "discovered_urls": page_urls[:40],
        "note": note,
        "source": "live_crawl",
    }


async def check_broken_links(url: str) -> dict[str, Any]:
    """Broken-link audit per broken-link-checker skill (mock-safe)."""
    from app.integrations.llm import extract_domain

    settings = get_settings()
    domain = extract_domain(url)
    if settings.use_mock_providers:
        return {
            "pages_scanned": 48,
            "total_links_checked": 312,
            "broken_count": 6,
            "redirect_chain_count": 2,
            "internal": [
                {
                    "source_page": "/blog/guide",
                    "broken_url": f"https://{domain}/old-sale",
                    "status": 404,
                    "suggested_fix": "301 redirect /old-sale → /category/sale",
                },
                {
                    "source_page": "/nav",
                    "broken_url": f"https://{domain}/About",
                    "status": 404,
                    "suggested_fix": "Fix case: /About → /about",
                },
                {
                    "source_page": "/footer",
                    "broken_url": f"https://{domain}/careers-2023",
                    "status": 404,
                    "suggested_fix": "Redirect to /careers or remove link",
                },
            ],
            "external": [
                {
                    "source_page": "/resources",
                    "broken_url": "https://partner-toolkit.example/legacy",
                    "status": "timeout",
                    "suggested_fix": "Remove or replace with current partner URL",
                },
                {
                    "source_page": "/blog/guide",
                    "broken_url": "https://dead-vendor.example/docs",
                    "status": 404,
                    "suggested_fix": "Replace with alternative documentation source",
                },
            ],
            "redirect_chains": [
                {
                    "start_url": f"https://{domain}/page-a",
                    "chain": ["/page-a → /page-b → /page-c → /page-d"],
                    "final_url": f"https://{domain}/page-d",
                    "hops": 3,
                },
                {
                    "start_url": f"https://{domain}/promo",
                    "chain": ["/promo → /sale → /category/sale"],
                    "final_url": f"https://{domain}/category/sale",
                    "hops": 2,
                },
            ],
            "quick_fixes": [
                "Add 301: /old-sale → /category/sale",
                "Update nav href /About → /about",
                "Point /page-a links directly to /page-d (skip chain)",
                "Remove dead external link to partner-toolkit.example/legacy",
            ],
            "severity": "warning",
        }

    # Live: check homepage only + extract a few anchors
    from html.parser import HTMLParser

    import httpx

    from app.integrations.web_fetch import assert_safe_url

    url_ok, safe_url = assert_safe_url(url)
    if not url_ok:
        return {
            "pages_scanned": 0,
            "total_links_checked": 0,
            "broken_count": 0,
            "redirect_chain_count": 0,
            "internal": [],
            "external": [],
            "redirect_chains": [],
            "quick_fixes": [],
            "error": safe_url,
            "severity": "warning",
        }

    class _LinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.hrefs: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag != "a":
                return
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)

    internal: list[dict] = []
    external: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=False, timeout=12) as client:
        try:
            home = await client.get(safe_url)
        except Exception as exc:  # noqa: BLE001
            return {
                "pages_scanned": 0,
                "total_links_checked": 0,
                "broken_count": 0,
                "redirect_chain_count": 0,
                "internal": [],
                "external": [],
                "redirect_chains": [],
                "quick_fixes": [],
                "error": str(exc),
                "severity": "warning",
            }
        parser = _LinkParser()
        parser.feed(home.text or "")
        pages_scanned = 1

        targets: list[str] = []
        for href in parser.hrefs[:40]:
            if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue
            if href.startswith("//"):
                targets.append("https:" + href)
            elif href.startswith("/"):
                targets.append(f"https://{domain}{href}")
            elif href.startswith("http"):
                targets.append(href)

        # Was one httpx.head() per link, awaited serially (up to 40 x 12s timeout
        # == ~8 minutes worst case, blocking the whole chat turn). run_seo_audit
        # already proves out this semaphore+gather shape elsewhere in this file —
        # apply it here too so the links are checked concurrently instead.
        sem = asyncio.Semaphore(8)

        async def _check(target: str) -> tuple[str, int | str]:
            async with sem:
                target_ok, safe_target = assert_safe_url(target)
                if not target_ok:
                    return target, "blocked_unsafe_host"
                try:
                    resp = await client.head(safe_target)
                    return target, resp.status_code
                except Exception:  # noqa: BLE001
                    return target, "timeout"

        results = await asyncio.gather(*[_check(t) for t in targets])
        checked = len(results)
        for target, status in results:
            row = {
                "source_page": "/",
                "broken_url": target,
                "status": status,
                "suggested_fix": "Verify destination or add redirect",
            }
            is_internal = domain in target
            if status in (404, 410, 500) or status == "timeout":
                (internal if is_internal else external).append(row)
    broken = len(internal) + len(external)
    return {
        "pages_scanned": pages_scanned,
        "total_links_checked": checked,
        "broken_count": broken,
        "redirect_chain_count": 0,
        "internal": internal,
        "external": external,
        "redirect_chains": [],
        "quick_fixes": [f"Fix {r['broken_url']}" for r in (internal + external)[:5]],
        "severity": "warning" if broken else "info",
    }


def _extract_keyword(message: str, fallback: str) -> str:
    m = re.search(
        r"(?:rankings? for|keyword[:\s]+|optimize(?:\s+for)?)\s+[\"']?([^\"'\n.]+)[\"']?",
        message,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()[:80]
    return fallback


async def optimize_on_page(
    url: str,
    *,
    display_name: str,
    message: str = "",
    keyword: str | None = None,
) -> dict[str, Any]:
    """On-page SEO report per on-page-seo skill (mock-safe)."""
    from app.integrations.llm import extract_domain

    settings = get_settings()
    domain = extract_domain(url)
    target = keyword or _extract_keyword(message, f"{display_name} products")
    page_name = domain.split(".")[0].replace("-", " ").title() + " homepage"

    if settings.use_mock_providers:
        before_title = f"{display_name} | Home"
        after_title = f"{target.title()} | {display_name} Official Site"
        before_meta = f"Welcome to {display_name}."
        after_meta = (
            f"Shop {target} at {display_name}. Free shipping on orders over $50 — "
            f"browse bestsellers and exclusive offers today."
        )[:160]
        return {
            "page_name": page_name,
            "page_url": url,
            "target_keyword": target,
            "search_intent": "commercial",
            "target_audience": f"Shoppers researching {target}",
            "current_score": 58,
            "optimized_score": 86,
            "title": {"before": before_title, "after": after_title, "length_after": len(after_title)},
            "meta_description": {
                "before": before_meta,
                "after": after_meta,
                "length_after": len(after_meta),
            },
            "url_slug": {
                "before": "/",
                "after": f"/{target.lower().replace(' ', '-')}",
                "notes": "Homepage can stay /; ensure keyword landing pages use hyphenated slugs.",
            },
            "headings": {
                "h1": f"Discover {target.title()} Built for Everyday Shoppers",
                "h2s": [
                    f"Why choose {display_name} for {target}",
                    "Bestsellers this season",
                    "How to buy — shipping & returns",
                    f"{target.title()} FAQs",
                ],
                "notes": "One H1 with keyword near the start; H2s for subtopics / LSI terms.",
            },
            "content_gaps": [
                f"Add a comparison section vs category alternatives for {target}",
                "Add FAQ block targeting featured-snippet questions",
                "Include social proof (reviews count, trust badges)",
                "Add LSI terms: durable, free shipping, best [year]",
            ],
            "images": [
                "Rename hero to hero-target-keyword.webp; add descriptive alt",
                "Compress product images; include keyword in alt where natural",
            ],
            "internal_links": [
                {"anchor": f"Browse {target}", "to": f"/{target.lower().replace(' ', '-')}"},
                {"anchor": "Shipping & returns", "to": "/shipping"},
                {"anchor": "Customer reviews", "to": "/reviews"},
                {"anchor": "Contact support", "to": "/contact"},
            ],
            "schema_json_ld": {
                "@context": "https://schema.org",
                "@type": "Organization",
                "name": display_name,
                "url": url,
                "description": after_meta,
            },
            "eeat_notes": [
                "Add author/about signals on content pages",
                "Surface return policy and contact details in footer",
            ],
            "severity": "warning",
        }

    # Live lightweight fetch of title/meta only
    from html.parser import HTMLParser

    import httpx

    from app.integrations.web_fetch import assert_safe_url

    url_ok, safe_url = assert_safe_url(url)
    if not url_ok:
        return {
            "page_name": page_name,
            "page_url": url,
            "target_keyword": target,
            "current_score": 0,
            "optimized_score": 0,
            "error": safe_url,
            "severity": "warning",
        }

    class _MetaParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.title = ""
            self._in_title = False
            self.meta_desc = ""

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "title":
                self._in_title = True
            if tag == "meta":
                ad = {k: (v or "") for k, v in attrs}
                if ad.get("name", "").lower() == "description":
                    self.meta_desc = ad.get("content", "")

        def handle_endtag(self, tag: str) -> None:
            if tag == "title":
                self._in_title = False

        def handle_data(self, data: str) -> None:
            if self._in_title:
                self.title += data

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(safe_url)
            parser = _MetaParser()
            parser.feed(resp.text or "")
        before_title = parser.title.strip() or f"{display_name}"
        before_meta = parser.meta_desc.strip() or ""
        after_title = f"{target.title()} | {display_name}"[:60]
        after_meta = (
            f"Learn about {target} with {display_name}. Get clear next steps and trusted guidance."
        )[:160]
        return {
            "page_name": page_name,
            "page_url": url,
            "target_keyword": target,
            "search_intent": "informational",
            "target_audience": "General visitors",
            "current_score": 50 if not before_meta else 65,
            "optimized_score": 82,
            "title": {"before": before_title, "after": after_title, "length_after": len(after_title)},
            "meta_description": {
                "before": before_meta or "(missing)",
                "after": after_meta,
                "length_after": len(after_meta),
            },
            "url_slug": {"before": url, "after": url, "notes": "Review slug for keyword fit."},
            "headings": {"h1": f"{target.title()}", "h2s": [], "notes": "Full heading crawl not run live."},
            "content_gaps": ["Expand content to match search intent depth"],
            "images": [],
            "internal_links": [],
            "schema_json_ld": {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": after_title,
                "url": url,
            },
            "eeat_notes": [],
            "severity": "info",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "page_name": page_name,
            "page_url": url,
            "target_keyword": target,
            "current_score": 0,
            "optimized_score": 0,
            "error": str(exc),
            "severity": "warning",
        }


async def run_technical_seo_audit(url: str, *, display_name: str) -> dict[str, Any]:
    """Technical SEO audit per technical-seo-audit skill (mock-safe)."""
    from app.integrations.llm import extract_domain

    settings = get_settings()
    domain = extract_domain(url)

    if settings.use_mock_providers:
        sections = {
            "crawlability": {
                "score": 72,
                "findings": [
                    f"robots.txt present at https://{domain}/robots.txt — Sitemap referenced",
                    "Disallow rules do not block CSS/JS",
                    "Canonical on homepage is self-referencing",
                    "Warning: /admin/ path not disallowed (low risk if auth-gated)",
                ],
            },
            "indexation": {
                "score": 68,
                "findings": [
                    "Homepage returns 200",
                    "3 soft-404 candidates under /old-blog/*",
                    "2 temporary 302s that should be 301s (/promo → /sale)",
                    "www vs non-www consolidates via redirect",
                ],
            },
            "performance": {
                "score": 61,
                "findings": [
                    "LCP ~3.1s (target <2.5s) — hero image uncompressed",
                    "INP ~180ms (pass)",
                    "CLS ~0.14 (target <0.1) — late-loading banner",
                    "TTFB ~320ms — consider CDN / edge caching",
                    "2 render-blocking scripts above the fold",
                ],
            },
            "mobile": {
                "score": 78,
                "findings": [
                    "Viewport meta present",
                    "Body font 16px+",
                    "Warning: footer social icons touch targets ~32px (prefer 44px)",
                ],
            },
            "security": {
                "score": 85,
                "findings": [
                    "HTTPS with HTTP→HTTPS redirect",
                    "HSTS present",
                    "No mixed content on homepage sample",
                ],
            },
            "structured_data": {
                "score": 55,
                "findings": [
                    "Organization schema present",
                    "Missing WebSite + SearchAction for sitelinks search box",
                    "Product pages lack complete Offer properties (sample)",
                ],
            },
        }
        scores = [s["score"] for s in sections.values()]
        overall = round(sum(scores) / len(scores))
        return {
            "site": domain,
            "display_name": display_name,
            "score": overall,
            "sections": sections,
            "priority_fixes": [
                {
                    "priority": "Critical",
                    "issue": "LCP above 2.5s on homepage",
                    "fix": "Convert hero to WebP/AVIF, set explicit dimensions, preload LCP image",
                },
                {
                    "priority": "High",
                    "issue": "Soft 404s under /old-blog/*",
                    "fix": "Return 410 or 301 to relevant replacements; remove from sitemap",
                },
                {
                    "priority": "High",
                    "issue": "302 redirects used for permanent moves",
                    "fix": "Change /promo → /sale to 301",
                },
                {
                    "priority": "Medium",
                    "issue": "CLS from late banner",
                    "fix": "Reserve space for promo banner; avoid inserting above-fold after paint",
                },
                {
                    "priority": "Medium",
                    "issue": "Incomplete Product Offer schema",
                    "fix": "Add price, availability, and currency on product JSON-LD",
                },
            ],
            "severity": "warning" if overall < 80 else "info",
        }

    # Live checks — web_fetch (host fallback) + Firecrawl when bot protection blocks HTML
    from app.integrations.web_fetch import fetch_page_html, fetch_url

    seed = url if url.startswith("http") else f"https://{domain}"
    findings_crawl: list[str] = []
    findings_index: list[str] = []
    findings_sec: list[str] = []
    findings_mobile: list[str] = []
    findings_schema: list[str] = []
    priority_fixes: list[dict[str, str]] = []
    homepage_fetched = False
    ld_count = 0

    # HTTP → HTTPS redirect
    http_seed = seed.replace("https://", "http://", 1) if seed.startswith("https://") else f"http://{domain}"
    http_probe = await fetch_url(http_seed, follow=False)
    if int(http_probe.get("status_code") or 0) in (301, 302, 307, 308):
        loc = (http_probe.get("headers") or {}).get("location") or ""
        if str(loc).startswith("https://"):
            findings_sec.append("HTTP → HTTPS redirect in place")
        else:
            findings_sec.append(f"HTTP redirect present but not clearly to HTTPS ({loc[:80]})")
            priority_fixes.append(
                {
                    "priority": "High",
                    "issue": "HTTP does not redirect cleanly to HTTPS",
                    "fix": "Issue a 301 from http:// to https:// on the canonical host",
                }
            )
    elif int(http_probe.get("status_code") or 0) == 200:
        findings_sec.append("WARNING: HTTP version serves 200 without redirect to HTTPS")
        priority_fixes.append(
            {
                "priority": "Critical",
                "issue": "HTTP serves content without redirecting to HTTPS",
                "fix": "Force HTTPS at CDN/host; redirect all http:// requests to https://",
            }
        )

    robots_text = ""
    robots = await fetch_url(f"https://{domain}/robots.txt", follow=True, timeout=12)
    r_status = int(robots.get("status_code") or 0)
    robots_text = str(robots.get("text") or "")
    if r_status == 200 and robots_text:
        findings_crawl.append("robots.txt reachable")
        if re.search(r"disallow:\s*/\s*$", robots_text, re.IGNORECASE | re.MULTILINE):
            findings_crawl.append("WARNING: robots.txt contains Disallow: / for a user-agent")
            priority_fixes.append(
                {
                    "priority": "Critical",
                    "issue": "robots.txt may block entire site",
                    "fix": "Remove blanket Disallow: / unless intentionally staging",
                }
            )
        sitemap_refs = re.findall(r"(?im)^sitemap:\s*(\S+)", robots_text)
        if sitemap_refs:
            findings_crawl.append(f"Sitemap referenced in robots.txt ({len(sitemap_refs)})")
        else:
            findings_crawl.append("No Sitemap: line in robots.txt")
    else:
        findings_crawl.append(f"robots.txt status {r_status or 'unreachable'}")

    sitemap_urls = re.findall(r"(?im)^sitemap:\s*(\S+)", robots_text) or [
        f"https://{domain}/sitemap.xml",
        f"https://{domain}/sitemap_index.xml",
    ]
    sitemap_ok = False
    for sm_url in sitemap_urls[:4]:
        sm = await fetch_url(sm_url, follow=True, timeout=15)
        sm_status = int(sm.get("status_code") or 0)
        if sm_status == 200 and ("<urlset" in (sm.get("text") or "") or "<sitemapindex" in (sm.get("text") or "")):
            findings_crawl.append(f"Sitemap OK: {sm_url}")
            sitemap_ok = True
            break
        findings_crawl.append(f"Sitemap probe {sm_url}: status {sm_status}")
    if not sitemap_ok:
        priority_fixes.append(
            {
                "priority": "High",
                "issue": "XML sitemap not confirmed",
                "fix": "Publish sitemap_index.xml, reference it in robots.txt, submit in GSC",
            }
        )

    home_meta: dict[str, Any] = {}
    html, home_meta = await fetch_page_html(seed)
    home_status = int(home_meta.get("status_code") or 0)
    final_url = str(home_meta.get("final_url") or seed)
    if html:
        homepage_fetched = True
        findings_index.append(f"Homepage status {home_status} ({home_meta.get('source')})")
        if final_url.startswith("https://"):
            findings_sec.append("HTTPS in use on homepage")
        else:
            findings_sec.append("Homepage not on HTTPS")
        if "www." in final_url and "www." not in seed:
            findings_index.append(f"Host canonicalization: redirects to {final_url}")
    else:
        findings_index.append(
            f"Homepage fetch failed (status {home_status}, {home_meta.get('error') or 'empty body'})"
        )
        priority_fixes.append(
            {
                "priority": "Critical",
                "issue": "Homepage HTML not readable by crawler",
                "fix": "Allowlist SEO crawlers / reduce bot challenges; verify SSR content is in initial HTML",
            }
        )

    if html:
        low = html.lower()
        if 'rel="canonical"' in low or "rel='canonical'" in low:
            findings_crawl.append("Canonical tag present on homepage")
        else:
            findings_crawl.append("No canonical tag found on homepage sample")
            priority_fixes.append(
                {
                    "priority": "High",
                    "issue": "Missing canonical on homepage",
                    "fix": "Add self-referencing rel=canonical on all indexable templates",
                }
            )
        if re.search(r'name=["\']robots["\'][^>]*noindex', low):
            findings_index.append("WARNING: homepage meta robots contains noindex")
            priority_fixes.append(
                {
                    "priority": "Critical",
                    "issue": "Homepage is noindex",
                    "fix": "Remove noindex from production homepage unless intentionally blocked",
                }
            )

        has_viewport = bool(re.search(r'<meta[^>]+name=["\']viewport["\']', low))
        findings_mobile.append(
            "Viewport meta tag present"
            if has_viewport
            else "No viewport meta tag found — mobile rendering at risk"
        )
        if not has_viewport:
            priority_fixes.append(
                {
                    "priority": "High",
                    "issue": "Missing viewport meta tag",
                    "fix": 'Add <meta name="viewport" content="width=device-width, initial-scale=1">',
                }
            )

        lang_m = re.search(r"<html[^>]+lang=[\"']([^\"']+)[\"']", html, re.IGNORECASE)
        if lang_m:
            findings_mobile.append(f"html lang={lang_m.group(1)}")
        else:
            findings_mobile.append("Missing lang attribute on <html>")

        h1_count = len(re.findall(r"<h1\b", html, re.IGNORECASE))
        if h1_count == 1:
            findings_index.append("Single H1 on homepage sample")
        elif h1_count == 0:
            findings_index.append("No H1 found on homepage sample")
            priority_fixes.append(
                {
                    "priority": "Medium",
                    "issue": "Homepage missing H1",
                    "fix": "Add one descriptive H1 aligned to primary intent",
                }
            )
        else:
            findings_index.append(f"Multiple H1 tags ({h1_count}) on homepage sample")

        ld_count = low.count("application/ld+json")
        findings_schema.append(
            f"{ld_count} JSON-LD block(s) found on homepage"
            if ld_count
            else "No JSON-LD structured data found on homepage"
        )
        if not ld_count:
            priority_fixes.append(
                {
                    "priority": "Medium",
                    "issue": "No JSON-LD on homepage",
                    "fix": "Add Organization + WebSite schema; validate in Rich Results Test",
                }
            )

        if "http://" in html and "https://" in final_url:
            findings_sec.append("WARNING: mixed http:// asset references may exist in HTML")

    # Presence checks below are real signal (found or not), unlike a fabricated
    # mid-range number — but they only cover the homepage sample, so they're a
    # weaker signal than a full crawl. Categories with no measurable signal at
    # all (performance) get score=None, never a guessed number — per this
    # skill's own rule: a confident score on an unmeasured category is worse
    # than an admitted gap.
    crawl_score = 70 if sitemap_ok and "robots.txt reachable" in findings_crawl else 45
    if any("WARNING" in f for f in findings_crawl):
        crawl_score = min(crawl_score, 50)
    index_score = 65 if homepage_fetched and not any("noindex" in f for f in findings_index) else 40
    sec_score = 80 if any("HTTPS" in f for f in findings_sec) else 45

    sections: dict[str, dict[str, Any]] = {
        "crawlability": {"score": crawl_score, "findings": findings_crawl or ["Insufficient data"]},
        "indexation": {"score": index_score, "findings": findings_index or ["Insufficient data"]},
        "performance": {
            "score": None,
            "findings": [
                "Not measured in this pass — LCP/INP/CLS need field or lab data. "
                "Run the cwv-measurement skill for real numbers."
            ],
        },
        "security": {"score": sec_score, "findings": findings_sec or ["Insufficient data"]},
    }
    if homepage_fetched:
        sections["mobile"] = {
            "score": 75 if "Viewport meta tag present" in findings_mobile else 35,
            "findings": findings_mobile,
        }
        sections["structured_data"] = {
            "score": 75 if ld_count else 35,
            "findings": findings_schema,
        }
    else:
        sections["mobile"] = {
            "score": None,
            "findings": ["Homepage fetch failed — viewport presence not checked"],
        }
        sections["structured_data"] = {
            "score": None,
            "findings": ["Homepage fetch failed — structured data presence not checked"],
        }

    if not priority_fixes:
        priority_fixes.append(
            {
                "priority": "High",
                "issue": "Complete performance + schema validation",
                "fix": "Run cwv-measurement for real Core Web Vitals; expand crawl for duplicate titles/meta",
            }
        )

    scores = [s["score"] for s in sections.values() if isinstance(s.get("score"), (int, float))]
    overall = round(sum(scores) / len(scores)) if scores else None
    return {
        "site": domain,
        "display_name": display_name,
        "score": overall,
        "sections": sections,
        "priority_fixes": priority_fixes[:12],
        "severity": "warning" if overall is not None and overall < 70 else "info",
        "fetch_source": home_meta.get("source") if homepage_fetched else None,
    }


def find_duplicate_field(pages: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Screaming-Frog-style duplicate report: group pages sharing the same
    normalized value for `key` (title / meta_description), flag any group with
    2+ pages. Duplicate titles/descriptions split ranking signal and CTR across
    pages that should each own a distinct query — seo-audit's per-page checks
    never compared pages against each other, so this was invisible before.
    """
    groups: dict[str, dict[str, Any]] = {}
    for p in pages:
        if not isinstance(p, dict):
            continue
        raw = str(p.get(key) or "").strip()
        if not raw:
            continue
        norm = re.sub(r"\s+", " ", raw.lower())
        bucket = groups.setdefault(norm, {"value": raw, "urls": []})
        url = p.get("url") or p.get("path") or ""
        if url and url not in bucket["urls"]:
            bucket["urls"].append(url)
    return [
        {"value": g["value"], "urls": g["urls"], "count": len(g["urls"])}
        for g in groups.values()
        if len(g["urls"]) >= 2
    ]


def _psi_metric(metrics: dict[str, Any], key: str) -> tuple[float | None, str | None]:
    m = metrics.get(key) or {}
    return m.get("percentile"), m.get("category")


def parse_pagespeed_response(data: dict[str, Any]) -> dict[str, Any]:
    """Pure parser for a PageSpeed Insights v5 response — field (CrUX) vs lab
    (Lighthouse) kept separate per cwv-measurement/SKILL.md: never present one
    as the other. No CrUX entry is normal for lower-traffic pages, not an error.
    """
    field: dict[str, Any] | None = None
    loading_exp = data.get("loadingExperience") or {}
    metrics = loading_exp.get("metrics") or {}
    if metrics:
        lcp_ms, lcp_cat = _psi_metric(metrics, "LARGEST_CONTENTFUL_PAINT_MS")
        cls_x100, cls_cat = _psi_metric(metrics, "CUMULATIVE_LAYOUT_SHIFT_SCORE")
        inp_ms, inp_cat = _psi_metric(metrics, "INTERACTION_TO_NEXT_PAINT")
        if inp_ms is None:
            inp_ms, inp_cat = _psi_metric(metrics, "FIRST_INPUT_DELAY_MS")
        field = {
            "lcp_ms": lcp_ms,
            "lcp_category": lcp_cat,
            "inp_ms": inp_ms,
            "inp_category": inp_cat,
            "cls": (cls_x100 / 100) if cls_x100 is not None else None,
            "cls_category": cls_cat,
            "overall_category": loading_exp.get("overall_category"),
        }

    lab: dict[str, Any] | None = None
    lh = data.get("lighthouseResult") or {}
    perf = (lh.get("categories") or {}).get("performance") or {}
    audits = lh.get("audits") or {}
    if perf.get("score") is not None:
        lab = {
            "performance_score": round(float(perf["score"]) * 100),
            "lcp_ms": (audits.get("largest-contentful-paint") or {}).get("numericValue"),
            "cls": (audits.get("cumulative-layout-shift") or {}).get("numericValue"),
            "tbt_ms": (audits.get("total-blocking-time") or {}).get("numericValue"),
        }

    passes: bool | None = None
    if field:
        cats = [field.get("lcp_category"), field.get("inp_category"), field.get("cls_category")]
        if all(cats):
            passes = all(c == "FAST" for c in cats)

    return {"field": field, "lab": lab, "passes_core_web_vitals": passes}


async def run_cwv_measurement(url: str, *, display_name: str) -> dict[str, Any]:
    """Core Web Vitals field/lab data per the cwv-measurement skill (mock-safe).

    Division of labour vs. technical-seo: this measures and diagnoses; the
    technical-seo-audit skill's checklist fixes. Field data (CrUX) is what
    Google's page-experience signal actually uses — lab data (Lighthouse) only
    explains why field numbers look the way they do. Never presented as the
    same thing; see cwv-measurement/SKILL.md.
    """
    from app.integrations.llm import extract_domain

    settings = get_settings()
    domain = extract_domain(url)
    target = url if url.startswith("http") else f"https://{domain}"

    if settings.use_mock_providers:
        return {
            "site": domain,
            "display_name": display_name,
            "strategy": "mobile",
            "field_data_available": True,
            "field": {
                "lcp_ms": 2350,
                "lcp_category": "AVERAGE",
                "inp_ms": 180,
                "inp_category": "FAST",
                "cls": 0.08,
                "cls_category": "FAST",
                "overall_category": "AVERAGE",
            },
            "lab": {"performance_score": 78, "lcp_ms": 2600, "cls": 0.09, "tbt_ms": 210},
            "passes_core_web_vitals": False,
            "source": "mock",
            "note": None,
        }

    import httpx

    params: dict[str, str] = {"url": target, "strategy": "mobile", "category": "performance"}
    if settings.pagespeed_api_key:
        params["key"] = settings.pagespeed_api_key
    base_result = {
        "site": domain,
        "display_name": display_name,
        "strategy": "mobile",
        "field_data_available": False,
        "field": None,
        "lab": None,
        "passes_core_web_vitals": None,
        "source": "pagespeed_insights",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params
            )
        if resp.status_code != 200:
            return {**base_result, "error": f"PageSpeed Insights returned {resp.status_code}"}
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {**base_result, "error": f"PageSpeed Insights unreachable: {exc}"}

    parsed = parse_pagespeed_response(data)
    field = parsed["field"]
    return {
        **base_result,
        "field_data_available": field is not None,
        "field": field,
        "lab": parsed["lab"],
        "passes_core_web_vitals": parsed["passes_core_web_vitals"],
        "note": (
            None
            if field
            else "No CrUX field data for this URL (below Chrome traffic threshold) — "
            "lab data only explains what might be slow, it is not real-user experience."
        ),
    }


async def run_seo_audit(
    url: str,
    *,
    display_name: str,
    commercial_scope: dict[str, Any] | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Comprehensive SEO audit across discovered site pages (sitemap + crawl).

    When commercial_scope (CDD) is provided, page selection and scoring follow
    business hierarchy: Home → service hubs → services → sub-services, with
    primary focus on URLs matching products / promotion list / keywords / geo.
    """
    from urllib.parse import urlparse

    from app.integrations.llm import extract_domain
    from app.integrations.web_fetch import discover_site_urls, fetch_url

    settings = get_settings()
    domain = extract_domain(url)
    page_cap = int(max_pages) if max_pages is not None and int(max_pages) > 0 else settings.effective_seo_audit_max_pages
    max_pages = page_cap
    commercial = dict(commercial_scope or {})

    def _done(report: dict[str, Any]) -> dict[str, Any]:
        from app.services.page_clusters import finalize_seo_audit_clusters

        report.setdefault("commercial_scope", commercial)
        pages = report.get("pages") or []
        dup_titles = find_duplicate_field(pages, "title")
        dup_meta = find_duplicate_field(pages, "meta_description")
        report["duplicate_titles"] = dup_titles
        report["duplicate_meta_descriptions"] = dup_meta
        if dup_titles:
            report.setdefault("warnings", []).append(
                {
                    "issue": f"{len(dup_titles)} duplicate title group(s) across "
                    f"{sum(d['count'] for d in dup_titles)} pages",
                    "ref": "duplicate titles",
                }
            )
        if dup_meta:
            report.setdefault("warnings", []).append(
                {
                    "issue": f"{len(dup_meta)} duplicate meta description group(s) across "
                    f"{sum(d['count'] for d in dup_meta)} pages",
                    "ref": "duplicate meta descriptions",
                }
            )
        return finalize_seo_audit_clusters(report, commercial=commercial)


    def _score_band(score: int) -> str:
        if score >= 90:
            return "Excellent"
        if score >= 70:
            return "Good"
        if score >= 50:
            return "Needs significant work"
        return "Critical SEO issues"

    def _page_score(critical: list, warnings: list, opportunities: list) -> int:
        return max(0, 100 - 12 * len(critical) - 5 * len(warnings) - 2 * len(opportunities))

    def _path_label(page_url: str) -> str:
        path = urlparse(page_url).path or "/"
        return path if path else "/"

    if settings.use_mock_providers:
        mock_pages = [
            {
                "url": f"https://{domain}/",
                "path": "/",
                "title": f"{display_name} — Home",
                "status": 200,
                "status_label": "Working",
                "overall_score": 68,
                "score_band": "Needs significant work",
                "critical": [
                    {"issue": "Homepage title is generic and under-optimized", "ref": "title tag"},
                ],
                "warnings": [
                    {"issue": "12 images missing descriptive alt text", "ref": "product grid"},
                ],
                "opportunities": [
                    {"issue": "Convert hero PNG to AVIF/WebP to improve LCP", "ref": "hero image"},
                ],
                "passing": ["Viewport meta present", "Single H1", "Canonical present"],
            },
            {
                "url": f"https://{domain}/about",
                "path": "/about",
                "title": "About us",
                "status": 200,
                "status_label": "Working",
                "overall_score": 82,
                "score_band": "Good",
                "critical": [],
                "warnings": [
                    {"issue": "Meta description missing", "ref": "meta description"},
                ],
                "opportunities": [
                    {"issue": "Add Open Graph title", "ref": "og:title"},
                ],
                "passing": ["Title tag present", "Single H1", "Viewport meta present"],
            },
            {
                "url": f"https://{domain}/services",
                "path": "/services",
                "title": "Services",
                "status": 200,
                "status_label": "Working",
                "overall_score": 74,
                "score_band": "Good",
                "critical": [],
                "warnings": [
                    {"issue": "Meta description missing on category pages", "ref": "meta description"},
                ],
                "opportunities": [
                    {"issue": "Add FAQPage schema on top support articles", "ref": "schema"},
                ],
                "passing": ["Title tag present", "Canonical present"],
            },
            {
                "url": f"https://{domain}/services/seo",
                "path": "/services/seo",
                "title": "SEO services",
                "status": 200,
                "status_label": "Working",
                "overall_score": 71,
                "score_band": "Good",
                "critical": [],
                "warnings": [
                    {"issue": "H1 does not match the service keyword", "ref": "h1"},
                ],
                "opportunities": [
                    {"issue": "Add Service schema", "ref": "schema"},
                ],
                "passing": ["Title tag present"],
            },
            {
                "url": f"https://{domain}/services/web-design",
                "path": "/services/web-design",
                "title": "Web design",
                "status": 200,
                "status_label": "Working",
                "overall_score": 69,
                "score_band": "Needs significant work",
                "critical": [],
                "warnings": [
                    {"issue": "Thin comparison to adjacent services", "ref": "body content"},
                ],
                "opportunities": [],
                "passing": ["Title tag present", "Canonical present"],
            },
            {
                "url": f"https://{domain}/blog",
                "path": "/blog",
                "title": "Blog",
                "status": 200,
                "status_label": "Working",
                "overall_score": 70,
                "score_band": "Good",
                "critical": [],
                "warnings": [
                    {"issue": "Open Graph image not set on blog posts", "ref": "og:image"},
                ],
                "opportunities": [
                    {"issue": "Diversify anchor text on related-post links", "ref": "internal links"},
                ],
                "passing": ["Title tag present", "Viewport meta present"],
            },
            {
                "url": f"https://{domain}/blog/local-seo-checklist",
                "path": "/blog/local-seo-checklist",
                "title": "Local SEO checklist",
                "status": 200,
                "status_label": "Working",
                "overall_score": 64,
                "score_band": "Needs significant work",
                "critical": [],
                "warnings": [
                    {"issue": "Article has no author byline", "ref": "E-E-A-T"},
                ],
                "opportunities": [
                    {"issue": "Add Article schema", "ref": "schema"},
                ],
                "passing": ["Title tag present"],
            },
            {
                "url": f"https://{domain}/contact",
                "path": "/contact",
                "title": "Contact",
                "status": 200,
                "status_label": "Working",
                "overall_score": 88,
                "score_band": "Good",
                "critical": [],
                "warnings": [],
                "opportunities": [
                    {"issue": "Add LocalBusiness schema", "ref": "schema"},
                ],
                "passing": ["Title tag present", "Meta description present", "Single H1"],
            },
            {
                "url": f"https://{domain}/guides/shipping-v1",
                "path": "/guides/shipping-v1",
                "title": "Shipping guide (legacy)",
                "status": 200,
                "status_label": "Working",
                "overall_score": 55,
                "score_band": "Needs significant work",
                "critical": [
                    {"issue": "Soft-404 style thin content", "ref": "body content"},
                ],
                "warnings": [
                    {"issue": "Internal orphan: 0 inbound links", "ref": "internal linking"},
                ],
                "opportunities": [],
                "passing": ["Viewport meta present"],
            },
        ]
        critical = [
            {**it, "ref": f"{p['url']} — {it['ref']}"}
            for p in mock_pages
            for it in p["critical"]
        ]
        warnings = [
            {**it, "ref": f"{p['url']} — {it['ref']}"}
            for p in mock_pages
            for it in p["warnings"]
        ]
        opportunities = [
            {**it, "ref": f"{p['url']} — {it['ref']}"}
            for p in mock_pages
            for it in p["opportunities"]
        ]
        score = round(sum(p["overall_score"] for p in mock_pages) / len(mock_pages))
        return _done({
            "site": domain,
            "display_name": display_name,
            "pages_analyzed": len(mock_pages),
            "overall_score": score,
            "score_band": _score_band(score),
            "critical": critical,
            "warnings": warnings,
            "opportunities": opportunities,
            "passing": [
                "robots.txt present and does not blanket-block",
                "HTTPS with redirect from HTTP",
                "Organization JSON-LD on homepage",
            ],
            "pages": mock_pages,
            "severity": "warning" if score < 80 else "info",
            "commercial_scope": commercial,
        })

    # Live: discover all pages, audit each
    from html.parser import HTMLParser

    class _AuditParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.title = ""
            self._in_title = False
            self.meta_desc = ""
            self.h1_count = 0
            self.imgs_missing_alt = 0
            self.has_canonical = False
            self.has_viewport = False
            self.has_og_title = False
            self.has_json_ld = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            ad = {k: (v or "") for k, v in attrs}
            if tag == "title":
                self._in_title = True
            if tag == "meta":
                name = ad.get("name", "").lower()
                prop = ad.get("property", "").lower()
                if name == "description":
                    self.meta_desc = ad.get("content", "")
                if name == "viewport":
                    self.has_viewport = True
                if prop == "og:title":
                    self.has_og_title = True
            if tag == "link" and ad.get("rel", "").lower() == "canonical":
                self.has_canonical = True
            if tag == "h1":
                self.h1_count += 1
            if tag == "img" and not ad.get("alt"):
                self.imgs_missing_alt += 1
            if tag == "script" and "ld+json" in ad.get("type", ""):
                self.has_json_ld = True

        def handle_endtag(self, tag: str) -> None:
            if tag == "title":
                self._in_title = False

        def handle_data(self, data: str) -> None:
            if self._in_title:
                self.title += data

    def _status_label(code: int) -> str:
        if 200 <= code < 300:
            return "Working"
        if 300 <= code < 400:
            return "Redirected"
        if code == 404:
            return "Page not found"
        if code == 403:
            return "Access blocked"
        if code >= 500:
            return "Server error"
        if code == 0:
            return "Unreachable"
        return f"Code {code}"

    def _audit_html(page_url: str, html: str, status_code: int) -> dict[str, Any]:
        critical: list[dict] = []
        warnings: list[dict] = []
        opportunities: list[dict] = []
        passing: list[str] = []
        title = ""
        meta_desc = ""

        if status_code == 0:
            critical.append({"issue": "Page could not be fetched", "ref": page_url})
        elif status_code >= 400:
            critical.append(
                {"issue": f"Page returned {_status_label(status_code)}", "ref": page_url}
            )
        else:
            p = _AuditParser()
            try:
                p.feed(html or "")
            except Exception:  # noqa: BLE001
                pass
            title = (p.title or "").strip()
            meta_desc = (p.meta_desc or "").strip()
            if not title:
                critical.append({"issue": "Missing title tag", "ref": "title tag"})
            elif len(title) < 20:
                warnings.append({"issue": "Title tag too short", "ref": "title tag"})
            elif len(title) > 70:
                warnings.append({"issue": "Title tag may be truncated in search results", "ref": "title tag"})
            else:
                passing.append("Title tag present")
            if not p.meta_desc:
                warnings.append({"issue": "Missing meta description", "ref": "meta description"})
            elif len(p.meta_desc) < 50:
                warnings.append({"issue": "Meta description too short", "ref": "meta description"})
            else:
                passing.append("Meta description present")
            if p.h1_count == 0:
                warnings.append({"issue": "No H1 found", "ref": "headings"})
            elif p.h1_count > 1:
                warnings.append({"issue": f"Expected 1 H1, found {p.h1_count}", "ref": "headings"})
            else:
                passing.append("Single H1")
            if p.imgs_missing_alt:
                warnings.append(
                    {
                        "issue": f"{p.imgs_missing_alt} image(s) missing alt text",
                        "ref": "images",
                    }
                )
            else:
                passing.append("Images have alt text (or no images)")
            if not p.has_canonical:
                opportunities.append({"issue": "Add canonical link", "ref": "canonical"})
            else:
                passing.append("Canonical present")
            if not p.has_viewport:
                critical.append({"issue": "Missing viewport meta", "ref": "mobile"})
            else:
                passing.append("Viewport meta present")
            if not p.has_og_title:
                opportunities.append({"issue": "Add Open Graph title", "ref": "social"})
            else:
                passing.append("Open Graph title present")
            if not p.has_json_ld:
                opportunities.append({"issue": "Consider structured data (JSON-LD)", "ref": "schema"})
            else:
                passing.append("Structured data present")

        score = _page_score(critical, warnings, opportunities)
        return {
            "url": page_url,
            "path": _path_label(page_url),
            "title": title or _path_label(page_url),
            "meta_description": meta_desc,
            "status": status_code,
            "status_label": _status_label(status_code),
            "overall_score": score,
            "score_band": _score_band(score),
            "critical": critical,
            "warnings": warnings,
            "opportunities": opportunities,
            "passing": passing,
        }

    site_critical: list[dict] = []
    site_warnings: list[dict] = []
    site_passing: list[str] = []

    def _audit_from_research_page(page: dict[str, Any]) -> dict[str, Any]:
        """Build a page audit from Perplexity research meta (no local HTML fetch)."""
        critical: list[dict] = []
        warnings: list[dict] = []
        opportunities: list[dict] = []
        passing: list[str] = []
        page_url = page.get("url") or ""
        status_code = int(page.get("status_code") or 0)
        title = (page.get("title") or "").strip()
        meta_desc = (page.get("meta_description") or "").strip()
        h1s = page.get("h1") or []
        checks = page.get("checks") or {}
        issues = page.get("issues") or []

        if status_code == 0:
            critical.append({"issue": "Page could not be fetched", "ref": page_url})
        elif status_code >= 400:
            critical.append(
                {"issue": f"Page returned {_status_label(status_code)}", "ref": page_url}
            )
        else:
            if not title:
                critical.append({"issue": "Missing title tag", "ref": "title tag"})
            elif len(title) < 20:
                warnings.append({"issue": "Title tag too short", "ref": "title tag"})
            elif len(title) > 70:
                warnings.append(
                    {"issue": "Title tag may be truncated in search results", "ref": "title tag"}
                )
            else:
                passing.append("Title tag present")
            if not meta_desc:
                warnings.append({"issue": "Missing meta description", "ref": "meta description"})
            elif len(meta_desc) < 50:
                warnings.append({"issue": "Meta description too short", "ref": "meta description"})
            else:
                passing.append("Meta description present")
            h1_count = len(h1s) if isinstance(h1s, list) else (1 if h1s else 0)
            if h1_count == 0:
                warnings.append({"issue": "No H1 found", "ref": "headings"})
            elif h1_count > 1:
                warnings.append({"issue": f"Expected 1 H1, found {h1_count}", "ref": "headings"})
            else:
                passing.append("Single H1")
            imgs_wo = int(page.get("images_without_alt") or 0)
            if imgs_wo:
                warnings.append(
                    {"issue": f"{imgs_wo} image(s) missing alt text", "ref": "images"}
                )
            else:
                passing.append("Images have alt text (or no images)")
            if not page.get("canonical"):
                opportunities.append({"issue": "Add canonical link", "ref": "canonical"})
            else:
                passing.append("Canonical present")
            if checks.get("is_http") is True:
                critical.append({"issue": "Page served over HTTP", "ref": "https"})
            else:
                passing.append("HTTPS")
            for issue in issues[:3]:
                if isinstance(issue, str) and issue.strip():
                    warnings.append({"issue": issue.strip(), "ref": "research"})

        score = _page_score(critical, warnings, opportunities)
        return {
            "url": page_url,
            "path": _path_label(page_url),
            "title": title or _path_label(page_url),
            "meta_description": meta_desc,
            "status": status_code,
            "status_label": _status_label(status_code),
            "overall_score": score,
            "score_band": _score_band(score),
            "critical": critical,
            "warnings": warnings,
            "opportunities": opportunities,
            "passing": passing,
        }

    # Site-level robots check once (may fail behind WAF — non-fatal)
    robots_url = f"https://{domain}/robots.txt"
    robots_res = await fetch_url(robots_url, timeout=10)
    if robots_res.get("status_code") == 200 and not robots_res.get("error"):
        site_passing.append("robots.txt reachable")
    elif (robots_res.get("error") or "") != "bot_challenge_blocked":
        site_warnings.append(
            {
                "issue": f"robots.txt {_status_label(int(robots_res.get('status_code') or 0))}",
                "ref": robots_url,
            }
        )

    # Bot-walled sites: Google index first, then Perplexity if the index is thin
    home_probe = await fetch_url(url if url.startswith("http") else f"https://{domain}")
    home_err = (home_probe.get("error") or "")
    home_blocked = home_err in {
        "bot_challenge_blocked",
        "soft_forbidden",
        "empty_or_shell_page",
    }
    canonical_base = ""
    home_final = str(home_probe.get("url") or (url if url.startswith("http") else f"https://{domain}"))
    if int(home_probe.get("status_code") or 0) == 200 and (home_probe.get("text") or ""):
        parsed_home = urlparse(home_final)
        if parsed_home.scheme and parsed_home.netloc:
            canonical_base = f"{parsed_home.scheme}://{parsed_home.netloc}"

    def _canonical_page_url(page_url: str) -> str:
        if not canonical_base:
            return page_url
        parsed = urlparse(page_url)
        bare = parsed.netloc.lower().removeprefix("www.")
        canon_bare = urlparse(canonical_base).netloc.lower().removeprefix("www.")
        if bare != canon_bare:
            return page_url
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{canonical_base}{path}{query}"
    if home_blocked:
        from app.integrations.site_research import research_ready, research_site_crawl

        indexed = await discover_site_urls(
            url if url.startswith("http") else f"https://{domain}",
            max_pages=max(max_pages * 3, max_pages),
        )
        if len(indexed) >= 2:
            from app.services.page_clusters import prioritize_urls_for_audit

            indexed = prioritize_urls_for_audit(
                indexed,
                commercial=commercial,
                max_pages=max_pages,
                home_url=url if url.startswith("http") else f"https://{domain}",
            )
            pages_list = [
                {
                    "url": u,
                    "path": _path_label(u),
                    "title": _path_label(u),
                    "status": 202,
                    "status_label": "WAF blocked — URL from Google index",
                    "overall_score": 0,
                    "score_band": "Not measured",
                    "critical": [],
                    "warnings": [
                        {
                            "issue": "Live HTML blocked by captcha/WAF — on-page checks skipped",
                            "ref": u,
                        }
                    ],
                    "opportunities": [],
                    "passing": [],
                }
                for u in indexed
            ]
            return _done({
                "site": domain,
                "display_name": display_name,
                "pages_analyzed": len(pages_list),
                "overall_score": 0,
                "score_band": "Not measured (WAF)",
                "critical": list(site_critical),
                "warnings": list(site_warnings)
                + [
                    {
                        "issue": (
                            f"SiteGround/captcha blocked live HTML. "
                            f"{len(pages_list)} pages listed from Google's index."
                        ),
                        "ref": domain,
                    }
                ],
                "opportunities": [],
                "passing": list(site_passing),
                "pages": pages_list,
                "severity": "warning",
            })

        if research_ready():
            research = await research_site_crawl(url, max_pages=max_pages)
            research_pages = research.get("pages") or []
            if research_pages:
                pages_list = [_audit_from_research_page(p) for p in research_pages]
                critical: list[dict] = list(site_critical)
                warnings: list[dict] = list(site_warnings)
                opportunities: list[dict] = []
                passing: list[str] = list(site_passing)
                for pr in pages_list:
                    for it in pr["critical"]:
                        critical.append(
                            {"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"}
                        )
                    for it in pr["warnings"]:
                        warnings.append(
                            {"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"}
                        )
                    for it in pr["opportunities"]:
                        opportunities.append(
                            {"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"}
                        )
                all_pass = [p for pr in pages_list for p in pr.get("passing") or []]
                for item in all_pass:
                    if item not in passing and len(passing) < 12:
                        passing.append(item)
                score = round(sum(int(p["overall_score"]) for p in pages_list) / len(pages_list))
                return _done({
                    "site": domain,
                    "display_name": display_name,
                    "pages_analyzed": len(pages_list),
                    "overall_score": score,
                    "score_band": _score_band(score),
                    "critical": critical,
                    "warnings": warnings,
                    "opportunities": opportunities,
                    "passing": passing,
                    "pages": pages_list,
                    "severity": "critical" if score < 50 else "warning" if score < 80 else "info",
                })

    from app.services.page_clusters import prioritize_urls_for_audit

    discovered = await discover_site_urls(url, max_pages=max(max_pages * 3, max_pages))
    if not discovered:
        discovered = [url if url.startswith("http") else f"https://{domain}"]
    page_urls = prioritize_urls_for_audit(
        discovered,
        commercial=commercial,
        max_pages=max_pages,
        home_url=url if url.startswith("http") else f"https://{domain}",
    )
    page_urls = list(dict.fromkeys(_canonical_page_url(u) for u in page_urls))

    # Perplexity only when the live crawl found almost nothing (JS / WAF).
    from app.integrations.site_research import research_ready, research_site_crawl

    if research_ready() and len(page_urls) < 8:
        research = await research_site_crawl(url, max_pages=max_pages)
        research_pages = research.get("pages") or []
        if research_pages:
            pages_list = [_audit_from_research_page(p) for p in research_pages]
            critical = list(site_critical)
            warnings = list(site_warnings)
            opportunities = []
            passing = list(site_passing)
            for pr in pages_list:
                for it in pr["critical"]:
                    critical.append(
                        {"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"}
                    )
                for it in pr["warnings"]:
                    warnings.append(
                        {"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"}
                    )
                for it in pr["opportunities"]:
                    opportunities.append(
                        {"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"}
                    )
            all_pass = [p for pr in pages_list for p in pr.get("passing") or []]
            for item in all_pass:
                if item not in passing and len(passing) < 12:
                    passing.append(item)
            score = round(sum(int(p["overall_score"]) for p in pages_list) / len(pages_list))
            return _done({
                "site": domain,
                "display_name": display_name,
                "pages_analyzed": len(pages_list),
                "overall_score": score,
                "score_band": _score_band(score),
                "critical": critical,
                "warnings": warnings,
                "opportunities": opportunities,
                "passing": passing,
                "pages": pages_list,
                "severity": "critical" if score < 50 else "warning" if score < 80 else "info",
            })

    sem = asyncio.Semaphore(5)

    async def _audit_one(page_url: str) -> dict[str, Any]:
        async with sem:
            from app.integrations.web_fetch import fetch_page_html

            html, meta = await fetch_page_html(page_url, timeout=15)
            final_url = str(meta.get("final_url") or page_url)
            status_code = int(meta.get("status_code") or 0)
            if not html:
                return {
                    "url": final_url,
                    "path": _path_label(final_url),
                    "title": _path_label(final_url),
                    "meta_description": "",
                    "status": status_code,
                    "status_label": _status_label(status_code) if status_code else "Unreachable",
                    "overall_score": 0,
                    "score_band": "Not measured",
                    "critical": [],
                    "warnings": [
                        {
                            "issue": "Live HTML blocked by captcha/WAF — on-page checks skipped",
                            "ref": page_url,
                        }
                    ],
                    "opportunities": [],
                    "passing": [],
                }
            return _audit_html(final_url, html, status_code or 200)

    page_reports = await asyncio.gather(*[_audit_one(u) for u in page_urls])
    pages_list = list(page_reports)

    # Site aggregates (with page refs)
    critical: list[dict] = list(site_critical)
    warnings: list[dict] = list(site_warnings)
    opportunities: list[dict] = []
    passing: list[str] = list(site_passing)

    for pr in pages_list:
        for it in pr["critical"]:
            critical.append({"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"})
        for it in pr["warnings"]:
            warnings.append({"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"})
        for it in pr["opportunities"]:
            opportunities.append(
                {"issue": it["issue"], "ref": f"{pr['url']} — {it.get('ref') or ''}"}
            )

    # Deduplicate identical passing strings; keep a short site summary
    all_pass = [p for pr in pages_list for p in pr.get("passing") or []]
    for item in all_pass:
        if item not in passing and len(passing) < 12:
            passing.append(item)

    if pages_list:
        measurable = [p for p in pages_list if p.get("score_band") != "Not measured"]
        if measurable:
            score = round(sum(int(p["overall_score"]) for p in measurable) / len(measurable))
        else:
            score = None
    else:
        score = _page_score(critical, warnings, opportunities)

    return _done({
        "site": domain,
        "display_name": display_name,
        "pages_analyzed": len(pages_list),
        "overall_score": score,
        "score_band": _score_band(score) if score is not None else "Not measured (WAF)",
        "critical": critical,
        "warnings": warnings,
        "opportunities": opportunities,
        "passing": passing,
        "pages": pages_list,
        "severity": (
            "critical"
            if score is not None and score < 50
            else "warning"
            if score is not None and score < 80
            else "info"
            if score is not None
            else "warning"
        ),
    })


async def discover_competitors(
    display_name: str,
    domain: str,
    industry: str | None = None,
    hints: list[str] | None = None,
) -> list[dict[str, str]]:
    """Auto-discover 6–10 competitors using the ads-category-competitors skill only (no DataForSEO)."""
    settings = get_settings()
    vertical = (industry or "general market").strip()
    if settings.use_mock_providers:
        slug = vertical.lower().replace(" ", "-")[:24] or "market"
        return [
            {"name": f"{display_name} Rival", "url": f"https://rival-{domain}", "source": "skill"},
            {"name": f"{vertical} Peer Co", "url": f"https://{slug}-peer.example", "source": "skill"},
            {"name": f"Premium {vertical}", "url": f"https://premium-{slug}.example", "source": "skill"},
            {"name": f"{vertical} Value Players", "url": f"https://value-{slug}.example", "source": "skill"},
            {"name": f"{vertical} Specialist", "url": f"https://specialist-{slug}.example", "source": "skill"},
            {"name": f"{vertical} Category Leader", "url": f"https://leader-{slug}.example", "source": "skill"},
        ]

    from app.agents.prompts import skill_system_preamble
    from app.integrations.llm import synthesize_json
    from app.integrations.web_fetch import fetch_url, page_text_excerpt, parse_html

    client_host = domain.lower().removeprefix("www.")
    client_aliases = {client_host}
    if client_host.endswith(".com") and not client_host.endswith(".com.au"):
        client_aliases.add(client_host[:-4] + ".com.au")
    elif client_host.endswith(".com.au"):
        client_aliases.add(client_host[:-7] + ".com")
    seen: set[str] = set()
    comps: list[dict[str, str]] = []

    def _add(name: str, url: str) -> None:
        url = (url or "").strip()
        name = (name or "").strip()
        if not url or not name:
            return
        if not url.startswith("http"):
            url = f"https://{url}"
        host = url.split("//", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")
        if not host or host in client_aliases:
            return
        if "example" in host or "localhost" in host:
            return
        if host in seen:
            return
        seen.add(host)
        # Prefer www host when apex often fails for agencies; scoring fetch has www fallback
        comps.append({"name": name, "url": f"https://www.{host}", "source": "ads-category-competitors"})

    skill_preamble = skill_system_preamble("competitor_market_agent")
    fetched = await fetch_url(f"https://{domain}")
    parser = parse_html(fetched.get("text") or "")
    excerpt = page_text_excerpt(parser, 3500)
    resolved = str(fetched.get("url") or domain)

    industry_focus = vertical if industry else "infer from site excerpt — any vertical"
    hint_line = ""
    if hints:
        hint_line = (
            "Discovery category hints (NOT domains — expand these into real company websites): "
            + "; ".join(str(h) for h in hints if str(h).strip())[:400]
            + "\n"
        )
    system = (
        f"{skill_preamble}\n\n"
        "For this step only: identify 8–10 real competing businesses for tiered analysis. "
        "Follow the skill Identify competitors workflow. "
        "Adapt dynamically to ANY industry — never default to digital-marketing agencies "
        "unless the client themselves is an agency in that category. "
        "Only real organizations with real public domains. Never invent *.example domains. "
        "Cover a MIX in the SAME vertical: local peers, direct alternatives, category leaders, "
        "and aspirational leaders one level above. "
        "You MUST return at least 8 competitors with distinct domains."
    )

    def _ingest(payload: dict | None) -> None:
        if not payload:
            return
        rows = payload.get("competitors")
        if not isinstance(rows, list) and isinstance(payload.get("items"), list):
            rows = payload["items"]
        if not isinstance(rows, list):
            return
        for c in rows:
            if isinstance(c, dict):
                _add(str(c.get("name") or ""), str(c.get("url") or c.get("domain") or ""))

    passes = [
        (
            f"Company: {display_name}\nDomain: {client_host}\nResolved URL: {resolved}\n"
            f"Industry / vertical focus: {industry_focus}\n"
            f"{hint_line}"
            f"Page title: {parser.title}\n"
            f"Meta: {parser.meta.get('description', '') if getattr(parser, 'meta', None) else ''}\n"
            f"Fetch error: {fetched.get('error')}\n"
            f"Site excerpt:\n{excerpt or '(empty — use company name + industry knowledge)'}\n\n"
            "Identify 8–10 SAME-INDUSTRY competitors across local, direct, and aspirational tiers. "
            "Peers means businesses competing for the same customers in this vertical "
            "(stores, clinics, manufacturers, SaaS products, hotels, agencies, etc. — "
            "whatever THIS client is). "
            'Return JSON only: {"competitors":[{"name":"...","url":"https://...","rationale":"why competing"}]}'
        ),
        (
            f"Expand the set for {display_name} ({client_host}), industry: {industry_focus}.\n"
            f"Already have ({len(comps)}): {[c['url'] for c in comps]}\n"
            "Add MORE real competitors using these angles (adapt wording to the vertical):\n"
            f'- "{industry_focus}" peers / providers in the same country/region\n'
            f'- "{display_name} alternatives" / "vs" style peers\n'
            f'- Category leaders and directories for {industry_focus} (Clutch/G2/industry awards only if relevant)\n'
            f"- Larger aspirational leaders one level above in {industry_focus}\n"
            "Do NOT return unrelated marketing agencies unless the client is an agency. "
            "Return at least 8 ADDITIONAL competitors not in the already-have list. "
            'JSON: {"competitors":[{"name":"...","url":"https://..."}]}'
        ),
        (
            f"Still need more competitors for {display_name} ({client_host}). "
            f"Have only {len(comps)} so far: {[c['name'] for c in comps]}.\n"
            f"Industry/services vertical: {industry_focus}.\n"
            "List 8 well-known real businesses that compete for the same customers in this "
            "exact vertical. Include homepage URLs. No duplicates. "
            'JSON: {"competitors":[{"name":"...","url":"https://..."}]}'
        ),
    ]

    last_error = ""
    for i, user_prompt in enumerate(passes):
        if len(comps) >= 8 and i > 0:
            break
        # Always run pass 0; later passes only if thin
        if i > 0 and len(comps) >= 6:
            break
        payload = None
        models_to_try = [settings.competitor_model]
        if settings.skill_model and settings.skill_model != settings.competitor_model:
            models_to_try.append(settings.skill_model)
        for model_name in models_to_try:
            try:
                payload = await synthesize_json(
                    system,
                    user_prompt,
                    model=model_name,
                    raise_on_error=True,
                    max_tokens=8192 if "gemini" in model_name.lower() else 4096,
                )
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)[:300]
                log.warning(
                    "discover_competitors_skill_failed",
                    pass_n=i,
                    model=model_name,
                    error=last_error,
                    domain=client_host,
                )
        if not payload:
            continue
        before = len(comps)
        _ingest(payload)
        log.info(
            "discover_competitors_skill_pass",
            pass_n=i,
            added=len(comps) - before,
            total=len(comps),
            domain=client_host,
        )

    log.info("discover_competitors_skill", count=len(comps), domain=client_host, error=last_error or None)
    if not comps and last_error:
        raise RuntimeError(last_error)
    return comps[:10]
