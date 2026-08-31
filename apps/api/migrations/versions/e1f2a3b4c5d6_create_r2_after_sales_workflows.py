"""create R2 after-sales workflow, audit, idempotency and Mock OMS ledgers

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-08-25 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    action_type = postgresql.ENUM(
        "return",
        "replacement",
        "complaint",
        name="after_sales_action_type",
    )
    workflow_status = postgresql.ENUM(
        "draft",
        "policy_checked",
        "awaiting_customer_confirmation",
        "awaiting_agent_approval",
        "queued",
        "executing",
        "completed",
        "failed",
        "compensation_required",
        "cancelled",
        name="after_sales_workflow_status",
    )
    action_type.create(bind, checkfirst=True)
    workflow_status.create(bind, checkfirst=True)
    action_type_column = postgresql.ENUM(
        "return",
        "replacement",
        "complaint",
        name="after_sales_action_type",
        create_type=False,
    )
    workflow_status_column = postgresql.ENUM(
        "draft",
        "policy_checked",
        "awaiting_customer_confirmation",
        "awaiting_agent_approval",
        "queued",
        "executing",
        "completed",
        "failed",
        "compensation_required",
        "cancelled",
        name="after_sales_workflow_status",
        create_type=False,
    )

    op.create_table(
        "after_sales_workflows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("action_type", action_type_column, nullable=False),
        sa.Column("status", workflow_status_column, nullable=False),
        sa.Column("order_number", sa.String(length=128), nullable=False),
        sa.Column(
            "policy_snapshot",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "customer_summary",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("customer_confirmed_by", sa.String(), nullable=True),
        sa.Column("customer_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_approved_by", sa.String(), nullable=True),
        sa.Column("agent_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "execution_attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "reconciliation_attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "external_idempotency_key_hash", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "external_status",
            sa.String(length=48),
            server_default="not_started",
            nullable=False,
        ),
        sa.Column("external_reference", sa.String(length=160), nullable=True),
        sa.Column(
            "external_receipt",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "reconciliation_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("compensation_reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["customer_confirmed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_after_sales_workflows_tenant_id", ["tenant_id"]),
        ("ix_after_sales_workflows_customer_id", ["customer_id"]),
        ("ix_after_sales_workflows_ticket_id", ["ticket_id"]),
        ("ix_after_sales_workflows_status", ["status"]),
        ("ix_after_sales_workflows_created_by", ["created_by"]),
        (
            "ix_after_sales_workflows_execution_lease_expires_at",
            ["execution_lease_expires_at"],
        ),
    ):
        op.create_index(index_name, "after_sales_workflows", columns)

    op.create_table(
        "after_sales_workflow_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("from_status", sa.String(length=64), nullable=True),
        sa.Column("to_status", sa.String(length=64), nullable=True),
        sa.Column(
            "summary", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["after_sales_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_after_sales_workflow_events_workflow_id",
        "after_sales_workflow_events",
        ["workflow_id"],
    )

    op.create_table(
        "after_sales_idempotency_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(length=180), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["principal_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["after_sales_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            "operation",
            "idempotency_key_hash",
            name="uq_after_sales_idempotency_scope",
        ),
    )
    for index_name, columns in (
        ("ix_after_sales_idempotency_records_tenant_id", ["tenant_id"]),
        ("ix_after_sales_idempotency_records_principal_id", ["principal_id"]),
        ("ix_after_sales_idempotency_records_workflow_id", ["workflow_id"]),
    ):
        op.create_index(index_name, "after_sales_idempotency_records", columns)

    op.create_table(
        "mock_oms_operations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("action_type", action_type_column, nullable=False),
        sa.Column(
            "external_idempotency_key_hash", sa.String(length=64), nullable=False
        ),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("external_reference", sa.String(length=160), nullable=True),
        sa.Column(
            "receipt", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["after_sales_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "external_idempotency_key_hash",
            name="uq_mock_oms_operation_idempotency",
        ),
    )
    op.create_index(
        "ix_mock_oms_operations_tenant_id", "mock_oms_operations", ["tenant_id"]
    )
    op.create_index(
        "ix_mock_oms_operations_workflow_id", "mock_oms_operations", ["workflow_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mock_oms_operations_workflow_id", table_name="mock_oms_operations"
    )
    op.drop_index("ix_mock_oms_operations_tenant_id", table_name="mock_oms_operations")
    op.drop_table("mock_oms_operations")

    for index_name in (
        "ix_after_sales_idempotency_records_workflow_id",
        "ix_after_sales_idempotency_records_principal_id",
        "ix_after_sales_idempotency_records_tenant_id",
    ):
        op.drop_index(index_name, table_name="after_sales_idempotency_records")
    op.drop_table("after_sales_idempotency_records")

    op.drop_index(
        "ix_after_sales_workflow_events_workflow_id",
        table_name="after_sales_workflow_events",
    )
    op.drop_table("after_sales_workflow_events")

    for index_name in (
        "ix_after_sales_workflows_execution_lease_expires_at",
        "ix_after_sales_workflows_created_by",
        "ix_after_sales_workflows_status",
        "ix_after_sales_workflows_ticket_id",
        "ix_after_sales_workflows_customer_id",
        "ix_after_sales_workflows_tenant_id",
    ):
        op.drop_index(index_name, table_name="after_sales_workflows")
    op.drop_table("after_sales_workflows")

    bind = op.get_bind()
    postgresql.ENUM(name="after_sales_workflow_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="after_sales_action_type").drop(bind, checkfirst=True)
