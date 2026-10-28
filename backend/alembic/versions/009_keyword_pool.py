"""Cross-client keyword pool — place-free keyword fetches and their entries.

Revision ID: 009
Revises: 008
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

from app.models.types import GUID, JSONType

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "keyword_pool_fetches",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("seed_key", sa.Text(), nullable=False),
        sa.Column("seed_text", sa.Text(), nullable=False),
        sa.Column("concept_key", sa.Text(), nullable=True),
        sa.Column("industry_key", sa.Text(), nullable=True),
        sa.Column("location_code", sa.Integer(), nullable=False),
        sa.Column("language_code", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("min_volume", sa.Integer(), nullable=False),
        sa.Column("row_limit", sa.Integer(), nullable=False),
        sa.Column("errors", JSONType(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "kind",
            "seed_key",
            "location_code",
            "language_code",
            name="uq_keyword_pool_fetch",
        ),
    )

    op.create_table(
        "keyword_pool_entries",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "fetch_id",
            GUID(),
            sa.ForeignKey("keyword_pool_fetches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keyword_norm", sa.Text(), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("row", JSONType(), nullable=False),
        sa.UniqueConstraint("fetch_id", "keyword_norm", name="uq_keyword_pool_entry"),
    )
    op.create_index(
        "ix_keyword_pool_entries_fetch_id", "keyword_pool_entries", ["fetch_id"]
    )
    op.create_index(
        "ix_keyword_pool_entries_keyword_norm", "keyword_pool_entries", ["keyword_norm"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_keyword_pool_entries_keyword_norm", table_name="keyword_pool_entries"
    )
    op.drop_index("ix_keyword_pool_entries_fetch_id", table_name="keyword_pool_entries")
    op.drop_table("keyword_pool_entries")
    op.drop_table("keyword_pool_fetches")
