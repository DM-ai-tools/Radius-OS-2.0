"""Comprehensive headline framework for titles and topics.

Interest = Curiosity + a BIG PROMISE (strongly desired benefit).

Maps IA page types / content formats onto blog, landing, comparison, and
service headline patterns, segmented by traffic temperature (cold / warm /
hot). Headlines lead with the audience's pain point where possible (problem
identification, objection crusher, never-again, warning-signs shapes).

Guardrail: never invents statistics, social proof, authority names, or
outcomes — specificity comes only from inputs the caller supplies (keyword,
audience, geo, pain point, objection, outline count).
"""

from __future__ import annotations

import re
from typing import Any

from app.services.topic_naming import (
    is_actionable_keyword,
    is_generic_label,
    phrase_title,
)

# Asset class for template selection
_ASSET_BY_PAGE = {
    "service": "landing",
    "landing": "landing",
    "product": "landing",
    "commercial": "landing",
    "location": "landing",
    "comparison": "comparison",
    "listicle": "blog",
    "guide": "blog",
    "hub": "blog",
    "spoke": "blog",
    "pillar": "blog",
    "cluster": "blog",
    "article": "blog",
    "blog": "blog",
    "post": "blog",
    "tool": "blog",
    "home": "landing",
}

_TRAFFIC_BY_INTENT = {
    "informational": "cold",
    "navigational": "warm",
    "commercial": "warm",
    "transactional": "hot",
}


def asset_class(
    *,
    page_type: str | None = None,
    content_type: str | None = None,
    intent: str | None = None,
) -> str:
    for raw in (page_type, content_type):
        key = _ASSET_BY_PAGE.get(str(raw or "").strip().lower())
        if key:
            return key
    intent_l = (intent or "").lower()
    if intent_l == "transactional":
        return "landing"
    if intent_l == "commercial":
        return "landing"
    return "blog"


def traffic_temperature(intent: str | None = None, *, explicit: str | None = None) -> str:
    if explicit and explicit.lower() in ("cold", "warm", "hot"):
        return explicit.lower()
    return _TRAFFIC_BY_INTENT.get((intent or "").lower(), "warm")


def _cap(title: str, limit: int = 70) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip(" -:|,") + "…"


def _benefit_from_keyword(keyword: str) -> str:
    """Turn a search phrase into a benefit-shaped fragment without inventing outcomes."""
    phrase = phrase_title(keyword)
    low = phrase.lower()
    if low.startswith("how to "):
        return phrase_title(phrase[7:])
    if low.startswith("what is "):
        return phrase_title(phrase[8:])
    if low.startswith(("best ", "top ")):
        return phrase
    return phrase


def _howto_title(keyword: str) -> str:
    """Grammar-aware 'How to …'. Only verb-led phrases get a bare 'How to X';
    noun-phrase keywords become 'How to Get Started with X'."""
    phrase = phrase_title(keyword)
    if phrase.lower().startswith("how to "):
        return phrase
    if is_actionable_keyword(keyword):
        return f"How To {phrase}"
    return f"How To Get Started With {phrase}"


def _problem_from_keyword(keyword: str) -> str:
    phrase = phrase_title(keyword)
    low = phrase.lower()
    if "without" in low:
        return phrase
    # Soft problem framing — do not invent a medical/scare claim
    return f"Guesswork Around {phrase}"


def _audience_label(audience: str | None, industry: str | None) -> str | None:
    raw = (audience or industry or "").strip()
    if not raw:
        return None
    # Keep short for headlines
    first = re.split(r"[,;/]| and ", raw, maxsplit=1)[0].strip()
    if len(first) > 40:
        first = first[:37].rstrip() + "…"
    return phrase_title(first)


def _objection(intent: str | None, page_type: str | None) -> str | None:
    intent_l = (intent or "").lower()
    pt = (page_type or "").lower()
    if pt in ("service", "landing", "product") or intent_l == "transactional":
        return "Wasting Budget On Guesswork"
    if intent_l == "commercial":
        return "Hiring Blind"
    if intent_l == "informational":
        return "Generic Advice"
    return None


def build_headline_options(
    keyword: str,
    *,
    page_type: str | None = None,
    content_type: str | None = None,
    intent: str | None = None,
    audience: str | None = None,
    industry: str | None = None,
    location: str | None = None,
    client_name: str | None = None,
    differentiation: str | None = None,
    traffic: str | None = None,
    outline_count: int | None = None,
    limit: int = 5,
    prefer_template: str | None = None,
    pain_point: str | None = None,
    keep_scores: bool = False,
) -> list[dict[str, Any]]:
    """Return ranked headline options with template provenance.

    Does not invent numbers, celebrities, or university claims. Specificity
    comes from the keyword, audience, geo, and optional outline_count only.

    ``prefer_template`` boosts a named template to the top so callers cycling
    through content angles get varied titles for the same keyword.
    """
    raw = (keyword or "").strip()
    if not raw:
        return [{"title": "Untitled", "template": "fallback", "asset": "blog"}]

    phrase = phrase_title(raw)
    benefit = _benefit_from_keyword(raw)
    asset = asset_class(page_type=page_type, content_type=content_type, intent=intent)
    temp = traffic_temperature(intent, explicit=traffic)
    who = _audience_label(audience, industry)
    geo = phrase_title(location) if location and location.lower() not in (
        "united states",
        "us",
        "usa",
        "national",
        "global",
    ) else ""
    objection = _objection(intent, page_type)
    pain = phrase_title(pain_point) if pain_point and pain_point.strip() else None
    n = outline_count if outline_count and 3 <= outline_count <= 15 else None
    diff_hint = ""
    if differentiation and len(differentiation.strip()) >= 24:
        # Pull a short angle phrase — first clause only
        diff_hint = re.split(r"[.!]", differentiation.strip(), maxsplit=1)[0].strip()
        if len(diff_hint) > 48:
            diff_hint = ""

    candidates: list[dict[str, Any]] = []

    def add(title: str, template: str, *, score: float = 1.0) -> None:
        t = _cap(title)
        if not t or any(c["title"].lower() == t.lower() for c in candidates):
            return
        # Soft ban on empty curiosity / pure keyword dumps when we can do better
        candidates.append(
            {
                "title": t,
                "template": template,
                "asset": asset,
                "traffic": temp,
                "score": score,
            }
        )

    # Preserve strong existing query shapes
    low = phrase.lower()
    if low.startswith(("how to ", "what is ")) or " vs " in low or low.startswith(("best ", "top ")):
        add(phrase, "keyword_shape", score=3.0)

    if asset == "comparison" or " vs " in low:
        if " vs " in low:
            add(f"{phrase}: Which Fits Your Situation?", "comparison_verdict", score=2.8)
            add(f"How To Choose: {phrase}", "comparison_choose", score=2.4)
        else:
            add(f"{phrase}: Options Compared", "comparison_options", score=2.6)
            add(f"How To Evaluate {phrase}", "comparison_evaluate", score=2.2)

    elif asset == "landing":
        if temp == "cold":
            add(
                f"Struggling With {pain or _problem_from_keyword(raw)}? Here's The Path Forward",
                "cold_struggle",
                score=2.6 if pain else 2.2,
            )
            if who:
                add(
                    f"What Every {who} Needs To Know About {phrase}",
                    "cold_what_audience",
                    score=2.5,
                )
            add(f"How To Get {benefit}" + (f" Without {objection}" if objection else ""), "cold_how_without", score=2.7)
        if temp in ("warm", "hot") or True:
            if who and objection:
                add(
                    f"How {who} Get {benefit} Without {objection}",
                    "warm_audience_without",
                    score=2.9,
                )
            if geo:
                add(f"{phrase} in {geo}" + (f" for {who}" if who else ""), "geo_service", score=2.8)
            if client_name:
                add(f"{phrase} With {phrase_title(client_name)}", "brand_service", score=2.3)
            add(
                f"Get Results on {phrase} Without {objection}" if objection else f"A Clear Path To {benefit}",
                "stop_start",
                score=2.0,
            )
            if temp == "hot":
                add(f"Get {benefit}" + (f" in {geo}" if geo else " — Next Steps"), "hot_get", score=2.6)
                if who:
                    add(f"The Practical {phrase} Path for {who}", "hot_practical", score=2.4)

    else:  # blog / guide / hub
        actionable = is_actionable_keyword(raw)
        if low.startswith("how to "):
            add(phrase + (f" ({geo})" if geo else ""), "howto", score=2.9)
        elif actionable:
            add(f"How To {benefit}" + (f" in {geo}" if geo else ""), "howto", score=2.7)
        else:
            # Bare "How to {noun phrase}" is ungrammatical — reframe and de-prioritize.
            add(f"How To Get Started With {phrase}" + (f" in {geo}" if geo else ""), "howto", score=1.8)

        if low.startswith("what is "):
            add(phrase, "what_is", score=2.9)
        else:
            add(f"What Is {phrase}? A Clear Guide", "what_is", score=2.6)

        # Pain-point shapes (problem identification / never-again / why-failing).
        # Framed from the keyword or a supplied pain point — nothing invented.
        # Long pain phrases drop the suffix cleanly rather than truncating mid-word.
        struggle = f"Struggling With {pain or phrase}? Here's The Fix"
        if len(struggle) > 60:
            struggle = f"Struggling With {pain or phrase}?"
        add(struggle, "pain_struggle", score=2.6 if pain else 2.3)
        failing = f"Why Your {phrase} Isn't Working (And How To Fix It)"
        if len(failing) > 60:
            failing = f"Why Your {phrase} Isn't Working"
        add(failing, "why_not_working", score=2.4)
        add(f"Never Struggle With {pain or phrase} Again", "never_again", score=2.2)
        if n:
            add(f"{n} Warning Signs Your {phrase} Is Falling Behind", "warning_signs", score=2.3)

        _verb = "" if actionable else "Improve "
        if n:
            add(f"{n} Practical Ways To {_verb}{benefit}".replace("  ", " "), "list_n_ways", score=2.6)
            add(f"{n} {phrase} Mistakes To Avoid", "list_mistakes", score=2.4)
        else:
            add(f"{phrase} Mistakes To Avoid", "list_mistakes", score=2.2)
        add(f"Best {phrase} Strategies", "best_strategies", score=2.3)
        add(f"{phrase} Case Study: Results & Lessons", "case_study", score=2.1)
        add(f"{phrase} Trends To Watch", "trends", score=2.1)
        add(f"{phrase} For Beginners", "beginner", score=2.0)
        add(f"Advanced {phrase}: A Deep Dive", "advanced", score=2.0)
        add(f"{phrase} Templates & Frameworks", "templates", score=2.0)
        if who:
            add(f"What {who} Need To Know About {phrase}", "audience_need", score=2.5)
            add(f"Why {who} Should Care About {phrase}", "audience_why", score=2.2)
        if objection:
            add(f"{benefit} Without {objection}", "without_objection", score=2.5)
        if (content_type or page_type or "").lower() in ("guide", "hub", "pillar"):
            add(f"{phrase}: A Practical Guide" + (f" for {who}" if who else ""), "practical_guide", score=2.4)
        add(f"The Truth About {phrase}", "truth_about", score=1.6)
        if diff_hint:
            add(f"{phrase}: {diff_hint}", "diff_angle", score=2.8)

    # Always keep a clean keyword title as a safe SEO option
    if not is_generic_label(raw) and len(phrase.split()) >= 2:
        add(phrase + (f" in {geo}" if geo and geo.lower() not in phrase.lower() else ""), "keyword_seo", score=2.0)
    elif geo:
        add(f"{phrase} in {geo}", "keyword_geo", score=2.1)
    elif who:
        add(f"{phrase} for {who}", "keyword_audience", score=2.0)
    else:
        add(phrase if not is_generic_label(phrase) else f"{phrase} Strategy", "keyword_fallback", score=1.2)

    if prefer_template:
        for c in candidates:
            if c["template"] == prefer_template:
                c["score"] = float(c["score"]) + 5.0

    candidates.sort(key=lambda c: (-float(c["score"]), len(c["title"])))
    out = candidates[: max(3, min(limit, 8))]
    if keep_scores:
        return out
    return [{k: v for k, v in c.items() if k != "score"} for c in out]


_PATTERN_TO_TEMPLATE = {
    "how_to": "howto",
    "listicle": "list_n_ways",
    "comparison": "comparison_verdict",
    "question": "what_is",
    "guide": "practical_guide",
}


def extract_title_patterns(titles: list[str]) -> dict[str, Any]:
    """Mine recurring SERP title shapes for angle selection."""
    counts = {
        "how_to": 0,
        "listicle": 0,
        "comparison": 0,
        "question": 0,
        "guide": 0,
    }
    samples: list[str] = []
    for raw in titles:
        t = str(raw or "").strip()
        if not t:
            continue
        if len(samples) < 6:
            samples.append(t)
        low = t.lower()
        if low.startswith("how to") or " how to " in low:
            counts["how_to"] += 1
        if re.search(r"\b(best|top)\b|\d+\s+(ways|tips|strategies|steps|examples)", low):
            counts["listicle"] += 1
        if " vs " in low or " versus " in low or "compare" in low:
            counts["comparison"] += 1
        if low.startswith(("what is", "what are", "why ", "when ", "where ")) or t.endswith("?"):
            counts["question"] += 1
        if "guide" in low or "complete guide" in low or "ultimate" in low:
            counts["guide"] += 1
    dominant = max(counts, key=lambda k: counts[k]) if any(counts.values()) else "guide"
    return {
        "counts": counts,
        "dominant_pattern": dominant,
        "sample_titles": samples,
    }


def determine_title_angle(
    *,
    serp_summary: dict[str, Any] | None = None,
    intent: str | None = None,
    content_type: str | None = None,
    title_patterns: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map SERP + intent signals to a headline template preference."""
    serp = dict(serp_summary or {})
    patterns = dict(title_patterns or {})
    dominant_format = str(serp.get("dominant_format") or "").lower()
    dominant_pattern = str(patterns.get("dominant_pattern") or "").lower()
    intent_l = (intent or serp.get("intent") or "informational").lower()
    ctype = (content_type or dominant_format or "guide").lower()

    if dominant_pattern in _PATTERN_TO_TEMPLATE:
        prefer = _PATTERN_TO_TEMPLATE[dominant_pattern]
        rationale = f"SERP titles skew toward {dominant_pattern.replace('_', ' ')}"
    elif dominant_format == "listicle":
        prefer = "list_n_ways"
        rationale = "SERP dominant format is listicle"
    elif dominant_format == "comparison":
        prefer = "comparison_verdict"
        rationale = "SERP dominant format is comparison"
    elif intent_l == "transactional" or ctype in ("service", "landing", "product"):
        prefer = "stop_start"
        rationale = "Commercial / transactional intent favors action-led title"
    elif intent_l == "informational":
        prefer = "howto" if dominant_pattern == "how_to" else "practical_guide"
        rationale = "Informational intent favors educational framing"
    else:
        prefer = "practical_guide"
        rationale = "Default guide angle when SERP signal is thin"

    return {
        "prefer_template": prefer,
        "dominant_pattern": dominant_pattern or dominant_format or "guide",
        "intent": intent_l,
        "rationale": rationale,
    }


def _title_tokens(text: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "is", "are"}
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if t not in stop and len(t) > 1
    }


def score_titles_against_serp(
    candidates: list[dict[str, Any]],
    *,
    serp_summary: dict[str, Any] | None = None,
    primary_keyword: str,
    intent: str | None = None,
) -> list[dict[str, Any]]:
    """Re-rank headline candidates using keyword overlap and SERP fit."""
    serp = dict(serp_summary or {})
    organic_titles = [
        str(r.get("title") or "")
        for r in (serp.get("organic") or [])
        if isinstance(r, dict)
    ] or list(serp.get("coverage_floor") or [])
    patterns = extract_title_patterns(organic_titles)
    angle = determine_title_angle(
        serp_summary=serp,
        intent=intent,
        title_patterns=patterns,
    )
    prefer = angle.get("prefer_template")
    kw_tokens = _title_tokens(primary_keyword)
    ranked: list[dict[str, Any]] = []
    for row in candidates:
        c = dict(row)
        title = str(c.get("title") or "")
        base = float(c.get("score") or 1.0)
        overlap = len(kw_tokens & _title_tokens(title)) / max(1, len(kw_tokens))
        serp_fit = 4.0 if c.get("template") == prefer else 0.0
        length_penalty = 0.0
        if len(title) > 60:
            length_penalty += 1.0
        if len(title) > 70:
            length_penalty += 2.0
        final = round(base + overlap * 3.0 + serp_fit - length_penalty, 2)
        c["score"] = final
        c["score_breakdown"] = {
            "base": base,
            "keyword_overlap": round(overlap, 2),
            "serp_template_fit": serp_fit > 0,
            "length_penalty": length_penalty,
        }
        ranked.append(c)
    ranked.sort(key=lambda x: (-float(x.get("score") or 0), len(str(x.get("title") or ""))))
    return ranked


def validate_title(
    title: str,
    *,
    keyword: str,
    serp_summary: dict[str, Any] | None = None,
    roadmap_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Lightweight title QA before human/agent approval."""
    text = re.sub(r"\s+", " ", (title or "").strip())
    issues: list[str] = []
    if not text:
        issues.append("empty_title")
    if len(text) > 70:
        issues.append("title_too_long")
    if len(text) < 12:
        issues.append("title_too_short")
    kw_tokens = _title_tokens(keyword)
    if kw_tokens and not (kw_tokens & _title_tokens(text)):
        issues.append("missing_keyword_tokens")
    norm = text.lower()
    for existing in roadmap_titles or []:
        if str(existing or "").strip().lower() == norm:
            issues.append("duplicate_roadmap_title")
            break
    serp = dict(serp_summary or {})
    if not serp.get("validated") and serp.get("note"):
        issues.append("serp_unvalidated")
    return {
        "ok": not any(i for i in issues if i != "serp_unvalidated"),
        "issues": issues,
        "title": text,
        "keyword": keyword,
    }


def pick_primary_headline(
    keyword: str,
    *,
    page_type: str | None = None,
    content_type: str | None = None,
    intent: str | None = None,
    audience: str | None = None,
    industry: str | None = None,
    location: str | None = None,
    client_name: str | None = None,
    differentiation: str | None = None,
    traffic: str | None = None,
    outline_count: int | None = None,
    prefer_template: str | None = None,
    pain_point: str | None = None,
) -> str:
    opts = build_headline_options(
        keyword,
        page_type=page_type,
        content_type=content_type,
        intent=intent,
        audience=audience,
        industry=industry,
        location=location,
        client_name=client_name,
        differentiation=differentiation,
        traffic=traffic,
        outline_count=outline_count,
        limit=3,
        prefer_template=prefer_template,
        pain_point=pain_point,
    )
    return str(opts[0]["title"]) if opts else phrase_title(keyword or "Untitled")
