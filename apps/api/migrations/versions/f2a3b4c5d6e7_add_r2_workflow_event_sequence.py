"""add durable per-workflow sequence to R2 after-sales events

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-25 13:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "after_sales_workflows",
        sa.Column(
            "last_event_sequence", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "after_sales_workflow_events",
        sa.Column("sequence", sa.Integer(), nullable=True),
    )
    # Existing Local/CI records predate the cursor. Give them a deterministic
    # historical order, then let the application allocate all future values
    # while holding the workflow row lock.
    op.execute(
        """
        WITH numbered AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY workflow_id
                       ORDER BY created_at, id
                   ) AS sequence
            FROM after_sales_workflow_events
        )
        UPDATE after_sales_workflow_events AS event
        SET sequence = numbered.sequence
        FROM numbered
        WHERE event.id = numbered.id
        """
    )
    op.alter_column("after_sales_workflow_events", "sequence", nullable=False)
    op.execute(
        """
        UPDATE after_sales_workflows AS workflow
        SET last_event_sequence = COALESCE(
            (
                SELECT max(event.sequence)
                FROM after_sales_workflow_events AS event
                WHERE event.workflow_id = workflow.id
            ),
            0
        )
        """
    )
    op.create_unique_constraint(
        "uq_after_sales_workflow_event_sequence",
        "after_sales_workflow_events",
        ["workflow_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_after_sales_workflow_event_sequence",
        "after_sales_workflow_events",
        type_="unique",
    )
    op.drop_column("after_sales_workflow_events", "sequence")
    op.drop_column("after_sales_workflows", "last_event_sequence")
