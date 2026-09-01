"""Add site_architecture CDP fields

Revision ID: 003
Revises: 002
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "client_digital_profiles",
        sa.Column("site_architecture_summary", sa.JSON(), nullable=True),
    )
    op.add_column(
        "client_digital_profiles",
        sa.Column(
            "site_architecture_status",
            sa.Text(),
            server_default="not_started",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("client_digital_profiles", "site_architecture_status")
    op.drop_column("client_digital_profiles", "site_architecture_summary")
