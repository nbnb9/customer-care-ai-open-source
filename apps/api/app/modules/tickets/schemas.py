from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.models.tickets import TicketEventType, TicketPriority, TicketStatus

NonEmptyShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
OptionalDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5_000)
]
Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TicketCreate(StrictRequest):
    order_id: Identifier | None = None
    subject: NonEmptyShortText
    description: OptionalDescription | None = None
    priority: TicketPriority = TicketPriority.normal


class TicketUpdate(StrictRequest):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> "TicketUpdate":
        if self.status is None and self.priority is None:
            raise ValueError("至少需要提供 status 或 priority")
        return self


class CommentCreate(StrictRequest):
    body: CommentBody
    is_internal: bool = False  # True = 内部备注，客户不可见


class AssignmentCreate(StrictRequest):
    assignee_id: Identifier


class TicketRead(BaseModel):
    id: str
    tenant_id: str
    customer_id: str
    order_id: str | None
    status: TicketStatus
    subject: str
    description: str | None
    priority: TicketPriority
    assignee_id: str | None
    created_at: datetime
    closed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TicketEventRead(BaseModel):
    id: str
    event_type: TicketEventType
    payload_digest: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
