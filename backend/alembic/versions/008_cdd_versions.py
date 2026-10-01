"""CDD version history + per-phase run state.

Revision ID: 008
Revises: 007
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

from app.models.types import GUID, JSONType

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cdd_versions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("client_id", GUID(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="upload"),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("fields", JSONType(), nullable=True),
        sa.Column("diff", JSONType(), nullable=True),
        sa.Column("rolled_back_from", GUID(), nullable=True),
        sa.Column("created_by", GUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("client_id", "version_no", name="uq_cdd_version_no"),
    )
    op.create_index("ix_cdd_versions_client_id", "cdd_versions", ["client_id"])
    op.create_index("ix_cdd_versions_content_hash", "cdd_versions", ["content_hash"])
    op.create_index("ix_cdd_versions_created_at", "cdd_versions", ["created_at"])

    op.create_table(
        "phase_states",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("client_id", GUID(), sa.ForeignKey("clients.id"), nullable=False),
        # NOT NULL with a zero-UUID default: a nullable run_id would break the
        # uniqueness constraint (NULLs compare as distinct in Postgres) and make
        # real multi-run support a harder migration later.
        sa.Column("run_id", GUID(), nullable=False),
        sa.Column("agent_key", sa.Text(), nullable=False),
        sa.Column("cdd_version_id", GUID(), nullable=True),
        sa.Column("dismissed_through_version_id", GUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("last_duration_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("client_id", "run_id", "agent_key", name="uq_phase_state"),
    )
    op.create_index("ix_phase_states_client_id", "phase_states", ["client_id"])
    op.create_index("ix_phase_states_run_id", "phase_states", ["run_id"])
    op.create_index("ix_phase_states_agent_key", "phase_states", ["agent_key"])


def downgrade() -> None:
    op.drop_index("ix_phase_states_agent_key", table_name="phase_states")
    op.drop_index("ix_phase_states_run_id", table_name="phase_states")
    op.drop_index("ix_phase_states_client_id", table_name="phase_states")
    op.drop_table("phase_states")
    op.drop_index("ix_cdd_versions_created_at", table_name="cdd_versions")
    op.drop_index("ix_cdd_versions_content_hash", table_name="cdd_versions")
    op.drop_index("ix_cdd_versions_client_id", table_name="cdd_versions")
    op.drop_table("cdd_versions")
