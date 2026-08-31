"""Safe DTOs for the local observability viewer."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ObservabilitySpanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trace_id: str
    span_id: str
    parent_span_id: str | None
    request_id: str | None
    conversation_id: str | None
    ticket_id: str | None
    workflow_id: str | None
    name: str
    kind: str
    status: str
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    attributes: dict[str, object]


class ObservabilityTraceRead(BaseModel):
    trace_id: str
    request_ids: list[str]
    conversation_ids: list[str]
    ticket_ids: list[str]
    workflow_ids: list[str]
    spans: list[ObservabilitySpanRead]


class ObservabilityOverviewRead(BaseModel):
    generated_at: datetime
    retained_span_count: int
    analysis_window_span_count: int
    customer_experience: dict[str, object]
    ai_quality: dict[str, object]
    safety: dict[str, object]
    workflow: dict[str, object]
    cost_capacity: dict[str, object]
    limitations: list[str] = Field(default_factory=list)
