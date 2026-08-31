from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.knowledge import KnowledgeImportStatus

KnowledgeAudience = Literal["public", "customer", "agent", "supervisor", "internal"]
KnowledgeDocumentStatus = Literal[
    "draft", "review", "published", "expired", "withdrawn"
]

Filename = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
Version = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\d+\.\d+\.\d+$")
]
SearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


class KnowledgeImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: Filename
    content: Annotated[str, StringConstraints(min_length=1, max_length=1_000_000)]
    title: ShortText | None = None
    document_key: ShortText
    owner: ShortText
    audience: KnowledgeAudience
    product: ShortText
    region: ShortText
    language: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=32),
    ]
    version: Version
    effective_at: datetime
    expires_at: datetime | None = None
    requested_status: KnowledgeDocumentStatus = "review"
    untrusted_content: bool = False

    @model_validator(mode="after")
    def validate_timestamps(self) -> "KnowledgeImportCreate":
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must include a timezone")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None:
                raise ValueError("expires_at must include a timezone")
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must be after effective_at")
        return self


class KnowledgeImportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    requested_by: str
    filename: str
    title: str
    document_key: str
    owner: str
    audience: KnowledgeAudience
    product: str
    region: str
    language: str
    version: str
    effective_at: datetime
    expires_at: datetime | None
    requested_status: KnowledgeDocumentStatus
    untrusted_content: bool
    source_sha256: str
    status: KnowledgeImportStatus
    attempts: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deduplicated: bool = False


class KnowledgeImportResponse(KnowledgeImportRead):
    """Response model that makes queue de-duplication explicit."""

    deduplicated: bool = Field(default=False)


class KnowledgeDocumentWithdrawalCreate(BaseModel):
    """Exact version selector for a controlled published-document withdrawal."""

    model_config = ConfigDict(extra="forbid")

    document_key: ShortText
    version: Version
    product: ShortText
    region: ShortText
    language: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=32),
    ]
    reason: ShortText


class KnowledgeDocumentWithdrawalResponse(BaseModel):
    """Public-safe operational record; document content is never returned."""

    id: str
    document_key: str
    version: str
    product: str
    region: str
    language: str
    status: KnowledgeDocumentStatus
    already_withdrawn: bool = False
    audit_event_id: str | None = None


class KnowledgeSearchRequest(BaseModel):
    """Caller-supplied applicability scope; identity ACL stays server-side."""

    model_config = ConfigDict(extra="forbid")

    query: SearchQuery
    product: ShortText
    region: ShortText
    language: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=32),
    ]
    limit: int = Field(default=5, ge=1, le=20)


class KnowledgeSearchResult(BaseModel):
    chunk_id: str
    document_key: str
    document_version: str
    title: str
    snippet: str
    product: str
    region: str
    language: str
    effective_at: datetime
    expires_at: datetime | None
    matched_lexically: bool
    untrusted_content: bool


class KnowledgeSearchResponse(BaseModel):
    results: list[KnowledgeSearchResult]


class KnowledgeAnswerRequest(KnowledgeSearchRequest):
    """Question plus applicability scope for a citation-backed answer."""


class KnowledgeSourceScope(BaseModel):
    """Applicability scope re-supplied when a citation source is opened."""

    model_config = ConfigDict(extra="forbid")

    product: ShortText
    region: ShortText
    language: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=32),
    ]


class KnowledgeCitation(BaseModel):
    """A public-safe citation constructed by the server from authorized data."""

    chunk_id: str
    document_key: str
    document_version: str
    title: str
    excerpt: str
    product: str
    region: str
    language: str
    effective_at: datetime
    expires_at: datetime | None
    source_locator: str


class KnowledgeAnswerResponse(BaseModel):
    answer: str
    citations: list[KnowledgeCitation] = Field(default_factory=list, max_length=8)
    needs_handoff: bool
    # Customer callers always receive an empty list. The API keeps this field
    # so the agent workspace can render non-public diagnostic labels.
    uncertainties: list[str] = Field(default_factory=list, max_length=20)


class KnowledgeCitationSource(BaseModel):
    """Authorized original chunk returned after an ACL check at click time."""

    chunk_id: str
    document_key: str
    document_version: str
    title: str
    text: str
    product: str
    region: str
    language: str
    effective_at: datetime
    expires_at: datetime | None
