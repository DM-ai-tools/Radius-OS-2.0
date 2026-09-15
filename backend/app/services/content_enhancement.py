"""Pre-write brief enhancement — human titles, differentiation, author, coverage."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.services.content_brief import (
    author_standing,
    differentiation_angle,
    finalize_writer_ready,
    merge_brief_memory,
)

_WEAK_DIFF = re.compile(
    r"^(lead with|ranking pages typically|n/?a|none|tbd|todo)\b",
    re.I,
)
_SLUG_PREFIX = re.compile(
    r"^(what-is|what-are|how-to|why-|when-to|guide-to|best-|top-\d*-)\s*",
    re.I,
)
_SMALL_WORDS = frozenset(
    {"a", "an", "the", "and", "or", "for", "to", "in", "on", "of", "vs", "with", "at", "by"}
)
_ACRONYMS = frozenset(
    {
        "seo",
        "cro",
        "ppc",
        "sem",
        "crm",
        "api",
        "b2b",
        "b2c",
        "roi",
        "kpi",
        "ux",
        "ui",
        "ai",
        "llm",
        "cdd",
        "ymyl",
        "cta",
        "url",
        "cms",
    }
)
_ANGLE_DIFF = {
    "pain-point": "names the operational pain and fix",
    "how-to": "step-by-step method from practitioner delivery",
    "mistakes": "common failure modes clients actually hit",
    "what-why": "plain definition tied to business impact",
    "why-failing": "diagnostic lens on why programs stall",
    "listicle": "curated checklist with named criteria",
    "comparison": "side-by-side verdict using buyer criteria",
    "case-study": "named engagement pattern without invented metrics",
    "trends": "what is changing now and what to do about it",
    "beginner": "first-principles path for new buyers",
    "advanced": "operator-level detail practitioners expect",
    "templates": "reusable framework the team can adopt",
    "never-again": "lessons from repeated client mistakes",
}


def _is_weak_diff(text: str | None) -> bool:
    t = str(text or "").strip()
    return not t or len(t) < 24 or bool(_WEAK_DIFF.search(t))


def _slug_like(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if "/" in t:
        return True
    if t.count("-") >= 2 and " " not in t:
        return True
    return t == t.lower() and "-" in t and len(t) > 18


def _title_case(text: str) -> str:
    words = text.split()
    out: list[str] = []
    for i, word in enumerate(words):
        bare = word.strip("():,")
        lower = bare.lower()
        if lower in _ACRONYMS:
            out.append(lower.upper())
        elif i > 0 and lower in _SMALL_WORDS:
            out.append(lower)
        elif lower.isupper() and len(lower) <= 4:
            out.append(word)
        else:
            out.append(bare[:1].upper() + bare[1:] if bare else word)
    return " ".join(out)


def humanize_title(
    keyword: str,
    *,
    angle: str | None = None,
    page_type: str | None = None,
    max_len: int = 72,
) -> str:
    """Turn URL slugs and raw keywords into a readable page title."""
    raw = str(keyword or "").strip()
    if not raw:
        return "Untitled page"
    if "/" in raw:
        raw = raw.rstrip("/").rsplit("/", 1)[-1]
    text = raw.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = _SLUG_PREFIX.sub("", text).strip()
    if angle == "how-to" and not text.lower().startswith("how"):
        text = f"How to {text[0].lower()}{text[1:]}" if text else text
    elif angle == "comparison" and " vs " not in text.lower():
        text = text.replace(" versus ", " vs ")
    title = _title_case(text)
    if len(title) > max_len:
        cut = title[: max_len - 1].rsplit(" ", 1)[0]
        title = cut.rstrip(":-,") + "…" if cut else title[:max_len]
    return title or _title_case(raw.replace("-", " "))


def strengthen_differentiation(
    brief: dict[str, Any],
    *,
    client_name: str,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
) -> str:
    """Build a draft-gate-safe differentiation angle from brief + client memory."""
    existing = str(brief.get("differentiation") or brief.get("differentiation_angle") or "").strip()
    if not _is_weak_diff(existing):
        return existing

    kw = str(brief.get("keyword") or brief.get("title") or "this topic").strip()
    serp = brief.get("serp") if isinstance(brief.get("serp"), dict) else {}
    mkt = merge_brief_memory(marketing=marketing, client_name=client_name)
    diff = differentiation_angle(
        client_name=client_name,
        keyword=kw,
        serp=serp,
        marketing=mkt,
        industry=industry,
    )
    if not _is_weak_diff(diff):
        return diff

    angle = str(brief.get("angle") or "").strip().lower()
    intake = dict(mkt.get("client_intake") or {})
    commercial = dict(mkt.get("commercial_scope") or {})
    positioning = str(
        intake.get("positioning") or commercial.get("positioning") or mkt.get("positioning") or ""
    ).strip()
    geo = str(intake.get("geographic_focus") or commercial.get("geographic_focus") or "").strip()
    method = positioning or industry or client_name
    proof = geo or "named delivery process and client constraints"
    angle_note = _ANGLE_DIFF.get(angle, "practitioner take")
    fmt = str(serp.get("dominant_format") or brief.get("content_type") or "article")
    diff = (
        f"{client_name} {angle_note} on {kw} — grounded in {method} with {proof}, "
        f"not another generic {fmt}."
    )
    return re.sub(r"\s+", " ", diff).strip()[:280]


def _ensure_author(
    brief: dict[str, Any],
    *,
    client_name: str,
    marketing: dict[str, Any] | None,
    industry: str | None,
) -> bool:
    pre = brief.setdefault("preflight", {})
    if not isinstance(pre, dict):
        pre = {}
        brief["preflight"] = pre
    current = pre.get("author") or brief.get("author")
    if current and str(current).strip().lower() not in ("none", "n/a", "tbd", "unknown", "—", "-"):
        return False

    kw = str(brief.get("keyword") or brief.get("title") or "")
    standing = author_standing(
        kw,
        industry=industry,
        marketing=marketing,
        client_name=client_name,
    )
    if not standing.get("author"):
        return False
    pre["author"] = standing["author"]
    if standing.get("standing"):
        pre["author_standing"] = standing["standing"]
    pre["ymyl"] = bool(standing.get("ymyl"))
    if standing.get("note"):
        pre["author_note"] = standing["note"]
    brief["author"] = standing["author"]
    return True


def _ensure_coverage(brief: dict[str, Any], *, client_name: str) -> bool:
    cov = brief.get("required_coverage")
    if not isinstance(cov, dict):
        cov = {}
        brief["required_coverage"] = cov
    kw = str(brief.get("keyword") or brief.get("title") or "this topic")
    title = str(brief.get("title") or humanize_title(kw, angle=brief.get("angle")))
    changed = False
    if not cov.get("outcomes"):
        cov["outcomes"] = [
            f"Leave with a clear decision on {title.lower()}",
        ]
        changed = True
    if not cov.get("must_name"):
        cov["must_name"] = [client_name]
        changed = True
    if not cov.get("must_address"):
        cov["must_address"] = [
            f"What {title} means in practice",
            "How to evaluate options and next steps",
        ]
        changed = True
    cov.setdefault("out_of_scope", [])
    return changed


def _ensure_meta_description(brief: dict[str, Any], *, client_name: str) -> bool:
    if "meta_description" in brief:
        return False
    title = str(brief.get("title") or "").strip()
    diff = str(brief.get("differentiation") or "").strip()
    if not title:
        return False
    snippet = diff.split("—")[0].split(" - ")[0].strip() if diff else title
    brief["meta_description"] = f"{title} — {snippet}"[:160].rstrip(" ,.;—-")
    return True


def _serp_competitor_titles(brief: dict[str, Any], *, limit: int = 5) -> list[str]:
    serp = brief.get("serp") if isinstance(brief.get("serp"), dict) else {}
    titles: list[str] = []
    for row in serp.get("organic") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title[:120])
        if len(titles) >= limit:
            break
    return titles


def _list_str(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    return [text] if text else []


def build_accuracy_grounding(
    brief: dict[str, Any],
    *,
    client_name: str,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    location: str | None = None,
    seo_strategy: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Facts the writer must honour — cluster terms, geo, SERP gaps, never invent."""
    from app.services.create_content import related_keywords

    mkt = merge_brief_memory(marketing=marketing, client_name=client_name)
    intake = dict(mkt.get("client_intake") or {})
    commercial = dict(mkt.get("commercial_scope") or {})
    primary, related = related_keywords(
        brief,
        seo_strategy=seo_strategy,
        search_demand=search_demand,
        limit=8,
    )
    geo = str(
        location
        or intake.get("geographic_focus")
        or commercial.get("geographic_focus")
        or mkt.get("geographic_focus")
        or mkt.get("location")
        or ""
    ).strip()
    products = _list_str(
        intake.get("products")
        or commercial.get("products")
        or mkt.get("products")
        or mkt.get("products_services")
    )[:6]
    positioning = str(
        intake.get("positioning") or commercial.get("positioning") or mkt.get("positioning") or ""
    ).strip()
    competitors = _serp_competitor_titles(brief)
    competitive = _list_str(brief.get("competitive_notes"))[:6]
    cov = brief.get("required_coverage") if isinstance(brief.get("required_coverage"), dict) else {}
    must_address = _list_str(cov.get("must_address"))[:8]
    out_of_scope = _list_str(cov.get("out_of_scope"))[:8]
    must_name = _list_str(cov.get("must_name")) or [client_name]
    if client_name and client_name not in must_name:
        must_name = [client_name, *must_name][:6]

    rules = [
        "Do not invent statistics, case studies, quotes, awards, or client names.",
        "Do not restate competitor SERP titles — answer the same query with a distinct angle.",
        "Use only related keywords supplied below; never invent extra target terms.",
        "Honour out_of_scope exactly.",
        "Mark any unsourced number/date/price/study with [VERIFY].",
    ]
    if geo:
        rules.append(f"Ground examples and audience in {geo} when relevant; do not invent other markets.")
    if positioning:
        rules.append(f"Stay inside positioning: {positioning[:160]}")

    return {
        "primary_keyword": primary or str(brief.get("keyword") or "").strip(),
        "related_keywords": related,
        "geo": geo,
        "industry": str(industry or mkt.get("industry") or "").strip(),
        "products": products,
        "positioning": positioning,
        "must_name": must_name,
        "must_address": must_address,
        "out_of_scope": out_of_scope,
        "serp_competitors_to_beat": competitors,
        "competitive_notes": competitive,
        "rules": rules,
    }


def _inject_related_keywords(brief: dict[str, Any], related: list[str]) -> bool:
    if not related:
        return False
    existing = _list_str(brief.get("secondary_keywords"))
    if existing:
        return False
    brief["secondary_keywords"] = related[:8]
    return True


def _enrich_must_address_from_serp(brief: dict[str, Any], grounding: dict[str, Any]) -> bool:
    cov = brief.get("required_coverage")
    if not isinstance(cov, dict):
        cov = {}
        brief["required_coverage"] = cov
    current = _list_str(cov.get("must_address"))
    added: list[str] = []
    for note in grounding.get("competitive_notes") or []:
        if note not in current and note not in added:
            added.append(note)
    # If SERP validated but coverage is thin, force gap-oriented prompts.
    serp = brief.get("serp") if isinstance(brief.get("serp"), dict) else {}
    if serp.get("validated") and len(current) + len(added) < 2:
        for title in grounding.get("serp_competitors_to_beat") or []:
            gap = f"Cover a decision angle missing from “{title[:60]}”"
            if gap not in current and gap not in added:
                added.append(gap)
                break
    if not added:
        return False
    cov["must_address"] = (current + added)[:8]
    return True


def enhance_brief_for_writing(
    brief: dict[str, Any],
    *,
    client_name: str,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    location: str | None = None,
    seo_strategy: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    Strengthen a brief before create-content gates.

    Returns (enhanced_brief, changes_applied). Does not mutate the input brief.
    """
    if not brief or not isinstance(brief, dict):
        return brief, []

    out = copy.deepcopy(brief)
    applied: list[str] = []
    kw = str(out.get("keyword") or out.get("title") or "").strip()

    title = str(out.get("title") or "").strip()
    if kw and (not title or _slug_like(title)):
        out["title"] = humanize_title(
            kw,
            angle=str(out.get("angle") or "") or None,
            page_type=str(out.get("content_type") or out.get("page_type") or "") or None,
        )
        applied.append("title_humanized")

    if _is_weak_diff(out.get("differentiation") or ""):
        out["differentiation"] = strengthen_differentiation(
            out,
            client_name=client_name,
            marketing=marketing,
            industry=industry,
        )
        applied.append("differentiation_strengthened")

    if _ensure_author(out, client_name=client_name, marketing=marketing, industry=industry):
        applied.append("author_resolved")

    if _ensure_coverage(out, client_name=client_name):
        applied.append("coverage_filled")

    if _ensure_meta_description(out, client_name=client_name):
        applied.append("meta_description_drafted")

    grounding = build_accuracy_grounding(
        out,
        client_name=client_name,
        marketing=marketing,
        industry=industry,
        location=location,
        seo_strategy=seo_strategy,
        search_demand=search_demand,
    )
    out["accuracy_grounding"] = grounding
    applied.append("accuracy_grounding")

    if _inject_related_keywords(out, list(grounding.get("related_keywords") or [])):
        applied.append("related_keywords_injected")

    if _enrich_must_address_from_serp(out, grounding):
        applied.append("serp_coverage_enriched")
        # Refresh grounding after coverage changes.
        grounding = build_accuracy_grounding(
            out,
            client_name=client_name,
            marketing=marketing,
            industry=industry,
            location=location,
            seo_strategy=seo_strategy,
            search_demand=search_demand,
        )
        out["accuracy_grounding"] = grounding

    if location and not out.get("location"):
        out["location"] = location
        applied.append("location_attached")

    finalize_writer_ready(out)
    return out, applied
