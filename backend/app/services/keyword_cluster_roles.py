"""The single place that decides a cluster's primary keyword, member roles and labels.

Both clustering paths (deterministic rules and the model-driven skill) group
keywords their own way, then hand every cluster to ``finalize_cluster``. It never
moves a keyword between clusters, never adds or drops one, and never invents a
metric: it ranks and labels the members a cluster already has.

- Labels: each member's intent and funnel come from ``label_keyword``, which
  records which source decided (provider, model, or heuristic), how confident the
  assignment is, and any disagreement between sources.
- Primary: a composite score over business fit, intent match with the page,
  measured demand, reachable difficulty, specificity and cleaning confidence.
  Unmeasured demand is not zero demand, and an exact commercial match for an
  offered service can win with no volume reading. The choice and its reason are
  recorded on the cluster as ``primary_selection``.
- Roles: Secondary means a close variant of the primary (same intent and funnel
  stage, would share its search results); Supporting means a narrower sub-angle
  or a related question. A cluster can have any number of secondaries, or none.
- Cluster intent and funnel come from the member distribution, weighted toward
  the primary and secondaries, and the distribution itself is recorded.

The cluster's ``primary_keyword`` and the one member marked ``Primary`` are set
together here, so they cannot disagree.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.keyword_opportunity import (
    VALID_FUNNELS,
    VALID_INTENTS,
    funnel_with_evidence,
    intent_with_evidence,
    is_broad_head_term,
    normalize_provider_intent,
    specificity_score,
)

ROLE_METHOD = "composite_v1"

# Composite weights for the primary decision (sum to 1.0).
_WEIGHTS = {
    "business_fit": 0.25,
    "intent_match": 0.20,
    "demand": 0.20,
    "relevance_confidence": 0.15,
    "reachability": 0.10,
    "specificity": 0.10,
}
# An exact commercial match for an offered service is the page's natural target.
_EXACT_SERVICE_BONUS = 0.15

# How strongly the cleaning stage's verdict says the keyword is relevant.
_RELEVANCE_STRENGTH = {
    "service_match": 0.9,
    "seed_exact": 0.85,
    "keep": 0.7,
    "seed_phrase": 0.7,
    "service_overlap": 0.7,
    "cdd_phrase": 0.6,
    "page_overlap": 0.6,
    "service_review": 0.55,
    "competitor_gap": 0.55,
    "seed_related": 0.5,
    "theme_overlap": 0.45,
    "relevance_unverified": 0.25,
    "no_business_evidence": 0.2,
    "hygiene_only": 0.2,
}
_RELEVANCE_UNSCORED = 0.5

_ROLE_WEIGHT = {"Primary": 3.0, "Secondary": 2.0, "Supporting": 1.0}
_CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.75, "low": 0.5}

_STOP = {"the", "a", "an", "and", "or", "of", "in", "on", "with", "to", "my", "your", "i"}
# Words that name the provider rather than the service: "seo agency" and
# "seo services" are the same search for role purposes.
_PROVIDER_WORDS = {
    "agency", "agencies", "service", "services", "company", "companies", "consultant",
    "consultants", "consultancy", "specialist", "specialists", "expert", "experts", "firm",
    "firms", "provider", "providers",
}
_GEO_WORDS = {
    "melbourne", "sydney", "brisbane", "perth", "adelaide", "canberra", "hobart", "darwin",
    "geelong", "australia", "auckland", "wellington", "near", "me", "nearby", "local",
}
# Adding or dropping one of these keeps the search the same page's target.
_LIGHT_MODIFIERS = _GEO_WORDS | {"best", "top", "online", "professional", "expert", "provider"}
_QUESTION_START = re.compile(
    r"^(what|how|why|when|where|which|who|can|does|do|is|are|should|will|would)\b"
)


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _slug(text: str) -> str:
    # Same rule as keyword_clustering._slug (imported there; no cycle here).
    return re.sub(r"[^a-z0-9]+", "-", _norm(text)).strip("-")[:70] or "cluster"


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _loose(text: str) -> str:
    """Spacing and spelling variants of the same search ("4ps" / "4 ps")."""
    text = _norm(text)
    for src, dst in (
        ("optimisation", "optimization"),
        ("optimise", "optimize"),
        ("organisation", "organization"),
        ("organise", "organize"),
        ("behaviour", "behavior"),
        ("colour", "color"),
        ("favour", "favor"),
        ("centre", "center"),
    ):
        text = text.replace(src, dst)
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [_singular(t) for t in re.findall(r"[a-z0-9]+", _loose(text)) if t not in _STOP]


def _core(text: str) -> set[str]:
    """Meaning-bearing tokens, with provider words folded together."""
    return {("provider" if t in _PROVIDER_WORDS or _singular(t) in _PROVIDER_WORDS else t) for t in _tokens(text)}


def _service_core(text: str) -> frozenset[str]:
    return frozenset(t for t in _tokens(text) if t not in _PROVIDER_WORDS and _singular(t) not in _PROVIDER_WORDS)


def _is_question(keyword: str) -> bool:
    return bool(_QUESTION_START.match(_norm(keyword)))


def client_brand_tokens(brand_name: str | None = None, domain: str | None = None) -> set[str]:
    """Distinctive tokens of the client's own brand (shared with clustering)."""
    generic = {
        "seo", "marketing", "digital", "agency", "services", "service", "solutions", "group",
        "company", "inc", "llc", "ltd", "the",
    }
    tokens: set[str] = set()
    if brand_name:
        tokens |= {t for t in re.findall(r"[a-z0-9]+", _norm(brand_name)) if t not in generic and len(t) > 2}
    if domain:
        root = str(domain).split(".")[0].lower()
        if root not in generic and len(root) > 2:
            tokens.add(root)
    return tokens


@dataclass
class RoleContext:
    """What the decider knows about the business beyond the keyword rows."""

    brand_tokens: set[str] = field(default_factory=set)
    # Offered services and their other names, as written by the operator.
    services: list[str] = field(default_factory=list)
    # The site's realistic ranking ceiling on the 0-100 difficulty scale, when known.
    site_strength: float | None = None
    # Cleaned keyword rows by normalised keyword: the source of provenance fields.
    rows_by_kw: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        rows: list[dict[str, Any]] | None = None,
        *,
        brand_name: str | None = None,
        domain: str | None = None,
        relevance_context: Any | None = None,
        site_strength: float | None = None,
    ) -> RoleContext:
        services: list[str] = []
        brand = client_brand_tokens(brand_name, domain)
        if relevance_context is not None:
            services += [str(s) for s in (getattr(relevance_context, "services", None) or []) if s]
            services += [str(s) for s in (getattr(relevance_context, "service_synonyms", None) or []) if s]
            brand |= {str(t).lower() for t in (getattr(relevance_context, "client_brand_tokens", None) or []) if t}
        by_kw: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            if isinstance(row, dict) and row.get("keyword"):
                by_kw.setdefault(_norm(row["keyword"]), row)
                if row.get("service"):
                    services.append(str(row["service"]))
        seen: set[str] = set()
        unique = [s for s in services if not (_norm(s) in seen or seen.add(_norm(s)))]
        return cls(brand_tokens=brand, services=unique, site_strength=site_strength, rows_by_kw=by_kw)

    def exact_service(self, keyword: str) -> str | None:
        """The offered service this keyword names exactly (provider words and
        location allowed around it: "seo agency melbourne" names "SEO")."""
        core = frozenset(t for t in _service_core(keyword) if t not in _GEO_WORDS)
        if not core:
            return None
        for service in self.services:
            if core == _service_core(service):
                return service
        return None


# --------------------------------------------------------------------------- labels


def _valid_funnel(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in VALID_FUNNELS else None


def label_keyword(
    row: dict[str, Any],
    *,
    brand_tokens: set[str] | None = None,
    llm_intent: Any = None,
    llm_funnel: Any = None,
) -> dict[str, Any]:
    """Intent and funnel for one keyword, with source, confidence and disagreements.

    Intent sources: the provider's label (validated against the four values), a
    model's label, and the keyword-text heuristic. The client's brand makes a
    query navigational; otherwise provider beats model beats heuristic. Funnel
    sources: text evidence, a model's label, then the decided intent; "default"
    means TOFU with no evidence at all.
    """
    kw = str(row.get("keyword") or "")
    provider = normalize_provider_intent(row.get("provider_intent"))
    if provider is None and row.get("intent_basis") == "measured" and "provider_intent" not in row:
        # Rows built before provider_intent existed: a measured label sits in "intent".
        provider = normalize_provider_intent(row.get("intent"))
    llm = normalize_provider_intent(llm_intent) or normalize_provider_intent(row.get("llm_intent"))
    if llm is None and row.get("intent_basis") == "llm":
        llm = normalize_provider_intent(row.get("intent"))
    heuristic, _source, signal = intent_with_evidence(kw, None, brand_tokens=brand_tokens)
    heuristic_has_evidence = signal != "default"

    if signal == "client_brand":
        intent, intent_source = "navigational", "heuristic"
    elif provider:
        intent, intent_source = provider, "provider"
    elif llm:
        intent, intent_source = llm, "llm"
    else:
        intent, intent_source = heuristic, "heuristic"

    opinions = {
        "provider": provider,
        "llm": llm,
        # A heuristic that found no evidence has no opinion to disagree with.
        "heuristic": heuristic if heuristic_has_evidence else None,
    }
    intent_disagreements = [
        {"source": s, "value": v} for s, v in opinions.items() if v and v != intent and s != intent_source
    ]
    agreeing = [s for s, v in opinions.items() if v == intent and s != intent_source]
    if intent_source == "heuristic" and signal == "client_brand":
        intent_confidence = "medium" if intent_disagreements else "high"
    elif intent_source == "provider":
        intent_confidence = "high" if agreeing else "medium"
    elif intent_source == "llm":
        intent_confidence = "high" if agreeing else ("low" if intent_disagreements else "medium")
    else:
        intent_confidence = "medium" if heuristic_has_evidence else "low"

    text_stage, basis = funnel_with_evidence(kw, intent, brand_tokens=brand_tokens)
    model_stage = _valid_funnel(llm_funnel) or _valid_funnel(row.get("llm_funnel"))
    text_has_evidence = basis not in ("from_intent", "default")
    if text_has_evidence:
        funnel, funnel_source = text_stage, "heuristic"
    elif model_stage:
        funnel, funnel_source = model_stage, "llm"
    elif basis == "from_intent":
        funnel, funnel_source = text_stage, "derived_from_intent"
    else:
        funnel, funnel_source = text_stage, "default"
    funnel_opinions = {"heuristic": text_stage if text_has_evidence else None, "llm": model_stage}
    funnel_disagreements = [
        {"source": s, "value": v} for s, v in funnel_opinions.items() if v and v != funnel and s != funnel_source
    ]
    if funnel_source == "heuristic":
        funnel_confidence = "high" if model_stage == funnel else ("low" if funnel_disagreements else "medium")
    elif funnel_source == "llm":
        funnel_confidence = "medium"
    elif funnel_source == "derived_from_intent":
        funnel_confidence = "medium" if intent_confidence == "high" else "low"
    else:
        funnel_confidence = "low"

    return {
        "intent": intent,
        "intent_source": intent_source,
        "intent_confidence": intent_confidence,
        "intent_evidence": "provider_label" if intent_source == "provider" else ("model_label" if intent_source == "llm" else signal),
        "funnel": funnel,
        "funnel_source": funnel_source,
        "funnel_confidence": funnel_confidence,
        "funnel_evidence": basis if funnel_source != "llm" else "model_label",
        "label_disagreement": bool(intent_disagreements or funnel_disagreements),
        "label_provenance": {
            "intent": {"sources": opinions, "disagreements": intent_disagreements},
            "funnel": {"sources": funnel_opinions, "disagreements": funnel_disagreements},
        },
    }


# --------------------------------------------------------------------------- primary


def _volume(row: dict[str, Any]) -> int | None:
    v = row.get("volume")
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _volume_basis(row: dict[str, Any]) -> str:
    if _volume(row) is None:
        return "unmeasured"
    if row.get("volume_estimated") or row.get("volume_status") == "estimated":
        return "estimated"
    return "measured"


def _relevance_strength(row: dict[str, Any]) -> float:
    reason = str(row.get("relevance_reason") or "").strip().lower()
    strength = _RELEVANCE_STRENGTH.get(reason, _RELEVANCE_UNSCORED)
    if row.get("relevance_unverified"):
        strength = min(strength, 0.25)
    return strength


def _business_fit(row: dict[str, Any], service: str | None) -> float:
    if service:
        return 1.0
    if row.get("commercial_target_eligible") is False or row.get("reroute_reason"):
        return 0.25
    named = str(row.get("service") or "")
    if named and _service_core(named) and _service_core(named) <= _service_core(str(row.get("keyword") or "")):
        return 0.8
    return 0.5


def _intent_match(intent: str, page_intent: str) -> float:
    if intent == page_intent:
        return 1.0
    if {intent, page_intent} == {"commercial", "transactional"}:
        return 0.6
    return 0.15


def _demand(row: dict[str, Any], max_volume: int) -> float:
    volume = _volume(row)
    if volume is None:
        return 0.25  # unknown demand, not zero demand
    ratio = math.log1p(volume) / math.log1p(max_volume) if max_volume > 0 else 0.0
    score = 0.3 + 0.7 * min(1.0, ratio)
    return score * 0.8 if _volume_basis(row) == "estimated" else score


def _reachability(row: dict[str, Any], site_strength: float | None) -> float:
    kd = row.get("difficulty")
    if not isinstance(kd, (int, float)):
        return 0.6
    kd = float(kd)
    if site_strength is not None:
        gap = kd - (float(site_strength) + 10)
        return 1.0 if gap <= 0 else max(0.15, 1.0 - gap / 50)
    if kd <= 30:
        return 1.0
    if kd <= 50:
        return 0.75
    if kd <= 70:
        return 0.45
    return 0.2


def _specificity(keyword: str, service: str | None) -> float:
    if is_broad_head_term(keyword) and not service:
        return 0.2
    return max(0.2, min(1.0, float(specificity_score(keyword))))


def _dominant(weights: dict[str, float], prefer: str | None = None) -> str | None:
    if not weights:
        return None
    top = max(weights.values())
    leaders = [k for k, v in weights.items() if v == top]
    return prefer if prefer in leaders else min(leaders)


def _score_members(members: list[dict[str, Any]], ctx: RoleContext, page_intent: str) -> list[dict[str, Any]]:
    max_volume = max((_volume(m) or 0) for m in members) if members else 0
    scored: list[dict[str, Any]] = []
    for m in members:
        kw = str(m.get("keyword") or "")
        src = ctx.rows_by_kw.get(_norm(kw)) or {}
        facts = {**src, **{k: v for k, v in m.items() if v is not None}}
        service = ctx.exact_service(kw) if m["intent"] in ("commercial", "transactional") else None
        signals = {
            "business_fit": _business_fit(facts, service),
            "intent_match": _intent_match(m["intent"], page_intent),
            "demand": _demand(facts, max_volume),
            "relevance_confidence": _relevance_strength(facts),
            "reachability": _reachability(facts, ctx.site_strength),
            "specificity": _specificity(kw, service),
        }
        score = sum(_WEIGHTS[k] * v for k, v in signals.items()) + (_EXACT_SERVICE_BONUS if service else 0.0)
        scored.append({"member": m, "facts": facts, "service": service, "signals": signals, "score": score})
    scored.sort(key=lambda s: (-s["score"], -(_volume(s["facts"]) or -1), len(str(s["member"].get("keyword") or "")), _norm(s["member"].get("keyword"))))
    return scored


def _bigger_rival(best: dict[str, Any], scored: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The highest-volume member that lost to a primary with less (or no) volume."""
    mine = _volume(best["facts"])
    bigger = [s for s in scored if s is not best and _volume(s["facts"]) is not None and (mine is None or _volume(s["facts"]) > mine)]
    return max(bigger, key=lambda s: _volume(s["facts"]) or 0) if bigger else None


def _primary_reason(best: dict[str, Any], page_intent: str, rival: dict[str, Any] | None, ctx: RoleContext) -> str:
    """Plain-language why. ``rival`` is the highest-volume member that lost, if any."""
    m, facts, signals = best["member"], best["facts"], best["signals"]
    parts: list[str] = []
    if best["service"]:
        parts.append(f"Exact match for the \"{best['service']}\" service the business offers")
    elif signals["business_fit"] >= 0.8:
        parts.append("Names a service the business offers")
    elif signals["business_fit"] <= 0.25:
        parts.append("Only weak evidence the business sells this")
    if signals["intent_match"] == 1.0:
        parts.append(f"its {m['intent']} intent matches this {page_intent} page")
    else:
        parts.append(f"its {m['intent']} intent differs from the page's {page_intent} intent")
    basis = _volume_basis(facts)
    volume = _volume(facts)
    if basis == "unmeasured":
        parts.append("no volume reading from the provider (unmeasured, not zero)")
    else:
        parts.append(f"{basis} volume {volume:,}/mo")
    kd = facts.get("difficulty")
    if isinstance(kd, (int, float)):
        reach = "within reach" if signals["reachability"] >= 0.75 else "hard to rank for"
        scale = "for this site" if ctx.site_strength is not None else "on absolute difficulty"
        parts.append(f"difficulty {int(kd)} ({reach} {scale})")
    else:
        parts.append("difficulty not measured")
    if signals["specificity"] <= 0.2:
        parts.append("a broad head term")
    reason = "; ".join(parts) + "."
    reason = reason[0].upper() + reason[1:]
    if rival is not None:
        rv = _volume(rival["facts"])
        # Where the bigger keyword lost the most ground, with the actual values.
        gaps = {k: _WEIGHTS[k] * (best["signals"][k] - rival["signals"][k]) for k in _WEIGHTS}
        rf = rival["facts"]
        why = {
            "business_fit": "there is weaker evidence the business sells it",
            "intent_match": f"its {rival['member']['intent']} intent does not fit this {page_intent} page",
            "relevance_confidence": (
                f"cleaning was less sure it is relevant ({rf.get('relevance_reason') or 'unscored'}"
                f" vs {facts.get('relevance_reason') or 'unscored'})"
            ),
            "reachability": (
                f"it is harder to rank for (difficulty {rf.get('difficulty')} vs {facts.get('difficulty')})"
                if isinstance(rf.get("difficulty"), (int, float))
                else "its difficulty was not measured"
            ),
            "specificity": "it is a broader head term",
            "demand": "it has lower demand",
        }[max(gaps, key=lambda k: gaps[k])]
        reason += f" Chosen over \"{rival['member'].get('keyword')}\" ({rv:,}/mo) because {why}."
    return reason


# --------------------------------------------------------------------------- roles


def _role_for(member: dict[str, Any], primary: dict[str, Any], serp_group: set[str]) -> tuple[str, str]:
    kw, pkw = str(member.get("keyword") or ""), str(primary.get("keyword") or "")
    if member["intent"] != primary["intent"]:
        return "Supporting", f"{member['intent']} intent; the primary is {primary['intent']}"
    if member["funnel"] != primary["funnel"]:
        return "Supporting", f"{member['funnel']} stage; the primary is {primary['funnel']}"
    if _norm(kw) in serp_group and _norm(pkw) in serp_group:
        return "Secondary", "same intent and stage, and shares the primary's search results"
    if _is_question(kw) != _is_question(pkw):
        return "Supporting", "answers a related question rather than the primary's query"
    mine, theirs = _core(kw), _core(pkw)
    if " for " in f" {_norm(kw)} " and " for " not in f" {_norm(pkw)} ":
        return "Supporting", "narrower angle on the primary (\"for ...\")"
    if mine == theirs:
        return "Secondary", "same intent and stage; the same search worded differently"
    extra = mine ^ theirs
    if (mine <= theirs or theirs <= mine) and extra <= _LIGHT_MODIFIERS:
        words = sorted(set(_tokens(kw)) ^ set(_tokens(pkw)))
        return "Secondary", f"same intent and stage; differs from the primary only by \"{' '.join(words)}\""
    shared = len(mine & theirs)
    return "Supporting", f"related sub-topic ({shared} of {len(mine | theirs)} terms shared with the primary)"


# --------------------------------------------------------------------------- cluster


def _distribution(members: list[dict[str, Any]], field_name: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    weighted: dict[str, float] = {}
    for m in members:
        value = m.get(field_name)
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
        w = _ROLE_WEIGHT.get(m.get("role"), 1.0) * _CONFIDENCE_WEIGHT.get(m.get(f"{field_name}_confidence"), 0.75)
        weighted[value] = weighted.get(value, 0.0) + w
    total = sum(weighted.values()) or 1.0
    return {
        "counts": dict(sorted(counts.items())),
        "weighted_share": {k: round(v / total * 100, 1) for k, v in sorted(weighted.items())},
        "_weights": weighted,
    }


def finalize_cluster(cluster: dict[str, Any], ctx: RoleContext) -> dict[str, Any]:
    """Label every member, pick the primary, assign roles, derive cluster labels.

    Idempotent: the inputs are the source rows and the model labels kept on each
    member (``llm_intent``/``llm_funnel``), never the labels this function wrote.
    """
    members = [dict(m) for m in (cluster.get("keywords") or []) if isinstance(m, dict) and m.get("keyword")]
    if not members:
        return dict(cluster)
    for m in members:
        src = ctx.rows_by_kw.get(_norm(m["keyword"]))
        if src is not None:
            # Provenance lives on the cleaned row; copy it so the member carries it.
            for key in ("provider_intent", "intent_basis", "llm_intent"):
                if key in src and m.get(key) is None:
                    m[key] = src[key]
            base = {**src, "llm_intent": m.get("llm_intent") or src.get("llm_intent")}
        else:
            # No cleaned row (a model-only member): only its recorded basis counts,
            # never a label this function wrote on an earlier pass.
            base = {
                "keyword": m["keyword"],
                "intent": m.get("intent") if m.get("intent_basis") in ("measured", "llm") else None,
                "intent_basis": m.get("intent_basis"),
                "provider_intent": m.get("provider_intent"),
                "llm_intent": m.get("llm_intent"),
            }
        m.update(label_keyword(base, brand_tokens=ctx.brand_tokens, llm_funnel=m.get("llm_funnel")))

    # The page this cluster becomes: the members' prevailing intent.
    page_weights: dict[str, float] = {}
    for m in members:
        page_weights[m["intent"]] = page_weights.get(m["intent"], 0.0) + _CONFIDENCE_WEIGHT[m["intent_confidence"]]
    page_intent = _dominant(page_weights) or "informational"

    scored = _score_members(members, ctx, page_intent)
    best = scored[0]
    runner = scored[1] if len(scored) > 1 else None
    primary = best["member"]
    serp_group = {_norm(k) for k in (cluster.get("serp_group") or [])}
    for s in scored:
        m = s["member"]
        m["role_score"] = round(s["score"] * 100, 1)
        if m is primary:
            m["role"], m["role_reason"] = "Primary", "highest composite score in the cluster"
        else:
            m["role"], m["role_reason"] = _role_for(m, primary, serp_group)
    ordered = [s["member"] for s in scored if s["member"] is primary]
    ordered += [s["member"] for s in scored if s["member"]["role"] == "Secondary"]
    ordered += [s["member"] for s in scored if s["member"]["role"] == "Supporting"]

    intents = _distribution(ordered, "intent")
    funnels = _distribution(ordered, "funnel")
    out = dict(cluster)
    old_primary = str(cluster.get("primary_keyword") or "")
    out["keywords"] = ordered
    out["primary_keyword"] = primary["keyword"]
    out["intent"] = _dominant(intents.pop("_weights"), prefer=primary["intent"]) or primary["intent"]
    out["funnel"] = _dominant(funnels.pop("_weights"), prefer=primary["funnel"]) or primary["funnel"]
    out["intent_distribution"] = intents
    out["funnel_distribution"] = funnels
    out["role_counts"] = {role: sum(1 for m in ordered if m["role"] == role) for role in ("Primary", "Secondary", "Supporting")}
    out["label_confidence"] = {
        "intent": {band: sum(1 for m in ordered if m["intent_confidence"] == band) for band in ("high", "medium", "low")},
        "funnel": {band: sum(1 for m in ordered if m["funnel_confidence"] == band) for band in ("high", "medium", "low")},
        "disagreements": sum(1 for m in ordered if m.get("label_disagreement")),
    }
    out["primary_selection"] = {
        "keyword": primary["keyword"],
        "method": ROLE_METHOD,
        "score": round(best["score"] * 100, 1),
        "reason": _primary_reason(best, page_intent, _bigger_rival(best, scored), ctx),
        "signals": {k: round(v, 3) for k, v in best["signals"].items()},
        "exact_service_match": best["service"],
        "volume_basis": _volume_basis(best["facts"]),
        "page_intent": page_intent,
        "runner_up": (
            {"keyword": runner["member"]["keyword"], "score": round(runner["score"] * 100, 1)} if runner else None
        ),
        "site_strength_known": ctx.site_strength is not None,
    }
    # A URL derived from the old primary follows the new one; a chosen URL stays.
    if (
        old_primary
        and _norm(old_primary) != _norm(primary["keyword"])
        and out.get("recommended_url") == f"/blog/{_slug(old_primary)}"
    ):
        out["recommended_url"] = f"/blog/{_slug(primary['keyword'])}"
    return out


# Both intents must carry at least this weighted share for a cluster to count
# as genuinely mixed (a lone long-tail at another stage is normal).
MIXED_INTENT_MIN_SHARE = 20.0


def conflicting_intents(cluster: dict[str, Any]) -> list[str]:
    """["informational", "transactional"] when the cluster genuinely spans both, else [].

    Secondaries now share the primary's intent by definition, so the conflict is
    read from the member intent distribution (weighted toward the primary and
    secondaries). Clusters without a distribution keep the original rule over
    Primary/Secondary rows.
    """
    share = (cluster.get("intent_distribution") or {}).get("weighted_share")
    if isinstance(share, dict) and share:
        pair = ("informational", "transactional")
        if all(float(share.get(i) or 0) >= MIXED_INTENT_MIN_SHARE for i in pair):
            return list(pair)
        return []
    intents = {
        str(k.get("intent") or "").strip().lower()
        for k in (cluster.get("keywords") or [])
        if isinstance(k, dict) and str(k.get("role") or "Secondary").lower() in ("primary", "secondary")
    }
    return ["informational", "transactional"] if {"informational", "transactional"} <= intents else []


def finalize_clusters(clusters: list[dict[str, Any]], ctx: RoleContext) -> list[dict[str, Any]]:
    return [finalize_cluster(c, ctx) if isinstance(c, dict) else c for c in clusters]


def sync_roadmap(report: dict[str, Any]) -> dict[str, Any]:
    """Point each roadmap row at its cluster's final primary keyword."""
    by_name = {str(c.get("name")): c for c in report.get("clusters") or [] if isinstance(c, dict)}
    for row in report.get("content_roadmap") or []:
        cluster = by_name.get(str(row.get("cluster")))
        if cluster is not None:
            row["target_keyword"] = cluster.get("primary_keyword")
            if "intent" in row:
                row["intent"] = cluster.get("intent")
            if "funnel" in row:
                row["funnel"] = cluster.get("funnel")
    return report


__all__ = [
    "MIXED_INTENT_MIN_SHARE",
    "ROLE_METHOD",
    "VALID_INTENTS",
    "RoleContext",
    "client_brand_tokens",
    "conflicting_intents",
    "finalize_cluster",
    "finalize_clusters",
    "label_keyword",
    "sync_roadmap",
]
