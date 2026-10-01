"""Phase 1 — fill discovery fields the user left blank via one AI call.

D1 pre-research runs before any client question. D2 asks the client. Anything
still blank after the questionnaire is sent here: one ``synthesize_json`` call
drafts the gaps from public evidence plus the answers the client DID give.

Two hard limits, enforced by ``NEVER_AI_FILL``:

  * System access / credential status can never be inferred. An AI-written
    "analytics access: granted" is a fabricated claim about the client's
    systems that would mislead whoever reads the CDD next.
  * Revenue, ticket size, LTV, traffic/lead targets and sales promises are
    institutional facts (and contractual ones). D1's prompt already forbids
    inventing them; so does this.

``competitors`` is excluded for a different reason: competitor discovery is
owned exclusively by Phase 4, and Phase 1 must not smuggle rivals in early.

Fills are stored at low confidence on purpose — ``compute_discovery_score``
already treats confidence < 0.5 as half credit and lists the field as
"(low confidence)", so an AI-filled profile never reads as verified.
"""

from __future__ import annotations

from typing import Any

from app.integrations.llm import synthesize_json
from app.logging_config import get_logger
from app.services.discovery_fields import (
    ACCESS_FIELDS,
    CLIENT_ONLY_FIELDS,
    FIELD_META,
    OBJECTIVE_OPTIONS,
    RESEARCH_FIELDS,
)

log = get_logger("discovery_gap_fill")

# Values the AI must never author, even when the field is blank.
NEVER_AI_FILL: tuple[str, ...] = (
    *ACCESS_FIELDS,
    # Money, targets, and commitments — institutional knowledge only.
    "average_ticket_size",
    "lifetime_value",
    "revenue_split",
    "seo_traffic_current",
    "seo_traffic_target",
    "sem_leads_current",
    "sem_leads_target",
    "other_marketing_spend",
    "sales_promises",
    # Phase 4 owns competitor discovery.
    "competitors",
)

AI_FILLABLE_FIELDS: tuple[str, ...] = tuple(
    key
    for key in (*RESEARCH_FIELDS, *CLIENT_ONLY_FIELDS)
    if key not in NEVER_AI_FILL
)

# compute_discovery_score() gives half credit and an explicit "(low confidence)"
# marker below 0.5. Staying under it is what keeps an AI-filled profile from
# reading as client-verified.
MAX_AI_FILL_CONFIDENCE = 0.45


def _clean_scalar(value: Any) -> Any:
    """Trim strings, drop blanks, keep lists/dicts as-is."""
    if isinstance(value, str):
        return value.strip() or None
    return value


def _coerce_to_options(value: Any, options: list[str]) -> Any:
    """Match a model answer to a declared select option, case-insensitively."""
    if not isinstance(value, str):
        return None
    wanted = value.strip().lower()
    for option in options:
        if option.lower() == wanted:
            return option
    return None


def _coerce_objectives(value: Any) -> list[str]:
    """Keep only objectives from the canonical checklist."""
    raw = value if isinstance(value, list) else [value]
    allowed = {o.lower(): o for o in OBJECTIVE_OPTIONS}
    out: list[str] = []
    for item in raw:
        match = allowed.get(str(item or "").strip().lower())
        if match and match not in out:
            out.append(match)
    return out


def normalize_gap_fill_response(
    raw: Any,
    *,
    missing: list[str],
) -> dict[str, dict[str, Any]]:
    """Validate a model gap-fill payload into storable field drafts.

    The model is never trusted to respect the request: keys outside the asked-for
    set, keys this module refuses to auto-fill, empty answers, out-of-vocabulary
    select values, and invented objectives are all dropped here rather than
    persisted.
    """
    if not isinstance(raw, dict):
        return {}

    requested = {
        key for key in missing if key in AI_FILLABLE_FIELDS and key not in NEVER_AI_FILL
    }
    out: dict[str, dict[str, Any]] = {}

    for key, payload in raw.items():
        field_key = str(key or "").strip()
        if field_key not in requested:
            continue
        if isinstance(payload, dict):
            value = payload.get("value")
            confidence = payload.get("confidence")
            rationale = payload.get("rationale") or payload.get("reason")
        else:
            value, confidence, rationale = payload, None, None

        value = _clean_scalar(value)
        if value in (None, "", [], {}):
            continue

        meta = FIELD_META.get(field_key) or {}
        if field_key == "objectives" or meta.get("input") == "objectives":
            value = _coerce_objectives(value)
        elif meta.get("input") == "select":
            value = _coerce_to_options(value, list(meta.get("options") or []))
        if value in (None, "", [], {}):
            continue

        try:
            score = float(confidence) if confidence is not None else MAX_AI_FILL_CONFIDENCE
        except (TypeError, ValueError):
            score = MAX_AI_FILL_CONFIDENCE
        score = max(0.05, min(score, MAX_AI_FILL_CONFIDENCE))

        entry: dict[str, Any] = {"value": value, "confidence": score}
        if isinstance(rationale, str) and rationale.strip():
            entry["rationale"] = rationale.strip()[:400]
        out[field_key] = entry

    return out


def pending_gap_fill_fields(known: dict[str, Any]) -> list[str]:
    """Eligible fields with no usable human answer yet."""
    pending: list[str] = []
    for key in AI_FILLABLE_FIELDS:
        value = known.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "", [], {}):
            pending.append(key)
    return pending


GAP_FILL_SYSTEM = (
    "You are the Radius OS Discovery Agent completing a client profile.\n"
    "The client filled part of the discovery questionnaire and left some fields blank. "
    "Draft ONLY the blank fields, using the public website evidence and the answers the "
    "client already gave. Stay consistent with those answers — never contradict them.\n"
    "Dynamically adapt to the client's real industry; assume no default vertical.\n"
    "HARD RULES:\n"
    "- Never state or guess system access, credentials, or whether access was granted.\n"
    "- Never invent revenue, average ticket size, lifetime value, traffic or lead "
    "numbers, or anything that was promised to the client.\n"
    "- Never return competitors — competitor discovery belongs to a later phase.\n"
    "- If the evidence for a field is thin, return null for it rather than guessing.\n"
    "Return one JSON object keyed by field name; each value is "
    '{"value": <answer>, "confidence": <0-0.45>, "rationale": "<short evidence note>"}.'
)


def build_gap_fill_prompt(
    *,
    display_name: str,
    primary_url: str,
    industry: str | None,
    known: dict[str, Any],
    missing: list[str],
    research: dict[str, Any] | None = None,
) -> str:
    """User prompt: business context, the client's own answers, then the blanks."""
    lines = [
        f"Company: {display_name}",
        f"Primary URL: {primary_url}",
        f"Industry: {(industry or '').strip() or '(not provided — infer from evidence)'}",
        "",
        "ANSWERS THE CLIENT ALREADY GAVE (treat as authoritative):",
    ]
    answered = False
    for key, value in (known or {}).items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        label = (FIELD_META.get(key) or {}).get("label") or key
        lines.append(f"- {label} [{key}]: {str(value)[:300]}")
        answered = True
    if not answered:
        lines.append("- (none yet)")

    evidence = {
        key: value
        for key, value in (research or {}).items()
        if key != "discrepancies" and value not in (None, "", [], {})
    }
    if evidence:
        lines.append("")
        lines.append("PUBLIC RESEARCH EVIDENCE (from D1 pre-research):")
        for key, value in list(evidence.items())[:20]:
            raw = value.get("value") if isinstance(value, dict) else value
            if raw in (None, "", [], {}):
                continue
            lines.append(f"- {key}: {str(raw)[:300]}")

    lines.append("")
    lines.append("FIELDS TO DRAFT (blank — return one entry per field, or null):")
    for key in missing:
        meta = FIELD_META.get(key) or {}
        label = meta.get("label") or key
        help_text = meta.get("help") or ""
        options = meta.get("options")
        suffix = f" Allowed values: {options}." if options else ""
        lines.append(f"- {key} — {label}. {help_text}{suffix}".rstrip())
    return "\n".join(lines)


async def fill_missing_discovery_fields(
    *,
    display_name: str,
    primary_url: str,
    industry: str | None,
    known: dict[str, Any],
    missing: list[str],
    research: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Draft the blank discovery fields in one model call.

    Returns ``{}`` on every failure path — no provider, empty answer, malformed
    JSON, or nothing eligible to fill — so Phase 1 submission never depends on it.
    """
    eligible = [
        key
        for key in missing
        if key in AI_FILLABLE_FIELDS and key not in NEVER_AI_FILL
    ]
    if not eligible:
        return {}

    prompt = build_gap_fill_prompt(
        display_name=display_name,
        primary_url=primary_url,
        industry=industry,
        known=known or {},
        missing=eligible,
        research=research,
    )
    try:
        raw = await synthesize_json(GAP_FILL_SYSTEM, prompt, max_tokens=2048)
    except Exception as exc:  # noqa: BLE001
        log.warning("discovery_gap_fill_failed", error=str(exc)[:200])
        return {}

    filled = normalize_gap_fill_response(raw, missing=eligible)
    log.info(
        "discovery_gap_fill_complete",
        requested=len(eligible),
        filled=len(filled),
    )
    return filled
