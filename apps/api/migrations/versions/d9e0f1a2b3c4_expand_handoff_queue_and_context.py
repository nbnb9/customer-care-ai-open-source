"""expand handoff queue and minimized context package

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-08-25 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum values are append-only.  The new values are not used as a
    # default in this transaction, so they are safe to add before the columns.
    for value in ("claimed", "closed", "safe_flow_restored"):
        op.execute(
            f"ALTER TYPE conversation_handoff_status ADD VALUE IF NOT EXISTS '{value}'"
        )

    ticket_priority = postgresql.ENUM(
        "low",
        "normal",
        "high",
        "urgent",
        name="ticket_priority",
        create_type=False,
    )
    op.add_column(
        "conversation_handoffs",
        sa.Column(
            "priority",
            ticket_priority,
            nullable=False,
            server_default=sa.text("'normal'::ticket_priority"),
        ),
    )
    op.add_column(
        "conversation_handoffs",
        sa.Column(
            "target_queue",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'general-support'"),
        ),
    )
    op.add_column(
        "conversation_handoffs",
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE conversation_handoffs "
        "SET sla_due_at = created_at + INTERVAL '4 hours' "
        "WHERE sla_due_at IS NULL"
    )
    op.alter_column("conversation_handoffs", "sla_due_at", nullable=False)
    op.add_column(
        "conversation_handoffs",
        sa.Column("assigned_agent_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_handoffs_assigned_agent_id_users",
        "conversation_handoffs",
        "users",
        ["assigned_agent_id"],
        ["id"],
    )
    op.add_column(
        "conversation_handoffs",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_handoffs",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_handoffs",
        sa.Column(
            "context_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.create_index(
        "ix_conversation_handoffs_assigned_agent_id",
        "conversation_handoffs",
        ["assigned_agent_id"],
    )
    op.create_index(
        "ix_conversation_handoffs_sla_due_at",
        "conversation_handoffs",
        ["sla_due_at"],
    )
    op.create_index(
        "ix_conversation_handoffs_queue_status_sla",
        "conversation_handoffs",
        ["target_queue", "status", "sla_due_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_handoffs_queue_status_sla",
        table_name="conversation_handoffs",
    )
    op.drop_index(
        "ix_conversation_handoffs_sla_due_at", table_name="conversation_handoffs"
    )
    op.drop_index(
        "ix_conversation_handoffs_assigned_agent_id",
        table_name="conversation_handoffs",
    )
    op.drop_column("conversation_handoffs", "context_snapshot")
    op.drop_column("conversation_handoffs", "completed_at")
    op.drop_column("conversation_handoffs", "claimed_at")
    op.drop_constraint(
        "fk_conversation_handoffs_assigned_agent_id_users",
        "conversation_handoffs",
        type_="foreignkey",
    )
    op.drop_column("conversation_handoffs", "assigned_agent_id")
    op.drop_column("conversation_handoffs", "sla_due_at")
    op.drop_column("conversation_handoffs", "target_queue")
    op.drop_column("conversation_handoffs", "priority")

    # PostgreSQL cannot remove enum values in place.  Reject a rollback that
    # would silently corrupt a claimed or completed handoff, then rebuild the
    # original one-value enum.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM conversation_handoffs
                WHERE status::text IN ('claimed', 'closed', 'safe_flow_restored')
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: non-requested conversation handoffs still exist';
            END IF;
        END
        $$
        """
    )
    op.execute("ALTER TABLE conversation_handoffs ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE conversation_handoffs "
        "ALTER COLUMN status TYPE VARCHAR USING status::text"
    )
    op.execute("DROP TYPE conversation_handoff_status")
    op.execute("CREATE TYPE conversation_handoff_status AS ENUM ('requested')")
    op.execute(
        "ALTER TABLE conversation_handoffs ALTER COLUMN status "
        "TYPE conversation_handoff_status USING status::conversation_handoff_status"
    )
