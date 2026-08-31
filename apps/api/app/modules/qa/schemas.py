"""Strict DTOs for the supervisor QA feedback loop."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.conversations import (
    ConversationActor,
    ConversationStatus,
    HandoffStatus,
)
from app.models.qa import (
    QAIssueCategory,
    QARegressionStatus,
    QARemediationType,
    QAReviewStatus,
    QARootCause,
    QASeverity,
)

Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
IssueText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)
]
EvidenceReference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QAOwnerRead(BaseModel):
    id: str
    display_name: str
    role: Literal["agent", "supervisor", "knowledge_operator"]


class QARegressionCatalogEntryRead(BaseModel):
    id: str
    evaluation_suite: Literal["triage", "rag"]
    evaluation_case_id: str
    category: QAIssueCategory
    remediation_type: QARemediationType
    description: str


class QAConversationSampleListItemRead(BaseModel):
    conversation_id: str
    created_at: datetime
    last_answer_message_id: str
    answer_mode: str | None
    final_result_preview: str
    citation_count: int = Field(ge=0)
    handoff_reason_code: str | None
    handoff_status: HandoffStatus | None


class QAConversationSampleMessageRead(BaseModel):
    id: str
    actor: ConversationActor
    text: str
    response_kind: str | None
    created_at: datetime


class QACitationRead(BaseModel):
    document_key: str
    document_version: str
    title: str
    excerpt: str
    source_locator: str


class QAToolSummaryRead(BaseModel):
    tool_name: str
    result_status: str


class QAConversationRouteRead(BaseModel):
    conversation_status: ConversationStatus
    answer_mode: str | None
    handoff_reason_code: str | None
    handoff_status: HandoffStatus | None


class QAConversationFinalResultRead(BaseModel):
    message_id: str
    text: str
    answer_mode: str | None
    created_at: datetime


class QADocumentVersionRead(BaseModel):
    document_key: str
    document_version: str


class QAConversationFeedbackSummaryRead(BaseModel):
    helpful_count: int = Field(ge=0)
    unhelpful_count: int = Field(ge=0)


class QAConversationSampleRead(BaseModel):
    schema_version: Literal["qa-conversation-sample-v1"] = "qa-conversation-sample-v1"
    conversation_id: str
    messages: list[QAConversationSampleMessageRead] = Field(default_factory=list)
    route: QAConversationRouteRead
    citations: list[QACitationRead] = Field(default_factory=list)
    tool_summaries: list[QAToolSummaryRead] = Field(default_factory=list)
    final_result: QAConversationFinalResultRead
    source_versions: list[QADocumentVersionRead] = Field(default_factory=list)
    feedback: QAConversationFeedbackSummaryRead


class QAIssueCreate(StrictRequest):
    source_conversation_id: Identifier
    category: QAIssueCategory
    severity: QASeverity
    owner_id: Identifier
    root_cause: QARootCause
    problem_summary: IssueText
    remediation_type: QARemediationType
    remediation_summary: IssueText
    regression_case_id: Identifier
    pause_r0_feature_flag: bool = False


class QAIssueUpdate(StrictRequest):
    status: QAReviewStatus
    regression_status: QARegressionStatus | None = None
    regression_evidence_ref: EvidenceReference | None = None


class QAFeatureFlagDecisionRead(BaseModel):
    paused: bool
    snapshot: dict[str, object] = Field(default_factory=dict)


class QARegressionRead(BaseModel):
    catalog_case_id: str
    evaluation_suite: Literal["triage", "rag"]
    evaluation_case_id: str
    status: QARegressionStatus
    evidence_ref: str | None


class QAReviewIssueRead(BaseModel):
    id: str
    source_conversation_id: str
    category: QAIssueCategory
    severity: QASeverity
    status: QAReviewStatus
    owner_id: str
    root_cause: QARootCause
    problem_summary: str
    remediation_type: QARemediationType
    remediation_summary: str
    feature_flag: QAFeatureFlagDecisionRead
    regression: QARegressionRead
    reported_by: str
    verified_by: str | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class QAIssueEventRead(BaseModel):
    id: str
    sequence: int = Field(ge=1)
    event_type: str
    actor_id: str | None
    summary: dict[str, object] = Field(default_factory=dict)
    request_id: str
    created_at: datetime


class QAReviewIssueDetailRead(QAReviewIssueRead):
    source: QAConversationSampleRead
    source_evidence: dict[str, object] = Field(default_factory=dict)
    events: list[QAIssueEventRead] = Field(default_factory=list)
