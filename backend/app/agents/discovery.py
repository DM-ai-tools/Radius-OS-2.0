"""Phase 1 — Discovery Agent (D1–D5) per discovery-agent skill."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm import live_pre_research
from app.models import (
    Client,
    ClientDigitalProfile,
    DiscoveryResponse,
    FindingsLedger,
)
from app.services.audit import log_event
from app.services.discovery_fields import (
    CLIENT_ONLY_FIELDS,
    FIELD_META,
    OBJECTIVE_OPTIONS,
    RESEARCH_FIELDS,
    field_ui_meta,
)
from app.services.readiness import compute_discovery_score, recompute_readiness
from app.skills import load_skill


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
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()

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
                    "required_role": "client_success_manager",
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
                f"D1 — Automated pre-research for {client.display_name}"
                + (
                    f" ({client.industry})"
                    if client.industry
                    else " (inferring industry from public footprint)"
                )
                + " — website, local/social signals, reviews, same-vertical competitors…"
            ),
        }
    )

    # D1 — automated pre-research BEFORE any client questions
    research = await live_pre_research(
        client.display_name,
        client.primary_url,
        industry=client.industry,
    )
    discrepancy_by_field = {
        d["field_key"]: d["explanation"] for d in research.get("discrepancies", [])
    }

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
        conf = float(payload.get("confidence", 0.5))
        row = DiscoveryResponse(
            client_id=client.id,
            source="pre_research",
            field_key=field_key,
            field_value={"value": payload["value"]},
            confidence=Decimal(str(conf)),
            discrepancy_flag=field_key in discrepancy_by_field,
            status="pending",
        )
        db.add(row)
        research_rows.append(row)
        draft_fields[field_key] = {
            "value": payload["value"],
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

    d1_card = {
        "card_type": "discovery_preresearch",
        "title": "D1 — Automated pre-research",
        "label": "Draft from public footprint — awaiting client confirmation",
        "subtitle": "Claude + web research",
        "fields": draft_fields,
        "discrepancies": research.get("discrepancies", []),
        "agent_key": "discovery_agent",
        "actions": [],
        "required_role": "client_success_manager",
        "step": "D1",
    }
    events.append(
        {
            "type": "agent_message",
            "agent_key": "discovery_agent",
            "content": (
                f"D1 complete for {client.display_name}: researched the public footprint "
                "(website, local/social clues, review themes, visible competitors) and drafted "
                "business model, products, and positioning — before asking the client anything. "
                "Confidence tags show how strong each source was. This is a draft, not final."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": d1_card})

    # D2 — short targeted questionnaire (client-only + confirm research)
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

    d2_card = {
        "card_type": "discovery_questionnaire",
        "title": "D2 — CDD questionnaire (pre-filled)",
        "subtitle": "APSA Client Discovery Document fields",
        "fields": questionnaire_fields,
        "objective_options": OBJECTIVE_OPTIONS,
        "field_catalog": field_ui_meta(),
        "agent_key": "discovery_agent",
        "actions": ["submit_questionnaire", "upload_cdd"],
        "required_role": "client_success_manager",
        "step": "D2",
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
                "D2: Client Discovery Document fields (APSA CDD). Confirm research drafts, "
                "complete client-only metrics (ticket size, LTV, lead modes, goals), or upload "
                "an existing CDD spreadsheet / Word / PDF to pre-fill."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": d2_card})
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
            "content": "Next: D3 completeness → D4 CSM sign-off → D5 publish to Client Digital Profile.",
        }
    )
    return events


async def build_discovery_signoff_events(
    db: AsyncSession,
    *,
    client_id: UUID,
) -> list[dict]:
    """D3 completeness + D4 sign-off cards after questionnaire submit."""
    events: list[dict] = []
    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one()
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
            if not any(d["field_key"] == key for d in discrepancies):
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

    d3_card = {
        "card_type": "discovery_completeness",
        "title": "D3 — Completeness scoring",
        "subtitle": "Weighted readiness score",
        "completeness_score": float(score),
        "missing_fields": missing,
        "agent_key": "discovery_agent",
        "actions": [],
        "step": "D3",
    }
    events.append(
        {
            "type": "agent_message",
            "agent_key": "discovery_agent",
            "content": (
                f"D3: discovery is {float(score):.0f}% complete on the weighted readiness model."
                + (
                    f" Missing or low-confidence: {', '.join(missing)}."
                    if missing
                    else " All scored fields present."
                )
            ),
        }
    )
    events.append({"type": "structured_card", "payload": d3_card})

    d4_card = {
        "card_type": "discovery_profile",
        "title": "D4 — Human sign-off",
        "subtitle": "Client Success Manager",
        "from_research": from_research,
        "confirmed_by_client": confirmed,
        "discrepancies": discrepancies,
        "completeness_score": float(score),
        "missing_fields": missing,
        "agent_key": "discovery_agent",
        "actions": ["approve", "edit", "reject"],
        "required_role": "client_success_manager",
        "client_name": client.display_name,
        "step": "D4",
    }
    events.append(
        {
            "type": "agent_message",
            "agent_key": "discovery_agent",
            "content": (
                "D4: Client Success Manager review. Discrepancies between client claims and "
                "independent research are flagged on purpose — treat them as insight, not noise. "
                "Approve publishes Commercial Scope + Marketing Context to the Client Digital Profile (D5)."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": d4_card})
    events.append({"type": "checkpoint", "payload": d4_card})
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
            "content": "Next: CSM Approve (D5 publish), then run tracking check.",
        }
    )
    return events
