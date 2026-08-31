"""Privacy-minimized local OpenTelemetry span storage for Step 39.

This table is a local trace viewer, not an audit trail or a production
telemetry backend. It only stores allow-listed correlation/version metadata;
customer text, prompts, model output, credentials and raw SQL never belong
here.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ObservabilitySpan(Base):
    __tablename__ = "observability_spans"
    __table_args__ = (
        UniqueConstraint("trace_id", "span_id", name="uq_observability_trace_span"),
        Index(
            "ix_observability_spans_tenant_trace_started",
            "tenant_id",
            "trace_id",
            "started_at",
        ),
        Index(
            "ix_observability_spans_tenant_request_started",
            "tenant_id",
            "request_id",
            "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    span_id: Mapped[str] = mapped_column(String(16))
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Correlation IDs intentionally have no foreign keys: trace retention must
    # not block legitimate deletion of business/customer data.
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ticket_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
