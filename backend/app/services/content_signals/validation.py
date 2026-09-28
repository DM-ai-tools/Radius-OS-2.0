"""Check a finished draft against the business rules in its content context.

The context tells the writer what to honour. This pass reads the draft back
and records where it breaks those rules: size claims, compliance, funnel
pressure, reach, inactive offers, and a page that already exists.
"""

from __future__ import annotations

import re
from typing import Any

_SIZE_OVERCLAIM = re.compile(
    r"\b(industry[ -]leading|market leader|number one|#1 agency|enterprise[ -]grade)\b",
    re.IGNORECASE,
)
_TOFU_HARD_SELL = re.compile(
    r"\b(book now|buy now|add to cart|limited time|act now)\b",
    re.IGNORECASE,
)
_NEAR_ME = re.compile(r"\bnear me\b", re.IGNORECASE)
_OFFER_EXCLUDED = re.compile(r"Offer '([^']+)' excluded", re.IGNORECASE)

_COMPLIANCE = {
    "healthcare": (
        re.compile(
            r"\b(guaranteed (cure|recovery|results)|we diagnose|diagnose you)\b",
            re.IGNORECASE,
        ),
        "Healthcare copy must not diagnose or promise a cure.",
    ),
    "agency": (
        re.compile(
            r"\b(guaranteed rankings?|ranking guarantee|guaranteed #1|guaranteed page one)\b",
            re.IGNORECASE,
        ),
        "Agency copy must not guarantee rankings.",
    ),
    "professional_services": (
        re.compile(
            r"\bthis (is|constitutes) (legal|financial) advice\b",
            re.IGNORECASE,
        ),
        "Professional-services copy must not present itself as legal or financial advice.",
    ),
}

_LOCAL_MODELS = {"location_based", "local", "city_wide", "regional"}


def _signal(context: dict[str, Any], name: str) -> dict[str, Any]:
    signal = (context.get("signals") or {}).get(name) or {}
    if not signal.get("available"):
        return {}
    data = signal.get("data") or {}
    return data if isinstance(data, dict) else {}


def _notes(context: dict[str, Any], name: str) -> list[str]:
    signal = (context.get("signals") or {}).get(name) or {}
    notes = signal.get("notes") or []
    return [str(n) for n in notes if n]


def draft_text(draft: dict[str, Any] | None) -> str:
    draft = draft or {}
    parts = [
        draft.get("title"),
        draft.get("markdown"),
        draft.get("page_markdown"),
        draft.get("opening"),
        draft.get("cta"),
        draft.get("meta_description"),
    ]
    return "\n".join(str(p) for p in parts if p)


def business_rules_block(context: dict[str, Any] | None) -> str:
    """Prompt checklist drawn from the same rules the validator enforces."""
    context = dict(context or {})
    lines: list[str] = []
    size = _signal(context, "business_size")
    if size.get("size") in {"solo", "small"} and size.get("claims"):
        lines.append(f"- Size ({size.get('label') or size['size']}): {size['claims']}.")
    btype = _signal(context, "business_type")
    for note in btype.get("compliance_notes") or []:
        lines.append(f"- {note}.")
    stage = str(context.get("funnel_stage") or "").upper()
    funnel = _signal(context, "funnel")
    if stage == "TOFU":
        avoid = funnel.get("avoid") or "hard-sell CTAs"
        lines.append(f"- Funnel is TOFU: avoid {avoid}.")
    reach = _signal(context, "reach")
    model = str(reach.get("model") or "")
    if model in _LOCAL_MODELS:
        places = ", ".join(str(p) for p in (reach.get("locations_served") or [])[:4])
        lines.append(
            "- Reach is "
            + str(reach.get("label") or model)
            + (f"; name {places}." if places else "; keep the copy local.")
        )
    elif model in {"national", "online"}:
        lines.append(
            f"- Reach is {reach.get('label') or model}: do not force 'near me' or a single-city frame."
        )
    for note in _notes(context, "offers"):
        if "excluded" in note.lower() or "expired" in note.lower():
            lines.append(f"- {note}")
    clash = (_signal(context, "past_performance").get("cannibalisation_clash") or {})
    if clash:
        lines.append(
            "- This topic is already covered"
            + (f" at {clash.get('url')}" if clash.get("url") else "")
            + ". Differentiate or say this refreshes that page."
        )
    if not lines:
        return ""
    return "BUSINESS LOGIC (the draft is checked against these after writing):\n" + "\n".join(lines)


def validate_business_logic(
    draft: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return pass/fail checks. A failure is a rule the draft broke."""
    context = dict(context or {})
    text = draft_text(draft)
    checks: list[dict[str, Any]] = []

    def _check(name: str, ok: bool, detail: str, *, severity: str = "fail") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "severity": severity})

    size = str(_signal(context, "business_size").get("size") or "")
    if size in {"solo", "small"}:
        hit = _SIZE_OVERCLAIM.search(text)
        _check(
            "size_claims",
            hit is None,
            "No scale claims a small business cannot support."
            if hit is None
            else f"Overclaim for a {size} business: “{hit.group(0)}”.",
        )

    btype = str(_signal(context, "business_type").get("type") or "")
    rule = _COMPLIANCE.get(btype)
    if rule:
        pattern, message = rule
        hit = pattern.search(text)
        _check(
            "compliance",
            hit is None,
            message if hit is None else f"{message} Found “{hit.group(0)}”.",
        )

    stage = str(context.get("funnel_stage") or "").upper()
    if stage == "TOFU":
        hit = _TOFU_HARD_SELL.search(text)
        _check(
            "funnel_pressure",
            hit is None,
            "TOFU draft stays soft."
            if hit is None
            else f"TOFU draft uses a hard sell (“{hit.group(0)}”).",
        )

    reach = _signal(context, "reach")
    model = str(reach.get("model") or "")
    places = [str(p).strip() for p in (reach.get("locations_served") or []) if str(p).strip()]
    if model in _LOCAL_MODELS and places:
        named = [p for p in places if p.lower() in text.lower()]
        _check(
            "reach_place",
            bool(named),
            f"Names a served place ({named[0]})." if named else f"Local reach but none of {', '.join(places[:3])} appear.",
            severity="warn",
        )
    if model == "online":
        hit = _NEAR_ME.search(text)
        _check(
            "reach_online",
            hit is None,
            "Online business is not framed as a local search."
            if hit is None
            else "E-commerce / online draft uses “near me”.",
        )

    excluded = _excluded_offer_names(context)
    leaked = [name for name in excluded if name.lower() in text.lower()]
    if excluded:
        _check(
            "inactive_offers",
            not leaked,
            "No inactive or unmatched offer is named."
            if not leaked
            else f"Draft names an offer that must stay out: {', '.join(leaked[:2])}.",
        )

    clash = _signal(context, "past_performance").get("cannibalisation_clash") or {}
    if isinstance(clash, dict) and clash:
        where = clash.get("url") or clash.get("keyword") or "an existing page"
        draft_url = str((draft or {}).get("url") or "").rstrip("/").lower()
        clash_url = str(clash.get("url") or "").rstrip("/").lower()
        refreshing = bool(draft_url and clash_url and draft_url == clash_url)
        _check(
            "already_covered",
            refreshing,
            f"This draft refreshes the existing page at {where}."
            if refreshing
            else f"Topic is already covered by {where}. Refresh that page or change the angle.",
        )

    failures = [c for c in checks if not c["ok"] and c["severity"] == "fail"]
    warnings = [c for c in checks if not c["ok"] and c["severity"] != "fail"]
    return {
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
    }


def _excluded_offer_names(context: dict[str, Any]) -> list[str]:
    names: list[str] = []
    data = (context.get("signals") or {}).get("offers") or {}
    payload = data.get("data") or {}
    for note in list(payload.get("excluded") or []) + _notes(context, "offers"):
        match = _OFFER_EXCLUDED.search(str(note))
        if match:
            names.append(match.group(1))
    return list(dict.fromkeys(names))
