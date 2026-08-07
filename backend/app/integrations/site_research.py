"""Site discovery / crawl fallback via Perplexity (OpenRouter research model).

Used when local HTTP fetch is blocked by SiteGround / MalCare / Cloudflare, etc.
Does not use DataForSEO OnPage.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.integrations.llm import OPENROUTER_URL
from app.logging_config import get_logger

log = get_logger("site_research")


def _host_bare(host: str) -> str:
    return (host or "").lower().split(":")[0].removeprefix("www.")


def _normalize_url(url: str, *, default_host: str = "") -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if not raw.startswith("http"):
        if raw.startswith("/"):
            if not default_host:
                return None
            raw = f"https://{default_host}{raw}"
        else:
            raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _parse_json_loose(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def research_ready() -> bool:
    settings = get_settings()
    return bool(settings.openrouter_api_key) and not settings.use_mock_llm


async def _perplexity_research(system: str, user: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Call Perplexity via OpenRouter; return (parsed_json, citation_urls)."""
    settings = get_settings()
    if not research_ready():
        return None, []
    model = settings.research_model or "perplexity/sonar-pro"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://searchfit.local",
        "X-Title": "Radius OS Site Research",
    }
    body = {
        "model": model,
        "max_tokens": 6000,
        "messages": [
            {"role": "system", "content": system + "\nReturn ONLY valid JSON. No markdown fences."},
            {"role": "user", "content": user},
        ],
    }
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()

    citations: list[str] = []
    for c in data.get("citations") or []:
        if isinstance(c, str) and c.startswith("http"):
            citations.append(c)
        elif isinstance(c, dict) and (c.get("url") or "").startswith("http"):
            citations.append(c["url"])

    choices = data.get("choices") or []
    if not choices:
        return None, citations
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text") or "")
            elif isinstance(part, str):
                parts.append(part)
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        return None, citations

    for m in re.findall(r"https?://[^\s\"'<>\]]+", content):
        citations.append(m.rstrip(").,;"))

    parsed = _parse_json_loose(content)
    return parsed, citations


def _normalize_pages(
    raw_pages: list[Any],
    *,
    target_host: str,
    max_pages: int,
    extra_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    bare = _host_bare(target_host)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(page: dict[str, Any]) -> None:
        if len(out) >= max_pages:
            return
        url = _normalize_url(str(page.get("url") or ""), default_host=target_host)
        if not url:
            return
        host = _host_bare(urlparse(url).netloc)
        if host != bare:
            return
        key = url.rstrip("/").lower() or url.lower()
        if key in seen:
            return
        seen.add(key)
        status = int(page.get("status_code") or page.get("status") or 200)
        title = str(page.get("title") or "").strip()
        h1_raw = page.get("h1")
        if isinstance(h1_raw, list):
            h1 = [str(x) for x in h1_raw if x]
        elif h1_raw:
            h1 = [str(h1_raw)]
        else:
            h1 = []
        out.append(
            {
                "url": url,
                "status_code": status,
                "title": title,
                "meta_description": str(
                    page.get("meta_description") or page.get("description") or ""
                ).strip(),
                "canonical": str(page.get("canonical") or "").strip(),
                "h1": h1,
                "issues": page.get("issues") if isinstance(page.get("issues"), list) else [],
                "images_without_alt": int(page.get("images_without_alt") or 0),
                "checks": page.get("checks") if isinstance(page.get("checks"), dict) else {},
            }
        )

    for item in raw_pages:
        if isinstance(item, dict):
            _add(item)
        elif isinstance(item, str):
            _add({"url": item, "status_code": 200})

    for u in extra_urls or []:
        _add({"url": u, "status_code": 200})

    return out[:max_pages]


async def research_site_crawl(url: str, *, max_pages: int = 0) -> dict[str, Any]:
    """
    Discover and summarize site pages with Perplexity web research.

    Returns: { pages, pages_crawled, broken_links, error, source, note }
    """
    if not research_ready():
        return {
            "error": "research_not_configured",
            "pages": [],
            "pages_crawled": 0,
            "source": "perplexity_research",
        }

    raw = (url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    parsed = urlparse(raw)
    target_host = (parsed.netloc or "").lower()
    bare = _host_bare(target_host)
    if not bare:
        return {
            "error": "invalid_url",
            "pages": [],
            "pages_crawled": 0,
            "source": "perplexity_research",
        }

    max_pages = max(1, int(max_pages or 0) or 2000)
    start = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"

    system = (
        "You are an SEO auditor with live web access.\n"
        "Research the public website and list EVERY real indexable HTML page on THAT domain only.\n"
        "Use sitemaps, site: search results, navigation, blog indexes, and public page listings.\n"
        "Do NOT invent URLs. Prefer pages you can verify from search/public sources.\n"
        "If the live site is behind a captcha/WAF, still list publicly known pages from the open web.\n"
        "For EACH page, fill SEO fields from public evidence (title, meta description, H1, issues).\n"
        "status_code: use 200 when the page is known to exist publicly; 404 only if clearly dead.\n"
        "Do not stop early — return the full set of pages you can verify (as many as exist)."
    )
    user = (
        f"Website: {start}\n"
        f"Domain: {bare}\n"
        f"Return all indexable pages you can verify (target complete coverage; "
        f"include at least every major section page). JSON shape:\n"
        "{\n"
        '  "pages_found": <int>,\n'
        '  "broken_links": <int estimate or 0>,\n'
        '  "note": "<one sentence about coverage>",\n'
        '  "pages": [\n'
        "    {\n"
        '      "url": "https://...",\n'
        '      "title": "...",\n'
        '      "status_code": 200,\n'
        '      "meta_description": "...",\n'
        '      "canonical": "https://...",\n'
        '      "h1": ["..."],\n'
        '      "images_without_alt": 0,\n'
        '      "issues": ["optional short on-page SEO issue"]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Include homepage first, then every service/about/contact/landing/blog/product/category "
        "page you can verify. Maximize coverage — this feeds a per-page SEO audit."
    )

    try:
        parsed_json, citations = await _perplexity_research(system, user)
    except Exception as exc:  # noqa: BLE001
        log.warning("research_site_crawl_failed", error=str(exc), url=start)
        return {
            "error": str(exc)[:200],
            "pages": [],
            "pages_crawled": 0,
            "source": "perplexity_research",
        }

    raw_pages = list((parsed_json or {}).get("pages") or [])
    same_host_citations = [
        c
        for c in citations
        if _host_bare(urlparse(c).netloc) == bare
        and not any(
            urlparse(c).path.lower().endswith(ext)
            for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".css", ".js")
        )
    ]
    pages = _normalize_pages(
        raw_pages,
        target_host=target_host or bare,
        max_pages=max_pages,
        extra_urls=same_host_citations,
    )

    if pages:
        home = _normalize_url(start, default_host=target_host)
        if home and not any(p["url"].rstrip("/") == home.rstrip("/") for p in pages):
            pages.insert(
                0,
                {
                    "url": home,
                    "status_code": 200,
                    "title": "",
                    "meta_description": "",
                    "canonical": "",
                    "h1": [],
                    "issues": [],
                    "images_without_alt": 0,
                    "checks": {},
                },
            )
            pages = pages[:max_pages]

    broken = int((parsed_json or {}).get("broken_links") or 0)
    note = str((parsed_json or {}).get("note") or "").strip()
    if not note:
        note = (
            f"Discovered {len(pages)} pages via Perplexity web research "
            "(direct crawl blocked by bot protection)."
        )

    log.info(
        "research_site_crawl_ok",
        domain=bare,
        pages=len(pages),
        citations=len(same_host_citations),
    )
    return {
        "error": None if pages else "no_pages_found",
        "pages": pages,
        "pages_crawled": len(pages),
        "broken_links": broken,
        "note": note,
        "source": "perplexity_research",
    }
