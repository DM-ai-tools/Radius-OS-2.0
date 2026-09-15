"""Composite keyword opportunity scoring: demand × difficulty × gap × trend × evergreen × fit."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def current_year() -> int:
    return datetime.now().year


_STALE_YEAR_RE = re.compile(r"\b(20[0-2]\d)\b")


def is_stale_year_keyword(keyword: str, *, now: int | None = None) -> bool:
    """True when the keyword embeds a past calendar year (e.g. '… 2024')."""
    yr = now or current_year()
    m = _STALE_YEAR_RE.search(_norm(keyword))
    if not m:
        return False
    try:
        return int(m.group(1)) < yr
    except ValueError:
        return False


def has_present_year(keyword: str, *, now: int | None = None) -> bool:
    """True when the text includes the current calendar year."""
    yr = now or current_year()
    return bool(re.search(rf"\b{yr}\b", keyword or ""))


def strip_stale_year(text: str, *, now: int | None = None) -> str:
    """Remove past-year tokens; keep present (and future) years."""
    yr = now or current_year()

    def repl(m: re.Match[str]) -> str:
        try:
            n = int(m.group(1))
        except ValueError:
            return ""
        if n < yr:
            return ""
        return m.group(1)

    cleaned = _STALE_YEAR_RE.sub(repl, text or "")
    return re.sub(r"\s+", " ", cleaned).strip(" -–—|,")


def prefer_present_year(text: str, *, now: int | None = None) -> str:
    """Rewrite any past year in a title/keyword framing to the present year."""
    yr = now or current_year()

    def repl(m: re.Match[str]) -> str:
        try:
            n = int(m.group(1))
        except ValueError:
            return str(yr)
        if n < yr:
            return str(yr)
        return m.group(1)

    cleaned = _STALE_YEAR_RE.sub(repl, text or "")
    return re.sub(r"\s+", " ", cleaned).strip(" -–—|,")


def detect_intent(keyword: str, existing: str | None = None) -> str:
    """Heuristic search intent when providers omit it.

    Prefers provider intents (Ahrefs) when present, normalized to:
    informational | commercial | transactional | navigational.
    """
    raw = str(existing or "").strip().lower()
    if raw and raw not in ("", "mixed", "none", "—", "-", "unknown"):
        # Ahrefs may return branded/local or multi-label strings
        aliases = {
            "info": "informational",
            "informational": "informational",
            "commercial": "commercial",
            "transactional": "transactional",
            "navigational": "navigational",
            "branded": "navigational",
            "brand": "navigational",
            "local": "commercial",
        }
        for token in re.split(r"[,;/|\s]+", raw):
            mapped = aliases.get(token)
            if mapped:
                return mapped
        if raw in aliases:
            return aliases[raw]
        return raw
    k = _norm(keyword)
    _GEO = (
        "melbourne",
        "sydney",
        "brisbane",
        "perth",
        "adelaide",
        "gold coast",
        "near me",
        "australia",
        "auckland",
        "wellington",
    )
    _SERVICE = (
        "seo",
        "sem",
        "ppc",
        "ads",
        "adwords",
        "marketing",
        "agency",
        "web design",
        "web development",
        "website",
        "aeo",
        "geo",
    )
    if any(g in k for g in _GEO) and any(s in k for s in _SERVICE):
        # Local service queries are commercial unless clearly educational.
        if any(x in k for x in ("what is", "what are", "how to", "guide", "meaning", "definition")):
            return "informational"
        if any(x in k for x in ("buy", "pricing", "price", "cost", "hire", "quote")):
            return "transactional"
        return "commercial"
    if any(
        x in k
        for x in (
            "buy",
            "pricing",
            "price",
            "cost",
            "hire",
            "quote",
            "near me",
            "agency",
            "services",
            "company",
            "consultant",
        )
    ):
        if any(x in k for x in ("what is", "what are", "guide", "how to", "meaning")):
            return "informational"
        if any(x in k for x in ("buy", "pricing", "price", "cost", "hire", "quote")):
            return "transactional"
        return "commercial"
    if any(x in k for x in ("best", "top", "vs", "versus", "alternative", "review", "compare")):
        return "commercial"
    if any(x in k for x in ("how to", "what is", "what are", "guide", "tips", "examples")):
        return "informational"
    if re.search(r"\b(login|sign in|signin|portal|dashboard|my account|customer portal)\b", k):
        return "navigational"
    if re.search(r"\b(official site|official website|contact number|phone number)\b", k):
        return "navigational"
    return "informational"


INTENT_LABELS = {
    "informational": "Informational",
    "navigational": "Navigational",
    "commercial": "Commercial",
    "transactional": "Transactional",
}


def intent_balance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count intent mix across a keyword pool."""
    counts = {key: 0 for key in INTENT_LABELS}
    for row in rows:
        intent = str(row.get("intent") or detect_intent(str(row.get("keyword") or ""), row.get("intent"))).lower()
        if intent not in counts:
            intent = "informational"
        counts[intent] += 1
    total = sum(counts.values())
    percent = {key: round((counts[key] / total) * 100, 1) if total else 0.0 for key in counts}
    dominant = max(counts, key=counts.get) if total else None
    dominant_share = (counts[dominant] / total) if total and dominant else 0.0
    represented = sum(1 for key in counts if counts[key] > 0)
    balanced = total > 0 and dominant_share <= 0.65 and represented >= 3
    return {
        "counts": counts,
        "percent": percent,
        "total": total,
        "balanced": balanced,
        "dominant_intent": dominant,
        "dominant_share": round(dominant_share * 100, 1) if total else 0.0,
    }


def intent_balance_warnings(counts: dict[str, int]) -> list[str]:
    """Flag intent pools that skew too heavily toward one stage."""
    total = sum(counts.values())
    if total == 0:
        return []
    warnings: list[str] = []
    for key, label in INTENT_LABELS.items():
        share = counts.get(key, 0) / total
        if counts.get(key, 0) == 0:
            warnings.append(f"No {label.lower()} keywords in pool — consider seeding or gap pulls for that intent.")
        elif share >= 0.7:
            warnings.append(
                f"Intent pool is {share * 100:.0f}% {label.lower()} — broaden seeds to balance informational/commercial/transactional mix."
            )
    return warnings


def stamp_keyword_intent(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure every keyword row has a normalized ``intent`` field."""
    kw = str(row.get("keyword") or "")
    intent = detect_intent(kw, row.get("intent"))
    if row.get("intent") == intent:
        return row
    return {**row, "intent": intent}


def detect_funnel(keyword: str, intent: str | None = None) -> str:
    """Per-keyword TOFU/MOFU/BOFU stage — the canonical heuristic shared by every
    place a keyword row is built (seeding, opportunity ranking, clustering), so a
    keyword doesn't get a different funnel label depending on which stage of the
    pipeline last touched it. Text patterns take priority over the coarser
    intent-only fallback: e.g. "beginner" is TOFU even under commercial intent.
    """
    k = _norm(str(keyword or ""))
    i = str(intent or "").strip().lower()
    if any(x in k for x in ("what is", "what are", "guide to", "beginner", "basics")):
        return "TOFU"
    if any(x in k for x in ("pricing", "price", "buy", "hire", "demo", "cost")):
        return "BOFU"
    if i == "transactional":
        return "BOFU"
    if i == "commercial":
        return "MOFU"
    return "TOFU"


def is_broad_head_term(keyword: str, products: list[str] | None = None) -> bool:
    """True for ultra-generic 1–2 word heads (e.g. 'seo services', 'ppc agency').

    These stay in clusters as head terms but should not dominate Best opportunities
    when more specific service/gap keywords exist.
    """
    kw = _norm(keyword)
    words = [w for w in kw.split() if w]
    if len(words) >= 4:
        return False
    modifiers = (
        "how to",
        "vs",
        "versus",
        "for ",
        "near me",
        "pricing",
        "cost",
        "checklist",
        "template",
        "examples",
        "alternatives",
        "white label",
        "local ",
        "technical ",
        "ecommerce",
        "e-commerce",
        "melbourne",
        "sydney",
        "brisbane",
        "gold coast",
        "perth",
        "adelaide",
    )
    if any(m in kw for m in modifiers):
        return False
    if len(words) <= 2:
        return True
    # 3-word generic like "digital marketing agency" without local/modifier
    if len(words) == 3 and words[-1] in ("agency", "services", "company", "marketing"):
        # Still broad unless it matches a longer specific product phrase
        for p in products or []:
            pn = _norm(str(p))
            if pn and pn != kw and kw in pn:
                return False
        return True
    return False


def is_noisy_keyword(keyword: str, brand_name: str | None = None) -> bool:
    """Drop junk / off-brand tokens that pollute opportunity lists."""
    kw = _norm(keyword)
    if not kw or len(kw) < 3:
        return True
    if is_stale_year_keyword(kw):
        return True
    # Finance / ticker bleed from short acronym seeds (e.g. AEO → "aeo stock price")
    if re.search(
        r"\b(stock price|share price|stock quote|nasdaq|nyse|ticker|earnings|"
        r"market cap|dividend|ipo|options chain)\b",
        kw,
    ):
        return True
    # Random brand+geo mashups with very odd tokens
    if re.search(r"\b(io|gmbh|llc|inc)\b", kw) and "white label" not in kw:
        # Allow only if clearly a product phrase; otherwise noisy
        if len(kw.split()) >= 5:
            return True
    if brand_name:
        bn = _norm(brand_name)
        if bn and bn in kw and len(kw.split()) <= 2:
            return True
    return False


def is_ambiguous_seed(seed: str) -> bool:
    """True for short acronym-like seeds that expand into unrelated industries."""
    tokens = [t for t in _norm(seed).split() if t]
    if len(tokens) == 1 and len(tokens[0]) <= 4:
        return True
    return False


def prune_redundant_acronym_seeds(seeds: list[str]) -> list[str]:
    """Drop bare acronyms when a longer seed already contains that token.

    Example: keep 'AEO & GEO Services (AI/answer engine…)' and drop bare 'AEO'.
    """
    norms = [_norm(s) for s in seeds if str(s or "").strip()]
    out: list[str] = []
    for seed in seeds:
        text = str(seed or "").strip()
        if not text:
            continue
        sn = _norm(text)
        tokens = sn.split()
        if len(tokens) == 1 and len(tokens[0]) <= 4:
            token = tokens[0]
            if any(
                other != sn and token in other.split() and len(other) > len(sn)
                for other in norms
            ):
                continue
        out.append(text)
    return out


def service_token_overlap(keyword: str, service: str) -> int:
    """Count distinctive token overlap between a keyword and its owning service."""
    weak = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
        "with",
        "services",
        "service",
        "agency",
        "company",
    }
    st = {t for t in _norm(service).split() if len(t) > 2 and t not in weak}
    kt = {t for t in _norm(keyword).split() if len(t) > 2 and t not in weak}
    return len(st & kt)


def specificity_score(keyword: str, products: list[str] | None = None) -> float:
    """Prefer specific / long-tail / service-tied phrases over ultra-generic heads."""
    kw = _norm(keyword)
    words = [w for w in kw.split() if w]
    n = len(words)
    score = 0.35
    if n >= 4:
        score += 0.35
    elif n == 3:
        score += 0.25
    elif n == 2:
        score += 0.12
    else:
        score -= 0.15  # single-word heads stay useful in clusters, not as top picks

    modifiers = (
        "best",
        "how to",
        "vs",
        "versus",
        "for",
        "near me",
        "pricing",
        "cost",
        "services",
        "company",
        "agency",
        "software",
        "tools",
        "guide",
        "checklist",
        "template",
        "examples",
        "alternatives",
    )
    if any(m in kw for m in modifiers):
        score += 0.15

    for product in products or []:
        p = _norm(str(product))
        if not p:
            continue
        if p in kw or all(t in kw for t in p.split() if len(t) > 2):
            score = max(score, 0.85)
            break

    return max(0.05, min(1.0, score))


def winnable_demand_score(volume: Any, difficulty: Any) -> tuple[float, float]:
    """Return (demand_0_1, win_0_1) with sweet-spot bias: solid volume + manageable KD."""
    if volume is None:
        demand = 0.12
    else:
        v = float(volume)
        demand = min(1.0, (max(v, 1.0) ** 0.45) / 55.0)
        if v >= 100:
            demand = min(1.0, demand + 0.08)
        if v >= 500:
            demand = min(1.0, demand + 0.08)

    if difficulty is None:
        win = 0.48
    else:
        kd = float(difficulty)
        if kd <= 45:
            win = max(0.35, 1.0 - kd / 90.0)
        elif kd <= 60:
            win = max(0.2, 0.75 - (kd - 45) / 50.0)
        else:
            win = max(0.05, 1.0 - kd / 100.0)
        if volume is not None and float(volume) >= 50 and kd <= 40:
            win = min(1.0, win + 0.12)
    return demand, win


def competitor_gap_boost(row: dict[str, Any]) -> float:
    """0–1: competitors rank well and client is missing / weak on the term."""
    if row.get("gap_flag"):
        boost = 0.85
    elif row.get("client_position") is None:
        boost = 0.45
    elif isinstance(row.get("client_position"), int) and row["client_position"] > 20:
        boost = 0.7
    elif isinstance(row.get("client_position"), int) and row["client_position"] <= 10:
        boost = 0.15
    else:
        boost = 0.4

    positions = row.get("competitor_positions") or []
    if isinstance(positions, list) and positions:
        try:
            best_pos = min(
                int(p.get("position")) for p in positions if p.get("position") is not None
            )
            if best_pos <= 3:
                boost = min(1.0, boost + 0.2)
            elif best_pos <= 10:
                boost = min(1.0, boost + 0.1)
        except (TypeError, ValueError):
            pass
    if row.get("competitor_domains") and row.get("gap_flag"):
        boost = min(1.0, boost + 0.05)
    return boost


def merge_keyword_metrics(
    ahrefs_rows: list[dict[str, Any]],
    dfs_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge Ahrefs + DataForSEO rows by keyword; never invent volume."""
    by_kw: dict[str, dict[str, Any]] = {}

    def upsert(row: dict[str, Any], provider: str) -> None:
        kw = _norm(str(row.get("keyword") or ""))
        if not kw:
            return
        cur = by_kw.setdefault(
            kw,
            {
                "keyword": str(row.get("keyword") or "").strip(),
                "sources": [],
                "volume_ahrefs": None,
                "volume_dataforseo": None,
                "volume": None,
                "difficulty_ahrefs": None,
                "difficulty_dataforseo": None,
                "difficulty": None,
                "traffic_potential": None,
                "cpc": None,
                "intent": None,
                "parent_topic": None,
                "trend": "stable",
                "gap_flag": False,
                "competitor_domains": [],
                "competitor_positions": [],
                "client_position": None,
                "volume_divergence": False,
                "serp_titles": [],
                "match_class": None,
                "seed": None,
            },
        )
        if provider not in cur["sources"]:
            cur["sources"].append(provider)
        # Prefer tighter match class when merging multi-mode seeds
        _rank = {"exact": 0, "phrase": 1, "related": 2, "broad": 3}
        incoming_class = row.get("match_class")
        if incoming_class in _rank:
            existing = cur.get("match_class")
            if existing not in _rank or _rank[incoming_class] < _rank[existing]:
                cur["match_class"] = incoming_class
        if row.get("seed") and not cur.get("seed"):
            cur["seed"] = row.get("seed")
        vol = row.get("volume")
        kd = row.get("difficulty")
        if provider == "ahrefs":
            if vol is not None:
                cur["volume_ahrefs"] = vol
            if kd is not None:
                cur["difficulty_ahrefs"] = kd
            if row.get("traffic_potential") is not None:
                cur["traffic_potential"] = row.get("traffic_potential")
            if row.get("intent"):
                cur["intent"] = row.get("intent")
            if row.get("parent_topic"):
                cur["parent_topic"] = row.get("parent_topic")
        else:
            if vol is not None:
                cur["volume_dataforseo"] = vol
            if kd is not None:
                cur["difficulty_dataforseo"] = kd
            if row.get("trend"):
                cur["trend"] = row.get("trend")
        if row.get("cpc") is not None:
            cur["cpc"] = row.get("cpc")
        if row.get("gap_flag"):
            cur["gap_flag"] = True
        cd = row.get("competitor_domain") or row.get("domain")
        if cd and cd not in cur["competitor_domains"]:
            cur["competitor_domains"].append(cd)
        pos = row.get("position")
        if cd and pos is not None:
            entry = {"domain": cd, "position": int(pos)}
            if entry not in cur["competitor_positions"]:
                cur["competitor_positions"].append(entry)
        if row.get("client_position") is not None:
            cur["client_position"] = row.get("client_position")
        title = row.get("title")
        if title and isinstance(title, str) and title.strip():
            t_entry = {
                "title": title.strip()[:120],
                "domain": cd or row.get("domain"),
                "url": row.get("url"),
                "position": pos,
            }
            if not any(x.get("title") == t_entry["title"] for x in cur["serp_titles"]):
                cur["serp_titles"].append(t_entry)

    for r in ahrefs_rows:
        upsert(r, "ahrefs")
    for r in dfs_rows:
        upsert(r, "dataforseo")

    for cur in by_kw.values():
        va, vd = cur["volume_ahrefs"], cur["volume_dataforseo"]
        if va is not None and vd is not None:
            cur["volume"] = int(round((va + vd) / 2))
            if max(va, vd) > 0 and abs(va - vd) / max(va, vd) > 0.4:
                cur["volume_divergence"] = True
        elif va is not None:
            cur["volume"] = va
        elif vd is not None:
            cur["volume"] = vd
        else:
            cur["volume"] = None

        ka, kd = cur["difficulty_ahrefs"], cur["difficulty_dataforseo"]
        if ka is not None and kd is not None:
            cur["difficulty"] = int(round((ka + kd) / 2))
        elif ka is not None:
            cur["difficulty"] = ka
        elif kd is not None:
            cur["difficulty"] = kd

    return list(by_kw.values())


def classify_evergreen(keyword: str, intent: str | None = None) -> str:
    """Return evergreen | seasonal | newsy.

    Present-year terms are treated as seasonal/trend-friendly (not stale newsy).
    Past-year terms are newsy/stale.
    """
    kw = _norm(keyword)
    if is_stale_year_keyword(kw):
        return "newsy"
    if has_present_year(kw):
        return "seasonal"  # current-year = timely, keep in trend/best pools
    newsy = (
        "news",
        "breaking",
        "today",
        "this week",
        "launch",
        # future year only — present year handled above
        str(current_year() + 1),
    )
    seasonal = (
        "black friday",
        "cyber monday",
        "christmas",
        "holiday",
        "valentine",
        "halloween",
        "summer sale",
        "january",
        "february",
        "march",
        "april",
        "may ",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "q1",
        "q2",
        "q3",
        "q4",
    )
    if any(t in kw for t in newsy):
        return "newsy"
    if any(t in kw for t in seasonal):
        return "seasonal"
    if intent and str(intent).lower() in ("informational", "commercial", "transactional"):
        return "evergreen"
    return "evergreen"


def business_fit_score(keyword: str, seeds: list[str], products: list[str]) -> float:
    """0–1 overlap with business keywords / products / CDD services."""
    kw = _norm(keyword)
    tokens = set(kw.split())
    score = 0.0
    for seed in seeds:
        s = _norm(seed)
        if not s:
            continue
        if s in kw or kw in s:
            score = max(score, 0.9)
        elif set(s.split()) & tokens:
            score = max(score, 0.55)
    for product in products:
        p = _norm(str(product))
        if not p:
            continue
        if p in kw:
            score = max(score, 0.95)
        elif any(t in kw for t in p.split() if len(t) > 3):
            score = max(score, 0.75)
    return min(1.0, score)


def score_opportunity(
    row: dict[str, Any],
    *,
    seeds: list[str],
    products: list[str],
    client_authority: float | None = None,
) -> dict[str, Any]:
    volume = row.get("volume")
    difficulty = row.get("difficulty")
    trend = str(row.get("trend") or "stable")
    kw = str(row.get("keyword") or "")
    intent = detect_intent(kw, row.get("intent"))
    evergreen = classify_evergreen(kw, intent)
    fit = business_fit_score(kw, seeds, products)
    spec = specificity_score(kw, products)
    demand, win = winnable_demand_score(volume, difficulty)
    demand_known = volume is not None
    gap = competitor_gap_boost(row)
    broad = is_broad_head_term(kw, products)

    if client_authority is not None and difficulty is not None:
        auth = max(0.0, min(100.0, float(client_authority))) / 100.0
        if float(difficulty) > auth * 100 + 25:
            win *= 0.55
        else:
            win = min(1.0, win + auth * 0.12)

    trend_score = {"rising": 0.85, "stable": 0.55, "declining": 0.15}.get(trend, 0.5)
    evergreen_score = {"evergreen": 0.95, "seasonal": 0.55, "newsy": 0.15}.get(
        evergreen, 0.6
    )
    if has_present_year(kw):
        evergreen_score = max(evergreen_score, 0.8)
        trend_score = max(trend_score, 0.85)

    # When CDD/services exist, weight specificity + gap harder than raw volume
    has_services = bool(products)
    if has_services:
        composite = (
            0.18 * demand
            + 0.22 * win
            + 0.26 * gap
            + 0.14 * fit
            + 0.14 * spec
            + 0.04 * trend_score
            + 0.02 * evergreen_score
        )
    else:
        composite = (
            0.26 * demand
            + 0.24 * win
            + 0.22 * gap
            + 0.12 * fit
            + 0.10 * spec
            + 0.04 * trend_score
            + 0.02 * evergreen_score
        )
    if not demand_known:
        composite *= 0.72
    if is_stale_year_keyword(kw):
        composite *= 0.25
    elif has_present_year(kw):
        composite = min(1.0, composite * 1.12)
    if broad and has_services:
        composite *= 0.55  # keep head terms, don't let them top Best
    if is_noisy_keyword(kw):
        composite *= 0.2

    word_count = len(_norm(kw).split())
    bucket = "best"
    if (
        demand >= 0.2
        and win >= 0.35
        and fit >= 0.4
        and (gap >= 0.55 or spec >= 0.55 or has_present_year(kw))
        and evergreen != "newsy"
        and not is_stale_year_keyword(kw)
        and not (broad and has_services and gap < 0.7)
        and not is_noisy_keyword(kw)
    ):
        bucket = "best"
    elif evergreen == "evergreen" and trend in ("stable", "rising") and composite >= 0.35:
        bucket = "evergreen"
    if trend == "rising" and evergreen != "evergreen":
        bucket = "trend"
    if has_present_year(kw) and not is_stale_year_keyword(kw) and not broad:
        if composite >= 0.35:
            bucket = "best" if (gap >= 0.5 or spec >= 0.45) else "trend"
    if trend == "rising" and composite >= 0.5 and evergreen == "evergreen" and word_count >= 3:
        bucket = "best"
    if (
        composite < 0.28
        or trend == "declining"
        or is_stale_year_keyword(kw)
        or is_noisy_keyword(kw)
        or ((difficulty or 0) >= 85 and gap < 0.4 and not has_present_year(kw))
    ):
        bucket = "avoid"
    # Broad heads → evergreen/head_terms, not Best (unless strong competitor gap)
    if broad and bucket == "best" and gap < 0.75:
        bucket = "evergreen"
    if word_count == 1 and bucket == "best" and gap < 0.7:
        bucket = "evergreen"

    rationale_parts = []
    if volume is not None:
        rationale_parts.append(f"vol≈{volume}")
    else:
        rationale_parts.append("vol unknown (no invented metrics)")
    if difficulty is not None:
        rationale_parts.append(f"KD≈{difficulty}")
    if gap >= 0.55:
        comps = ", ".join(row.get("competitor_domains") or []) or "competitors"
        rationale_parts.append(f"gap vs {comps}")
    rationale_parts.append(f"intent={intent}")
    rationale_parts.append(f"spec={spec:.0%}")
    rationale_parts.append(f"fit={fit:.0%}")
    if broad:
        rationale_parts.append("broad-head")
    rationale_parts.append(f"trend={trend}")
    rationale_parts.append(f"type={evergreen}")
    if has_present_year(kw):
        rationale_parts.append(f"present-year={current_year()}")
    if is_stale_year_keyword(kw):
        rationale_parts.append("stale-year demoted")
    if row.get("volume_divergence"):
        rationale_parts.append("Ahrefs↔DataForSEO volume diverge")

    return {
        **row,
        "intent": intent,
        "funnel": row.get("funnel") or detect_funnel(kw, intent),
        "evergreen_class": evergreen,
        "business_fit": round(fit, 3),
        "specificity": round(spec, 3),
        "gap_score": round(gap, 3),
        "is_broad_head": broad,
        "opportunity_score": round(composite * 100, 1),
        "bucket": bucket,
        "rationale": "; ".join(rationale_parts),
    }


def _topic_sort_key(r: dict[str, Any]) -> tuple:
    """Prioritize competitor gaps + specific service keywords with good vol/KD."""
    kw = str(r.get("keyword") or "")
    return (
        0 if r.get("is_broad_head") else 1,
        1 if r.get("gap_flag") else 0,
        1 if has_present_year(kw) else 0,
        float(r.get("gap_score") or 0),
        float(r.get("specificity") or 0),
        float(r.get("business_fit") or 0),
        float(r.get("opportunity_score") or 0),
        float(r.get("volume") or 0),
    )


def _dedupe_scored(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        kw = _norm(str(r.get("keyword") or ""))
        if not kw:
            continue
        prev = best.get(kw)
        if not prev or (r.get("opportunity_score") or 0) > (prev.get("opportunity_score") or 0):
            best[kw] = r
    return list(best.values())


def rank_opportunities(
    rows: list[dict[str, Any]],
    *,
    seeds: list[str],
    products: list[str],
    client_authority: float | None = None,
    limit_per_bucket: int = 12,
    relevance_context: Any | None = None,
) -> dict[str, Any]:
    if relevance_context is not None:
        from app.services.keyword_relevance import filter_relevant_keywords

        rows, _excl, _audit = filter_relevant_keywords(
            [r for r in rows if isinstance(r, dict)],
            relevance_context,
            source_label="opportunity",
        )
    scored = [
        score_opportunity(r, seeds=seeds, products=products, client_authority=client_authority)
        for r in rows
        if not is_noisy_keyword(str(r.get("keyword") or ""))
    ]
    if products:
        scored = [
            r
            for r in scored
            if (r.get("business_fit") or 0) >= 0.35
            or str(r.get("match_class") or "") in ("exact", "phrase")
            or r.get("relevance_reason") in (
                "seed_exact",
                "seed_phrase",
                "seed_related",
                "cdd_phrase",
                "service_overlap",
                "page_overlap",
                "theme_overlap",
                "competitor_gap",
            )
        ]
    scored = _dedupe_scored(scored)
    fresh = [r for r in scored if not is_stale_year_keyword(str(r.get("keyword") or ""))]
    scored_sorted = sorted(scored, key=lambda r: r.get("opportunity_score") or 0, reverse=True)
    fresh_sorted = sorted(fresh, key=lambda r: r.get("opportunity_score") or 0, reverse=True)

    # Best = specific / gap first — never pad with broad heads when specifics exist
    specific_best = [
        r
        for r in fresh_sorted
        if r["bucket"] == "best" and not r.get("is_broad_head")
    ]
    specific_best.sort(key=_topic_sort_key, reverse=True)
    best = specific_best[:limit_per_bucket]

    if len(best) < 6:
        # Allow strong gap broad heads only to fill
        for r in fresh_sorted:
            if r in best:
                continue
            if r.get("bucket") == "best" and r.get("gap_flag") and r.get("gap_score", 0) >= 0.75:
                best.append(r)
            if len(best) >= min(8, limit_per_bucket):
                break

    evergreen = [
        r
        for r in fresh_sorted
        if r["bucket"] == "evergreen" or (r.get("is_broad_head") and r not in best)
    ][:limit_per_bucket]
    for r in fresh_sorted:
        if r["evergreen_class"] == "evergreen" and r not in best and r not in evergreen:
            evergreen.append(r)
        if len(evergreen) >= limit_per_bucket:
            break

    head_terms = [
        r
        for r in fresh_sorted
        if (r.get("is_broad_head") or len(_norm(str(r.get("keyword") or "")).split()) <= 2)
        and r.get("bucket") != "avoid"
    ][:limit_per_bucket]

    trend = [
        r
        for r in fresh_sorted
        if (r["bucket"] == "trend" or r.get("trend") == "rising" or has_present_year(str(r.get("keyword") or "")))
        and not is_stale_year_keyword(str(r.get("keyword") or ""))
        and not r.get("is_broad_head")
    ][:limit_per_bucket]
    avoid = [
        r
        for r in scored_sorted
        if r["bucket"] == "avoid" or is_stale_year_keyword(str(r.get("keyword") or ""))
    ][:limit_per_bucket]

    if not best and fresh_sorted:
        candidates = sorted(
            [r for r in fresh_sorted if not r.get("is_broad_head")],
            key=_topic_sort_key,
            reverse=True,
        ) or fresh_sorted
        best = candidates[: min(8, len(candidates))]
        for r in best:
            if r["bucket"] == "avoid":
                r["bucket"] = "best"

    return {
        "best_opportunities": best,
        "strong_evergreen": evergreen,
        "trend_plays": trend,
        "avoid": avoid,
        "head_terms": head_terms,
        "all_scored": scored_sorted,
    }


def cluster_keywords(scored: list[dict[str, Any]], *, max_clusters: int = 12) -> list[dict[str, Any]]:
    """Lightweight parent-topic grouping (legacy helper)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in scored:
        parent = _norm(str(row.get("parent_topic") or row.get("keyword") or ""))
        if not parent:
            continue
        if not row.get("parent_topic"):
            parts = parent.split()[:2]
            parent = " ".join(parts)
        groups.setdefault(parent, []).append(row)

    clusters: list[dict[str, Any]] = []
    for name, items in groups.items():
        items_sorted = sorted(
            items, key=lambda r: r.get("opportunity_score") or 0, reverse=True
        )
        primary = items_sorted[0]
        clusters.append(
            {
                "name": name,
                "primary_keyword": primary.get("keyword"),
                "intent": primary.get("intent") or "informational",
                "keywords": [r.get("keyword") for r in items_sorted[:12]],
                "opportunity_score": primary.get("opportunity_score"),
                "volume": primary.get("volume"),
            }
        )
    clusters.sort(key=lambda c: c.get("opportunity_score") or 0, reverse=True)
    return clusters[:max_clusters]


def build_topics(
    clusters: list[dict[str, Any]],
    best: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    for c in clusters[:8]:
        topics.append(
            {
                "role": "pillar",
                "pillar": c.get("name"),
                "primary_keyword": c.get("primary_keyword"),
                "supporting_keywords": (c.get("keywords") or [])[1:6],
                "intent": c.get("intent") or "informational",
                "opportunity_score": c.get("opportunity_score"),
                "competitor_domains": c.get("competitor_domains") or [],
            }
        )
    for row in best[:6]:
        if any(
            _norm(str(t.get("primary_keyword") or "")) == _norm(str(row.get("keyword") or ""))
            for t in topics
        ):
            continue
        topics.append(
            {
                "role": "supporting",
                "pillar": row.get("parent_topic") or row.get("keyword"),
                "primary_keyword": row.get("keyword"),
                "supporting_keywords": [],
                "intent": row.get("intent") or "informational",
                "opportunity_score": row.get("opportunity_score"),
                "competitor_domains": row.get("competitor_domains") or [],
            }
        )
    return topics


def select_topic_keywords(
    best: list[dict[str, Any]],
    evergreen: list[dict[str, Any]],
    *,
    products: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Ordered keyword rows for topic generation — gaps + specific services first."""
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in list(best) + list(evergreen):
        kw = _norm(str(row.get("keyword") or ""))
        if not kw or kw in seen or is_stale_year_keyword(kw):
            continue
        seen.add(kw)
        pool.append(row)
    pool.sort(key=_topic_sort_key, reverse=True)
    if products:
        for product in products[:6]:
            p = _norm(str(product))
            if not p:
                continue
            if any(p in _norm(str(r.get("keyword") or "")) for r in pool[:limit]):
                continue
            hit = next(
                (
                    r
                    for r in pool
                    if p in _norm(str(r.get("keyword") or ""))
                    or any(
                        t in _norm(str(r.get("keyword") or ""))
                        for t in p.split()
                        if len(t) > 3
                    )
                ),
                None,
            )
            if hit and hit in pool:
                pool.remove(hit)
                pool.insert(0, hit)
    return pool[:limit]


def _keyword_stem(keyword: str) -> str:
    """Collapse near-duplicates like 'best seo tool' / 'best seo tools'."""
    tokens = [
        t[:-1] if t.endswith("s") and len(t) > 3 else t
        for t in _norm(keyword).split()
        if t
    ]
    return " ".join(tokens)


def map_service_to_competitor_category(service: str) -> str | None:
    """Map a CDD service label to Phase 4 competitor category slug."""
    text = _norm(service)
    if not text:
        return None
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("seo", ("seo", "search engine", "organic search", "link building")),
        ("google_ads", ("google ads", "ppc", "paid search", "sem", "adwords")),
        ("meta", ("meta", "facebook ads", "instagram ads", "paid social", "social ads")),
        ("email_marketing", ("email", "newsletter", "drip campaign", "email marketing")),
        ("cro", ("cro", "conversion rate", "landing page", "a/b test", "ux audit")),
    ]
    for category, phrases in rules:
        if any(phrase in text for phrase in phrases):
            return category
    return None


def _scored_keyword_lookup(scored_keywords: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("keyword") or "").strip().lower(): row
        for row in scored_keywords
        if isinstance(row, dict) and row.get("keyword")
    }


def _rollup_keyword_competitors(
    keywords: list[dict[str, Any]],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from collections import Counter

    gap_keywords: list[dict[str, Any]] = []
    gap_count = 0
    comp_counts: Counter[str] = Counter()
    for kw_row in keywords:
        if not isinstance(kw_row, dict):
            continue
        kw = str(kw_row.get("keyword") or "").strip().lower()
        full = lookup.get(kw) or kw_row
        if full.get("gap_flag"):
            gap_count += 1
            if len(gap_keywords) < 8:
                gap_keywords.append(
                    {
                        "keyword": full.get("keyword"),
                        "volume": full.get("volume"),
                        "competitor_domains": list(full.get("competitor_domains") or [])[:5],
                    }
                )
        for domain in full.get("competitor_domains") or []:
            d = str(domain).lower().removeprefix("www.").split("/")[0]
            if d:
                comp_counts[d] += 1
    return {
        "gap_keyword_count": gap_count,
        "gap_keywords": gap_keywords,
        "competitor_leaders": [
            {"domain": domain, "keyword_hits": count}
            for domain, count in comp_counts.most_common(5)
        ],
    }


def _enrich_seed_bucket(
    seed: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> None:
    for kw_row in seed.get("keywords") or []:
        if not isinstance(kw_row, dict):
            continue
        kw = str(kw_row.get("keyword") or "").strip().lower()
        full = lookup.get(kw)
        if not full:
            continue
        if full.get("gap_flag") is not None:
            kw_row["gap_flag"] = bool(full.get("gap_flag"))
        if full.get("competitor_domains"):
            kw_row["competitor_domains"] = list(full.get("competitor_domains") or [])
    rollup = _rollup_keyword_competitors(seed.get("keywords") or [], lookup)
    seed.update(rollup)


def enrich_service_clusters_with_competitors(
    service_clusters: list[dict[str, Any]],
    scored_keywords: list[dict[str, Any]],
    *,
    competitive_landscape: dict[str, Any] | None = None,
    competitors: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach gap keywords and competitor leaders to subservices for Phase 5 UI."""
    lookup = _scored_keyword_lookup(scored_keywords)
    svc_comparison = (competitive_landscape or {}).get("service_level_comparison") or {}
    best_by_service = svc_comparison.get("best_by_service") or {}

    comp_name_by_domain: dict[str, str] = {}
    for comp in competitors or []:
        if not isinstance(comp, dict):
            continue
        raw = str(comp.get("domain") or comp.get("url") or "").strip().lower()
        raw = raw.removeprefix("https://").removeprefix("http://").removeprefix("www.")
        domain = raw.split("/")[0]
        if domain:
            comp_name_by_domain[domain] = str(comp.get("name") or domain)

    matrix: list[dict[str, Any]] = []
    for group in service_clusters:
        if not isinstance(group, dict):
            continue
        service = str(group.get("service") or "")
        category = map_service_to_competitor_category(service)
        category_leader = best_by_service.get(category) if category else None
        group["competitor_category"] = category
        group["category_leader"] = category_leader

        for seed in group.get("seeds") or []:
            if isinstance(seed, dict):
                _enrich_seed_bucket(seed, lookup)

        for sub in group.get("subservices") or []:
            if not isinstance(sub, dict):
                continue
            all_keywords: list[dict[str, Any]] = []
            for seed in sub.get("seeds") or []:
                if isinstance(seed, dict):
                    _enrich_seed_bucket(seed, lookup)
                    all_keywords.extend(seed.get("keywords") or [])
            rollup = _rollup_keyword_competitors(all_keywords, lookup)
            sub.update(rollup)
            sub["competitor_category"] = category
            sub["category_leader"] = category_leader
            leaders: list[dict[str, Any]] = []
            for leader in sub.get("competitor_leaders") or []:
                if not isinstance(leader, dict):
                    continue
                domain = str(leader.get("domain") or "")
                leaders.append(
                    {
                        **leader,
                        "name": comp_name_by_domain.get(domain, domain),
                    }
                )
            sub["competitor_leaders"] = leaders
            matrix.append(
                {
                    "parent_service": service,
                    "subservice": sub.get("subservice"),
                    "page_path": sub.get("page_path"),
                    "keyword_count": sub.get("keyword_count"),
                    "gap_keyword_count": sub.get("gap_keyword_count"),
                    "gap_keywords": sub.get("gap_keywords") or [],
                    "competitor_leaders": leaders,
                    "competitor_category": category,
                    "category_leader": category_leader,
                }
            )

    matrix.sort(
        key=lambda row: (
            -int(row.get("gap_keyword_count") or 0),
            str(row.get("parent_service") or "").lower(),
            str(row.get("subservice") or "").lower(),
        )
    )
    return service_clusters, matrix


def select_topics_from_service_clusters(
    service_clusters: list[dict[str, Any]],
    *,
    products: list[str] | None = None,
    limit: int = 10,
    max_per_service: int = 1,
) -> list[dict[str, Any]]:
    """Build Topic Plan primaries from the same service groups shown in Multi-mode UI.

    One (or max_per_service) keyword per service, preferring exact/phrase and
    skipping ultra-broad heads. Intent + match_class are preserved on each row.
    """
    products = products or []
    class_rank = {"exact": 0, "phrase": 1, "related": 2, "broad": 3}
    intent_rank = {"transactional": 0, "commercial": 1, "informational": 2}

    per_service: list[tuple[str, list[dict[str, Any]]]] = []
    for group in service_clusters:
        if not isinstance(group, dict):
            continue
        service = str(group.get("service") or "").strip()
        if not service or _norm(service) in {"_other", "other website topics"}:
            # Still allow "Other" only after real services are exhausted
            continue

        def _collect_candidates(
            seeds: list[dict[str, Any]],
            *,
            subservice: str | None = None,
        ) -> list[dict[str, Any]]:
            candidates: list[dict[str, Any]] = []
            seen: set[str] = set()
            for seed_entry in seeds:
                if not isinstance(seed_entry, dict):
                    continue
                seed = seed_entry.get("seed")
                target = seed_entry.get("target") or service
                for row in seed_entry.get("keywords") or []:
                    if not isinstance(row, dict):
                        continue
                    kw = str(row.get("keyword") or "").strip()
                    nkw = _norm(kw)
                    if not nkw or nkw in seen or is_stale_year_keyword(nkw):
                        continue
                    if is_noisy_keyword(kw):
                        continue
                    match_class = str(row.get("match_class") or "broad").lower()
                    words = nkw.split()
                    broad = is_broad_head_term(kw, products)
                    if len(words) <= 2 and (broad or match_class == "broad"):
                        continue
                    if match_class == "broad" and len(words) < 4:
                        continue
                    intent = detect_intent(kw, row.get("intent"))
                    seen.add(nkw)
                    fit = service_token_overlap(kw, service)
                    if subservice:
                        fit = max(fit, service_token_overlap(kw, subservice))
                    # Require at least weak service fit when the service name is specific
                    service_tokens = [
                        t
                        for t in _norm(service).split()
                        if len(t) > 2
                        and t
                        not in {
                            "the",
                            "and",
                            "for",
                            "services",
                            "service",
                            "agency",
                            "company",
                        }
                    ]
                    if len(service_tokens) >= 2 and fit <= 0 and match_class in ("related", "broad"):
                        continue
                    item = {
                        **row,
                        "keyword": kw,
                        "intent": intent,
                        "match_class": match_class,
                        "is_broad_head": broad,
                        "seed": seed,
                        "target": target,
                        "service": service,
                        "service_fit": fit,
                        "gap_flag": bool(row.get("gap_flag")),
                        "opportunity_score": float(row.get("opportunity_score") or 0),
                    }
                    if subservice:
                        item["subservice"] = subservice
                    candidates.append(item)
            candidates.sort(
                key=lambda r: (
                    class_rank.get(str(r.get("match_class") or "broad"), 3),
                    0 if not r.get("is_broad_head") else 1,
                    # Prefer rows that actually share tokens with the service name
                    -int(r.get("service_fit") or 0),
                    intent_rank.get(str(r.get("intent") or "informational"), 3),
                    0 if r.get("gap_flag") else 1,
                    -(float(r.get("opportunity_score") or 0)),
                    -(r.get("volume") or 0) if (r.get("volume") or 0) < 50000 else 0,
                    abs((r.get("volume") or 0) - 3000),
                )
            )
            return candidates

        candidates = _collect_candidates(group.get("seeds") or [])
        if candidates:
            per_service.append((service, candidates))
        for sub in group.get("subservices") or []:
            if not isinstance(sub, dict):
                continue
            sub_name = str(sub.get("subservice") or "").strip()
            if not sub_name:
                continue
            sub_candidates = _collect_candidates(sub.get("seeds") or [], subservice=sub_name)
            if sub_candidates:
                per_service.append((f"{service} · {sub_name}", sub_candidates))

    # Sort services by total opportunity (prefer those with better exact/phrase rows)
    per_service.sort(
        key=lambda item: (
            class_rank.get(str(item[1][0].get("match_class") or "broad"), 3),
            -(item[1][0].get("volume") or 0) if (item[1][0].get("volume") or 0) < 50000 else 0,
        )
    )

    selected: list[dict[str, Any]] = []
    used_stems: set[str] = set()
    per_counts: dict[str, int] = {}

    def _take(service: str, rows: list[dict[str, Any]]) -> bool:
        if per_counts.get(service, 0) >= max_per_service:
            return False
        for row in rows:
            stem = _keyword_stem(str(row.get("keyword") or ""))
            if stem in used_stems:
                continue
            selected.append(row)
            used_stems.add(stem)
            per_counts[service] = per_counts.get(service, 0) + 1
            return True
        return False

    # Pass 1: exactly one per service
    for service, rows in per_service:
        if len(selected) >= limit:
            break
        _take(service, rows)

    # Pass 2: fill remaining slots with a second pick from richest services
    if max_per_service > 1:
        for service, rows in per_service:
            if len(selected) >= limit:
                break
            _take(service, rows)

    return selected[:limit]


def select_seeded_topic_keywords(
    rows: list[dict[str, Any]],
    *,
    products: list[str] | None = None,
    limit: int = 10,
    max_per_seed: int = 1,
) -> list[dict[str, Any]]:
    """Compatibility wrapper: group flat seeded rows by target/seed, then select."""
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        service = str(row.get("target") or row.get("seed") or "Other").strip()
        key = _norm(service)
        if key not in groups:
            groups[key] = {
                "service": service,
                "seeds": [
                    {
                        "seed": row.get("seed") or service,
                        "target": row.get("target") or service,
                        "keywords": [],
                    }
                ],
            }
        groups[key]["seeds"][0]["keywords"].append(row)
    return select_topics_from_service_clusters(
        list(groups.values()),
        products=products,
        limit=limit,
        max_per_service=max_per_seed,
    )
