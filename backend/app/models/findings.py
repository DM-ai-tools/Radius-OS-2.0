import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID, JSONType


class DiscoveryResponse(Base):
    __tablename__ = "discovery_responses"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("clients.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    field_key: Mapped[str] = mapped_column(Text, nullable=False)
    field_value: Mapped[dict | None] = mapped_column(JSONType)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    discrepancy_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text, default="pending")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TrackingAudit(Base):
    __tablename__ = "tracking_audits"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("clients.id"), nullable=False)
    element: Mapped[str] = mapped_column(Text, nullable=False)
    check_result: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONType)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(Text, default="pending")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"))


class WebsiteAudit(Base):
    __tablename__ = "website_audits"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("clients.id"), nullable=False)
    audit_type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[dict | None] = mapped_column(JSONType)
    severity: Mapped[str] = mapped_column(Text, default="info")
    linked_job_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("agent_jobs.id"))
    status: Mapped[str] = mapped_column(Text, default="pending")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CompetitorProfile(Base):
    __tablename__ = "competitor_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    positioning_cluster: Mapped[str | None] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BacklinkSnapshot(Base):
    __tablename__ = "backlink_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("clients.id"))
    competitor_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("competitor_profiles.id")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    referring_domains: Mapped[int] = mapped_column(Integer, nullable=False)
    authority_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    spam_risk_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    top_anchor_text: Mapped[dict | None] = mapped_column(JSONType)
    pulled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CompetitorRanking(Base):
    __tablename__ = "competitor_rankings"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    competitor_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("competitor_profiles.id"), nullable=False, index=True
    )
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    search_volume: Mapped[int | None] = mapped_column(Integer)
    client_position: Mapped[int | None] = mapped_column(Integer)
    gap_flag: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
