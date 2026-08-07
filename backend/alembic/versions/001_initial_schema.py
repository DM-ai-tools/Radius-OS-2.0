"""Initial SearchFit Phase 1–4 schema

Revision ID: 001
Revises:
Create Date: 2026-08-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables are created via SQLAlchemy metadata.create_all on startup for SQLite.
    # This migration documents the schema for PostgreSQL deployments.
    # Run: alembic upgrade head against Postgres after enabling pgvector.
    pass


def downgrade() -> None:
    pass
