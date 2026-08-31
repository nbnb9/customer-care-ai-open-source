"""create customer conversations and durable SSE events

Revision ID: b7c8d9e0f1a2
Revises: f6a7b8c9d0e
Create Date: 2026-08-24 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    conversation_status = postgresql.ENUM(
        "active",
        "awaiting_customer",
        "awaiting_agent",
        "handed_off",
        "closed",
        "blocked_for_safety",
        name="conversation_status",
    )
    conversation_actor = postgresql.ENUM(
        "customer", "assistant", "system", name="conversation_actor"
    )
    handoff_status = postgresql.ENUM("requested", name="conversation_handoff_status")
    conversation_status.create(bind, checkfirst=True)
    conversation_actor.create(bind, checkfirst=True)
    handoff_status.create(bind, checkfirst=True)
    conversation_status_column = postgresql.ENUM(
        "active",
        "awaiting_customer",
        "awaiting_agent",
        "handed_off",
        "closed",
        "blocked_for_safety",
        name="conversation_status",
        create_type=False,
    )
    conversation_actor_column = postgresql.ENUM(
        "customer", "assistant", "system", name="conversation_actor", create_type=False
    )
    handoff_status_column = postgresql.ENUM(
        "requested", name="conversation_handoff_status", create_type=False
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("status", conversation_status_column, nullable=False),
        sa.Column(
            "last_sequence", sa.Integer(), server_default=sa.text("0"), nullable=False
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
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("actor", conversation_actor_column, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("reply_to_message_id", sa.String(), nullable=True),
        sa.Column("generation_status", sa.String(length=32), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["reply_to_message_id"], ["conversation_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_messages_conversation_id",
        "conversation_messages",
        ["conversation_id"],
    )

    op.create_table(
        "conversation_events",
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_events_sequence"
        ),
    )
    op.create_index(
        "ix_conversation_events_conversation_id",
        "conversation_events",
        ["conversation_id"],
    )

    op.create_table(
        "conversation_handoffs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("status", handoff_status_column, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_handoffs_conversation_id",
        "conversation_handoffs",
        ["conversation_id"],
    )

    op.create_table(
        "conversation_idempotency_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(length=180), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["principal_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            "operation",
            "idempotency_key_hash",
            name="uq_conversation_idempotency_scope",
        ),
    )
    op.create_index(
        "ix_conversation_idempotency_records_tenant_id",
        "conversation_idempotency_records",
        ["tenant_id"],
    )
    op.create_index(
        "ix_conversation_idempotency_records_principal_id",
        "conversation_idempotency_records",
        ["principal_id"],
    )
    op.create_index(
        "ix_conversation_idempotency_records_conversation_id",
        "conversation_idempotency_records",
        ["conversation_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    for index_name in (
        "ix_conversation_idempotency_records_conversation_id",
        "ix_conversation_idempotency_records_principal_id",
        "ix_conversation_idempotency_records_tenant_id",
    ):
        op.drop_index(index_name, table_name="conversation_idempotency_records")
    op.drop_table("conversation_idempotency_records")
    op.drop_index(
        "ix_conversation_handoffs_conversation_id", table_name="conversation_handoffs"
    )
    op.drop_table("conversation_handoffs")
    op.drop_index(
        "ix_conversation_events_conversation_id", table_name="conversation_events"
    )
    op.drop_table("conversation_events")
    op.drop_index(
        "ix_conversation_messages_conversation_id", table_name="conversation_messages"
    )
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_customer_id", table_name="conversations")
    op.drop_index("ix_conversations_tenant_id", table_name="conversations")
    op.drop_table("conversations")
    postgresql.ENUM(name="conversation_handoff_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="conversation_actor").drop(bind, checkfirst=True)
    postgresql.ENUM(name="conversation_status").drop(bind, checkfirst=True)
