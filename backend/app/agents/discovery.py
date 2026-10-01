"""Phase 1 — Discovery Agent (D1–D5) per discovery-agent skill.

Cold start asks for the Client Discovery Document first. Public-site research is
not run on init. After CDD upload the questionnaire is prefilled; on submit,
AI gap-fill drafts only remaining blanks, then the CSM confirmation gate.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill
from app.models import (
    Client,
    ClientDigitalProfile,
    DiscoveryResponse,
)
from app.services.agent_runtime import get_profile
from app.services.audit import log_event
from app.services.discovery_fields import (
    CLIENT_ONLY_FIELDS,
    FIELD_META,
    OBJECTIVE_OPTIONS,
    RESEARCH_FIELDS,
    field_ui_meta,
)
from app.services.readiness import compute_discovery_score, recompute_readiness
from app.services.role_skills import required_role_for


def _conf_label(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"


def _blank_field(key: str) -> dict[str, Any]:
    return {
        "value": None,
        "prefilled": False,
        "client_only": key in CLIENT_ONLY_FIELDS,
        "confidence": "unknown",
        **{k: v for k, v in (FIELD_META.get(key) or {}).items() if k != "client_only"},
    }


def build_questionnaire_fields(
    *,
    intake: dict[str, Any] | None = None,
    prior_rows: list[DiscoveryResponse] | None = None,
) -> dict[str, Any]:
    """Empty catalog, then overlay intake + CDD / questionnaire rows."""
    questionnaire_fields: dict[str, Any] = {
        key: _blank_field(key) for key in list(RESEARCH_FIELDS) + list(CLIENT_ONLY_FIELDS)
    }
    intake = dict(intake or {})
    for key in list(RESEARCH_FIELDS) + list(CLIENT_ONLY_FIELDS):
        if key in intake and intake[key] not in (None, "", [], {}):
            questionnaire_fields[key]["value"] = intake[key]
            questionnaire_fields[key]["prefilled"] = True
            questionnaire_fields[key]["from_intake"] = True

    # Newest row wins the value. CDD / AI origin is kept when a later client
    # submit repeats the same value, so the UI can still color the source.
    _rank = {"client_questionnaire": 3, "cdd_upload": 2, "ai_gap_fill": 1}
    chosen: dict[str, DiscoveryResponse] = {}
    origin: dict[str, str] = {}
    for row in reversed(list(prior_rows or [])):
        if row.field_key == "competitors":
            continue
        if row.source not in _rank:
            continue
        val = (row.field_value or {}).get("value")
        if val in (None, "", [], {}):
            continue
        prev = chosen.get(row.field_key)
        prev_val = (prev.field_value or {}).get("value") if prev else None
        if row.source == "client_questionnaire" and origin.get(row.field_key) in {"cdd", "ai"}:
            if prev_val != val:
                origin[row.field_key] = "client"
        elif row.source == "cdd_upload":
            origin[row.field_key] = "cdd"
        elif row.source == "ai_gap_fill" and origin.get(row.field_key) != "cdd":
            origin[row.field_key] = "ai"
        elif row.field_key not in origin:
            origin[row.field_key] = "client"
        current = chosen.get(row.field_key)
        if current is None or _rank[row.source] >= _rank[current.source]:
            chosen[row.field_key] = row

    for key, row in chosen.items():
        val = (row.field_value or {}).get("value")
        meta = questionnaire_fields.get(key) or _blank_field(key)
        src = origin.get(key)
        questionnaire_fields[key] = {
            **meta,
            "value": val,
            "prefilled": True,
            "from_cdd_upload": src == "cdd",
            "from_ai": src == "ai",
            "confidence": (
                "high"
                if src == "cdd"
                else "low"
                if src == "ai"
                else (meta.get("confidence") or "high")
            ),
        }
    return questionnaire_fields


def build_discovery_questionnaire_card(
    *,
    client: Client,
    fields: dict[str, Any],
    confirm_rerun: bool = False,
    profile: ClientDigitalProfile | None = None,
) -> dict[str, Any]:
    return {
        "card_type": "discovery_report",
        "title": "Phase 1 — Discovery questionnaire",
        "subtitle": "Review CDD fields, complete blanks, then submit",
        "fields": fields,
        "research_fields": {},
        "discrepancies": [],
        "objective_options": OBJECTIVE_OPTIONS,
        "field_catalog": field_ui_meta(),
        "agent_key": "discovery_agent",
        "actions": ["submit_questionnaire", "upload_cdd"],
        "required_role": required_role_for("discovery_agent"),
        "step": "Phase 1",
        "prior_approved": (
            {
                "commercial_scope": profile.commercial_scope if profile else None,
                "marketing_context": profile.marketing_context if profile else None,
            }
            if confirm_rerun and profile is not None
            else None
        ),
        "client_name": client.display_name,
    }


async def load_discovery_prior_rows(
    db: AsyncSession, client_id: UUID
) -> list[DiscoveryResponse]:
    return list(
        (
            await db.execute(
                select(DiscoveryResponse)
                .where(DiscoveryResponse.client_id == client_id)
                .order_by(DiscoveryResponse.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def run_discovery(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    """Emit CDD upload prompt. Questionnaire + AI gap-fill follow upload/submit."""
    _ = load_skill("discovery_agent")
    _ = session_id
    _ = user_id
    events: list[dict] = []
    profile = await get_profile(db, client.id)

    lowered = message.lower()
    confirm_rerun = any(
        k in lowered
        for k in ("overwrite", "confirm re-run", "confirm rerun", "yes re-run", "refresh discovery")
    )

    if profile.discovery_status == "complete" and not confirm_rerun:
        prior = profile.commercial_scope or {}
        events.append(
            {
                "type": "agent_message",
                "agent_key": "discovery_agent",
                "content": (
                    f"Discovery is already complete for {client.display_name}. "
                    "Re-running will draft a new profile while keeping the prior approved data "
                    "visible for comparison. Reply with \"confirm re-run discovery\" to overwrite, "
                    "or leave the existing profile as-is."
                ),
            }
        )
        events.append(
            {
                "type": "structured_card",
                "payload": {
                    "card_type": "discovery_rerun_confirm",
                    "title": "Discovery already complete",
                    "prior_commercial_scope": prior,
                    "prior_marketing_context": profile.marketing_context or {},
                    "agent_key": "discovery_agent",
                    "actions": [],
                    "required_role": required_role_for("discovery_agent"),
                    "hint": 'Type: confirm re-run discovery',
                },
            }
        )
        return events

    # If a CDD was already uploaded this session, skip the prompt and show the form.
    prior_rows = await load_discovery_prior_rows(db, client.id)
    has_cdd = any(
        r.source == "cdd_upload"
        and (r.field_value or {}).get("value") not in (None, "", [], {})
        for r in prior_rows
    )

    profile.discovery_status = "in_progress"
    await db.flush()

    if has_cdd:
        intake = ((profile.marketing_context or {}).get("client_intake") or {}) if profile.marketing_context else {}
        fields = build_questionnaire_fields(intake=intake, prior_rows=prior_rows)
        card = build_discovery_questionnaire_card(
            client=client, fields=fields, confirm_rerun=confirm_rerun, profile=profile
        )
        events.append(
            {
                "type": "agent_message",
                "agent_key": "discovery_agent",
                "content": (
                    f"Phase 1 Discovery for {client.display_name}: CDD fields are loaded. "
                    "Review, complete any blanks, then submit — AI will draft only what is still missing."
                ),
            }
        )
        events.append({"type": "structured_card", "payload": card})
    else:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Phase 1 — Discovery for {client.display_name}: upload the Client "
                    "Discovery Document (Excel, Word, or PDF) to begin."
                ),
            }
        )
        events.append(
            {
                "type": "agent_message",
                "agent_key": "discovery_agent",
                "content": (
                    f"Upload the CDD for {client.display_name} to start Discovery. "
                    "After the file is imported, confirm the extracted fields and fill any "
                    "gaps. AI drafts only remaining blanks after you submit — nothing is "
                    "researched until then."
                ),
            }
        )
        events.append(
            {
                "type": "structured_card",
                "payload": {
                    "card_type": "discovery_cdd_prompt",
                    "title": "Phase 1 — Upload Client Discovery Document",
                    "subtitle": "CDD first — then confirm fields and submit",
                    "agent_key": "discovery_agent",
                    "actions": ["upload_cdd"],
                    "required_role": required_role_for("discovery_agent"),
                    "step": "Phase 1",
                    "client_name": client.display_name,
                    "hint": "Accepted: .xlsx, .xlsm, .csv, .docx, .pdf, .txt",
                },
            }
        )

    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="tool_call",
        event_detail={
            "tool": "discovery_cdd_prompt" if not has_cdd else "discovery_questionnaire",
            "agent": "discovery_agent",
            "step": "D1",
        },
    )

    events.append(
        {
            "type": "phase_status",
            "payload": {
                "discovery_status": profile.discovery_status,
                "overall_readiness_score": float(profile.overall_readiness_score or 0),
            },
        }
    )
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Next: upload the CDD, confirm fields, submit the questionnaire, "
                "then approve the confirmation gate."
                if not has_cdd
                else "Next: submit the Discovery questionnaire, then approve the confirmation gate."
            ),
        }
    )
    return events


async def build_questionnaire_events_after_cdd(
    db: AsyncSession,
    *,
    client: Client,
) -> list[dict]:
    """Emit the editable questionnaire after a successful CDD import."""
    profile = await get_profile(db, client.id)
    prior_rows = await load_discovery_prior_rows(db, client.id)
    intake = ((profile.marketing_context or {}).get("client_intake") or {}) if profile.marketing_context else {}
    fields = build_questionnaire_fields(intake=intake, prior_rows=prior_rows)
    card = build_discovery_questionnaire_card(client=client, fields=fields, profile=profile)
    return [
        {
            "type": "agent_message",
            "agent_key": "discovery_agent",
            "content": (
                f"CDD imported for {client.display_name}. Review the extracted fields, "
                "complete anything blank, then submit. AI will fill only remaining gaps."
            ),
        },
        {"type": "structured_card", "payload": card},
        {
            "type": "system_notice",
            "content": "Next: submit the Discovery questionnaire, then approve the confirmation gate.",
        },
    ]


async def build_discovery_signoff_events(
    db: AsyncSession,
    *,
    client_id: UUID,
    ai_filled: dict[str, dict] | None = None,
) -> list[dict]:
    """Build the single Discovery confirmation gate after report submission."""
    events: list[dict] = []
    ai_filled = dict(ai_filled or {})
    profile = await get_profile(db, client_id)
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one()

    rows = (
        await db.execute(
            select(DiscoveryResponse).where(DiscoveryResponse.client_id == client_id)
        )
    ).scalars().all()

    from_research: dict = {}
    from_cdd: dict = {}
    confirmed: dict = {}
    discrepancies = []
    for r in rows:
        val = (r.field_value or {}).get("value")
        if r.source == "ai_gap_fill":
            if r.field_key not in ai_filled and val not in (None, "", [], {}):
                ai_filled[r.field_key] = {
                    "value": val,
                    "confidence": float(r.confidence) if r.confidence is not None else 0.4,
                    "rationale": (r.field_value or {}).get("rationale"),
                }
            continue
        if r.source == "pre_research":
            from_research[r.field_key] = val
            if r.discrepancy_flag:
                discrepancies.append(
                    {
                        "field_key": r.field_key,
                        "explanation": "Research vs client answer may conflict — review before sign-off.",
                    }
                )
        elif r.source == "cdd_upload":
            from_cdd[r.field_key] = val
        elif r.source == "client_questionnaire":
            confirmed[r.field_key] = val

    # Prefer CDD extract as the "from document" column when no pre_research ran.
    baseline = from_research or from_cdd

    for key, cval in confirmed.items():
        if key in baseline and baseline[key] != cval:
            if not any(
                isinstance(d, dict) and d.get("field_key") == key for d in discrepancies
            ):
                discrepancies.append(
                    {
                        "field_key": key,
                        "explanation": (
                            f"Client answer differs from "
                            f"{'pre-research' if from_research else 'CDD'} "
                            f"for {key.replace('_', ' ')}."
                        ),
                    }
                )

    score, missing = await compute_discovery_score(db, client_id)
    await recompute_readiness(db, client_id)
    profile.discovery_status = "pending_signoff"
    await db.flush()

    confirmation_card = {
        "card_type": "discovery_confirmation",
        "title": "Discovery confirmation gate",
        "subtitle": "Client Success Manager approval",
        "from_research": baseline,
        "from_cdd": from_cdd,
        "confirmed_by_client": confirmed,
        "discrepancies": discrepancies,
        "completeness_score": float(score),
        "missing_fields": missing,
        "ai_filled": ai_filled,
        "agent_key": "discovery_agent",
        "actions": ["approve", "edit", "reject"],
        "required_role": required_role_for("discovery_agent"),
        "client_name": client.display_name,
        "step": "Confirmation gate",
    }
    ai_note = (
        f" {len(ai_filled)} blank field(s) were AI-drafted and count as low confidence "
        "— verify them before approving."
        if ai_filled
        else ""
    )
    events.append(
        {
            "type": "agent_message",
            "agent_key": "discovery_agent",
            "content": (
                f"Discovery is {float(score):.0f}% complete. Review the report, resolve any "
                "discrepancies, and approve once to publish Commercial Scope + Marketing Context."
                + ai_note
            ),
        }
    )
    events.append({"type": "structured_card", "payload": confirmation_card})
    events.append({"type": "checkpoint", "payload": confirmation_card})
    events.append(
        {
            "type": "phase_status",
            "payload": {
                "discovery_status": profile.discovery_status,
                "overall_readiness_score": float(profile.overall_readiness_score or 0),
            },
        }
    )
    events.append(
        {
            "type": "system_notice",
            "content": "Next: CSM approve the Discovery confirmation gate, then run tracking check.",
        }
    )
    return events
