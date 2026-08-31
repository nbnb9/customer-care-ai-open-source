"""add enterprise guards

Revision ID: 9f3a21d4c8b7
Revises: 51c53c77b620
Create Date: 2026-08-18 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3a21d4c8b7"
down_revision: str | Sequence[str] | None = "51c53c77b620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("resource_type", sa.String(), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("resource_id", sa.String(), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("before_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("after_summary", sa.Text(), nullable=True),
    )
    op.create_index(
        op.f("ix_audit_events_resource_id"),
        "audit_events",
        ["resource_id"],
        unique=False,
    )
    op.add_column(
        "triage_suggestions",
        sa.Column(
            "suggested_priority",
            sa.String(),
            server_default="normal",
            nullable=False,
        ),
    )
    op.add_column(
        "triage_suggestions",
        sa.Column("edited_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "triage_suggestions",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["principal_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            "operation",
            "idempotency_key_hash",
            name="uq_idempotency_records_scope",
        ),
    )
    op.create_index(
        op.f("ix_idempotency_records_principal_id"),
        "idempotency_records",
        ["principal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_idempotency_records_tenant_id"),
        "idempotency_records",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_idempotency_records_tenant_id"),
        table_name="idempotency_records",
    )
    op.drop_index(
        op.f("ix_idempotency_records_principal_id"),
        table_name="idempotency_records",
    )
    op.drop_table("idempotency_records")
    op.drop_column("triage_suggestions", "decided_at")
    op.drop_column("triage_suggestions", "edited_summary")
    op.drop_column("triage_suggestions", "suggested_priority")
    op.drop_index(op.f("ix_audit_events_resource_id"), table_name="audit_events")
    op.drop_column("audit_events", "after_summary")
    op.drop_column("audit_events", "before_summary")
    op.drop_column("audit_events", "resource_id")
    op.drop_column("audit_events", "resource_type")
