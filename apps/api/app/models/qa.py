"""Durable supervisor QA feedback records.

The tables intentionally store source identifiers and safe evidence digests,
not a second copy of raw customer conversations.  A supervisor can view a
tenant-scoped, redacted source sample through the QA service when reviewing an
issue; retention of the original conversation remains owned by that domain.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class QAIssueCategory(StrEnum):
    factual_error = "factual_error"
    citation_unsupported = "citation_unsupported"
    outdated_policy = "outdated_policy"
    incorrect_routing = "incorrect_routing"
    escalation_missing = "escalation_missing"
    authorization_privacy = "authorization_privacy"
    tool_parameter = "tool_parameter"
    ui_usability = "ui_usability"
    agent_misuse = "agent_misuse"


class QASeverity(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class QAReviewStatus(StrEnum):
    open = "open"
    in_remediation = "in_remediation"
    verification_pending = "verification_pending"
    verified = "verified"
    closed = "closed"


class QARootCause(StrEnum):
    knowledge_data = "knowledge_data"
    retrieval = "retrieval"
    routing_rule = "routing_rule"
    authorization = "authorization"
    tool_contract = "tool_contract"
    ui = "ui"
    workflow = "workflow"
    model_behavior = "model_behavior"
    agent_process = "agent_process"


class QARemediationType(StrEnum):
    knowledge = "knowledge"
    rule = "rule"
    code = "code"
    ui = "ui"
    workflow = "workflow"
    process = "process"


class QARegressionStatus(StrEnum):
    not_run = "not_run"
    passed = "passed"
    failed = "failed"


class QAReviewIssue(Base):
    """One supervisor-owned, tenant-scoped quality issue.

    Every issue is bound to a completed customer conversation.  The immutable
    source snapshot contains identifiers, hashes, route/citation versions and
    tool summaries only; it must never become a shadow transcript store.
    """

    __tablename__ = "qa_review_issues"
    __table_args__ = (
        Index(
            "ix_qa_review_issues_tenant_status_severity_created",
            "tenant_id",
            "status",
            "severity",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    source_conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    category: Mapped[QAIssueCategory] = mapped_column(
        Enum(QAIssueCategory, name="qa_issue_category"), index=True
    )
    severity: Mapped[QASeverity] = mapped_column(
        Enum(QASeverity, name="qa_issue_severity"), index=True
    )
    status: Mapped[QAReviewStatus] = mapped_column(
        Enum(QAReviewStatus, name="qa_review_status"),
        default=QAReviewStatus.open,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    root_cause: Mapped[QARootCause] = mapped_column(
        Enum(QARootCause, name="qa_root_cause")
    )
    # Free text is direct-identifier redacted by the service before persistence.
    problem_summary: Mapped[str] = mapped_column(Text)
    remediation_type: Mapped[QARemediationType] = mapped_column(
        Enum(QARemediationType, name="qa_remediation_type")
    )
    # Free text is direct-identifier redacted by the service before persistence.
    remediation_summary: Mapped[str] = mapped_column(Text)
    regression_case_id: Mapped[str] = mapped_column(String(128))
    evaluation_suite: Mapped[str] = mapped_column(String(32))
    evaluation_case_id: Mapped[str] = mapped_column(String(128))
    # A high-severity report can close the tenant R0 path, but cannot enable it.
    feature_flag_paused: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    feature_flag_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    source_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    regression_status: Mapped[QARegressionStatus] = mapped_column(
        Enum(QARegressionStatus, name="qa_regression_status"),
        default=QARegressionStatus.not_run,
        server_default=QARegressionStatus.not_run.value,
    )
    regression_evidence_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    reported_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    verified_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_sequence: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class QAReviewIssueEvent(Base):
    """Append-only supervisor QA lifecycle evidence."""

    __tablename__ = "qa_review_issue_events"
    __table_args__ = (
        UniqueConstraint(
            "issue_id",
            "sequence",
            name="uq_qa_review_issue_events_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    issue_id: Mapped[str] = mapped_column(ForeignKey("qa_review_issues.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100))
    # No free-form diagnosis or raw conversation content belongs in this event.
    summary: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    request_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QAIdempotencyRecord(Base):
    """Committed response snapshot for a supervisor QA mutation."""

    __tablename__ = "qa_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "principal_id",
            "operation",
            "idempotency_key_hash",
            name="uq_qa_idempotency_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(180))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    issue_id: Mapped[str] = mapped_column(ForeignKey("qa_review_issues.id"), index=True)
    response_body: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
