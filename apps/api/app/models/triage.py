from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class TriageSuggestion(Base):
    __tablename__ = "triage_suggestions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    # 模型建议字段（只读，不直接改工单）
    intent: Mapped[str] = mapped_column(String)
    risk_level: Mapped[str] = mapped_column(String)
    route: Mapped[str] = mapped_column(String)
    suggested_priority: Mapped[str] = mapped_column(String, default="normal")
    summary: Mapped[str] = mapped_column(Text)
    missing_information: Mapped[list[str]] = mapped_column(JSON, default=list)
    needs_human_review: Mapped[bool] = mapped_column(default=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String, default="legacy_unknown")
    model: Mapped[str] = mapped_column(String, default="fake")
    prompt_version: Mapped[str] = mapped_column(String, default="v1")
    schema_version: Mapped[str] = mapped_column(String, default="triage-result-v1")
    generation_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    # 坐席决定（事实）
    decision: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # accepted | edited | rejected
    decision_reason_code: Mapped[str | None] = mapped_column(String, nullable=True)
    edited_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
