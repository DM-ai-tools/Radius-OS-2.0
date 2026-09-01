"""Add Phase 5/6 CDP fields

Revision ID: 002
Revises: 001
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "client_digital_profiles",
        sa.Column("search_demand_summary", sa.JSON(), nullable=True),
    )
    op.add_column(
        "client_digital_profiles",
        sa.Column("seo_strategy_summary", sa.JSON(), nullable=True),
    )
    op.add_column(
        "client_digital_profiles",
        sa.Column("search_demand_status", sa.Text(), server_default="not_started", nullable=False),
    )
    op.add_column(
        "client_digital_profiles",
        sa.Column("seo_strategy_status", sa.Text(), server_default="not_started", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("client_digital_profiles", "seo_strategy_status")
    op.drop_column("client_digital_profiles", "search_demand_status")
    op.drop_column("client_digital_profiles", "seo_strategy_summary")
    op.drop_column("client_digital_profiles", "search_demand_summary")
