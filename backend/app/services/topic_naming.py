"""Specific topic / cluster / page title naming.

Avoid collapsing multi-word opportunities into ultra-generic heads
like \"SEO\" or \"Marketing\". Prefer the ranked keyword phrase.
"""

from __future__ import annotations

import re
from typing import Any

_ACRONYMS = {
    "seo",
    "sem",
    "ppc",
    "crm",
    "b2b",
    "b2c",
    "ai",
    "api",
    "ux",
    "ui",
    "roi",
    "kpi",
    "cms",
    "saas",
    "ecommerce",
    "e-commerce",
}

_SMALL = {"a", "an", "the", "to", "for", "of", "and", "or", "in", "on", "with", "vs", "versus"}

# Verbs that make a bare "How to {keyword}" grammatical. Noun-phrase keywords
# (e.g. "content marketing") must not be wrapped as "How to Content Marketing".
_ACTION_VERBS = {
    "improve", "increase", "boost", "build", "create", "choose", "start", "optimize",
    "optimise", "rank", "grow", "generate", "reduce", "fix", "measure", "track", "write",
    "design", "launch", "set", "get", "use", "find", "hire", "plan", "manage", "automate",
    "convert", "scale", "run", "make", "learn", "understand", "avoid", "prevent", "speed",
    "lower", "win", "drive", "earn", "do", "master", "calculate", "estimate", "install",
    "configure", "migrate", "audit", "research", "pick", "select", "setup", "grade",
}


def is_actionable_keyword(kw: str) -> bool:
    """True when the phrase starts with an action verb (so 'How to X' reads well)."""
    toks = re.findall(r"[a-z0-9]+", (kw or "").lower())
    return bool(toks) and toks[0] in _ACTION_VERBS

_GENERIC_HEADS = {
    "seo",
    "marketing",
    "digital",
    "business",
    "content",
    "sales",
    "service",
    "services",
    "agency",
    "strategy",
    "growth",
    "branding",
    "advertising",
    "software",
    "tools",
    "platform",
    "solutions",
}


def phrase_title(kw: str) -> str:
    """Title-case a keyword phrase without mangling acronyms."""
    parts: list[str] = []
    for w in re.sub(r"\s+", " ", (kw or "").strip()).split(" "):
        if not w:
            continue
        low = w.lower()
        if low in _ACRONYMS or (low.replace("-", "") in _ACRONYMS):
            parts.append(low.upper() if "-" not in low else "-".join(p.upper() for p in low.split("-")))
        elif low in _SMALL and parts:
            parts.append("vs" if low in ("vs", "versus") else low)
        else:
            parts.append(w[0].upper() + w[1:] if len(w) > 1 else w.upper())
    return " ".join(parts) or "Untitled"


def is_generic_label(name: str) -> bool:
    toks = [t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in _SMALL]
    if not toks:
        return True
    if len(toks) == 1 and toks[0] in _GENERIC_HEADS:
        return True
    if len(toks) == 2 and toks[0] in {"digital", "online", "internet"} and toks[1] in _GENERIC_HEADS:
        return True
    return False


def specific_cluster_name(
    primary_keyword: str,
    *,
    intent: str | None = None,
    head: str | None = None,
    mod: str | None = None,
) -> str:
    """Name a cluster from the primary keyword — never a lone generic head."""
    pkw = (primary_keyword or "").strip()
    phrase = phrase_title(pkw)
    intent_l = (intent or "").lower()
    mod_l = (mod or "").lower()
    head_l = (head or "").strip()

    if mod_l == "comparison" or " vs " in pkw.lower() or " versus " in pkw.lower():
        return phrase if " vs " in phrase.lower() else f"{phrase} Comparison"
    if mod_l.startswith("location:"):
        loc = mod_l.split(":", 1)[1]
        base = phrase if not is_generic_label(phrase) else phrase_title(head_l or pkw)
        return f"{base} in {phrase_title(loc)}"
    if mod_l == "how-to" and not pkw.lower().startswith("how to"):
        return f"How to {phrase}" if is_generic_label(phrase) else phrase
    if mod_l == "listicle" and not pkw.lower().startswith(("best ", "top ")):
        return phrase if len(phrase.split()) >= 3 else f"Best {phrase}"
    if mod_l == "what-is" and not pkw.lower().startswith("what is"):
        return phrase if len(phrase.split()) >= 3 else f"What Is {phrase}?"
    if mod_l == "tools":
        return phrase if "tool" in pkw.lower() else f"{phrase} Tools"

    if not is_generic_label(phrase) and len(phrase.split()) >= 2:
        return phrase

    # Ultra-generic single heads — qualify by intent / service framing
    if intent_l == "transactional":
        return f"{phrase} Services & Pricing"
    if intent_l == "commercial":
        return f"{phrase} Buyer's Guide"
    if intent_l == "navigational":
        return f"{phrase} (Brand / Login)"
    return f"{phrase} Strategy"


def specific_page_title(
    keyword: str,
    *,
    content_type: str | None = None,
    intent: str | None = None,
    industry: str | None = None,
    location: str | None = None,
    page_type: str | None = None,
    audience: str | None = None,
    client_name: str | None = None,
    differentiation: str | None = None,
    outline_count: int | None = None,
    template_hint: str | None = None,
    pain_point: str | None = None,
) -> str:
    """Headline-framework title grounded in the exact keyword — curiosity + benefit.

    ``template_hint`` lets a caller (e.g. a topic angle) prefer a specific headline
    shape so a repeated keyword yields varied titles instead of one 'How to …'.
    ``pain_point`` grounds problem-framed shapes in the audience's real pain."""
    from app.services.headline_framework import pick_primary_headline

    raw = (keyword or "").strip()
    if not raw:
        return "Untitled"
    return pick_primary_headline(
        raw,
        page_type=page_type or content_type,
        content_type=content_type,
        intent=intent,
        industry=industry,
        location=location,
        audience=audience,
        client_name=client_name,
        differentiation=differentiation,
        outline_count=outline_count,
        prefer_template=template_hint,
        pain_point=pain_point,
    )


def specific_pillar_label(
    name: str | None,
    primary_keyword: str | None,
    *,
    intent: str | None = None,
) -> str:
    """Pillar display name: prefer specific keyword over generic topic_plan title."""
    pkw = (primary_keyword or "").strip()
    nm = (name or "").strip()
    if pkw and (not nm or is_generic_label(nm) or is_generic_label(pkw) is False and len(pkw.split()) >= len(nm.split())):
        if not is_generic_label(pkw) or len(pkw.split()) >= 2:
            return specific_cluster_name(pkw, intent=intent)
    if nm and not is_generic_label(nm):
        return phrase_title(nm)
    if pkw:
        return specific_cluster_name(pkw, intent=intent)
    return phrase_title(nm or "Topic Hub")


def prefer_specific_keyword(rows: list[dict[str, Any]], fallback: str = "") -> str:
    """Pick the most specific keyword string from scored rows."""
    best = fallback
    best_score = -1.0
    for r in rows:
        if not isinstance(r, dict):
            continue
        kw = str(r.get("keyword") or "").strip()
        if not kw:
            continue
        toks = len(kw.split())
        spec = float(r.get("specificity") or 0)
        score = spec * 10 + toks + (0 if is_generic_label(kw) else 5)
        if score > best_score:
            best_score = score
            best = kw
    return best
