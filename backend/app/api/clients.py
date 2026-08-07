from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.deps import get_current_user
from app.models import (
    AgentJob,
    ApiCredential,
    AuditTrail,
    BacklinkSnapshot,
    ChatMessage,
    ChatSession,
    Client,
    ClientDigitalProfile,
    CompetitorProfile,
    CompetitorRanking,
    DiscoveryResponse,
    FindingsLedger,
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

router = APIRouter(prefix="/clients", tags=["clients"])


class AiFillRequest(BaseModel):
    url: str


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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Client).order_by(Client.created_at.desc()))
    return [client_out(c) for c in result.scalars().all()]


@router.post("", response_model=ClientDetail)
async def create_client(
    body: ClientCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = body.name.strip()
    client = Client(
        legal_name=name,
        display_name=name,
        primary_url=body.primary_url,
        industry=body.industry,
        tier="B",
        status="onboarding",
    )
    db.add(client)
    await db.flush()
    intake = intake_from_body(body)
    profile = ClientDigitalProfile(
        client_id=client.id,
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
    user: User = Depends(get_current_user),
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
    for key in ("primary_url", "industry", "status"):
        if key in data:
            setattr(client, key, data[key])
            core[key] = data[key]

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
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    session_ids = list(
        (
            await db.execute(select(ChatSession.id).where(ChatSession.client_id == client_id))
        ).scalars().all()
    )

    # Website audits may FK to agent_jobs — remove before jobs
    await db.execute(delete(WebsiteAudit).where(WebsiteAudit.client_id == client_id))

    if session_ids:
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
            delete(CompetitorRanking).where(
                CompetitorRanking.competitor_profile_id.in_(competitor_ids)
            )
        )
        await db.execute(
            delete(BacklinkSnapshot).where(
                BacklinkSnapshot.competitor_profile_id.in_(competitor_ids)
            )
        )
    await db.execute(delete(BacklinkSnapshot).where(BacklinkSnapshot.client_id == client_id))
    await db.execute(delete(CompetitorProfile).where(CompetitorProfile.client_id == client_id))

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
    from decimal import Decimal

    from app.services.intake_ai import extract_cdd_fields_from_text, extract_text_from_upload

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
        text = extract_text_from_upload(filename, raw)
    except Exception as exc:  # noqa: BLE001
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
            client.primary_url = url if url.startswith("http") else f"https://{url}"
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


@router.get("/{client_id}/profile", response_model=ProfileOut)
async def get_profile(
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
