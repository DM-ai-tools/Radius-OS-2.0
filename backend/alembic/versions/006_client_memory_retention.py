"""Client-memory retention and onboarding exemption.

Revision ID: 006
Revises: 005
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "is_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE clients SET is_onboarding = "
            "CASE WHEN LOWER(status) = 'onboarding' THEN true ELSE false END"
        )
    )
    op.create_index("ix_clients_is_onboarding", "clients", ["is_onboarding"])

    op.add_column(
        "client_digital_profiles",
        sa.Column(
            "is_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_client_digital_profiles_is_onboarding",
        "client_digital_profiles",
        ["is_onboarding"],
    )
    op.add_column(
        "chat_sessions",
        sa.Column(
            "is_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE chat_sessions SET is_onboarding = "
            "CASE WHEN EXISTS (SELECT 1 FROM clients "
            "WHERE clients.id = chat_sessions.client_id "
            "AND clients.is_onboarding = true) THEN true ELSE false END"
        )
    )
    op.create_index(
        "ix_chat_sessions_is_onboarding",
        "chat_sessions",
        ["is_onboarding"],
    )

    for table in (
        "client_digital_profiles",
        "chat_sessions",
        "chat_messages",
        "agent_jobs",
    ):
        op.add_column(
            table,
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(f"ix_{table}_archived_at", table, ["archived_at"])


def downgrade() -> None:
    for table in reversed(
        (
            "client_digital_profiles",
            "chat_sessions",
            "chat_messages",
            "agent_jobs",
        )
    ):
        op.drop_index(f"ix_{table}_archived_at", table_name=table)
        op.drop_column(table, "archived_at")

    op.drop_index("ix_chat_sessions_is_onboarding", table_name="chat_sessions")
    op.drop_column("chat_sessions", "is_onboarding")
    op.drop_index(
        "ix_client_digital_profiles_is_onboarding",
        table_name="client_digital_profiles",
    )
    op.drop_column("client_digital_profiles", "is_onboarding")
    op.drop_index("ix_clients_is_onboarding", table_name="clients")
    op.drop_column("clients", "is_onboarding")
