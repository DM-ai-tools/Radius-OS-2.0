"""Signal 10 — offer relevance.

Offers live in ``commercial_scope.offers`` as a list of records:

    {id, name, type, description, url, cta, starts_at, ends_at, active,
     applies_to[], locations[], funnel_stages[], priority (1-3), terms?}

An offer is inactive when ``active`` is false OR the current time falls outside
``starts_at``/``ends_at``. Inactive offers are never returned, never scored and
never reach the prompt — an expired promotion in published copy is a real-world
liability, so this is a hard filter rather than a ranking penalty.

Dates are ISO 8601 with timezone. A naive timestamp is treated as UTC; an
unparseable one makes the offer inactive (fail closed) with a note, rather than
being silently ignored.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.logging_config import get_logger
from app.services.content_signals.base import SignalResult, unavailable

log = get_logger("content_signals.offers")

FUNNEL_STAGES = ("TOFU", "MOFU", "BOFU")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value: Any) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(value)) if len(t) > 2}


def parse_iso(value: Any) -> datetime | None:
    """ISO 8601 → timezone-aware datetime. Naive input is assumed UTC."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def offer_is_active(offer: dict[str, Any], *, now: datetime | None = None) -> tuple[bool, str]:
    """(active, reason). Fail closed: anything unparseable is inactive."""
    now = now or datetime.now(timezone.utc)
    if offer.get("active") is False:
        return False, "flagged inactive"

    raw_start, raw_end = offer.get("starts_at"), offer.get("ends_at")
    starts = parse_iso(raw_start)
    ends = parse_iso(raw_end)

    if raw_start and starts is None:
        return False, f"unparseable starts_at {raw_start!r}"
    if raw_end and ends is None:
        return False, f"unparseable ends_at {raw_end!r}"
    if starts and now < starts:
        return False, f"starts {starts.date().isoformat()}"
    if ends and now > ends:
        return False, f"expired {ends.date().isoformat()}"
    return True, "active"


def active_offers(
    offers: list[dict[str, Any]] | None, *, now: datetime | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split offers into active ones and operator-facing notes for the rest."""
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    for offer in offers or []:
        if not isinstance(offer, dict) or not _norm(offer.get("name")):
            continue
        ok, reason = offer_is_active(offer, now=now)
        if ok:
            kept.append(offer)
        else:
            notes.append(f"Offer {offer.get('name')!r} excluded: {reason}.")
    return kept, notes


def score_offer_match(
    offer: dict[str, Any],
    *,
    keyword: str,
    funnel_stage: str | None = None,
    services: list[str] | None = None,
    locations: list[str] | None = None,
) -> tuple[int, list[str]]:
    """0-100 alignment between one offer and one content piece, plus reasons.

    Weighted so a funnel-stage mismatch cannot be outvoted by loose keyword
    overlap: an offer meant for BOFU has no business in a TOFU explainer.
    """
    reasons: list[str] = []
    score = 0

    stages = [str(s).strip().upper() for s in (offer.get("funnel_stages") or [])]
    stage = str(funnel_stage or "").strip().upper()
    if stages and stage:
        if stage in stages:
            score += 35
            reasons.append(f"funnel stage {stage} matches offer")
        else:
            return 0, [f"offer is for {'/'.join(stages)}, content is {stage}"]
    elif not stages:
        score += 15
        reasons.append("offer has no funnel restriction")

    applies = [a for a in (offer.get("applies_to") or []) if _norm(a)]
    kw_tokens = _tokens(keyword)
    if applies:
        hits = [a for a in applies if _tokens(a) & kw_tokens]
        if hits:
            score += 35
            reasons.append(f"applies_to matches keyword ({hits[0]})")
        else:
            svc_tokens: set[str] = set()
            for svc in services or []:
                svc_tokens |= _tokens(svc)
            if any(_tokens(a) & svc_tokens for a in applies):
                score += 15
                reasons.append("applies_to matches a client service")
    else:
        score += 10
        reasons.append("offer applies to everything")

    offer_locs = [loc for loc in (offer.get("locations") or []) if _norm(loc)]
    if offer_locs:
        content_locs = {_norm(x) for x in (locations or [])}
        if content_locs and any(_norm(loc) in content_locs for loc in offer_locs):
            score += 15
            reasons.append("offer location matches content location")
        elif content_locs:
            return 0, ["offer is location-restricted and does not cover this content"]
    else:
        score += 10
        reasons.append("offer covers all locations")

    try:
        priority = int(offer.get("priority") or 3)
    except (TypeError, ValueError):
        priority = 3
    priority = min(3, max(1, priority))
    score += {1: 15, 2: 8, 3: 0}[priority]
    if priority == 1:
        reasons.append("priority 1 offer")

    return min(100, score), reasons


def collect_offers(
    *,
    commercial: dict[str, Any] | None = None,
    keyword: str = "",
    funnel_stage: str | None = None,
    services: list[str] | None = None,
    locations: list[str] | None = None,
    now: datetime | None = None,
    weight: str = "medium",
) -> SignalResult:
    commercial = dict(commercial or {})
    raw = commercial.get("offers")
    if not isinstance(raw, list) or not raw:
        return unavailable(
            "offers",
            "No offers defined in commercial_scope.offers.",
            weight=weight,
        )

    live, excluded_notes = active_offers(raw, now=now)
    if not live:
        return SignalResult(
            name="offers",
            available=False,
            weight=weight,
            notes=["No currently-active offers.", *excluded_notes],
        )

    ranked: list[dict[str, Any]] = []
    for offer in live:
        score, reasons = score_offer_match(
            offer,
            keyword=keyword,
            funnel_stage=funnel_stage,
            services=services,
            locations=locations,
        )
        if score <= 0:
            excluded_notes.append(
                f"Offer {offer.get('name')!r} not matched: {reasons[0] if reasons else 'no overlap'}."
            )
            continue
        ranked.append(
            {
                "id": offer.get("id"),
                "name": offer.get("name"),
                "type": offer.get("type"),
                "description": offer.get("description"),
                "url": offer.get("url"),
                "cta": offer.get("cta"),
                "terms": offer.get("terms"),
                "ends_at": offer.get("ends_at"),
                "match_score": score,
                "match_reasons": reasons,
            }
        )

    if not ranked:
        return SignalResult(
            name="offers",
            available=False,
            weight=weight,
            notes=["Active offers exist but none match this content.", *excluded_notes],
        )

    ranked.sort(key=lambda o: -int(o["match_score"]))
    best = ranked[0]
    return SignalResult(
        name="offers",
        available=True,
        weight=weight,
        data={
            "primary_offer": best,
            "alternatives": ranked[1:3],
            "excluded": excluded_notes,
        },
        notes=[
            f"Offer linked: {best['name']!r} (match {best['match_score']}/100 — "
            f"{'; '.join(best['match_reasons'][:2])}).",
            *excluded_notes,
        ],
        suggests=[f"connect to offer: {best['name']}"],
    )
