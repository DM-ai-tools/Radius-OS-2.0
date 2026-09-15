"""Phase validations table (company-aware QC history).

Revision ID: 005
Revises: 004
Create Date: 2026-08-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "phase_validations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("chat_sessions.id"), nullable=True),
        sa.Column("agent_key", sa.Text(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("output_fingerprint", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_phase_validations_client_id", "phase_validations", ["client_id"])
    op.create_index("ix_phase_validations_session_id", "phase_validations", ["session_id"])
    op.create_index("ix_phase_validations_agent_key", "phase_validations", ["agent_key"])
    op.create_index("ix_phase_validations_decision", "phase_validations", ["decision"])
    op.create_index("ix_phase_validations_created_at", "phase_validations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_phase_validations_created_at", table_name="phase_validations")
    op.drop_index("ix_phase_validations_decision", table_name="phase_validations")
    op.drop_index("ix_phase_validations_agent_key", table_name="phase_validations")
    op.drop_index("ix_phase_validations_session_id", table_name="phase_validations")
    op.drop_index("ix_phase_validations_client_id", table_name="phase_validations")
    op.drop_table("phase_validations")
