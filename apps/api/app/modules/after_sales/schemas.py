from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.models.after_sales import AfterSalesActionType, AfterSalesWorkflowStatus

Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AfterSalesDraftCreate(StrictRequest):
    # The linked ticket is the only browser-supplied target.  Its tenant,
    # customer and internal order binding are re-derived on the server.
    ticket_id: Identifier
    action_type: AfterSalesActionType


class CustomerConfirmationRequest(StrictRequest):
    decision: Literal["confirm", "cancel"]


class AgentApprovalRequest(StrictRequest):
    decision: Literal["approve", "reject", "edit"]
    edited_action_type: AfterSalesActionType | None = None

    @model_validator(mode="after")
    def validate_edit(self) -> "AgentApprovalRequest":
        if self.decision == "edit" and self.edited_action_type is None:
            raise ValueError("edited_action_type is required when decision is edit")
        if self.decision != "edit" and self.edited_action_type is not None:
            raise ValueError("edited_action_type is allowed only when decision is edit")
        return self


class AfterSalesPolicyRead(BaseModel):
    policy_version: str
    eligible: bool
    decision_code: str
    action_type: AfterSalesActionType
    order_number: str
    order_status: str
    warranty_active: bool


class AfterSalesCustomerSummaryRead(BaseModel):
    action_label: str
    impact_summary: str
    amount_minor: int | None
    currency: str | None
    manual_approval_required: bool
    risk_notices: list[str]


class AfterSalesWorkflowRead(BaseModel):
    id: str
    ticket_id: str
    action_type: AfterSalesActionType
    status: AfterSalesWorkflowStatus
    order_number: str
    policy: AfterSalesPolicyRead
    customer_summary: AfterSalesCustomerSummaryRead
    customer_confirmed_at: datetime | None
    agent_approved_at: datetime | None
    queued_at: datetime | None
    external_status: str
    external_reference: str | None
    receipt_summary: dict[str, object]
    reconciliation_required: bool
    failure_code: str | None
    compensation_reason_code: str | None
    created_at: datetime
    updated_at: datetime


class AfterSalesWorkflowEventRead(BaseModel):
    id: str
    sequence: int
    event_type: str
    from_status: str | None
    to_status: str | None
    summary: dict[str, object]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
