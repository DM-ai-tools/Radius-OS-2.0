"""Add Phase 7–12 CDP fields

Revision ID: 004
Revises: 003
Create Date: 2026-08-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUMMARY_COLS = (
    "technical_seo_summary",
    "content_audit_summary",
    "content_planning_summary",
    "content_production_summary",
    "on_page_seo_summary",
    "publishing_summary",
)

_STATUS_COLS = (
    "technical_seo_status",
    "content_audit_status",
    "content_planning_status",
    "content_production_status",
    "on_page_seo_status",
    "publishing_status",
)


def upgrade() -> None:
    for col in _SUMMARY_COLS:
        op.add_column(
            "client_digital_profiles",
            sa.Column(col, sa.JSON(), nullable=True),
        )
    for col in _STATUS_COLS:
        op.add_column(
            "client_digital_profiles",
            sa.Column(
                col,
                sa.Text(),
                server_default="not_started",
                nullable=False,
            ),
        )


def downgrade() -> None:
    for col in reversed(_STATUS_COLS):
        op.drop_column("client_digital_profiles", col)
    for col in reversed(_SUMMARY_COLS):
        op.drop_column("client_digital_profiles", col)
