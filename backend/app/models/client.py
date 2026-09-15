import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.types import GUID, JSONType


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    primary_url: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(Text, nullable=False, default="B")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="onboarding")
    is_onboarding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    profile: Mapped["ClientDigitalProfile"] = relationship(
        back_populates="client", uselist=False
    )


class ClientDigitalProfile(Base):
    __tablename__ = "client_digital_profiles"
    __table_args__ = (UniqueConstraint("client_id", name="uq_cdp_client"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, unique=True
    )
    commercial_scope: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    marketing_context: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    tracking_baseline: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    website_situation_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    competitive_landscape_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    search_demand_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    seo_strategy_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    site_architecture_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    technical_seo_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    content_audit_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    content_planning_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    content_production_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    on_page_seo_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    publishing_summary: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    is_onboarding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    discovery_status: Mapped[str] = mapped_column(Text, default="not_started")
    tracking_status: Mapped[str] = mapped_column(Text, default="not_started")
    website_status: Mapped[str] = mapped_column(Text, default="not_started")
    competitor_status: Mapped[str] = mapped_column(Text, default="not_started")
    search_demand_status: Mapped[str] = mapped_column(Text, default="not_started")
    seo_strategy_status: Mapped[str] = mapped_column(Text, default="not_started")
    site_architecture_status: Mapped[str] = mapped_column(Text, default="not_started")
    technical_seo_status: Mapped[str] = mapped_column(Text, default="not_started")
    content_audit_status: Mapped[str] = mapped_column(Text, default="not_started")
    content_planning_status: Mapped[str] = mapped_column(Text, default="not_started")
    content_production_status: Mapped[str] = mapped_column(Text, default="not_started")
    on_page_seo_status: Mapped[str] = mapped_column(Text, default="not_started")
    publishing_status: Mapped[str] = mapped_column(Text, default="not_started")
    overall_readiness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    ready_for_phase5: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client: Mapped["Client"] = relationship(back_populates="profile")
