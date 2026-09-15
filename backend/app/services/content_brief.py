"""Phase 9 — Content brief generator (pre-flight + SERP + coverage, no word-count targets)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.agents.prompts import load_shared_reference, load_skill_file
from app.integrations import dataforseo
from app.integrations.llm import synthesize_json
from app.services.create_topic import (
    format_audience_label,
    funnel_balance,
    funnel_balance_warnings,
    funnel_from_intent,
)

YMYL_HINTS = (
    "health",
    "medical",
    "doctor",
    "diagnosis",
    "finance",
    "loan",
    "mortgage",
    "invest",
    "insurance",
    "tax",
    "legal",
    "lawyer",
    "attorney",
    "safety",
    "supplement",
    "crypto",
    "credit",
)

# Hard YMYL always requires credentials. Soft terms are ignored when the topic is
# clearly marketing/SEO services (e.g. "invest in seo", "ad credit").
_HARD_YMYL = {
    "health",
    "medical",
    "doctor",
    "diagnosis",
    "mortgage",
    "insurance",
    "lawyer",
    "attorney",
    "supplement",
    "crypto",
    "loan",
}
_SOFT_YMYL = {"finance", "invest", "tax", "legal", "safety", "credit"}
_MARKETING_TOPIC = {
    "seo",
    "sem",
    "ppc",
    "ads",
    "adwords",
    "marketing",
    "content",
    "agency",
    "website",
    "web",
    "aeo",
    "geo",
}

_FORUM_DOMAINS = ("reddit.com", "quora.com", "stackexchange.com", "stackoverflow.com", "forum.")
_WEAK_DIFF = re.compile(
    r"^(lead with|ranking pages typically|n/?a|none|tbd|todo)\b",
    re.IGNORECASE,
)
_ROLE_ONLY = re.compile(
    r"^(the\s+)?((seo|content|marketing|editorial|copy)\s+)?"
    r"(team|specialist|writer|strategist|manager|agency|department)\s*$",
    re.IGNORECASE,
)
_AUTHOR_KEYS = (
    "content_author",
    "author",
    "primary_contact_name",
    "owner_name",
    "founder",
    "founder_name",
    "account_owner",
    "writer",
    "editor",
    "byline",
)


def _path(url: str) -> str:
    if not url:
        return "/"
    if "://" in url:
        return urlparse(url).path or "/"
    return url if str(url).startswith("/") else f"/{url}"


def _norm_kw(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _audience_label(audience: Any, client_name: str) -> str | None:
    label = format_audience_label(audience)
    if label:
        return label
    if audience and not isinstance(audience, (dict, list)):
        text = str(audience).strip()
        if text and not text.startswith("{"):
            return text
    return client_name if client_name else None


def _kw_overlap(a: str, b: str) -> bool:
    na, nb = _norm_kw(a), _norm_kw(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    aw, bw = set(na.split()), set(nb.split())
    if len(aw) < 2 or len(bw) < 2:
        return False
    return len(aw & bw) / max(1, min(len(aw), len(bw))) >= 0.7


def _breadcrumb(parent: str | None, path: str) -> str:
    bits = ["Home"]
    if parent and parent not in ("/", path):
        label = parent.strip("/").replace("-", " ").title() or "Parent"
        bits.append(label)
    leaf = path.strip("/").split("/")[-1].replace("-", " ").title() if path not in ("", "/") else "This page"
    bits.append(leaf or "This page")
    return " > ".join(bits)


def _same_page(path: str, sug: str) -> bool:
    """True only when both paths are the same page (trailing slash ignored).

    Substring matching cannot be used here. "/" is a substring of every URL, so the
    homepage would claim ownership of every planned page; and a parent hub like "/blog"
    is not the owner of "/blog/local-seo-pricing" — treating it as one retargets every
    new child page onto the hub. Genuine topical overlap is handled separately by the
    keyword/title check below.
    """
    a = (path or "").rstrip("/")
    b = (sug or "").rstrip("/")
    return bool(a) and bool(b) and a == b


def find_existing_intent(
    keyword: str,
    *,
    content_audit: dict[str, Any],
    website: dict[str, Any],
    suggested_url: str | None = None,
) -> dict[str, Any] | None:
    """Return the closest existing page that already targets this intent, if any."""
    want = _norm_kw(keyword)
    sug = _path(suggested_url or "")
    candidates: list[dict[str, Any]] = []
    for row in list(content_audit.get("inventory") or []) + list(content_audit.get("refresh_queue") or []):
        if isinstance(row, dict):
            candidates.append(row)
    from app.services.site_sitemap import sitemap_pages

    for row in sitemap_pages(website):
        candidates.append(row)
    for key in ("sample_urls", "top_pages", "pages", "crawled_pages"):
        raw = website.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    candidates.append(item)
                elif item:
                    candidates.append({"url": str(item), "title": str(item)})

    best: dict[str, Any] | None = None
    for row in candidates:
        path = _path(str(row.get("path") or row.get("url") or ""))
        title = str(row.get("title") or row.get("keyword") or "")
        kw = str(row.get("keyword") or row.get("primary_keyword") or "")
        if sug and sug != "/" and _same_page(path, sug):
            best = row
            break
        if (kw and _kw_overlap(want, kw)) or (title and _kw_overlap(want, title)):
            best = row
            break
    if not best:
        return None
    disp = str(best.get("disposition") or best.get("verdict") or "").lower()
    performing = disp in ("keep", "optimize", "optimise", "retitle") and disp not in (
        "refresh",
        "consolidate",
        "delete",
        "delete_candidate",
        "retire",
        "noindex",
    )
    if disp in ("refresh", "consolidate"):
        band = "underperforming"
    elif disp in ("delete", "delete_candidate", "retire"):
        band = "delete_candidate"
    elif performing or disp == "keep":
        band = "performing"
    else:
        band = "unknown"
    return {
        "url": best.get("url") or best.get("path"),
        "path": _path(str(best.get("path") or best.get("url") or "")),
        "title": best.get("title"),
        "disposition": disp or None,
        "band": band,
    }


def resolve_ia_placement(
    *,
    keyword: str,
    suggested_url: str | None,
    site_architecture: dict[str, Any],
    roadmap_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tree = site_architecture.get("target_url_tree") or []
    sug = _path(suggested_url or (roadmap_row or {}).get("url") or (roadmap_row or {}).get("path") or "")
    match: dict[str, Any] | None = None
    for node in tree:
        if not isinstance(node, dict):
            continue
        path = _path(str(node.get("url") or node.get("path") or ""))
        kw = str(node.get("keyword") or node.get("primary_keyword") or "")
        if sug and sug != "/" and path == sug:
            match = node
            break
        if kw and _kw_overlap(keyword, kw):
            match = node
            break
    if roadmap_row and not match:
        match = roadmap_row
    path = _path(str((match or {}).get("url") or (match or {}).get("path") or sug or ""))
    parent = (match or {}).get("parent") or ("/" if path not in ("", "/") else None)
    page_type = (match or {}).get("type") or (match or {}).get("page_type") or "article"
    assigned = bool(path and path != "/")
    return {
        "url": (match or {}).get("url") or path,
        "path": path,
        "parent": parent,
        "page_type": page_type,
        "breadcrumb": _breadcrumb(str(parent) if parent else None, path),
        "depth": (match or {}).get("depth"),
        "assigned": assigned,
    }


def _person_name(val: Any) -> str | None:
    if isinstance(val, dict):
        val = val.get("name") or val.get("full_name") or val.get("author")
    text = str(val or "").strip()
    if not text or len(text) < 3:
        return None
    if text.lower() in {"none", "n/a", "tbd", "unknown", "-", "—"}:
        return None
    if _ROLE_ONLY.match(text):
        return None
    return text[:120]


def _listish(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in str(raw).replace(";", ",").split(",") if p.strip()]


def merge_brief_memory(
    *,
    marketing: dict[str, Any] | None = None,
    commercial: dict[str, Any] | None = None,
    client_name: str | None = None,
) -> dict[str, Any]:
    """Fold commercial + intake so author / positioning / products are visible to briefing."""
    mkt = dict(marketing or {})
    intake = dict(mkt.get("client_intake") or {})
    comm = dict(commercial or mkt.get("commercial_scope") or {})
    for src in (comm, mkt):
        for key in (*_AUTHOR_KEYS, "author_credentials", "expertise", "positioning", "products", "geographic_focus"):
            if src.get(key) not in (None, "", [], {}) and key not in intake:
                intake[key] = src[key]
    if client_name:
        intake.setdefault("client_name", client_name)
    mkt["client_intake"] = intake
    if comm:
        mkt["commercial_scope"] = comm
    return mkt


def _first_author(*blobs: dict[str, Any] | None) -> str | None:
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in _AUTHOR_KEYS:
            name = _person_name(blob.get(key))
            if name:
                return name
    return None


def _word_in(needle: str, haystack: str) -> bool:
    """Whole-word match — avoids taxonomy→tax, accreditation→credit, syntax→tax."""
    if not needle or not haystack:
        return False
    return bool(re.search(rf"\b{re.escape(needle)}\b", haystack, flags=re.IGNORECASE))


def is_ymyl_topic(keyword: str, industry: str | None = None) -> bool:
    """Detect YMYL-adjacent topics without substring false positives."""
    blob = f"{keyword} {industry or ''}".lower()
    if any(_word_in(h, blob) for h in _HARD_YMYL):
        return True
    if any(_word_in(h, blob) for h in _SOFT_YMYL):
        # "invest in seo" / "meta ads credit" are marketing metaphors, not YMYL advice.
        if any(_word_in(m, blob) for m in _MARKETING_TOPIC):
            return False
        return True
    return False


def author_standing(
    keyword: str,
    *,
    industry: str | None,
    marketing: dict[str, Any] | None = None,
    client_name: str | None = None,
) -> dict[str, Any]:
    ymyl = is_ymyl_topic(keyword, industry)
    mkt = merge_brief_memory(marketing=marketing, client_name=client_name)
    intake = dict(mkt.get("client_intake") or {})
    commercial = dict(mkt.get("commercial_scope") or {})
    author = _first_author(intake, mkt, commercial)
    credentials = (
        intake.get("author_credentials")
        or intake.get("expertise")
        or intake.get("positioning")
        or mkt.get("expertise")
        or commercial.get("expertise")
        or commercial.get("positioning")
    )
    if not author and client_name and not ymyl:
        # Organization as accountable publisher — create-content needs a named byline.
        author = client_name
        standing = "organization"
        note = (
            str(credentials)
            if credentials
            else f"No individual in CDD — {client_name} is the accountable publisher until a person is named."
        )
        return {"ymyl": False, "author": author, "standing": standing, "note": note}
    if ymyl and not credentials:
        return {
            "ymyl": True,
            "author": author,
            "standing": "unverified",
            "note": (
                "YMYL-adjacent topic — no demonstrated credentials in shared memory. "
                "Do not send to a writer until an expert author is named."
            ),
        }
    return {
        "ymyl": ymyl,
        "author": author,
        "standing": "recorded" if credentials else ("unverified" if ymyl else "generalist"),
        "note": credentials or ("Practitioner SERP likely" if ymyl else None),
    }


def differentiation_angle(
    *,
    client_name: str,
    keyword: str,
    serp: dict[str, Any],
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
) -> str:
    """Specific angle create-content will accept (>=24 chars, not a weak SERP restatement)."""
    mkt = merge_brief_memory(marketing=marketing, client_name=client_name)
    intake = dict(mkt.get("client_intake") or {})
    commercial = dict(mkt.get("commercial_scope") or {})
    positioning = str(
        intake.get("positioning") or commercial.get("positioning") or mkt.get("positioning") or ""
    ).strip()
    products = _listish(
        intake.get("products") or commercial.get("products") or mkt.get("products")
    )
    geo = str(
        intake.get("geographic_focus") or commercial.get("geographic_focus") or ""
    ).strip()
    fmt = str(serp.get("dominant_format") or "article")
    organic = [r for r in (serp.get("organic") or []) if isinstance(r, dict)]
    titles = " ".join(str(r.get("title") or "") for r in organic).lower()
    validated = bool(serp.get("validated") and organic)
    method = positioning or (products[0] if products else "") or (industry or keyword)
    proof = geo or "named delivery process and client constraints"
    gap = f"a generic {keyword} {fmt}"
    if validated:
        missing: list[str] = []
        for prod in products[:3]:
            if prod.lower() not in titles:
                missing.append(prod)
                break
        if geo and geo.lower() not in titles:
            missing.append(geo)
        if "pricing" not in titles and "cost" not in titles and "how to choose" not in titles:
            missing.append("decision criteria")
        if missing:
            gap = f"current {fmt} results that omit {', '.join(missing[:3])}"
        elif organic:
            top = str(organic[0].get("title") or "the current #1")[:48]
            gap = f"current {fmt} results such as “{top}”"
    text = (
        f"{client_name} publishes from {method} practice with {proof} — "
        f"not another restatement of {gap}."
    )
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 24 or _WEAK_DIFF.search(text):
        text = (
            f"{client_name} original {keyword} method with named process "
            f"and local proof, not a generic {fmt}."
        )
    return text[:280]


def preflight(
    *,
    keyword: str,
    suggested_url: str | None,
    action: str | None,
    content_audit: dict[str, Any],
    website: dict[str, Any],
    site_architecture: dict[str, Any],
    roadmap_row: dict[str, Any] | None,
    industry: str | None,
    marketing: dict[str, Any] | None,
    client_name: str | None = None,
) -> dict[str, Any]:
    existing = find_existing_intent(
        keyword, content_audit=content_audit, website=website, suggested_url=suggested_url
    )
    from app.services.url_mapping import lookup_url_map_entry

    url_map_entry = lookup_url_map_entry(
        {"final_url_map": site_architecture.get("final_url_map") or []},
        keyword=keyword,
        cluster=str((roadmap_row or {}).get("cluster") or ""),
    )
    if url_map_entry and url_map_entry.get("action") in ("OPTIMIZE_EXISTING", "REVIEW_MERGE_REDIRECT"):
        mapped = url_map_entry.get("selected_url")
        existing = existing or {
            "url": mapped,
            "path": _path(str(mapped or "")),
            "title": keyword,
            "band": "performing" if url_map_entry.get("score_band") == "HIGH" else "underperforming",
            "match_confidence": url_map_entry.get("match_confidence"),
        }
        if url_map_entry.get("match_confidence") is not None:
            existing["match_confidence"] = url_map_entry.get("match_confidence")
    ia = resolve_ia_placement(
        keyword=keyword,
        suggested_url=suggested_url,
        site_architecture=site_architecture,
        roadmap_row=roadmap_row,
    )
    author = author_standing(
        keyword,
        industry=industry,
        marketing=marketing,
        client_name=client_name,
    )

    blockers: list[str] = []
    recommended_action = action or "needs_review"
    sug_path = _path(
        suggested_url
        or (roadmap_row or {}).get("url")
        or (roadmap_row or {}).get("path")
        or ""
    )
    existing_is_same = bool(
        existing
        and sug_path
        and sug_path != "/"
        and _same_page(_path(str(existing.get("path") or existing.get("url") or "")), sug_path)
    )
    if existing:
        if existing_is_same and recommended_action == "create":
            recommended_action = "refresh"
            ia["url"] = existing.get("url") or ia["url"]
            ia["path"] = existing.get("path") or ia["path"]
        elif existing["band"] == "performing" and recommended_action == "create":
            blockers.append(
                f"Existing page already owns this intent ({existing.get('url')}). "
                "Stop — brief a refresh via content-audit, not a new URL."
            )
            recommended_action = "stop_refresh_existing"
        elif existing["band"] == "underperforming":
            recommended_action = "refresh"
            ia["url"] = existing.get("url") or ia["url"]
            ia["path"] = existing.get("path") or ia["path"]
        elif existing["band"] == "delete_candidate":
            recommended_action = "create"
        elif existing["band"] == "unknown" and recommended_action == "create":
            blockers.append(
                f"Possible existing page for this intent ({existing.get('url')}) — "
                "run content-audit before commissioning a new URL."
            )
            recommended_action = "stop_refresh_existing"
    if not ia.get("assigned"):
        blockers.append("URL / parent not assigned in site-architecture — brief stays draft.")
    if not author.get("author"):
        blockers.append(
            "Named author missing in CDD/marketing — create-content will refuse without accountable standing."
        )
    if author.get("ymyl") and author.get("standing") == "unverified":
        blockers.append(str(author.get("note")))

    writer_ready = not blockers and recommended_action in ("create", "refresh")
    return {
        "existing_page": existing,
        "ia": ia,
        "author": author,
        "recommended_action": recommended_action,
        "blockers": blockers,
        "writer_ready": writer_ready,
        "status": "ready" if writer_ready else "draft",
        "url_map": url_map_entry,
        "match_confidence": (
            url_map_entry.get("match_confidence")
            if url_map_entry
            else (existing or {}).get("match_confidence")
        ),
    }


def _dominant_format(organic: list[dict[str, Any]]) -> str:
    titles = " ".join(str(r.get("title") or "") for r in organic).lower()
    if sum(1 for r in organic if any(d in str(r.get("domain") or "") for d in _FORUM_DOMAINS)) >= 4:
        return "forum"
    if " vs " in titles or "versus" in titles or "compared" in titles:
        return "comparison"
    if re.search(r"\b(\d+|best|top)\b", titles):
        return "listicle"
    if "how to" in titles or "guide" in titles:
        return "guide"
    return "article"


def _intent_from_serp(organic: list[dict[str, Any]], fallback: str | None) -> str:
    titles = " ".join(str(r.get("title") or "") for r in organic).lower()
    if any(x in titles for x in ("buy", "pricing", "price", "cost", "near me")):
        return "transactional"
    if any(x in titles for x in ("best", "vs", "review", "compare", "alternative")):
        return "commercial"
    return fallback or "informational"


def summarize_serp(serp: dict[str, Any], *, fallback_intent: str | None = None) -> dict[str, Any]:
    organic = [r for r in (serp.get("organic") or []) if isinstance(r, dict)]
    validated = bool(serp.get("validated") and organic)
    types = list(serp.get("item_types") or [])
    snippet = serp.get("featured_snippet") if isinstance(serp.get("featured_snippet"), dict) else None
    paa = [str(q) for q in (serp.get("people_also_ask") or []) if q]
    return {
        "validated": validated,
        "note": None
        if validated
        else str(serp.get("note") or "Live SERP unavailable — format and coverage are unvalidated assumptions."),
        "dominant_format": _dominant_format(organic) if organic else "article",
        "intent": _intent_from_serp(organic, fallback_intent),
        "item_types": types,
        "organic": [
            {"position": r.get("position"), "title": r.get("title"), "url": r.get("url"), "domain": r.get("domain")}
            for r in organic[:10]
        ],
        "people_also_ask": paa,
        "featured_snippet": {
            "url": snippet.get("url"),
            "title": snippet.get("title"),
            "format": snippet.get("snippet_format") or "paragraph",
        }
        if snippet
        else None,
        "coverage_floor": [str(r.get("title")) for r in organic[:6] if r.get("title")],
    }


def _brief_image_requirements(
    *,
    item: dict[str, Any],
    keyword: str,
    client_name: str,
    industry: str | None,
) -> dict[str, Any]:
    from app.services.content_strategy import image_suggestions_for

    suggestions = [
        s
        for s in (item.get("image_suggestions") or [])
        if isinstance(s, dict) and str(s.get("prompt") or "").strip()
    ]
    outline = item.get("outline") or item.get("sections") or []
    if not suggestions:
        suggestions = image_suggestions_for(
            keyword,
            str(item.get("content_type") or "blog"),
            str(item.get("intent") or "informational"),
            industry=industry,
            location=str(item.get("location") or item.get("geographic_focus") or "") or None,
            outline=outline if isinstance(outline, list) else None,
            count=item.get("image_count") if isinstance(item.get("image_count"), int) else None,
        )
    hero = next(
        (str(s.get("prompt")) for s in suggestions if str(s.get("role") or "") == "hero"),
        str(suggestions[0].get("prompt")) if suggestions else f"Original diagram of {keyword} for {client_name}",
    )
    supporting = [
        str(s.get("prompt"))
        for s in suggestions
        if str(s.get("role") or "") != "hero" and s.get("prompt")
    ]
    if not supporting:
        supporting = [f"Process diagram of how {client_name} delivers {keyword} — not stock"]
    return {
        "hero": hero,
        "supporting": supporting,
        "suggestions": suggestions,
        "count": len(suggestions) if suggestions else (1 + len(supporting)),
    }


def _who_how_why(
    *,
    author: str | None,
    standing: str | None,
    ymyl: bool,
    audience: str | None,
    differentiation: str,
    client_name: str,
) -> dict[str, Any]:
    """Google helpful-content Who / How / Why (shared evidence base A3)."""
    why = (
        f"Help {audience or 'the intended audience'} act on this topic with "
        f"{client_name}'s specific angle — not to fill a search calendar."
        if differentiation
        else f"Help people who need this topic from {client_name} (differentiation still required)."
    )
    return {
        "who": {
            "author": author,
            "standing": standing,
            "byline_expected": True,
            "ymyl": ymyl,
            "note": (
                "Author fame alone is not a ranking factor (A2/A7). "
                "The page must demonstrate expertise in the copy."
            ),
        },
        "how": {
            "method": "AI-assisted draft from an approved brief; named human reviews before publish",
            "automation_disclosure": (
                "Disclose AI assistance when a reader would reasonably ask how the page was created (A3/A4)."
            ),
            "one_page_per_run": True,
        },
        "why": {
            "primary": why,
            "search_engine_first": False,
            "note": (
                "If the why is primarily to attract search visits, that is not aligned with "
                "what Google seeks to reward (A3) and risks scaled content abuse (A5)."
            ),
        },
        "evidence": "skills/references/google-helpful-content.md",
    }


def _people_first_gates(*, audience: str | None, differentiation: str, author: str | None) -> dict[str, Any]:
    """Self-check against Google people-first questions (A1) — not a ranking score."""
    return {
        "has_intended_audience": bool(audience),
        "has_differentiation_beyond_consensus": bool(differentiation and len(differentiation) >= 24),
        "has_named_author_or_org": bool(author),
        "no_word_count_target": True,
        "no_keyword_density_target": True,
        "named_human_review_required": True,
        "note": (
            "These gates mirror Google's helpful-content self-assessment. "
            "They are quality checks, not an E-E-A-T score (E-E-A-T is not a ranking factor)."
        ),
    }


def _rule_brief(
    *,
    item: dict[str, Any],
    pre: dict[str, Any],
    serp: dict[str, Any],
    client_name: str,
    audience: str | None,
    secondary: list[str],
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
) -> dict[str, Any]:
    from app.services.content_pipeline import run_cluster_page_pipeline
    from app.services.create_content import default_outline_for, resolve_page_type

    kw = str(item.get("keyword") or item.get("title") or "topic").strip()
    ia = pre["ia"]
    intent = serp.get("intent") or item.get("intent") or "informational"
    paa = list(serp.get("people_also_ask") or [])
    page_type = resolve_page_type(
        {
            **item,
            "url": ia.get("url") or item.get("url") or item.get("path") or item.get("suggested_url"),
            "path": ia.get("path") or item.get("path"),
            "page_type": ia.get("page_type") or item.get("page_type") or item.get("type"),
            "content_type": item.get("content_type") or ia.get("page_type"),
            "search_intent": intent,
            "preflight": {"page_type": ia.get("page_type"), "url": ia.get("url")},
        }
    )
    mkt = dict(marketing or {})
    intake = dict(mkt.get("client_intake") or {})
    geo = str(
        mkt.get("geographic_focus")
        or intake.get("geographic_focus")
        or item.get("location")
        or ""
    ).strip() or None
    who = audience or intake.get("target_demographic") or mkt.get("target_audience")
    body_outline = default_outline_for(page_type, keyword=kw, client_name=client_name)
    cluster_stub = {
        "name": item.get("cluster") or kw,
        "primary_keyword": kw,
        "intent": intent,
        "content_type": page_type,
        "keywords": [{"keyword": kw, "role": "Primary"}]
        + [{"keyword": s, "role": "Secondary"} for s in secondary[:8]],
        "competitor_domains": list(item.get("competitor_domains") or []),
    }
    page_pipeline = run_cluster_page_pipeline(
        cluster_stub,
        serp_summary=serp,
        preflight=pre,
        client_name=client_name,
        audience=str(who) if who else None,
        industry=industry,
        location=geo,
        page_type=page_type,
        outline_count=len(body_outline),
    )
    headline_rows = page_pipeline.get("title_candidates") or []
    selected = page_pipeline.get("selected_title") or {}
    titles = [str(h.get("title") or "") for h in headline_rows if h.get("title")] or [
        str(selected.get("title") or kw.title())[:60],
        f"{kw.title()} for {client_name}"[:60],
    ]
    if page_type in ("service", "subservice", "sub_service", "landing", "product", "location"):
        outcomes = [
            f"Know what {client_name} includes in {kw}",
            "See how the work runs from first conversation to delivery",
            f"Decide whether to enquire about {kw}",
        ]
    elif page_type == "comparison":
        outcomes = [
            f"Compare options for {kw} on named criteria",
            "Pick the option that fits their situation",
            "Take a next step without opening another tab",
        ]
    else:
        outcomes = [
            f"Understand what {kw} means for their situation",
            f"Follow a practical path for {kw}",
            f"Take a next step matched to {intent} intent",
        ]
    must_address = list(serp.get("coverage_floor") or [])[:6]
    out_of_scope = [
        "Adjacent commercial pages owned by other URLs in the roadmap — link, do not duplicate",
    ]
    outline = [
        {
            "heading": "H1",
            "title": titles[0],
            "notes": [
                "Answer the query in the opening — publishable prose, not a brief",
                "Write for this page type, not a generic blog template",
            ],
        },
        *[
            {"heading": "H2", "title": row["title"], "notes": list(row.get("notes") or [])}
            for row in body_outline
        ],
        {
            "heading": "H2",
            "title": "FAQ",
            "notes": paa[:4] or ["PAA unvalidated — do not invent FAQ questions"],
        },
        {
            "heading": "H2",
            "title": "Next step",
            "notes": [f"Finished CTA copy for a {page_type} page — not a Contact label"],
        },
    ]
    snippet = serp.get("featured_snippet")
    parent = ia.get("parent") or "/"
    diff = differentiation_angle(
        client_name=client_name,
        keyword=kw,
        serp=serp,
        marketing=marketing,
        industry=industry,
    )
    author_name = pre["author"].get("author")
    return {
        "title": titles[0],
        "keyword": kw,
        "secondary_keywords": secondary[:8],
        "search_intent": intent,
        "content_type": page_type,
        "page_type": page_type,
        "tone": "professional",
        "freshness": "evergreen",
        "audience": audience,
        "content_goal": "organic traffic + qualified enquiry" if intent != "informational" else "education + topical coverage",
        "preflight": {
            "existing_page_for_intent": (
                f"{pre['existing_page'].get('url')} ({pre['existing_page'].get('band')}, "
                f"{pre['existing_page'].get('disposition') or 'no verdict'})"
                if pre.get("existing_page")
                else "none found"
            ),
            "url": ia.get("url"),
            "parent": parent,
            "page_type": page_type or ia.get("page_type"),
            "breadcrumb": ia.get("breadcrumb"),
            "author": author_name,
            "author_standing": pre["author"].get("standing"),
            "ymyl": pre["author"].get("ymyl"),
            "blockers": pre.get("blockers") or [],
        },
        "writer_ready": pre.get("writer_ready"),
        "status": pre.get("status"),
        "action": pre.get("recommended_action"),
        "title_options": titles,
        "headline_framework": [
            {
                "title": h.get("title"),
                "template": h.get("template"),
                "asset": h.get("asset"),
                "score": h.get("score"),
            }
            for h in headline_rows[:5]
        ],
        "content_pipeline": {
            "title_angle": page_pipeline.get("title_angle"),
            "title_patterns": page_pipeline.get("title_patterns"),
            "title_validation": page_pipeline.get("title_validation"),
            "ranking_competitors": page_pipeline.get("ranking_competitors"),
            "existing_page": page_pipeline.get("existing_page"),
            "approval_ready": page_pipeline.get("approval_ready"),
        },
        "meta_description": (
            f"{client_name} {kw} — what is included, how it works, and how to start."
            if page_type in ("service", "subservice", "sub_service", "landing", "product", "location")
            else (
                f"{kw.title()} for {aud_label}: a practical {page_type} you can use."
                if (aud_label := _audience_label(audience, client_name))
                else f"{kw.title()} — a practical {page_type} you can use."
            )
        )[:160],
        "required_coverage": {
            "outcomes": outcomes,
            "must_address": must_address,
            "must_name": [client_name],
            "out_of_scope": out_of_scope,
        },
        "outline": outline,
        "faq": [{"q": q} for q in paa[:6]],
        "featured_snippet_target": {
            "current_holder": (snippet or {}).get("url"),
            "format": (snippet or {}).get("format") or "paragraph",
            "section": outline[1]["title"],
        }
        if snippet
        else None,
        "keyword_placement": [
            {"keyword": kw, "where": "Title, H1, opening paragraph, one H2, conclusion"},
            *[
                {"keyword": s, "where": "One H2 or H3, body where it fits the sentence"}
                for s in secondary[:4]
            ],
        ],
        "internal_links": [
            {"to": parent, "anchor": "Parent hub", "why": "Keeps the page in the IA tree"},
            {"to": "/", "anchor": client_name, "why": "Home / brand context"},
        ],
        "external_links": [
            {"source": "Primary source or official documentation", "for": "Any factual claim that needs a citation"},
        ],
        "image_requirements": _brief_image_requirements(
            item=item,
            keyword=kw,
            client_name=client_name,
            industry=industry,
        ),
        "schema_type": (
            "FAQPage"
            if paa
            else (
                "Service"
                if page_type in ("service", "subservice", "sub_service")
                else "Article"
            )
        ),
        "competitive_notes": must_address,
        "differentiation": diff,
        "who_how_why": _who_how_why(
            author=author_name,
            standing=pre["author"].get("standing"),
            ymyl=bool(pre["author"].get("ymyl")),
            audience=audience,
            differentiation=str(diff or ""),
            client_name=client_name,
        ),
        "people_first_gates": _people_first_gates(
            audience=audience,
            differentiation=str(diff or ""),
            author=author_name,
        ),
        "evidence_base": "skills/references/google-helpful-content.md",
        "serp": {
            "validated": serp.get("validated"),
            "note": serp.get("note"),
            "dominant_format": serp.get("dominant_format") or page_type,
            "organic": serp.get("organic") or [],
        },
        "url": ia.get("url"),
        "path": ia.get("path"),
        "funnel": str(item.get("funnel") or funnel_from_intent(intent, kw)).upper(),
        "angle": item.get("angle"),
        "business_fit": item.get("business_fit"),
        "from_phase5_topic": bool(item.get("from_phase5_topic")),
    }


async def _enrich_brief_llm(brief: dict[str, Any], *, client_name: str) -> dict[str, Any]:
    skill = load_skill_file("content-brief")
    evidence = load_shared_reference("google-helpful-content.md", max_chars=6000)
    parsed = await synthesize_json(
        (skill[:8000] if skill else "You are a content brief specialist.")
        + "\n\nShared Google evidence (follow; do not invent ranking myths):\n"
        + (evidence or "No preferred word count; no keyword density; people-first; Who/How/Why.")
        + "\nReturn JSON only. Do not include word_count, target_word_count, or keyword density. "
        "FAQ questions must come from people_also_ask; if empty, faq must be []. "
        "Keep writer_ready, preflight.blockers, url, keyword, action, who_how_why, "
        "people_first_gates, and evidence_base unchanged. "
        "differentiation MUST be a specific client angle (>=24 chars) naming a method, "
        "proof, or omission vs the SERP — never empty, never start with 'Lead with' "
        "or 'Ranking pages typically'. Never claim Google requires pillar pages or a "
        "topical authority score.",
        (
            f"Client: {client_name}\n"
            f"Draft brief to refine (keep url/keyword/action/writer_ready):\n{brief}"
        ),
    )
    if not isinstance(parsed, dict):
        return brief
    protected = {
        "writer_ready": brief.get("writer_ready"),
        "status": brief.get("status"),
        "action": brief.get("action"),
        "preflight": brief.get("preflight"),
        "serp": brief.get("serp"),
        "url": brief.get("url"),
        "path": brief.get("path"),
        "keyword": brief.get("keyword"),
        "who_how_why": brief.get("who_how_why"),
        "people_first_gates": brief.get("people_first_gates"),
        "evidence_base": brief.get("evidence_base"),
    }
    merged = {**brief, **parsed, **protected}
    merged.pop("word_count", None)
    merged.pop("target_word_count", None)
    merged.pop("keyword_density", None)
    llm_diff = str(merged.get("differentiation") or "").strip()
    if len(llm_diff) < 24 or _WEAK_DIFF.search(llm_diff):
        merged["differentiation"] = brief.get("differentiation") or llm_diff
    if not (brief.get("serp") or {}).get("validated"):
        merged["faq"] = []
    return merged


def refresh_brief_author_gates(
    brief: dict[str, Any],
    *,
    industry: str | None,
    marketing: dict[str, Any] | None = None,
    client_name: str | None = None,
) -> dict[str, Any]:
    """Re-apply YMYL / author standing on a cached Phase 10 brief.

    Topic re-picks reuse prior briefs; without this, a credentials fix never
    clears a stale ``YMYL-adjacent…`` blocker.
    """
    out = dict(brief)
    kw = str(out.get("keyword") or out.get("title") or "")
    standing = author_standing(
        kw,
        industry=industry,
        marketing=marketing,
        client_name=client_name,
    )
    pre = out.get("preflight") if isinstance(out.get("preflight"), dict) else {}
    pre = dict(pre)
    if standing.get("author"):
        pre["author"] = standing["author"]
        out["author"] = standing["author"]
    pre["author_standing"] = standing.get("standing")
    pre["ymyl"] = bool(standing.get("ymyl"))
    if standing.get("note"):
        pre["author_note"] = standing["note"]
    drop = (
        "ymyl-adjacent",
        "demonstrated credentials",
        "named author missing",
        "create-content will refuse without accountable",
    )
    blockers = [
        str(b)
        for b in (pre.get("blockers") or [])
        if b and not any(n in str(b).lower() for n in drop)
    ]
    if standing.get("ymyl") and standing.get("standing") == "unverified":
        blockers.append(str(standing.get("note") or "YMYL — credentials required."))
    elif not standing.get("author"):
        blockers.append(
            "Named author missing in CDD/marketing — create-content will refuse without accountable standing."
        )
    pre["blockers"] = blockers
    out["preflight"] = pre
    return finalize_writer_ready(out)


def finalize_writer_ready(brief: dict[str, Any]) -> dict[str, Any]:
    """Re-evaluate create-content gates after author / differentiation are filled."""
    pre = brief.get("preflight") if isinstance(brief.get("preflight"), dict) else {}
    drop_needles = (
        "named author missing",
        "differentiation empty",
        "create-content will refuse",
    )
    blockers = [
        str(b)
        for b in (pre.get("blockers") or [])
        if b and not any(n in str(b).lower() for n in drop_needles)
    ]
    diff = str(brief.get("differentiation") or "").strip()
    author = pre.get("author") or brief.get("author")
    if not diff or len(diff) < 24 or _WEAK_DIFF.search(diff):
        blockers.append(
            "Differentiation empty — create-content will refuse (scaled content risk)."
        )
    if not author:
        blockers.append("Named author missing — create-content will refuse.")
    action = str(brief.get("action") or "")
    ready = not blockers and action in ("create", "refresh")
    brief["writer_ready"] = ready
    brief["status"] = "ready" if ready else "draft"
    if isinstance(brief.get("preflight"), dict):
        brief["preflight"]["blockers"] = blockers
        if author and not brief["preflight"].get("author"):
            brief["preflight"]["author"] = author
    return brief


def select_brief_targets(
    *,
    roadmap: list[dict[str, Any]],
    strategy: dict[str, Any],
    limit: int = 6,
) -> list[dict[str, Any]]:
    queue = [
        r
        for r in (strategy.get("combined_priority_queue") or strategy.get("priority_queue") or strategy.get("priority_pages") or [])
        if isinstance(r, dict) and (r.get("keyword") or r.get("title"))
    ]
    by_kw = {_norm_kw(str(r.get("keyword") or r.get("title"))): r for r in queue}
    rows: list[dict[str, Any]] = []
    for node in roadmap:
        if not isinstance(node, dict):
            continue
        if node.get("action") not in (None, "create", "refresh"):
            continue
        kw = str(node.get("keyword") or node.get("primary_keyword") or node.get("title") or "")
        pri = by_kw.get(_norm_kw(kw), {})
        rows.append({**pri, **node, "keyword": kw or pri.get("keyword")})
    if not rows and not roadmap:
        for r in queue:
            if str(r.get("priority") or r.get("priority_tier") or "").lower() in ("avoid",):
                continue
            rows.append(r)
    rows.sort(
        key=lambda r: (
            int(r.get("priority_rank") or 999),
            0 if r.get("action") == "refresh" else 1,
            int(r.get("wave") or 9),
        )
    )
    return rows[: max(3, min(limit, 8))]


async def generate_briefs(
    *,
    client_name: str,
    industry: str | None = None,
    audience: str | None = None,
    marketing: dict[str, Any] | None = None,
    seo_strategy: dict[str, Any] | None = None,
    site_architecture: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
    website: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
    roadmap: list[dict[str, Any]] | None = None,
    location_code: int | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    strategy = dict(seo_strategy or {})
    ia = dict(site_architecture or {})
    audit = dict(content_audit or {})
    site = dict(website or {})
    demand = dict(search_demand or {})
    marketing = merge_brief_memory(marketing=marketing, client_name=client_name)
    loc = int(location_code or demand.get("location_code") or dataforseo.DEFAULT_LOCATION)

    targets = select_brief_targets(roadmap=list(roadmap or []), strategy=strategy, limit=limit)
    cluster_rows: list[dict[str, Any]] = []
    for c in list(demand.get("clusters") or []) + list(strategy.get("core_topics") or []):
        if not isinstance(c, dict):
            continue
        cluster_rows.append(c)
        for child in c.get("clusters") or []:
            if isinstance(child, dict):
                cluster_rows.append(child)
    report = demand.get("cluster_report")
    if isinstance(report, dict):
        for c in report.get("clusters") or []:
            if isinstance(c, dict):
                cluster_rows.append(c)

    def _cluster_members(row: dict[str, Any]) -> list[str]:
        members: list[str] = []
        head = str(row.get("primary_keyword") or row.get("name") or row.get("keyword") or "").strip()
        if head:
            members.append(head)
        for k in row.get("keywords") or row.get("supporting_keywords") or row.get("secondary_keywords") or []:
            term = str(k.get("keyword") if isinstance(k, dict) else k).strip()
            if term:
                members.append(term)
        return members

    def _related_for(keyword: str) -> list[str]:
        nk = _norm_kw(keyword)
        found: list[str] = []
        seen: set[str] = {nk}
        for row in cluster_rows:
            members = _cluster_members(row)
            norms = {_norm_kw(m) for m in members}
            if nk not in norms:
                continue
            for m in members:
                mn = _norm_kw(m)
                if mn in seen:
                    continue
                seen.add(mn)
                found.append(m)
        return found[:8]

    briefs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    serp_errors: list[str] = []

    for item in targets:
        kw = str(item.get("keyword") or item.get("primary_keyword") or item.get("title") or "").strip()
        if not kw:
            continue
        pre = preflight(
            keyword=kw,
            suggested_url=str(item.get("suggested_url") or item.get("url") or item.get("path") or "") or None,
            action=(str(item.get("action")) if item.get("action") is not None else None),
            content_audit=audit,
            website=site,
            site_architecture=ia,
            roadmap_row=item,
            industry=industry,
            marketing=marketing,
            client_name=client_name,
        )
        if pre["recommended_action"] == "stop_refresh_existing":
            skipped.append(
                {
                    "keyword": kw,
                    "reason": (pre.get("blockers") or ["Existing page owns this intent"])[0],
                    "existing_url": (pre.get("existing_page") or {}).get("url"),
                }
            )
            # Still emit a draft brief so the strategist can see the stop
        serp_raw, err = await dataforseo.serp_advanced(kw, location_code=loc)
        serp_errors.extend(err)
        serp = summarize_serp(serp_raw, fallback_intent=item.get("intent"))
        secondaries = _related_for(kw) or [
            str(s)
            for s in (item.get("supporting_keywords") or item.get("secondary_keywords") or [])
            if s
        ]
        brief = _rule_brief(
            item=item,
            pre=pre,
            serp=serp,
            client_name=client_name,
            audience=audience,
            secondary=secondaries,
            marketing=marketing,
            industry=industry,
        )
        if pre.get("recommended_action") in ("create", "refresh") and (pre.get("ia") or {}).get("assigned"):
            brief = await _enrich_brief_llm(brief, client_name=client_name)
        briefs.append(finalize_writer_ready(brief))

    ready = sum(1 for b in briefs if b.get("writer_ready"))
    balance = funnel_balance(briefs)
    return {
        "briefs": briefs,
        "skipped_new_urls": skipped,
        "brief_count": len(briefs),
        "writer_ready_count": ready,
        "draft_brief_count": len(briefs) - ready,
        "serp_errors": list(dict.fromkeys(serp_errors)),
        "funnel_balance": balance,
        "funnel_balance_warnings": funnel_balance_warnings(balance),
        "source": "content_brief",
    }
