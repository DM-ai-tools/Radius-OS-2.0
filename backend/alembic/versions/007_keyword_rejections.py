"""Keyword rejection log — full record of gate exclusions.

Revision ID: 007
Revises: 006
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

from app.models.types import GUID

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "keyword_rejections",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "client_id",
            GUID(),
            sa.ForeignKey("clients.id"),
            nullable=False,
        ),
        sa.Column("run_id", GUID(), nullable=True),
        sa.Column("agent_key", sa.Text(), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("seed", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_label", sa.Text(), nullable=True),
        sa.Column("match_class", sa.Text(), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_keyword_rejections_client_id", "keyword_rejections", ["client_id"]
    )
    op.create_index("ix_keyword_rejections_run_id", "keyword_rejections", ["run_id"])
    op.create_index(
        "ix_keyword_rejections_agent_key", "keyword_rejections", ["agent_key"]
    )
    op.create_index("ix_keyword_rejections_keyword", "keyword_rejections", ["keyword"])
    op.create_index("ix_keyword_rejections_reason", "keyword_rejections", ["reason"])
    op.create_index(
        "ix_keyword_rejections_source_path", "keyword_rejections", ["source_path"]
    )
    op.create_index(
        "ix_keyword_rejections_created_at", "keyword_rejections", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_keyword_rejections_created_at", table_name="keyword_rejections")
    op.drop_index("ix_keyword_rejections_source_path", table_name="keyword_rejections")
    op.drop_index("ix_keyword_rejections_reason", table_name="keyword_rejections")
    op.drop_index("ix_keyword_rejections_keyword", table_name="keyword_rejections")
    op.drop_index("ix_keyword_rejections_agent_key", table_name="keyword_rejections")
    op.drop_index("ix_keyword_rejections_run_id", table_name="keyword_rejections")
    op.drop_index("ix_keyword_rejections_client_id", table_name="keyword_rejections")
    op.drop_table("keyword_rejections")
