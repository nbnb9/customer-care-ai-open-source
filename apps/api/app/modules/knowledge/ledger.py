"""Deterministic, model-independent knowledge asset ledger validation.

Step 27 deliberately stops at metadata and publication policy. Importing,
chunking, embedding, and retrieval belong to later steps and must consume this
validated contract rather than infer authorization from document text.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


KnowledgeAudience = Literal["public", "customer", "agent", "supervisor", "internal"]
KnowledgeRole = Literal["customer", "agent", "supervisor"]


class KnowledgeDocument(BaseModel):
    """The metadata required before a knowledge asset can be published."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    document_key: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    audience: KnowledgeAudience
    product: str = Field(min_length=1)
    region: str = Field(min_length=1)
    language: str = Field(min_length=2)
    version: str = Field(min_length=1)
    effective_at: datetime
    expires_at: datetime | None = None
    status: KnowledgeStatus
    untrusted_content: bool = False
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("effective_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("knowledge timestamps must include a timezone")
        return value

    @field_validator("path")
    @classmethod
    def reject_path_escape(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("knowledge path must stay inside the ledger directory")
        return value

    @field_validator("version")
    @classmethod
    def require_semver_shape(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError("knowledge version must use major.minor.patch")
        return value


class KnowledgeLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    ledger_version: str = Field(min_length=1)
    source: Literal["synthetic"]
    as_of: datetime
    documents: tuple[KnowledgeDocument, ...] = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("ledger as_of must include a timezone")
        return value


class KnowledgeLedgerError(ValueError):
    """Raised when metadata, bytes, or publication invariants are invalid."""


def load_ledger(manifest_path: str | Path) -> KnowledgeLedger:
    """Load and validate a ledger plus every referenced document file."""

    manifest = Path(manifest_path)
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        ledger = KnowledgeLedger.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise KnowledgeLedgerError(f"invalid knowledge ledger: {manifest}") from exc

    if len({document.id for document in ledger.documents}) != len(ledger.documents):
        raise KnowledgeLedgerError("knowledge document ids must be unique")
    if len({document.path for document in ledger.documents}) != len(ledger.documents):
        raise KnowledgeLedgerError("knowledge document paths must be unique")

    published_by_scope: dict[tuple[str, str, str, str, str], KnowledgeDocument] = {}
    for document in ledger.documents:
        document_path = manifest.parent / document.path
        if not document_path.is_file():
            raise KnowledgeLedgerError(f"missing knowledge document: {document.path}")
        digest = hashlib.sha256(document_path.read_bytes()).hexdigest()
        if digest != document.content_sha256:
            raise KnowledgeLedgerError(
                f"content hash mismatch for knowledge document: {document.id}"
            )
        if (
            document.expires_at is not None
            and document.expires_at <= document.effective_at
        ):
            raise KnowledgeLedgerError(
                f"expires_at must be after effective_at: {document.id}"
            )
        _validate_lifecycle(document, ledger.as_of)
        if document.status is KnowledgeStatus.PUBLISHED:
            scope = (
                document.tenant_id,
                document.document_key,
                document.product,
                document.region,
                document.language,
            )
            if scope in published_by_scope:
                previous = published_by_scope[scope]
                raise KnowledgeLedgerError(
                    "only one published version is allowed for scope "
                    f"{scope}: {previous.id} and {document.id}"
                )
            published_by_scope[scope] = document
    return ledger


def _validate_lifecycle(document: KnowledgeDocument, as_of: datetime) -> None:
    if document.status is KnowledgeStatus.PUBLISHED:
        if document.effective_at > as_of:
            raise KnowledgeLedgerError(
                f"published document is not effective at as_of: {document.id}"
            )
        if document.expires_at is not None and document.expires_at <= as_of:
            raise KnowledgeLedgerError(
                f"published document is expired at as_of: {document.id}"
            )
    elif document.status is KnowledgeStatus.EXPIRED:
        if document.expires_at is None or document.expires_at > as_of:
            raise KnowledgeLedgerError(
                f"expired document needs an expiry at or before as_of: {document.id}"
            )


def accessible_documents(
    ledger: KnowledgeLedger,
    *,
    tenant_id: str,
    role: KnowledgeRole,
    at: datetime | None = None,
) -> tuple[KnowledgeDocument, ...]:
    """Return only currently published documents allowed by tenant and role."""

    now = at or ledger.as_of
    if now.tzinfo is None:
        raise KnowledgeLedgerError("access check time must include a timezone")
    return tuple(
        document
        for document in ledger.documents
        if document.status is KnowledgeStatus.PUBLISHED
        and document.tenant_id == tenant_id
        and document.effective_at <= now
        and (document.expires_at is None or now < document.expires_at)
        and _audience_allows(document.audience, role)
    )


def validate_document_lifecycle(
    document: KnowledgeDocument, *, as_of: datetime
) -> None:
    """Validate publish/expiry semantics for one imported document."""

    if as_of.tzinfo is None:
        raise KnowledgeLedgerError("validation time must include a timezone")
    if document.expires_at is not None and document.expires_at <= document.effective_at:
        raise KnowledgeLedgerError(
            f"expires_at must be after effective_at: {document.id}"
        )
    _validate_lifecycle(document, as_of)


def _audience_allows(audience: KnowledgeAudience, role: KnowledgeRole) -> bool:
    if audience == "public":
        return True
    if audience == "internal":
        return role in {"agent", "supervisor"}
    return audience == role
