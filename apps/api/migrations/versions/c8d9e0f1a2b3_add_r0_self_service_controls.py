"""add R0 self-service controls and conversation feedback

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-24 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("response_kind", sa.String(length=32), nullable=True),
    )

    op.create_table(
        "r0_self_service_feature_flags",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "allow_all_customers",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "customer_ids",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(), nullable=True),
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
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    op.create_table(
        "conversation_feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("answer_message_id", sa.String(), nullable=False),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("feedback", sa.String(length=16), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
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
        sa.ForeignKeyConstraint(["answer_message_id"], ["conversation_messages.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "answer_message_id",
            "customer_id",
            name="uq_conversation_feedback_customer_answer",
        ),
    )
    op.create_index(
        "ix_conversation_feedback_conversation_id",
        "conversation_feedback",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_feedback_answer_message_id",
        "conversation_feedback",
        ["answer_message_id"],
    )
    op.create_index(
        "ix_conversation_feedback_customer_id",
        "conversation_feedback",
        ["customer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_feedback_customer_id", table_name="conversation_feedback"
    )
    op.drop_index(
        "ix_conversation_feedback_answer_message_id",
        table_name="conversation_feedback",
    )
    op.drop_index(
        "ix_conversation_feedback_conversation_id", table_name="conversation_feedback"
    )
    op.drop_table("conversation_feedback")
    op.drop_table("r0_self_service_feature_flags")
    op.drop_column("conversation_messages", "response_kind")
