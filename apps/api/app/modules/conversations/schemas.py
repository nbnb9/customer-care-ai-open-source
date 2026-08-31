from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.conversations import ConversationStatus, HandoffStatus
from app.models.tickets import TicketPriority

ConversationText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(StrictRequest):
    """The server derives customer and tenant identity; only web is supported."""

    channel: Literal["web"] = "web"


class ConversationRead(BaseModel):
    id: str
    status: ConversationStatus
    last_sequence: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationMessageCreate(StrictRequest):
    text: ConversationText


class ConversationMessageAccepted(BaseModel):
    conversation_id: str
    message_id: str
    accepted_sequence: int = Field(ge=1)
    request_id: str


class ConversationHandoffCreate(StrictRequest):
    # System routing uses separate, non-client-controlled reason codes. A
    # customer is allowed to express only this intent at this stage.
    reason_code: Literal["customer_requested"] = "customer_requested"


class ConversationHandoffRead(BaseModel):
    id: str
    conversation_id: str
    reason_code: str
    status: HandoffStatus
    created_at: datetime
    already_requested: bool = False

    model_config = ConfigDict(from_attributes=True)


class HandoffContextFactRead(BaseModel):
    label: str
    value: str


class HandoffToolSummaryRead(BaseModel):
    tool_name: str
    result_status: str


class HandoffCustomerVisibleCitationRead(BaseModel):
    title: str
    document_version: str
    excerpt: str
    source_locator: str


class HandoffContextPackageRead(BaseModel):
    """The deliberately narrow staff context contract for a handoff."""

    schema_version: str = "handoff-context-v1"
    verification_level: str | None = None
    conversation_summary: str = "上下文快照不可用；请按受控核验流程处理。"
    confirmed_facts: list[HandoffContextFactRead] = Field(default_factory=list)
    tool_summaries: list[HandoffToolSummaryRead] = Field(default_factory=list)
    customer_visible_citations: list[HandoffCustomerVisibleCitationRead] = Field(
        default_factory=list
    )
    unresolved_questions: list[str] = Field(default_factory=list)
    risk_reasons: list[str] = Field(default_factory=list)


class HandoffQueueItemRead(BaseModel):
    id: str
    conversation_id: str
    reason_code: str
    status: HandoffStatus
    priority: TicketPriority
    target_queue: str
    sla_due_at: datetime
    assigned_agent_id: str | None
    claimed_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HandoffDetailRead(HandoffQueueItemRead):
    context: HandoffContextPackageRead


class HandoffClaimCreate(StrictRequest):
    pass


class HandoffResolutionCreate(StrictRequest):
    action: Literal["close_self_service", "restore_safe_flow"]


class ConversationFeedbackCreate(StrictRequest):
    answer_message_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    feedback: Literal["helpful", "unhelpful"]


class ConversationFeedbackRead(BaseModel):
    id: str
    conversation_id: str
    answer_message_id: str
    feedback: Literal["helpful", "unhelpful"]
    already_recorded: bool = False

    model_config = ConfigDict(from_attributes=True)


class ConversationEventEnvelope(BaseModel):
    """The JSON object placed in each SSE `data:` field."""

    event_id: str
    sequence: int = Field(ge=1)
    type: str
    conversation_id: str
    request_id: str
    payload: dict[str, object]
