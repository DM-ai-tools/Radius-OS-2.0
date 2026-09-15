"""Shared competitive + geographic context for Phase 5–6 (and beyond).

Later stages must:
1. Treat CompetitorProfile / CDP competitive landscape as the competitor set
2. Resolve geographic_focus to Ahrefs country + DataForSEO location_code
3. Optionally crawl competitor sites (blogs, hubs) for comparison
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm import extract_domain
from app.integrations.web_fetch import discover_site_urls, fetch_url, parse_html
from app.models import CompetitorProfile

# DataForSEO country codes
COUNTRY_LOCATION_CODES: dict[str, int] = {
    "us": 2840,
    "gb": 2826,
    "uk": 2826,
    "ca": 2124,
    "au": 2036,
    "in": 2356,
    "sg": 2702,
    "ae": 2784,
    "nz": 2554,
    "ie": 2372,
    "de": 2276,
    "fr": 2250,
}

# Common metro codes (Keywords Data / SERP). Unknown cities fall back to country.
CITY_LOCATION_CODES: dict[str, tuple[int, str, str]] = {
    # city_key -> (location_code, display_name, country)
    "los angeles": (1013962, "Los Angeles,California,United States", "us"),
    "new york": (1023191, "New York,New York,United States", "us"),
    "nyc": (1023191, "New York,New York,United States", "us"),
    "chicago": (1013366, "Chicago,Illinois,United States", "us"),
    "houston": (1013988, "Houston,Texas,United States", "us"),
    "phoenix": (1013990, "Phoenix,Arizona,United States", "us"),
    "philadelphia": (1013983, "Philadelphia,Pennsylvania,United States", "us"),
    "san antonio": (1014040, "San Antonio,Texas,United States", "us"),
    "san diego": (1014041, "San Diego,California,United States", "us"),
    "dallas": (1013755, "Dallas,Texas,United States", "us"),
    "san jose": (1014044, "San Jose,California,United States", "us"),
    "austin": (1013445, "Austin,Texas,United States", "us"),
    "jacksonville": (1013997, "Jacksonville,Florida,United States", "us"),
    "san francisco": (1014044, "San Francisco,California,United States", "us"),
    "seattle": (1014047, "Seattle,Washington,United States", "us"),
    "denver": (1013764, "Denver,Colorado,United States", "us"),
    "washington": (1014221, "Washington,District of Columbia,United States", "us"),
    "boston": (1013485, "Boston,Massachusetts,United States", "us"),
    "miami": (1013996, "Miami,Florida,United States", "us"),
    "atlanta": (1013433, "Atlanta,Georgia,United States", "us"),
    "london": (9047038, "London,England,United Kingdom", "gb"),
    "manchester": (9047081, "Manchester,England,United Kingdom", "gb"),
    "toronto": (1002417, "Toronto,Ontario,Canada", "ca"),
    "vancouver": (1002514, "Vancouver,British Columbia,Canada", "ca"),
    "sydney": (1000286, "Sydney,New South Wales,Australia", "au"),
    "melbourne": (1000259, "Melbourne,Victoria,Australia", "au"),
    "mumbai": (1007751, "Mumbai,Maharashtra,India", "in"),
    "delhi": (1007745, "Delhi,India", "in"),
    "bangalore": (1007740, "Bengaluru,Karnataka,India", "in"),
    "bengaluru": (1007740, "Bengaluru,Karnataka,India", "in"),
    "singapore": (2702, "Singapore", "sg"),
    "dubai": (9044034, "Dubai,United Arab Emirates", "ae"),
}

_BLOG_PATH_RE = re.compile(
    r"/(blog|blogs|articles?|news|insights?|resources?|guides?|learn|magazine)(/|$)",
    re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def resolve_geographic_focus(
    commercial: dict[str, Any] | None = None,
    intake: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map discovery geographic_focus → Ahrefs country + DataForSEO location_code.

    Never invents a city. Empty / unknown focus → United States national (2840),
    not Los Angeles or any metro default.
    """
    commercial = commercial or {}
    intake = intake or {}
    marketing = marketing or {}
    raw = (
        commercial.get("geographic_focus")
        or intake.get("geographic_focus")
        or marketing.get("geographic_focus")
        or ""
    )
    raw_s = str(raw).strip()
    geo = raw_s.lower()

    country = "us"
    location_code = COUNTRY_LOCATION_CODES["us"]
    location_name = "United States"
    location_type = "Country"
    city: str | None = None
    is_local = False

    if not geo:
        return {
            "geographic_focus": "",
            "country": country,
            "location_code": location_code,
            "labs_location_code": location_code,
            "location_name": location_name,
            "location_type": location_type,
            "city": None,
            "is_local": False,
            "note": "No geographic_focus on profile — using United States national.",
        }

    # Longest city match first so "san francisco" beats "san"
    for city_key, (code, name, ctry) in sorted(
        CITY_LOCATION_CODES.items(), key=lambda x: -len(x[0])
    ):
        if city_key in geo:
            country = ctry
            location_code = code
            location_name = name
            location_type = "City"
            city = city_key.title() if city_key not in ("nyc",) else "New York"
            if city_key == "nyc":
                city = "New York"
            elif city_key == "los angeles":
                city = "Los Angeles"
            else:
                city = " ".join(w.capitalize() for w in city_key.split())
            is_local = True
            break

    if not is_local:
        # Phrase / token country detection — longest phrases first; avoid "us" in "australia"
        phrase_map = [
            ("united states", "us"),
            ("united kingdom", "gb"),
            ("great britain", "gb"),
            ("australia", "au"),
            ("new zealand", "nz"),
            ("singapore", "sg"),
            ("canada", "ca"),
            ("india", "in"),
            ("ireland", "ie"),
            ("germany", "de"),
            ("france", "fr"),
            ("united arab emirates", "ae"),
            ("u.a.e", "ae"),
            ("uae", "ae"),
            ("england", "gb"),
            ("scotland", "gb"),
            ("wales", "gb"),
        ]
        matched = False
        for phrase, code in phrase_map:
            if phrase in geo:
                country = code
                matched = True
                break
        if not matched:
            toks = _tokens(geo)
            token_map = {
                "usa": "us",
                "us": "us",
                "uk": "gb",
                "gb": "gb",
                "au": "au",
                "ca": "ca",
                "in": "in",
                "sg": "sg",
                "nz": "nz",
                "ae": "ae",
                "de": "de",
                "fr": "fr",
                "ie": "ie",
            }
            for tok, code in token_map.items():
                if tok in toks:
                    country = code
                    matched = True
                    break
        # US state names → country us, still national (no city invent)
        us_states = {
            "california",
            "texas",
            "florida",
            "new york",
            "illinois",
            "pennsylvania",
            "ohio",
            "georgia",
            "north carolina",
            "michigan",
            "new jersey",
            "virginia",
            "washington",
            "arizona",
            "massachusetts",
            "tennessee",
            "indiana",
            "missouri",
            "maryland",
            "wisconsin",
            "colorado",
            "minnesota",
            "south carolina",
            "alabama",
            "louisiana",
            "kentucky",
            "oregon",
            "oklahoma",
            "connecticut",
            "utah",
            "iowa",
            "nevada",
            "arkansas",
            "mississippi",
            "kansas",
            "new mexico",
            "nebraska",
            "idaho",
            "hawaii",
            "alaska",
        }
        if any(st in geo for st in us_states) and country == "us":
            location_type = "Region"
            is_local = True  # regional intent, but keep national code unless city matched

        location_code = COUNTRY_LOCATION_CODES.get(country, 2840)
        location_name = {
            "us": "United States",
            "gb": "United Kingdom",
            "ca": "Canada",
            "au": "Australia",
            "in": "India",
            "sg": "Singapore",
            "ae": "United Arab Emirates",
            "nz": "New Zealand",
            "ie": "Ireland",
            "de": "Germany",
            "fr": "France",
        }.get(country, "United States")

    labs_location_code = int(COUNTRY_LOCATION_CODES.get(country, 2840))

    return {
        "geographic_focus": raw_s,
        "country": country,
        "location_code": int(location_code),
        "labs_location_code": labs_location_code,
        "location_name": location_name,
        "location_type": location_type,
        "city": city,
        "is_local": is_local and bool(city),
        "note": (
            f"Using {location_name} (code {location_code}; Labs {labs_location_code}) "
            f"from geographic_focus={raw_s!r}."
        ),
    }


def _normalize_comp_row(
    *,
    name: str | None,
    url: str | None,
    source: str | None = None,
    cluster: str | None = None,
    confirmed: bool | None = None,
) -> dict[str, Any] | None:
    domain = extract_domain(url or "") if url else ""
    label = (name or "").strip() or domain
    if not label and not domain:
        return None
    return {
        "name": label,
        "url": (url or (f"https://{domain}" if domain else "")).strip(),
        "domain": domain,
        "source": source or "shared_memory",
        "cluster": cluster,
        "confirmed": bool(confirmed) if confirmed is not None else True,
    }


async def resolve_competitors(
    db: AsyncSession,
    client_id: UUID,
    *,
    competitive_summary: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    limit: int = 8,
    prefer_confirmed: bool = True,
) -> list[dict[str, Any]]:
    """Pick competitors from shared memory / CompetitorProfile — never re-discover.

    Priority:
    1. Confirmed CompetitorProfile rows (post-approve)
    2. CDP competitive_landscape_summary.competitors
    3. Any CompetitorProfile rows (pending signoff)
    4. Discovery marketing_context.competitors (names/URLs carried forward)
    """
    competitive_summary = competitive_summary or {}
    marketing = marketing or {}
    rows = (
        await db.execute(
            select(CompetitorProfile).where(CompetitorProfile.client_id == client_id)
        )
    ).scalars().all()

    confirmed = [r for r in rows if r.confirmed]
    pool = confirmed if (prefer_confirmed and confirmed) else list(rows)

    out: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    seen_names: set[str] = set()

    def _add(item: dict[str, Any] | None) -> None:
        if not item:
            return
        d = (item.get("domain") or "").lower()
        n = (item.get("name") or "").strip().lower()
        if d and d in seen_domains:
            return
        if not d and n and n in seen_names:
            return
        if d:
            seen_domains.add(d)
        if n:
            seen_names.add(n)
        out.append(item)

    for r in pool:
        _add(
            _normalize_comp_row(
                name=r.name,
                url=r.url,
                source=r.source,
                cluster=r.positioning_cluster,
                confirmed=r.confirmed,
            )
        )

    if not out:
        for row in competitive_summary.get("competitors") or []:
            if not isinstance(row, dict):
                continue
            _add(
                _normalize_comp_row(
                    name=row.get("name"),
                    url=row.get("url"),
                    source=row.get("source") or "cdp",
                    cluster=row.get("cluster"),
                    confirmed=True,
                )
            )

    if not out:
        for key in ("competitors", "known_competitors"):
            raw = marketing.get(key)
            if isinstance(raw, list):
                for row in raw:
                    if isinstance(row, dict):
                        _add(
                            _normalize_comp_row(
                                name=row.get("name"),
                                url=row.get("url"),
                                source="discovery",
                            )
                        )
                    elif isinstance(row, str) and row.strip():
                        _add(
                            _normalize_comp_row(
                                name=row.strip(),
                                url=None,
                                source="discovery",
                            )
                        )
            elif isinstance(raw, str) and raw.strip():
                for part in re.split(r"[,;\n]+", raw):
                    part = part.strip()
                    if part:
                        _add(_normalize_comp_row(name=part, url=None, source="discovery"))

    return out[:limit]


def competitor_domains(competitors: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in competitors:
        d = (c.get("domain") or extract_domain(c.get("url") or "") or "").lower()
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def competitor_names(competitors: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in competitors:
        n = str(c.get("name") or c.get("domain") or "").strip()
        key = n.lower()
        if n and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def local_seed_variants(seeds: list[str], geo: dict[str, Any], *, cap: int = 6) -> list[str]:
    """Only when geographic_focus is a known city — append '{seed} {city}' variants.

    Does not invent metros (e.g. never adds Los Angeles unless geo says so).
    """
    if not geo.get("is_local") or not geo.get("city"):
        return []
    city = str(geo["city"])
    extras: list[str] = []
    for s in seeds[:8]:
        base = s.strip()
        if not base:
            continue
        if city.lower() in base.lower():
            continue
        extras.append(f"{base} {city}")
        if len(extras) >= cap:
            break
    return extras


def _classify_path(path: str) -> str:
    p = (path or "/").lower()
    if _BLOG_PATH_RE.search(p):
        return "blog"
    if any(x in p for x in ("/service", "/product", "/pricing", "/solution")):
        return "commercial"
    if any(x in p for x in ("/about", "/team", "/company", "/contact")):
        return "about"
    return "other"


async def fetch_competitor_site_snapshots(
    competitors: list[dict[str, Any]],
    *,
    max_competitors: int = 4,
    max_pages_each: int = 28,
    sample_titles: int = 8,
) -> list[dict[str, Any]]:
    """Crawl competitor domains for blogs / key pages used in phase comparisons."""
    snapshots: list[dict[str, Any]] = []
    for comp in competitors[:max_competitors]:
        domain = comp.get("domain") or extract_domain(comp.get("url") or "")
        if not domain:
            snapshots.append(
                {
                    "name": comp.get("name"),
                    "domain": None,
                    "url": comp.get("url"),
                    "error": "missing_url",
                    "pages": [],
                    "blogs": [],
                    "page_count": 0,
                    "blog_count": 0,
                }
            )
            continue
        start = comp.get("url") or f"https://{domain}"
        try:
            urls = await discover_site_urls(start, max_pages=max_pages_each)
        except Exception as exc:  # noqa: BLE001
            snapshots.append(
                {
                    "name": comp.get("name"),
                    "domain": domain,
                    "url": start,
                    "error": str(exc)[:160],
                    "pages": [],
                    "blogs": [],
                    "page_count": 0,
                    "blog_count": 0,
                }
            )
            continue

        pages: list[dict[str, Any]] = []
        blogs: list[dict[str, Any]] = []
        # Prefer blog URLs for title sampling, then homepage-ish paths
        ordered = sorted(
            urls,
            key=lambda u: (0 if _BLOG_PATH_RE.search(u) else 1, len(u)),
        )
        failures = 0
        for u in ordered[: max(sample_titles + 4, 12)]:
            try:
                fetched = await fetch_url(u, timeout=8)
            except Exception:  # noqa: BLE001
                failures += 1
                if failures >= 2:
                    break
                continue
            if fetched.get("error") or int(fetched.get("status_code") or 0) in (0, 403, 404):
                failures += 1
                if failures >= 2:
                    break
                continue
            failures = 0
            html = fetched.get("text") or ""
            title = ""
            if html:
                try:
                    title = parse_html(html).title or ""
                except Exception:  # noqa: BLE001
                    title = ""

            path = urlparse(str(fetched.get("url") or u)).path or "/"
            kind = _classify_path(path)
            row = {
                "url": fetched.get("url") or u,
                "path": path,
                "title": (title or "")[:160],
                "kind": kind,
                "status": fetched.get("status_code"),
            }
            pages.append(row)
            if kind == "blog":
                blogs.append(row)

        # Also count blog-ish URLs even if we didn't fetch titles
        blog_urls = [u for u in urls if _BLOG_PATH_RE.search(u)]
        snapshots.append(
            {
                "name": comp.get("name"),
                "domain": domain,
                "url": start,
                "error": None,
                "page_count": len(urls),
                "blog_count": len(blog_urls),
                "blog_paths_sample": blog_urls[:12],
                "pages": pages[:sample_titles],
                "blogs": blogs[:sample_titles],
                "hub_paths": sorted(
                    {
                        "/".join((urlparse(u).path or "/").rstrip("/").split("/")[:2]) or "/"
                        for u in urls[:40]
                    }
                )[:15],
            }
        )
    return snapshots


def competitor_context_blob(
    competitors: list[dict[str, Any]],
    snapshots: list[dict[str, Any]] | None = None,
    geo: dict[str, Any] | None = None,
) -> str:
    """Compact text block for LLM prompts — names, domains, blog samples, geo."""
    lines = [
        "COMPETITORS (from shared memory / competitor research — use ONLY these for comparison):"
    ]
    for c in competitors:
        lines.append(
            f"- {c.get('name')} | {c.get('domain') or c.get('url') or 'no-url'} "
            f"(source={c.get('source')}, confirmed={c.get('confirmed')})"
        )
    if snapshots:
        lines.append("COMPETITOR SITE / BLOG SNAPSHOTS:")
        for s in snapshots:
            lines.append(
                f"- {s.get('name')} ({s.get('domain')}): "
                f"{s.get('page_count')} pages, {s.get('blog_count')} blog URLs"
            )
            for b in (s.get("blogs") or s.get("pages") or [])[:4]:
                if b.get("title"):
                    lines.append(f"    · [{b.get('kind')}] {b.get('title')} — {b.get('path')}")
    if geo:
        lines.append(
            f"GEOGRAPHIC FOCUS: {geo.get('geographic_focus') or 'not set'} → "
            f"{geo.get('location_name')} (country={geo.get('country')}, "
            f"location_code={geo.get('location_code')}). "
            "Do NOT invent other cities (e.g. do not add Los Angeles unless focus is LA)."
        )
    return "\n".join(lines)
