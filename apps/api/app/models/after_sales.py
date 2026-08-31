from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class AfterSalesActionType(StrEnum):
    """The deliberately small R2 action allow-list.

    Refunds, order cancellation and customer notifications are intentionally not
    action types.  They therefore cannot be reached through the draft API or
    the worker by a model-shaped request.
    """

    return_request = "return"
    replacement = "replacement"
    complaint = "complaint"


class AfterSalesWorkflowStatus(StrEnum):
    draft = "draft"
    policy_checked = "policy_checked"
    awaiting_customer_confirmation = "awaiting_customer_confirmation"
    awaiting_agent_approval = "awaiting_agent_approval"
    queued = "queued"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    compensation_required = "compensation_required"
    cancelled = "cancelled"


def _after_sales_action_type_enum() -> Enum:
    """Persist API values such as `return`, not Python member names."""

    return Enum(
        AfterSalesActionType,
        name="after_sales_action_type",
        values_callable=lambda enum_class: [member.value for member in enum_class],
    )


class AfterSalesWorkflow(Base):
    """A durable, server-controlled R2 after-sales workflow.

    Policy and customer-facing summaries are frozen at draft time.  The worker
    recalculates the policy before an external write and fails closed if the
    current facts no longer match that snapshot.
    """

    __tablename__ = "after_sales_workflows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    action_type: Mapped[AfterSalesActionType] = mapped_column(
        _after_sales_action_type_enum()
    )
    status: Mapped[AfterSalesWorkflowStatus] = mapped_column(
        Enum(AfterSalesWorkflowStatus, name="after_sales_workflow_status"),
        index=True,
    )
    # Events are ordered by this durable cursor rather than timestamp. Multiple
    # transitions commonly occur in one transaction and can share a timestamp.
    last_event_sequence: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    # This customer-visible identifier is necessary to show the draft and to
    # re-query the Mock OMS policy.  Internal OMS IDs never leave the policy
    # adapter or appear in API read models.
    order_number: Mapped[str] = mapped_column(String(128))
    policy_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    customer_summary: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    customer_confirmed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agent_approved_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    agent_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    reconciliation_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    # The raw external key is deterministic from workflow ID and is never
    # returned.  Persisting only its digest makes tampering detectable without
    # turning an API read response into a replay credential.
    external_idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    # This durable parent context ties the later worker execution to the API
    # draft without making the W3C trace header part of any public DTO.
    observability_traceparent: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    external_status: Mapped[str] = mapped_column(
        String(48), default="not_started", server_default="not_started"
    )
    external_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    external_receipt: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    reconciliation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    compensation_reason_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AfterSalesWorkflowEvent(Base):
    """Append-only lifecycle evidence, separate from mutable workflow state."""

    __tablename__ = "after_sales_workflow_events"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "sequence",
            name="uq_after_sales_workflow_event_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("after_sales_workflows.id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100))
    from_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Only fixed action/status/reason-code summaries belong here.  Do not copy
    # free-form customer text, provider errors, an order ID or an external key.
    summary: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    request_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AfterSalesIdempotencyRecord(Base):
    """Committed responses for mutating R2 API operations."""

    __tablename__ = "after_sales_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "principal_id",
            "operation",
            "idempotency_key_hash",
            name="uq_after_sales_idempotency_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(180))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("after_sales_workflows.id"), index=True
    )
    response_body: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MockOmsOperation(Base):
    """Independent, idempotent ledger owned by the local Mock OMS adapter."""

    __tablename__ = "mock_oms_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "external_idempotency_key_hash",
            name="uq_mock_oms_operation_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("after_sales_workflows.id"), index=True
    )
    action_type: Mapped[AfterSalesActionType] = mapped_column(
        _after_sales_action_type_enum()
    )
    external_idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(48))
    external_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    receipt: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
