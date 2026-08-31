from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.tickets import TicketPriority


class Intent(StrEnum):
    product_faq = "product_faq"
    order_status = "order_status"
    warranty = "warranty"
    return_request = "return_request"
    complaint = "complaint"
    safety = "safety"
    account = "account"
    unknown = "unknown"


class RiskLevel(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class Route(StrEnum):
    self_service = "self_service"
    agent_assist = "agent_assist"
    human_handoff = "human_handoff"
    emergency_flow = "emergency_flow"


class MissingInformationCode(StrEnum):
    """Machine-readable information that is necessary to route a ticket."""

    order_number = "order_number"
    issue_description = "issue_description"


class TriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    risk_level: RiskLevel
    route: Route
    suggested_priority: TicketPriority
    summary: str = Field(min_length=1, max_length=2_000)
    missing_information: list[MissingInformationCode] = Field(
        default_factory=list, max_length=2
    )
    needs_human_review: bool = False
    reason_codes: list[
        Annotated[str, StringConstraints(min_length=1, max_length=100)]
    ] = Field(default_factory=list, max_length=20)


class CitationEvidence(BaseModel):
    """A server-authorized chunk made available to the answer task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    document_key: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    document_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    snippet: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class CitationAnswer(BaseModel):
    """Strict output contract for evidence-constrained knowledge answers."""

    model_config = ConfigDict(extra="forbid")

    answer: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)
    ]
    citation_ids: list[
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
        ]
    ] = Field(default_factory=list, max_length=8)
    uncertainties: list[
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
    ] = Field(default_factory=list, max_length=20)
    needs_handoff: bool = False
