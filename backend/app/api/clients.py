import asyncio
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
    return await build_draft_site_preview(
        client_name=client.display_name,
        primary_url=client.primary_url,
        draft=body.model_dump(),
        site_architecture=site_architecture if isinstance(site_architecture, dict) else {},
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

    # Apply business name / URL / industry onto client when present
    if fields.get("business_name"):
        name = str(fields["business_name"]).strip()
        if name:
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

    await log_event(
        db,
        client_id=client_id,
        actor_type="user",
        actor_id=user.id,
        event_type="cdd_document_imported",
        event_detail={"filename": filename, "fields": written},
    )
    await db.flush()
    return {
        "ok": True,
        "filename": filename,
        "fields": {k: fields[k] for k in written if k in fields},
        "client": client_out(client).model_dump(),
    }


class ServicePrioritizationUpdate(BaseModel):
    selected_service_ids: list[str] = []
    selected_subservice_ids: list[str] = []
    primary_service_ids: list[str] = []
    service_catalog: list[dict] = []
    competitor_tree_id: str | None = None
    adopt_competitor_service_ids: list[str] = []
    competitor_trees: list[dict] | None = None
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
    from app.agents.search_demand import _page_target_seeds, _products_list
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
    services = _products_list(commercial, marketing, intake, commercial)
    competitors = await resolve_competitors(
        db,
        client.id,
        competitive_summary=competitive,
        marketing=marketing,
        limit=8,
        prefer_confirmed=True,
    )
    trees = await discover_competitor_service_trees(competitors)
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
