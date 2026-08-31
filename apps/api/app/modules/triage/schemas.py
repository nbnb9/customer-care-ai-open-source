from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.models.tickets import TicketPriority
from app.modules.llm_gateway.schemas import Intent, RiskLevel, Route

DecisionReasonCode = Literal[
    "incorrect_category",
    "missing_context",
    "policy_exception",
    "summary_correction",
    "other",
]


class TriageSuggestionRead(BaseModel):
    id: str
    ticket_id: str
    intent: Intent
    risk_level: RiskLevel
    route: Route
    suggested_priority: TicketPriority
    summary: str
    missing_information: list[str] = Field(default_factory=list)
    needs_human_review: bool
    reason_codes: list[str] = Field(default_factory=list)
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    generation_latency_ms: int
    decision: Literal["accepted", "edited", "rejected"] | None
    decision_reason_code: str | None
    edited_summary: str | None
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TriageDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    decision: Literal["accepted", "edited", "rejected"]
    reason_code: DecisionReasonCode | None = None
    edited_summary: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_edited_summary(self) -> "TriageDecisionCreate":
        if self.decision == "edited" and self.edited_summary is None:
            raise ValueError("edited 决定必须提供 edited_summary")
        if self.decision == "rejected" and self.reason_code is None:
            raise ValueError("rejected 决定必须提供 reason_code")
        if self.decision != "edited" and self.edited_summary is not None:
            raise ValueError("仅 edited 决定可以提供 edited_summary")
        return self
