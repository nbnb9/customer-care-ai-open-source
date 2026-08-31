from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .tickets import TicketPriority


class ConversationStatus(StrEnum):
    active = "active"
    awaiting_customer = "awaiting_customer"
    awaiting_agent = "awaiting_agent"
    handed_off = "handed_off"
    closed = "closed"
    blocked_for_safety = "blocked_for_safety"


class ConversationActor(StrEnum):
    customer = "customer"
    assistant = "assistant"
    system = "system"


class ConversationEventType(StrEnum):
    conversation_created = "conversation.created"
    message_created = "message.created"
    answer_delta = "answer.delta"
    answer_completed = "answer.completed"
    citation_available = "citation.available"
    workflow_updated = "workflow.updated"
    handoff_created = "handoff.created"
    handoff_updated = "handoff.updated"
    feedback_recorded = "feedback.recorded"
    error = "error"


class HandoffStatus(StrEnum):
    requested = "requested"
    claimed = "claimed"
    closed = "closed"
    safe_flow_restored = "safe_flow_restored"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status"),
        default=ConversationStatus.active,
    )
    # This is advanced only while the conversation row is locked. It is the
    # authoritative cursor used to repair a disconnected SSE subscription.
    last_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    actor: Mapped[ConversationActor] = mapped_column(
        Enum(ConversationActor, name="conversation_actor")
    )
    # Messages are not copied into audit summaries or application logs. The
    # current demo stores them for the customer's own durable timeline; a
    # production retention/encryption policy remains a later release gate.
    body: Mapped[str] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_messages.id"), nullable=True
    )
    # Only customer messages enter the asynchronous reply lifecycle.
    generation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Assistant messages identify the server-controlled response path that
    # created them. This prevents a browser from attaching R0 quality feedback
    # to a non-self-service acknowledgement or system notice.
    response_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConversationEvent(Base):
    __tablename__ = "conversation_events"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_events_sequence"
        ),
    )

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    # Keep this a string rather than a PostgreSQL enum: adding an event type is
    # an additive protocol change and must not require an enum rewrite.
    event_type: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConversationHandoff(Base):
    __tablename__ = "conversation_handoffs"
    __table_args__ = (
        Index(
            "ix_conversation_handoffs_queue_status_sla",
            "target_queue",
            "status",
            "sla_due_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    requested_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reason_code: Mapped[str] = mapped_column(String(100))
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus, name="conversation_handoff_status"),
        default=HandoffStatus.requested,
    )
    # Reuse the established service-desk priority vocabulary.  Routing policy
    # remains server-controlled; a customer cannot select any of these values.
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority"),
        default=TicketPriority.normal,
    )
    target_queue: Mapped[str] = mapped_column(String(100))
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    assigned_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # This stores only the intentionally minimized, redacted package built at
    # handoff time.  It must never contain a system prompt, model reasoning,
    # another customer's data, or a full raw trace.
    context_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConversationFeedback(Base):
    """Customer quality signal for one completed R0 answer.

    It stores the customer's latest explicit rating. It is intentionally not a
    quality verdict and never changes routing or knowledge publication by
    itself.
    """

    __tablename__ = "conversation_feedback"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "answer_message_id",
            "customer_id",
            name="uq_conversation_feedback_customer_answer",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    answer_message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.id"), index=True
    )
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    feedback: Mapped[str] = mapped_column(String(16))
    request_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConversationIdempotencyRecord(Base):
    """Committed response snapshot for a customer-conversation write."""

    __tablename__ = "conversation_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "principal_id",
            "operation",
            "idempotency_key_hash",
            name="uq_conversation_idempotency_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(180))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    response_body: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
