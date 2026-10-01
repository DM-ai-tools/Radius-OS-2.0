import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID, JSONType


class FindingsLedger(Base):
    __tablename__ = "findings_ledger"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, index=True
    )
    agent_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_table: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditTrail(Base):
    __tablename__ = "audit_trail"

    id: Mapped[int] = mapped_column(Integer().with_variant(BigInteger(), "postgresql"), primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_detail: Mapped[dict | None] = mapped_column(JSONType)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ReadinessScore(Base):
    __tablename__ = "readiness_scores"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, index=True
    )
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    missing_fields: Mapped[dict | None] = mapped_column(JSONType)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Sentinel run id for the single active run per client. Uniqueness constraints
# need a real value here — a NULL run_id would compare as distinct in Postgres
# and let duplicate phase_state rows through. When real multi-run support
# arrives, existing rows keep this id as "the original run".
DEFAULT_RUN_ID = uuid.UUID(int=0)


class CddVersion(Base):
    """An immutable snapshot of a client's Client Discovery Document.

    Client-scoped on purpose: the CDD is the client's document, and future
    parallel runs would share its history rather than forking it. Run-scoped
    state lives on ``PhaseState`` instead.
    """

    __tablename__ = "cdd_versions"
    __table_args__ = (
        UniqueConstraint("client_id", "version_no", name="uq_cdd_version_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="upload")
    content_hash: Mapped[str | None] = mapped_column(Text, index=True)
    filename: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    # The canonical field snapshot this version represents.
    fields: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    # Diff against the preceding version, stored so staleness does not have to
    # recompute the whole chain on every request.
    diff: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    rolled_back_from: Mapped[uuid.UUID | None] = mapped_column(GUID())
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class PhaseState(Base):
    """Per-phase run bookkeeping: which CDD version produced this output.

    Deliberately NOT more columns on ``client_digital_profiles`` — that table is
    one row per client by construction, so anything added there would have to be
    unpicked to support multiple runs. Keying on (client_id, run_id, agent_key)
    from the start means adding real runs is a backfill, not a rewrite.
    """

    __tablename__ = "phase_states"
    __table_args__ = (
        UniqueConstraint("client_id", "run_id", "agent_key", name="uq_phase_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, default=DEFAULT_RUN_ID, index=True
    )
    agent_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # The CDD version captured when this phase STARTED, not when it finished —
    # a phase that was running when a new version landed keeps the older id and
    # therefore reads as stale once it completes.
    cdd_version_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    dismissed_through_version_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    last_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KeywordRejection(Base):
    """Every keyword the relevance gate rejected, and why.

    The in-memory cleaning audit only ever kept a 12-row sample, so there was no
    way to tune the gate against real rejections or to spot a false negative
    after the fact. This table keeps the full set.
    """

    __tablename__ = "keyword_rejections"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    agent_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    seed: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Coarse bucket: provider | fallback | competitor | chat | opportunity | unknown
    source_path: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # The fine-grained source_label the filter was called with.
    source_label: Mapped[str | None] = mapped_column(Text)
    match_class: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class PhaseValidation(Base):
    """Traceable QC result for a phase output iteration."""

    __tablename__ = "phase_validations"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("clients.id"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    agent_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decision: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    result: Mapped[dict | None] = mapped_column(JSONType)
    output_fingerprint: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
