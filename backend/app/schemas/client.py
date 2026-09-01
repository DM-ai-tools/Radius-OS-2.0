from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class ClientCreate(BaseModel):
    name: str
    primary_url: str
    industry: str | None = None
    # Optional early intake — stored on profile and used to pre-fill discovery
    primary_contact_name: str | None = None
    primary_contact_email: str | None = None
    primary_contact_phone: str | None = None
    company_size: str | None = None
    headquarters_location: str | None = None
    target_audience: str | None = None
    geographic_focus: str | None = None
    known_competitors: str | None = None
    objectives: list[str] | None = None
    website_priorities: str | None = None
    business_keywords: str | None = None
    b2b_b2c: str | None = None
    business_model: str | None = None


class ClientUpdate(BaseModel):
    name: str | None = None
    primary_url: str | None = None
    industry: str | None = None
    status: str | None = None
    primary_contact_name: str | None = None
    primary_contact_email: str | None = None
    primary_contact_phone: str | None = None
    company_size: str | None = None
    headquarters_location: str | None = None
    target_audience: str | None = None
    geographic_focus: str | None = None
    known_competitors: str | None = None
    objectives: list[str] | None = None
    website_priorities: str | None = None
    business_keywords: str | None = None
    b2b_b2c: str | None = None
    business_model: str | None = None


INTAKE_KEYS = (
    "primary_contact_name",
    "primary_contact_email",
    "primary_contact_phone",
    "company_size",
    "headquarters_location",
    "target_audience",
    "geographic_focus",
    "known_competitors",
    "objectives",
    "website_priorities",
    "business_keywords",
    "b2b_b2c",
    "business_model",
)


def intake_from_body(body: BaseModel) -> dict:
    data = body.model_dump(exclude_unset=True)
    out: dict = {}
    for key in INTAKE_KEYS:
        val = data.get(key)
        if val not in (None, "", [], {}):
            out[key] = val
    return out


class ClientOut(BaseModel):
    id: UUID
    name: str
    primary_url: str
    industry: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


def client_out(client) -> ClientOut:
    """Map DB columns (legal_name/display_name) to a single public name."""
    return ClientOut(
        id=client.id,
        name=client.display_name or client.legal_name,
        primary_url=client.primary_url,
        industry=client.industry,
        status=client.status,
        created_at=client.created_at,
    )


class ProfileOut(BaseModel):
    id: UUID
    client_id: UUID
    commercial_scope: dict | None
    marketing_context: dict | None
    tracking_baseline: dict | None
    website_situation_summary: dict | None
    competitive_landscape_summary: dict | None
    search_demand_summary: dict | None = None
    seo_strategy_summary: dict | None = None
    site_architecture_summary: dict | None = None
    technical_seo_summary: dict | None = None
    content_audit_summary: dict | None = None
    content_planning_summary: dict | None = None
    content_production_summary: dict | None = None
    on_page_seo_summary: dict | None = None
    publishing_summary: dict | None = None
    discovery_status: str
    tracking_status: str
    website_status: str
    competitor_status: str
    search_demand_status: str = "not_started"
    seo_strategy_status: str = "not_started"
    site_architecture_status: str = "not_started"
    technical_seo_status: str = "not_started"
    content_audit_status: str = "not_started"
    content_planning_status: str = "not_started"
    content_production_status: str = "not_started"
    on_page_seo_status: str = "not_started"
    publishing_status: str = "not_started"
    overall_readiness_score: Decimal | None
    ready_for_phase5: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientDetail(ClientOut):
    profile: ProfileOut | None = None
