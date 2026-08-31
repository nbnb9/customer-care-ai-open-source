"""add step39 observability

Revision ID: e9a5daefcaa7
Revises: a3b4c5d6e7f8
Create Date: 2026-08-25 12:55:36.666898

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9a5daefcaa7"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "observability_spans",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_span_id", sa.String(length=16), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("conversation_id", sa.String(length=128), nullable=True),
        sa.Column("ticket_id", sa.String(length=128), nullable=True),
        sa.Column("workflow_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trace_id", "span_id", name="uq_observability_trace_span"),
    )
    op.create_index(
        op.f("ix_observability_spans_started_at"),
        "observability_spans",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_observability_spans_tenant_request_started",
        "observability_spans",
        ["tenant_id", "request_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_observability_spans_tenant_trace_started",
        "observability_spans",
        ["tenant_id", "trace_id", "started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_observability_spans_trace_id"),
        "observability_spans",
        ["trace_id"],
        unique=False,
    )
    op.add_column(
        "after_sales_workflows",
        sa.Column("observability_traceparent", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "knowledge_imports",
        sa.Column("observability_traceparent", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("knowledge_imports", "observability_traceparent")
    op.drop_column("after_sales_workflows", "observability_traceparent")
    op.drop_index(
        op.f("ix_observability_spans_trace_id"),
        table_name="observability_spans",
    )
    op.drop_index(
        "ix_observability_spans_tenant_trace_started",
        table_name="observability_spans",
    )
    op.drop_index(
        "ix_observability_spans_tenant_request_started",
        table_name="observability_spans",
    )
    op.drop_index(
        op.f("ix_observability_spans_started_at"),
        table_name="observability_spans",
    )
    op.drop_table("observability_spans")
