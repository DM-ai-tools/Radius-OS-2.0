"""Phase 1 — Discovery Agent (D1–D5) per discovery-agent skill."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm import live_pre_research
from app.models import (
    Client,
    DiscoveryResponse,
    FindingsLedger,
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
from app.agents.prompts import load_skill


def _conf_label(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"


async def run_discovery(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    """Execute D1 + D2 emission. D3/D4 follow questionnaire submit. D5 on approve."""
    _ = load_skill("discovery_agent")  # ensure skill contract is loadable
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

    profile.discovery_status = "in_progress"
    await db.flush()

    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Phase 1 — Discovery research for {client.display_name}"
                + (
                    f" ({client.industry})"
                    if client.industry
                    else " (inferring industry from public footprint)"
                )
                + " — website, local/social signals, and review themes…"
            ),
        }
    )

    # D1 — automated pre-research BEFORE any client questions
    research = await live_pre_research(
        client.display_name,
        client.primary_url,
        industry=client.industry,
    )
    discrepancy_by_field = {}
    for item in research.get("discrepancies") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("field_key") or "").strip()
        if not key:
            continue
        discrepancy_by_field[key] = str(item.get("explanation") or "").strip()

    # Persist inferred industry so competitor/tracking phases stay vertical-aware
    inferred_payload = research.get("inferred_industry") or {}
    inferred_industry = inferred_payload.get("value") if isinstance(inferred_payload, dict) else None
    if isinstance(inferred_industry, str):
        inferred_industry = inferred_industry.strip() or None
    if inferred_industry and not (client.industry or "").strip():
        client.industry = inferred_industry
    elif inferred_industry and client.industry and inferred_industry.lower() != client.industry.lower():
        discrepancy_by_field.setdefault(
            "inferred_industry",
            f"Intake industry was '{client.industry}'; site research suggests '{inferred_industry}'.",
        )

    research_rows: list[DiscoveryResponse] = []
    draft_fields: dict[str, dict] = {}
    for field_key, payload in research.items():
        if field_key == "discrepancies":
            continue
        if not isinstance(payload, dict):
            payload = {"value": payload, "confidence": 0.3}
        conf = float(payload.get("confidence", 0.5) or 0.5)
        row = DiscoveryResponse(
            client_id=client.id,
            source="pre_research",
            field_key=field_key,
            field_value={"value": payload.get("value")},
            confidence=Decimal(str(conf)),
            discrepancy_flag=field_key in discrepancy_by_field,
            status="pending",
        )
        db.add(row)
        research_rows.append(row)
        draft_fields[field_key] = {
            "value": payload.get("value"),
            "confidence": _conf_label(conf),
            "confidence_score": conf,
            "source": "pre_research",
        }
    await db.flush()

    # Publish draft into shared playground memory immediately (before CSM approve)
    commercial_draft: dict = {}
    marketing_draft: dict = dict(profile.marketing_context or {})
    for field_key, meta in draft_fields.items():
        val = meta.get("value")
        if val in (None, "", [], {}):
            continue
        if field_key in (
            "business_model",
            "products",
            "positioning",
            "geographic_focus",
            "b2b_b2c",
            "business_goal",
        ):
            commercial_draft[field_key] = val
        else:
            marketing_draft[field_key] = val
    if commercial_draft:
        commercial_draft["_draft"] = True
        profile.commercial_scope = {**(profile.commercial_scope or {}), **commercial_draft}
    if marketing_draft:
        marketing_draft["_draft"] = True
        profile.marketing_context = marketing_draft

    for row in research_rows:
        db.add(
            FindingsLedger(
                client_id=client.id,
                agent_key="discovery_agent",
                source_table="discovery_responses",
                source_id=row.id,
                confidence=_conf_label(float(row.confidence) if row.confidence is not None else None),
                status="pending",
            )
        )

    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="tool_call",
        event_detail={"tool": "web_research", "agent": "discovery_agent", "step": "D1"},
    )

    # One Phase 1 report: public research is pre-filled into the confirmation form.
    intake = ((profile.marketing_context or {}).get("client_intake") or {}) if profile.marketing_context else {}
    questionnaire_fields = {}
    for key, meta in draft_fields.items():
        questionnaire_fields[key] = {
            "value": meta["value"] if key not in CLIENT_ONLY_FIELDS else (
                meta["value"] if meta["confidence"] != "low" else None
            ),
            "prefilled": key not in CLIENT_ONLY_FIELDS or meta["confidence"] != "low",
            "client_only": key in CLIENT_ONLY_FIELDS,
            "confidence": meta["confidence"],
            **{k: v for k, v in (FIELD_META.get(key) or {}).items() if k != "client_only"},
        }
    for key in list(RESEARCH_FIELDS) + list(CLIENT_ONLY_FIELDS):
        if key not in questionnaire_fields:
            questionnaire_fields[key] = {
                "value": None,
                "prefilled": False,
                "client_only": key in CLIENT_ONLY_FIELDS,
                "confidence": "unknown",
                **{k: v for k, v in (FIELD_META.get(key) or {}).items() if k != "client_only"},
            }
        # Early intake may pre-fill overlapping client fields (e.g. objectives)
        if key in intake and intake[key] not in (None, "", [], {}):
            current = questionnaire_fields[key].get("value")
            if current in (None, "", [], {}):
                questionnaire_fields[key]["value"] = intake[key]
                questionnaire_fields[key]["prefilled"] = True
                questionnaire_fields[key]["from_intake"] = True

    # Merge any prior CDD upload rows
    prior_rows = (
        await db.execute(
            select(DiscoveryResponse)
            .where(DiscoveryResponse.client_id == client.id)
            .order_by(DiscoveryResponse.created_at.desc())
        )
    ).scalars().all()
    seen_keys: set[str] = set()
    for row in prior_rows:
        if row.field_key in seen_keys:
            continue
        seen_keys.add(row.field_key)
        # Competitor discovery belongs exclusively to Phase 4. Ignore legacy
        # or uploaded competitor rows when rebuilding the Phase 1 report.
        if row.field_key == "competitors":
            continue
        if row.source not in ("cdd_upload", "client_questionnaire"):
            continue
        val = (row.field_value or {}).get("value")
        if val in (None, "", [], {}):
            continue
        meta = questionnaire_fields.get(row.field_key) or {
            "client_only": row.field_key in CLIENT_ONLY_FIELDS,
            **{k: v for k, v in (FIELD_META.get(row.field_key) or {}).items() if k != "client_only"},
        }
        meta = {
            **meta,
            "value": val,
            "prefilled": True,
            "from_cdd_upload": row.source == "cdd_upload",
            "confidence": meta.get("confidence") or "high",
        }
        questionnaire_fields[row.field_key] = meta

    discovery_report = {
        "card_type": "discovery_report",
        "title": "Phase 1 — Discovery report",
        "subtitle": "Public research, client context, and commercial scope",
        "fields": questionnaire_fields,
        "research_fields": draft_fields,
        "discrepancies": research.get("discrepancies", []),
        "objective_options": OBJECTIVE_OPTIONS,
        "field_catalog": field_ui_meta(),
        "agent_key": "discovery_agent",
        "actions": ["submit_questionnaire", "upload_cdd"],
        "required_role": required_role_for("discovery_agent"),
        "step": "Phase 1",
        "prior_approved": (
            {
                "commercial_scope": profile.commercial_scope,
                "marketing_context": profile.marketing_context,
            }
            if confirm_rerun
            else None
        ),
    }
    events.append(
        {
            "type": "agent_message",
            "agent_key": "discovery_agent",
            "content": (
                f"Phase 1 Discovery report ready for {client.display_name}. "
                "Public research is pre-filled; confirm the facts and complete any client-only "
                "commercial fields before the single confirmation gate."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": discovery_report})
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
            "content": "Next: submit the Discovery report, then approve the single confirmation gate.",
        }
    )
    return events


async def build_discovery_signoff_events(
    db: AsyncSession,
    *,
    client_id: UUID,
) -> list[dict]:
    """Build the single Discovery confirmation gate after report submission."""
    events: list[dict] = []
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
    confirmed: dict = {}
    discrepancies = []
    for r in rows:
        val = (r.field_value or {}).get("value")
        if r.source == "pre_research":
            from_research[r.field_key] = val
            if r.discrepancy_flag:
                discrepancies.append(
                    {
                        "field_key": r.field_key,
                        "explanation": "Research vs client answer may conflict — review before sign-off.",
                    }
                )
        elif r.source == "client_questionnaire":
            confirmed[r.field_key] = val

    # Enrich discrepancies when research != confirmed
    for key, cval in confirmed.items():
        if key in from_research and from_research[key] != cval:
            if not any(
                isinstance(d, dict) and d.get("field_key") == key for d in discrepancies
            ):
                discrepancies.append(
                    {
                        "field_key": key,
                        "explanation": (
                            f"Client answer differs from pre-research for {key.replace('_', ' ')}."
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
        "from_research": from_research,
        "confirmed_by_client": confirmed,
        "discrepancies": discrepancies,
        "completeness_score": float(score),
        "missing_fields": missing,
        "agent_key": "discovery_agent",
        "actions": ["approve", "edit", "reject"],
        "required_role": required_role_for("discovery_agent"),
        "client_name": client.display_name,
        "step": "Confirmation gate",
    }
    events.append(
        {
            "type": "agent_message",
            "agent_key": "discovery_agent",
            "content": (
                f"Discovery is {float(score):.0f}% complete. Review the report, resolve any "
                "discrepancies, and approve once to publish Commercial Scope + Marketing Context."
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
