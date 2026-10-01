import asyncio
import re
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user, require_permission
from app.integrations.web_fetch import normalize_primary_url
from app.logging_config import get_logger
from app.models import (
    AgentJob,
    ApiCredential,
    ApiUsageLog,
    AuditTrail,
    BacklinkSnapshot,
    ChatMessage,
    ChatSession,
    Client,
    ClientDigitalProfile,
    CompetitorProfile,
    DiscoveryResponse,
    FindingsLedger,
    PhaseValidation,
    ReadinessScore,
    TrackingAudit,
    User,
    WebsiteAudit,
)
from app.schemas.client import (
    ClientCreate,
    ClientDetail,
    ClientOut,
    ClientUpdate,
    ProfileOut,
    client_out,
    intake_from_body,
)
from app.services.audit import log_event

log = get_logger("clients_api")

router = APIRouter(prefix="/clients", tags=["clients"])


def _can_manage_clients(user: User) -> bool:
    if get_settings().auth_disabled:
        return True
    role = (user.role.name if user.role else "") or ""
    return role in ("head_of_department", "client_success_manager")


async def require_client_management(user: User = Depends(get_current_user)) -> User:
    """Gate for identity-mutating client endpoints (create/update/delete).

    Read endpoints stay open to any authenticated role, since most phase
    specialists only need to view a client to do their own work — but
    creating, editing, or deleting the client record itself is CSM/HoD
    onboarding-and-offboarding work, not something every role should do.
    """
    if not _can_manage_clients(user):
        raise HTTPException(
            status_code=403,
            detail="Managing clients requires Head of Department or CSM role.",
        )
    return user


class AiFillRequest(BaseModel):
    url: str


class DraftSitePreviewRequest(BaseModel):
    title: str | None = None
    url: str | None = None
    meta_description: str | None = None
    keyword: str | None = None
    markdown: str = ""
    images: list[dict] | None = None
    media_base: str | None = None
    # When True, inline images as data URIs so a downloaded HTML file works offline.
    embed_images: bool = False


@router.post("/{client_id}/content-production/site-preview")
async def content_production_site_preview(
    client_id: UUID,
    body: DraftSitePreviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Build a fresh client-branded site preview from a draft (not cached in chat history)."""
    await require_permission(user, db, "publishing", need_trigger=True)
    from app.services.publish_preview import build_draft_site_preview

    result = await db.execute(
        select(Client)
        .options(selectinload(Client.profile))
        .where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    if not body.markdown.strip():
        raise HTTPException(400, "Draft markdown is required")

    profile = client.profile
    site_architecture = (
        profile.site_architecture_summary if profile and profile.site_architecture_summary else {}
    )
    on_page = profile.on_page_seo_summary if profile and profile.on_page_seo_summary else {}
    planning = (
        profile.content_planning_summary if profile and profile.content_planning_summary else {}
    )
    demand = profile.search_demand_summary if profile and profile.search_demand_summary else {}
    topic_plan = {}
    if isinstance(demand, dict):
        topic_plan = demand.get("topic_plan") if isinstance(demand.get("topic_plan"), dict) else {}
        if not topic_plan and isinstance(demand.get("create_topic"), dict):
            topic_plan = demand.get("create_topic") or {}
    return await build_draft_site_preview(
        client_name=client.display_name,
        primary_url=client.primary_url,
        draft=body.model_dump(exclude={"embed_images"}),
        site_architecture=site_architecture if isinstance(site_architecture, dict) else {},
        on_page_seo=on_page if isinstance(on_page, dict) else {},
        content_planning=planning if isinstance(planning, dict) else {},
        topic_plan=topic_plan if isinstance(topic_plan, dict) else {},
        embed_images=bool(body.embed_images),
    )


@router.post("/ai-fill")
async def ai_fill_client_form(
    body: AiFillRequest,
    _: User = Depends(get_current_user),
):
    """Research a URL with Perplexity Pro and return non-personal client form fields."""
    from app.services.intake_ai import fill_client_form_from_url

    if not (body.url or "").strip():
        raise HTTPException(400, "URL is required")
    fields = await fill_client_form_from_url(body.url.strip())
    return {"fields": fields}


@router.get("", response_model=list[ClientOut])
async def list_clients(
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Client).order_by(Client.created_at.desc()).offset(offset).limit(limit)
    )
    return [client_out(c) for c in result.scalars().all()]


@router.post("", response_model=ClientDetail)
async def create_client(
    body: ClientCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_client_management),
):
    name = body.name.strip()
    client = Client(
        legal_name=name,
        display_name=name,
        primary_url=normalize_primary_url(body.primary_url),
        industry=body.industry,
        tier="B",
        status="onboarding",
        is_onboarding=body.is_onboarding,
    )
    db.add(client)
    await db.flush()
    intake = intake_from_body(body)
    profile = ClientDigitalProfile(
        client_id=client.id,
        is_onboarding=client.is_onboarding,
        marketing_context={"client_intake": intake} if intake else {},
    )
    db.add(profile)
    await log_event(
        db,
        client_id=client.id,
        actor_type="user",
        actor_id=user.id,
        event_type="client_created",
        event_detail={"name": client.display_name},
    )
    await db.flush()
    return ClientDetail(
        **client_out(client).model_dump(),
        profile=ProfileOut.model_validate(profile),
    )


@router.patch("/{client_id}", response_model=ClientDetail)
async def update_client(
    client_id: UUID,
    body: ClientUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_client_management),
):
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.profile))
        .where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "No fields to update")

    intake_patch = intake_from_body(body)
    core: dict = {}
    if "name" in data and data["name"] is not None:
        name = str(data["name"]).strip()
        client.legal_name = name
        client.display_name = name
        core["name"] = name
    for key in ("primary_url", "industry", "status", "is_onboarding"):
        if key in data:
            value = normalize_primary_url(data[key]) if key == "primary_url" else data[key]
            setattr(client, key, value)
            core[key] = value
    if "status" in data and "is_onboarding" not in data:
        client.is_onboarding = str(data["status"] or "").lower() == "onboarding"
        core["is_onboarding"] = client.is_onboarding

    if intake_patch and client.profile:
        ctx = dict(client.profile.marketing_context or {})
        existing = dict(ctx.get("client_intake") or {})
        existing.update(intake_patch)
        ctx["client_intake"] = existing
        client.profile.marketing_context = ctx

    await log_event(
        db,
        client_id=client.id,
        actor_type="user",
        actor_id=user.id,
        event_type="client_updated",
        event_detail={**core, **({"intake": intake_patch} if intake_patch else {})},
    )
    await db.flush()
    return ClientDetail(
        **client_out(client).model_dump(),
        profile=ProfileOut.model_validate(client.profile) if client.profile else None,
    )


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_client_management),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    # Structured log, not an AuditTrail row: AuditTrail.client_id is a hard FK
    # to clients.id, so a row logged here would either get wiped by the
    # AuditTrail cleanup below or (if left behind) block the Client delete
    # itself with the same FK-violation failure mode this function otherwise
    # has for ApiUsageLog (see the api_usage_log cleanup below).
    log.warning(
        "client_deleted",
        client_id=str(client_id),
        client_name=client.display_name,
        actor_id=str(user.id),
        actor_email=user.email,
    )

    # Every metered external API call logs a row here; without cleaning it up
    # first, the Client delete below raises a foreign-key violation on any
    # client that has ever incurred one (i.e. virtually every real client).
    await db.execute(delete(ApiUsageLog).where(ApiUsageLog.client_id == client_id))

    session_ids = list(
        (
            await db.execute(select(ChatSession.id).where(ChatSession.client_id == client_id))
        ).scalars().all()
    )

    # Website audits may FK to agent_jobs — remove before jobs
    await db.execute(delete(WebsiteAudit).where(WebsiteAudit.client_id == client_id))

    if session_ids:
        await db.execute(delete(PhaseValidation).where(PhaseValidation.session_id.in_(session_ids)))
        await db.execute(delete(AgentJob).where(AgentJob.session_id.in_(session_ids)))
        await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
        await db.execute(delete(ChatSession).where(ChatSession.id.in_(session_ids)))

    competitor_ids = list(
        (
            await db.execute(
                select(CompetitorProfile.id).where(CompetitorProfile.client_id == client_id)
            )
        ).scalars().all()
    )
    if competitor_ids:
        await db.execute(
            delete(BacklinkSnapshot).where(
                BacklinkSnapshot.competitor_profile_id.in_(competitor_ids)
            )
        )
    await db.execute(delete(BacklinkSnapshot).where(BacklinkSnapshot.client_id == client_id))
    await db.execute(delete(CompetitorProfile).where(CompetitorProfile.client_id == client_id))

    await db.execute(delete(PhaseValidation).where(PhaseValidation.client_id == client_id))
    await db.execute(delete(DiscoveryResponse).where(DiscoveryResponse.client_id == client_id))
    await db.execute(delete(TrackingAudit).where(TrackingAudit.client_id == client_id))
    await db.execute(delete(FindingsLedger).where(FindingsLedger.client_id == client_id))
    await db.execute(delete(ReadinessScore).where(ReadinessScore.client_id == client_id))
    await db.execute(delete(ApiCredential).where(ApiCredential.client_id == client_id))
    await db.execute(delete(AuditTrail).where(AuditTrail.client_id == client_id))
    await db.execute(
        delete(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
    )
    await db.execute(delete(Client).where(Client.id == client_id))
    await db.flush()
    return Response(status_code=204)


@router.get("/{client_id}", response_model=ClientDetail)
async def get_client(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.profile))
        .where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    return ClientDetail(
        **client_out(client).model_dump(),
        profile=ProfileOut.model_validate(client.profile) if client.profile else None,
    )


@router.get("/{client_id}/playground")
async def get_playground(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Shared Client Digital Profile memory, shaped for the logged-in role."""
    from app.services.role_skills import (
        AGENT_LABELS,
        build_playground_sections,
        permissions_for_role,
        role_label,
        triggerable_agents,
    )

    result = await db.execute(
        select(Client)
        .options(selectinload(Client.profile))
        .where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    if not client or not client.profile:
        raise HTTPException(404, "Client not found")

    role_name = user.role.name if user.role else ""
    return {
        "client": client_out(client).model_dump(),
        "role_name": role_name,
        "role_label": role_label(role_name),
        "permissions": permissions_for_role(role_name),
        "allowed_skills": [
            {"agent_key": a, "label": AGENT_LABELS.get(a, a)}
            for a in triggerable_agents(role_name)
        ],
        "sections": build_playground_sections(
            role_name,
            client_name=client.display_name,
            industry=client.industry,
            profile=client.profile,
        ),
    }


@router.post("/{client_id}/discovery/import-document")
async def import_discovery_document(
    client_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parse CDD spreadsheet / Word / PDF and write discovery_responses for questionnaire."""
    await require_permission(user, db, "discovery_agent", need_trigger=True)
    from decimal import Decimal

    from app.services.intake_ai import (
        extract_cdd_fields_from_text,
        extract_text_from_upload,
    )

    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 12MB)")

    filename = file.filename or "upload"
    try:
        # extract_text_from_upload is a synchronous, CPU-bound parser (XLSX/DOCX/
        # PDF up to 12MB) — running it inline would block the single event loop
        # thread for every other concurrent request (chat SSE streams included).
        text = await asyncio.to_thread(extract_text_from_upload, filename, raw)
    except Exception as exc:
        raise HTTPException(400, f"Could not read file: {exc}") from exc

    if not (text or "").strip():
        raise HTTPException(400, "No text extracted from file")

    fields = await extract_cdd_fields_from_text(text, filename=filename)

    # Validate BEFORE a version exists, so a malformed upload leaves the active
    # version — and the run — untouched.
    from app.services.cdd_versions import (
        build_staleness_report,
        create_cdd_version,
        validate_cdd_fields,
    )

    valid, validation_error = validate_cdd_fields(fields)
    if not valid:
        await log_event(
            db,
            client_id=client_id,
            actor_type="user",
            actor_id=user.id,
            event_type="cdd_document_rejected",
            event_detail={"filename": filename, "reason": validation_error},
        )
        raise HTTPException(400, validation_error or "Invalid CDD document")

    # Apply business name / URL / industry onto client when present.
    # Document stamps ("21/09/2026 (draft)") are CDD file titles, not names —
    # writing them here was renaming the client card on every upload.
    from app.services.intake_ai import _is_document_stamp

    if fields.get("business_name"):
        name = str(fields["business_name"]).strip()
        if name and not _is_document_stamp(name):
            client.legal_name = name
            client.display_name = name
    if fields.get("website_url"):
        url = str(fields["website_url"]).strip()
        if url:
            client.primary_url = normalize_primary_url(url)
    if fields.get("inferred_industry"):
        client.industry = str(fields["inferred_industry"]).strip()

    written: list[str] = []
    for key, value in fields.items():
        if key in ("business_name", "website_url"):
            continue
        row = DiscoveryResponse(
            client_id=client_id,
            source="cdd_upload",
            field_key=key,
            field_value={"value": value},
            confidence=Decimal("0.85"),
            discrepancy_flag=False,
            status="pending",
        )
        db.add(row)
        written.append(key)

    # Append an immutable version and refresh the CDD-derived slice of the
    # profile, so phases that have not started use the new CDD automatically.
    version = await create_cdd_version(
        db,
        client_id=client_id,
        fields=fields,
        source="upload",
        filename=filename,
        created_by=user.id,
    )
    await db.flush()
    staleness = await build_staleness_report(db, client_id=client_id)
    stale_phases = {
        agent: entry
        for agent, entry in staleness.items()
        if entry.get("state") in ("stale_direct", "stale_upstream")
    }

    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="cdd_document_imported",
        event_detail={
            "filename": filename,
            "fields": written,
            "cdd_version_no": version.version_no,
            "changed_keys": (version.diff or {}).get("changed_keys", []),
            "stale_phases": sorted(stale_phases),
        },
    )
    await db.flush()

    # Unlock the questionnaire immediately after CDD import (CDD-first Discovery).
    from app.agents.discovery import build_questionnaire_events_after_cdd
    from app.orchestration.pipeline import _persist_agent_events

    questionnaire_events = await build_questionnaire_events_after_cdd(db, client=client)
    session = (
        await db.execute(
            select(ChatSession)
            .where(ChatSession.client_id == client_id, ChatSession.user_id == user.id)
            .order_by(ChatSession.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session is not None:
        await _persist_agent_events(db, session, client_id, questionnaire_events)

    await db.flush()
    return {
        "ok": True,
        "filename": filename,
        "fields": {k: fields[k] for k in written if k in fields},
        "client": client_out(client).model_dump(),
        "cdd_version": {
            "id": str(version.id),
            "version_no": version.version_no,
            "created_at": version.created_at.isoformat() if version.created_at else None,
        },
        "diff": version.diff or {},
        "staleness": staleness,
        "stale_phases": sorted(stale_phases),
        "events": questionnaire_events,
        "questionnaire_card": next(
            (
                e.get("payload")
                for e in questionnaire_events
                if e.get("type") == "structured_card"
            ),
            None,
        ),
    }


class StalenessDismissRequest(BaseModel):
    agent_key: str


@router.get("/{client_id}/cdd-versions")
async def list_cdd_versions(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """CDD version history, newest last."""
    from app.services.cdd_versions import list_versions

    return {
        "versions": [
            {
                "id": str(v.id),
                "version_no": v.version_no,
                "source": v.source,
                "filename": v.filename,
                "changed_keys": (v.diff or {}).get("changed_keys", []),
                "rolled_back_from": str(v.rolled_back_from) if v.rolled_back_from else None,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in await list_versions(db, client_id)
        ]
    }


@router.get("/{client_id}/cdd-staleness")
async def get_cdd_staleness(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Which phases are stale against the latest CDD, why, and redo estimates."""
    from app.services.cdd_versions import active_version, build_staleness_report
    from app.services.cdd_redo import estimate_redo

    report = await build_staleness_report(db, client_id=client_id)
    version = await active_version(db, client_id)
    for agent, entry in report.items():
        if entry.get("state") in ("stale_direct", "stale_upstream"):
            entry["estimate"] = await estimate_redo(db, client_id=client_id, agent_key=agent)
    return {
        "active_version": (
            {"id": str(version.id), "version_no": version.version_no} if version else None
        ),
        "phases": report,
    }


@router.post("/{client_id}/cdd-staleness/dismiss")
async def dismiss_cdd_staleness(
    client_id: UUID,
    body: StalenessDismissRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Accept a phase as-is against the current CDD version."""
    from app.services.agent_handoff import PHASE_ORDER
    from app.services.cdd_versions import build_staleness_report, dismiss_staleness

    if body.agent_key not in PHASE_ORDER:
        raise HTTPException(400, f"Unknown phase {body.agent_key}")
    await require_permission(user, db, body.agent_key, need_approve=True)
    await dismiss_staleness(db, client_id=client_id, agent_key=body.agent_key)
    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="cdd_staleness_dismissed",
        event_detail={"agent_key": body.agent_key},
    )
    await db.flush()
    return {"ok": True, "phases": await build_staleness_report(db, client_id=client_id)}


@router.post("/{client_id}/cdd-versions/{version_id}/activate")
async def activate_cdd_version(
    client_id: UUID,
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_client_management),
):
    """Roll back by appending a copy of an older version. History is never mutated."""
    from app.services.cdd_versions import build_staleness_report, rollback_to_version

    try:
        version = await rollback_to_version(
            db, client_id=client_id, version_id=version_id, created_by=user.id
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="cdd_version_rolled_back",
        event_detail={"from": str(version_id), "new_version_no": version.version_no},
    )
    await db.flush()
    return {
        "ok": True,
        "cdd_version": {"id": str(version.id), "version_no": version.version_no},
        "phases": await build_staleness_report(db, client_id=client_id),
    }


class ServicePrioritizationUpdate(BaseModel):
    selected_service_ids: list[str] = []
    selected_subservice_ids: list[str] = []
    primary_service_ids: list[str] = []
    service_catalog: list[dict] = []
    competitor_tree_id: str | None = None
    adopt_competitor_service_ids: list[str] = []
    competitor_trees: list[dict] | None = None
    top_priority_service_id: str | None = None
    keyword_pool_target: int | None = None
    confirmed: bool = True


@router.get("/{client_id}/service-prioritization")
async def get_service_prioritization(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Build or return the service prioritization pack (pre–Phase 5 checkpoint)."""
    await require_permission(user, db, "search_demand", need_trigger=True)
    from app.agents.search_demand import _page_target_seeds, _primary_client_services
    from app.services.competitive_context import resolve_competitors
    from app.services.service_prioritization import (
        build_prioritization_pack,
        discover_competitor_service_trees,
    )

    result = await db.execute(
        select(Client)
        .options(selectinload(Client.profile))
        .where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    if not client or not client.profile:
        raise HTTPException(404, "Client not found")

    profile = client.profile
    commercial = dict(profile.commercial_scope or {})
    marketing = dict(profile.marketing_context or {})
    intake = dict(marketing.get("client_intake") or {})
    website = dict(profile.website_situation_summary or {})
    competitive = dict(profile.competitive_landscape_summary or {})
    # Card lists CDD top services only — not the full promotion/keyword union.
    services = _primary_client_services(commercial, marketing, intake, commercial)
    competitors = await resolve_competitors(
        db,
        client.id,
        competitive_summary=competitive,
        marketing=marketing,
        limit=8,
        prefer_confirmed=True,
    )
    trees = await discover_competitor_service_trees(competitors, client_id=str(client.id))
    pack = build_prioritization_pack(
        client_name=client.display_name,
        services=services,
        website=website,
        page_seeds=_page_target_seeds(website),
        competitors=competitors,
        competitor_trees=trees,
        existing=dict(commercial.get("service_prioritization") or {}),
    )
    return pack


@router.put("/{client_id}/service-prioritization")
async def save_service_prioritization(
    client_id: UUID,
    body: ServicePrioritizationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(
        user,
        db,
        "search_demand",
        need_trigger=not body.confirmed,
        need_approve=body.confirmed,
    )
    from app.services.service_prioritization import confirm_prioritization

    result = await db.execute(
        select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Profile not found")

    commercial = dict(profile.commercial_scope or {})
    payload = confirm_prioritization(
        {
            **body.model_dump(exclude_none=True),
            "selected_service_ids": body.selected_service_ids,
            "selected_subservice_ids": body.selected_subservice_ids,
            "primary_service_ids": body.primary_service_ids,
            "service_catalog": body.service_catalog,
            "competitor_tree_id": body.competitor_tree_id,
            "adopt_competitor_service_ids": body.adopt_competitor_service_ids,
            "competitor_trees": body.competitor_trees,
            "top_priority_service_id": body.top_priority_service_id,
        }
        if body.confirmed
        else body.model_dump(exclude={"confirmed"}, exclude_none=True)
    )
    if not body.confirmed:
        payload.pop("confirmed_at", None)
    commercial["service_prioritization"] = payload
    if body.keyword_pool_target is not None:
        commercial["keyword_pool_target"] = body.keyword_pool_target
    profile.commercial_scope = commercial
    if body.confirmed:
        profile.search_demand_status = "not_started"
    await db.flush()
    return {"ok": True, "service_prioritization": payload}


# --- content generation settings -------------------------------------------
#
# Same shape as service-prioritization above: the operator configures something a
# later phase consumes, GET returns the saved pack plus the catalogs the UI needs,
# PUT writes it to a JSON column on the profile.
#
# There is deliberately NO "generate now" route here. Content production runs
# through chat -> run_phase_with_validation, which applies the CDD staleness gate,
# the validation/revision loop and the findings ledger. A REST trigger would
# bypass all three. These endpoints only persist settings; the Phase 10/11 agent
# reads them at run time, exactly as Phase 5 reads service_prioritization.


class ContentSettingsUpdate(BaseModel):
    preset: str | None = None
    weight_overrides: dict[str, str] | None = None
    skill_override: str | None = None
    framework_choice: str | None = None
    selected_competitors_only: bool | None = None
    creative_mode: bool | None = None
    google_policy_surface: str | None = None
    google_policy_human_in_loop: bool | None = None
    google_policy_house_keyword_rule: str | None = None


def _content_settings_payload(commercial: dict) -> dict:
    from app.services.content_signals.framework import available_frameworks
    from app.services.content_signals.skills import available_skills
    from app.services.content_signals.weights import PRESETS, SIGNAL_NAMES, resolve_weights

    saved = dict(commercial.get("content_settings") or {})
    weights, preset = resolve_weights(saved.get("preset"), saved.get("weight_overrides"))
    return {
        "preset": preset,
        "weights": weights,
        "weight_overrides": saved.get("weight_overrides") or {},
        "skill_override": saved.get("skill_override"),
        "framework_choice": saved.get("framework_choice") or "auto",
        "selected_competitors_only": bool(saved.get("selected_competitors_only")),
        "creative_mode": bool(saved.get("creative_mode")),
        "google_policy_surface": saved.get("google_policy_surface") or "organic_search",
        "google_policy_human_in_loop": (
            True
            if saved.get("google_policy_human_in_loop") is None
            else bool(saved.get("google_policy_human_in_loop"))
        ),
        "google_policy_house_keyword_rule": saved.get("google_policy_house_keyword_rule") or None,
        "google_policy_surfaces": [
            "organic_search",
            "google_ads",
            "google_shopping",
            "google_play",
            "youtube",
            "google_business_profile",
        ],
        "available_presets": sorted(PRESETS),
        "available_skills": available_skills(),
        "available_frameworks": ["auto", *available_frameworks()],
        "signal_names": list(SIGNAL_NAMES),
        "weight_labels": ["off", "low", "medium", "high"],
    }


async def _content_profile_or_404(db: AsyncSession, client_id: UUID) -> ClientDigitalProfile:
    result = await db.execute(
        select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Profile not found")
    return profile


@router.get("/{client_id}/content-settings")
async def get_content_settings(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Saved signal settings plus the catalogs the UI builds its controls from."""
    profile = await _content_profile_or_404(db, client_id)
    return _content_settings_payload(dict(profile.commercial_scope or {}))


@router.put("/{client_id}/content-settings")
async def save_content_settings(
    client_id: UUID,
    body: ContentSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(user, db, "content_production", need_trigger=True)
    from app.services.content_signals.base import WEIGHTS
    from app.services.content_signals.framework import available_frameworks
    from app.services.content_signals.skills import available_skills
    from app.services.content_signals.weights import PRESETS, SIGNAL_NAMES

    profile = await _content_profile_or_404(db, client_id)
    saved = dict((profile.commercial_scope or {}).get("content_settings") or {})

    if body.preset is not None:
        if body.preset not in PRESETS:
            raise HTTPException(
                400, f"Unknown preset {body.preset!r}. Known: {sorted(PRESETS)}"
            )
        saved["preset"] = body.preset

    if body.weight_overrides is not None:
        for name, label in body.weight_overrides.items():
            if name not in SIGNAL_NAMES:
                raise HTTPException(
                    400, f"Unknown signal {name!r}. Known: {list(SIGNAL_NAMES)}"
                )
            if str(label).strip().lower() not in WEIGHTS:
                raise HTTPException(
                    400,
                    f"Unknown weight {label!r} for signal {name!r}. "
                    f"Use one of {sorted(WEIGHTS)}",
                )
        saved["weight_overrides"] = {
            k: str(v).strip().lower() for k, v in body.weight_overrides.items()
        }

    if body.skill_override is not None:
        stem = body.skill_override.strip().lower()
        if stem and stem not in available_skills():
            raise HTTPException(
                400, f"Unknown skill {stem!r}. Available: {available_skills()}"
            )
        saved["skill_override"] = stem or None

    if body.framework_choice is not None:
        choice = body.framework_choice.strip().lower() or "auto"
        if choice != "auto" and choice not in available_frameworks():
            raise HTTPException(
                400, f"Unknown framework {choice!r}. Available: {available_frameworks()}"
            )
        saved["framework_choice"] = choice

    if body.selected_competitors_only is not None:
        saved["selected_competitors_only"] = bool(body.selected_competitors_only)
    if body.creative_mode is not None:
        saved["creative_mode"] = bool(body.creative_mode)

    if body.google_policy_surface is not None:
        from app.services.google_policy_gate import SURFACES

        surface = body.google_policy_surface.strip().lower()
        if surface not in SURFACES:
            raise HTTPException(
                400,
                f"Unknown Google policy surface {surface!r}. Known: {sorted(SURFACES)}",
            )
        saved["google_policy_surface"] = surface
    if body.google_policy_human_in_loop is not None:
        saved["google_policy_human_in_loop"] = bool(body.google_policy_human_in_loop)
    if body.google_policy_house_keyword_rule is not None:
        rule = body.google_policy_house_keyword_rule.strip()
        saved["google_policy_house_keyword_rule"] = rule or None

    commercial = dict(profile.commercial_scope or {})
    commercial["content_settings"] = saved
    profile.commercial_scope = commercial
    await db.flush()
    return {"ok": True, "content_settings": _content_settings_payload(commercial)}


# --- business profile + offers ---------------------------------------------


class OfferIn(BaseModel):
    id: str | None = None
    name: str
    type: str | None = None
    description: str | None = None
    url: str | None = None
    cta: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    active: bool = True
    applies_to: list[str] = []
    locations: list[str] = []
    funnel_stages: list[str] = []
    priority: int = 3
    terms: str | None = None


class BusinessProfileUpdate(BaseModel):
    business_type: str | None = None
    business_size: str | None = None
    location_model: str | None = None
    locations_served: list[str] | None = None
    offers: list[OfferIn] | None = None


def _business_profile_payload(commercial: dict) -> dict:
    from app.services.content_signals.business_profile import (
        BUSINESS_TYPES,
        REACH_MODELS,
        SIZE_GUIDANCE,
    )
    from app.services.content_signals.offers import offer_is_active

    blob = dict(commercial.get("business_profile") or {})
    offers = []
    for offer in commercial.get("offers") or []:
        if not isinstance(offer, dict):
            continue
        ok, reason = offer_is_active(offer)
        offers.append({**offer, "currently_active": ok, "status_reason": reason})
    return {
        "business_type": blob.get("business_type"),
        "business_size": blob.get("business_size"),
        "location_model": blob.get("location_model"),
        "locations_served": list(blob.get("locations_served") or []),
        "offers": offers,
        "available_business_types": sorted(BUSINESS_TYPES),
        "available_business_sizes": sorted(SIZE_GUIDANCE),
        "available_location_models": sorted(REACH_MODELS),
        "funnel_stages": ["TOFU", "MOFU", "BOFU"],
    }


@router.get("/{client_id}/business-profile")
async def get_business_profile(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Business type/size/reach and offers, each offer flagged active or not."""
    profile = await _content_profile_or_404(db, client_id)
    return _business_profile_payload(dict(profile.commercial_scope or {}))


@router.put("/{client_id}/business-profile")
async def save_business_profile(
    client_id: UUID,
    body: BusinessProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Business facts are CDD data, so this is gated on the Discovery phase that
    # owns them, not on content production which only reads them.
    await require_permission(user, db, "discovery_agent", need_trigger=True)
    import uuid as _uuid

    from app.services.content_signals.business_profile import (
        BUSINESS_TYPES,
        REACH_MODELS,
        SIZE_GUIDANCE,
    )
    from app.services.content_signals.offers import FUNNEL_STAGES, parse_iso

    profile = await _content_profile_or_404(db, client_id)
    commercial = dict(profile.commercial_scope or {})
    blob = dict(commercial.get("business_profile") or {})

    if body.business_type is not None:
        value = body.business_type.strip().lower()
        if value and value not in BUSINESS_TYPES:
            raise HTTPException(
                400, f"Unknown business_type {value!r}. Known: {sorted(BUSINESS_TYPES)}"
            )
        blob["business_type"] = value or None
    if body.business_size is not None:
        value = body.business_size.strip().lower()
        if value and value not in SIZE_GUIDANCE:
            raise HTTPException(
                400, f"Unknown business_size {value!r}. Known: {sorted(SIZE_GUIDANCE)}"
            )
        blob["business_size"] = value or None
    if body.location_model is not None:
        value = body.location_model.strip().lower()
        if value and value not in REACH_MODELS:
            raise HTTPException(
                400, f"Unknown location_model {value!r}. Known: {sorted(REACH_MODELS)}"
            )
        blob["location_model"] = value or None
    if body.locations_served is not None:
        blob["locations_served"] = [s.strip() for s in body.locations_served if s.strip()]

    if body.offers is not None:
        cleaned: list[dict] = []
        for offer in body.offers:
            record = offer.model_dump()
            name = str(record.get("name") or "").strip()
            if not name:
                raise HTTPException(400, "Every offer needs a name.")
            record["name"] = name
            # Reject bad dates at the boundary. An unparseable date makes the
            # offer permanently inactive downstream, which is silent data loss.
            for field in ("starts_at", "ends_at"):
                raw = record.get(field)
                if raw and parse_iso(raw) is None:
                    raise HTTPException(
                        400,
                        f"Offer {name!r}: {field} {raw!r} is not ISO 8601 with timezone.",
                    )
            stages = [str(s).strip().upper() for s in (record.get("funnel_stages") or [])]
            for stage in stages:
                if stage not in FUNNEL_STAGES:
                    raise HTTPException(
                        400,
                        f"Offer {name!r}: unknown funnel stage {stage!r}. "
                        f"Use {list(FUNNEL_STAGES)}",
                    )
            record["funnel_stages"] = stages
            try:
                record["priority"] = min(3, max(1, int(record.get("priority") or 3)))
            except (TypeError, ValueError):
                raise HTTPException(400, f"Offer {name!r}: priority must be 1-3.") from None
            record["id"] = str(record.get("id") or "").strip() or _uuid.uuid4().hex[:12]
            cleaned.append(record)
        commercial["offers"] = cleaned

    commercial["business_profile"] = blob
    profile.commercial_scope = commercial
    await db.flush()
    return {"ok": True, **_business_profile_payload(commercial)}


# --- per-service competitors + benchmarking ---------------------------------
#
# Competitors are reusable and client-scoped (CompetitorProfile rows, which Phase
# 4 populates). The link to a service is JSON under
# commercial_scope.service_prioritization.service_competitors, because services
# themselves are catalog entries in that same blob rather than rows — see
# app/services/service_competitors.py for why.


class ServiceCompetitorSelection(BaseModel):
    competitor_ids: list[str] = []


class ServiceCompetitorCreate(BaseModel):
    name: str
    website: str
    notes: str | None = None
    attach_to_service: bool = True


class BenchmarkRequest(BaseModel):
    # Defaults to the competitors selected for the service (Feature 1).
    competitor_ids: list[str] | None = None
    target_keywords: list[str] | None = None
    # Live fetch is the only source of body text; off falls back to Phase 5 cache.
    allow_live_fetch: bool = True
    # competitor_id -> pasted content, highest-priority source.
    pasted: dict[str, str] | None = None


async def _client_with_profile(db: AsyncSession, client_id: UUID) -> Client:
    result = await db.execute(
        select(Client).options(selectinload(Client.profile)).where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    if not client or not client.profile:
        raise HTTPException(404, "Client not found")
    return client


async def _competitor_rows(db: AsyncSession, client_id: UUID) -> list[dict]:
    rows = (
        await db.execute(
            select(CompetitorProfile).where(CompetitorProfile.client_id == client_id)
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "url": row.url,
            "source": row.source,
            "confirmed": bool(row.confirmed),
            "notes": row.positioning_cluster or "",
        }
        for row in rows
    ]


@router.get("/{client_id}/services/{service_id}/competitors")
async def get_service_competitors(
    client_id: UUID,
    service_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Competitors selected for this service, plus every competitor available to pick."""
    from app.services.service_competitors import (
        competitor_domain,
        competitor_ids_for_service,
    )

    client = await _client_with_profile(db, client_id)
    commercial = dict(client.profile.commercial_scope or {})
    rows = await _competitor_rows(db, client_id)
    selected_ids = competitor_ids_for_service(commercial, service_id)

    available = [
        {**row, "domain": competitor_domain(row), "selected": row["id"] in selected_ids}
        for row in rows
    ]
    order = {cid: i for i, cid in enumerate(selected_ids)}
    return {
        "service_id": service_id,
        "selected_competitor_ids": selected_ids,
        "selected": sorted(
            (c for c in available if c["selected"]),
            key=lambda c: order.get(c["id"], 999),
        ),
        "available": available,
    }


@router.put("/{client_id}/services/{service_id}/competitors")
async def save_service_competitors(
    client_id: UUID,
    service_id: str,
    body: ServiceCompetitorSelection,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Replace this service's competitor selection. Order is preserved."""
    await require_permission(user, db, "content_production", need_trigger=True)
    from app.services.service_competitors import (
        competitor_ids_for_service,
        set_service_competitors,
    )

    client = await _client_with_profile(db, client_id)
    rows = await _competitor_rows(db, client_id)
    commercial = dict(client.profile.commercial_scope or {})
    try:
        commercial, warnings = set_service_competitors(
            commercial,
            service_id=service_id,
            competitor_ids=body.competitor_ids,
            known_ids={row["id"] for row in rows},
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    client.profile.commercial_scope = commercial
    await db.flush()
    return {
        "ok": True,
        "service_id": service_id,
        "selected_competitor_ids": competitor_ids_for_service(commercial, service_id),
        "warnings": warnings,
    }


@router.post("/{client_id}/services/{service_id}/competitors")
async def create_service_competitor(
    client_id: UUID,
    service_id: str,
    body: ServiceCompetitorCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a competitor inline and attach it to this service.

    Reuses an existing row when the domain already exists for this client, so the
    same competitor is never stored twice.
    """
    await require_permission(user, db, "content_production", need_trigger=True)
    from app.services.service_competitors import (
        InvalidCompetitorUrl,
        competitor_domain,
        competitor_ids_for_service,
        normalize_domain,
        set_service_competitors,
    )

    client = await _client_with_profile(db, client_id)
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(400, "A competitor name is required.")
    try:
        domain = normalize_domain(body.website)
    except InvalidCompetitorUrl as exc:
        raise HTTPException(400, str(exc)) from None

    existing_rows = (
        await db.execute(
            select(CompetitorProfile).where(CompetitorProfile.client_id == client_id)
        )
    ).scalars().all()
    reused = next(
        (r for r in existing_rows if competitor_domain({"url": r.url}) == domain), None
    )
    if reused is not None:
        competitor = reused
        created = False
        if body.notes and not competitor.positioning_cluster:
            competitor.positioning_cluster = body.notes.strip()[:500]
    else:
        competitor = CompetitorProfile(
            client_id=client_id,
            name=name,
            url=f"https://{domain}",
            source="manual",
            confirmed=True,
            positioning_cluster=(body.notes or "").strip()[:500] or None,
        )
        db.add(competitor)
        created = True
    await db.flush()

    warnings: list[str] = []
    if body.attach_to_service:
        commercial = dict(client.profile.commercial_scope or {})
        current = competitor_ids_for_service(commercial, service_id)
        if str(competitor.id) in current:
            warnings.append(f"{name} is already attached to this service.")
        else:
            current = [*current, str(competitor.id)]
        commercial, extra = set_service_competitors(
            commercial,
            service_id=service_id,
            competitor_ids=current,
            known_ids={str(r.id) for r in existing_rows} | {str(competitor.id)},
        )
        warnings.extend(extra)
        client.profile.commercial_scope = commercial
        await db.flush()

    return {
        "ok": True,
        "created": created,
        "competitor": {
            "id": str(competitor.id),
            "name": competitor.name,
            "url": competitor.url,
            "domain": domain,
            "notes": competitor.positioning_cluster or "",
            "source": competitor.source,
        },
        "warnings": warnings,
    }


@router.delete("/{client_id}/services/{service_id}/competitors/{competitor_id}")
async def detach_service_competitor(
    client_id: UUID,
    service_id: str,
    competitor_id: str,
    delete_everywhere: bool = Query(
        False, description="Also delete the competitor from the client, not just this service."
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Detach a competitor from this service; optionally delete it entirely."""
    await require_permission(user, db, "content_production", need_trigger=True)
    from app.services.service_competitors import (
        competitor_ids_for_service,
        detach_competitor_everywhere,
        set_service_competitors,
    )

    client = await _client_with_profile(db, client_id)
    commercial = dict(client.profile.commercial_scope or {})

    if delete_everywhere:
        await require_permission(user, db, "competitor_market_agent", need_trigger=True)
        commercial = detach_competitor_everywhere(commercial, competitor_id)
        await db.execute(
            delete(CompetitorProfile).where(
                CompetitorProfile.client_id == client_id,
                CompetitorProfile.id == competitor_id,
            )
        )
    else:
        remaining = [
            cid for cid in competitor_ids_for_service(commercial, service_id)
            if cid != competitor_id
        ]
        commercial, _ = set_service_competitors(
            commercial, service_id=service_id, competitor_ids=remaining
        )

    client.profile.commercial_scope = commercial
    await db.flush()
    return {
        "ok": True,
        "service_id": service_id,
        "deleted": bool(delete_everywhere),
        "selected_competitor_ids": competitor_ids_for_service(commercial, service_id),
    }


@router.post("/{client_id}/services/{service_id}/benchmark")
async def benchmark_service(
    client_id: UUID,
    service_id: str,
    body: BenchmarkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Benchmark our generated content for this service against its competitors.

    Defaults to the competitors selected in the service's competitor panel. A
    competitor whose content cannot be retrieved is reported as a failed row and
    the rest of the report still renders.
    """
    await require_permission(user, db, "content_production", need_trigger=True)
    from app.services.content_benchmark.report import run_benchmark
    from app.services.service_competitors import resolve_service_competitors

    client = await _client_with_profile(db, client_id)
    profile = client.profile
    commercial = dict(profile.commercial_scope or {})
    rows = await _competitor_rows(db, client_id)

    if body.competitor_ids:
        wanted = {str(c) for c in body.competitor_ids}
        competitors = [
            {**row, "domain": row["url"]} for row in rows if row["id"] in wanted
        ]
    else:
        competitors = resolve_service_competitors(
            commercial, service_id=service_id, competitor_rows=rows
        )

    service_name = _service_display_name(commercial, service_id)
    production = dict(profile.content_production_summary or {})
    our_content = _our_content_for_service(production, service_id, service_name)
    demand = dict(profile.search_demand_summary or {})

    report = await run_benchmark(
        service_id=service_id,
        service_name=service_name,
        our_content=our_content,
        competitors=competitors,
        target_keywords=body.target_keywords or _service_keywords(demand, service_name),
        snapshots=list(demand.get("competitor_sites") or []),
        pasted=body.pasted,
        allow_live=bool(body.allow_live_fetch),
    )
    return report


def _service_display_name(commercial: dict, service_id: str) -> str:
    prio = dict(commercial.get("service_prioritization") or {})
    for entry in prio.get("service_catalog") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id")) == service_id:
            return str(entry.get("name") or service_id)
        for sub in entry.get("subservices") or []:
            if isinstance(sub, dict) and str(sub.get("id")) == service_id:
                return str(sub.get("name") or service_id)
    return service_id.replace("-", " ")


def _our_content_for_service(
    production: dict, service_id: str, service_name: str
) -> dict:
    """The most relevant generated draft for this service, if one exists."""
    candidates: list[dict] = []
    for key in ("drafts", "pages", "content", "briefs"):
        value = production.get(key)
        if isinstance(value, list):
            candidates.extend(r for r in value if isinstance(r, dict))
    if isinstance(production.get("draft"), dict):
        candidates.append(production["draft"])

    wanted = {t for t in re.findall(r"[a-z0-9]+", service_name.lower()) if len(t) > 2}
    best, best_hits = None, -1
    for row in candidates:
        blob = " ".join(
            str(row.get(k) or "") for k in ("keyword", "title", "url", "service_id")
        ).lower()
        if str(row.get("service_id") or "") == service_id:
            return row
        hits = sum(1 for t in wanted if t in blob)
        if hits > best_hits:
            best, best_hits = row, hits
    return best or {}


def _service_keywords(demand: dict, service_name: str) -> list[str]:
    """Target keywords for this service from the Phase 5 pool."""
    wanted = {t for t in re.findall(r"[a-z0-9]+", service_name.lower()) if len(t) > 2}
    out: list[str] = []
    for row in demand.get("keyword_dataset") or []:
        if not isinstance(row, dict):
            continue
        if row.get("relevance_unverified"):
            continue
        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        tokens = {t for t in re.findall(r"[a-z0-9]+", keyword.lower()) if len(t) > 2}
        if wanted & tokens:
            out.append(keyword)
    return list(dict.fromkeys(out))[:25]


@router.get("/{client_id}/profile", response_model=ProfileOut)
async def get_profile_endpoint(
    client_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Profile not found")
    return profile


REPORT_EXPORT_FORMATS = {
    "pdf": ("application/pdf", "pdf"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
}


def _build_export_body(bundle: dict, *, fmt: str, single: bool) -> bytes:
    if fmt == "docx":
        from app.services.report_docx import build_reports_docx

        return build_reports_docx(bundle, single=single)
    from app.services.report_pdf import build_reports_pdf

    return build_reports_pdf(bundle, single=single)


@router.get("/{client_id}/reports/{card_type}/export")
async def export_single_client_report(
    client_id: UUID,
    card_type: str,
    format: str = "pdf",
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Download one structured phase report as a PDF or Word document."""
    from app.services.report_export import (
        build_report_export,
        find_report,
        report_filename_slug,
    )

    fmt = format.lower()
    if fmt not in REPORT_EXPORT_FORMATS:
        raise HTTPException(400, f"Unsupported format '{format}'. Use pdf or docx.")

    try:
        bundle = await build_report_export(db, client_id)
    except LookupError:
        raise HTTPException(404, "Client not found") from None

    report = find_report(bundle, card_type)
    if not report:
        raise HTTPException(404, f"Report not found: {card_type}")

    slug = bundle.get("filename_slug") or "client"
    stamp = str(bundle.get("exported_at") or "")[:10].replace("-", "") or "export"
    single_bundle = {**bundle, "reports": [report]}
    media_type, ext = REPORT_EXPORT_FORMATS[fmt]
    body = _build_export_body(single_bundle, fmt=fmt, single=True)
    title_slug = report_filename_slug(report, bundle.get("client"))
    filename = f"{slug}-{title_slug}-{stamp}.{ext}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{client_id}/reports/export")
async def export_client_reports(
    client_id: UUID,
    format: str = "pdf",
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Download all structured phase reports for a client as a PDF or Word document."""
    from app.services.report_export import build_report_export

    fmt = format.lower()
    if fmt not in REPORT_EXPORT_FORMATS:
        raise HTTPException(400, f"Unsupported format '{format}'. Use pdf or docx.")

    try:
        bundle = await build_report_export(db, client_id)
    except LookupError:
        raise HTTPException(404, "Client not found") from None

    slug = bundle.get("filename_slug") or "client"
    stamp = str(bundle.get("exported_at") or "")[:10].replace("-", "") or "export"
    media_type, ext = REPORT_EXPORT_FORMATS[fmt]
    body = _build_export_body(bundle, fmt=fmt, single=False)
    filename = f"{slug}-reports-{stamp}.{ext}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
