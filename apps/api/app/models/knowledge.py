from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class KnowledgeImportStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    published = "published"
    failed = "failed"


class KnowledgeDocumentStatus(StrEnum):
    draft = "draft"
    review = "review"
    published = "published"
    expired = "expired"
    withdrawn = "withdrawn"


class KnowledgeImport(Base):
    """Durable queue item; content is staged until a worker processes it."""

    __tablename__ = "knowledge_imports"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_key",
            "version",
            "source_sha256",
            name="uq_knowledge_imports_dedup",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="", server_default="")
    document_key: Mapped[str] = mapped_column(String(255))
    owner: Mapped[str] = mapped_column(String(255))
    audience: Mapped[str] = mapped_column(String(32))
    product: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        Enum(KnowledgeDocumentStatus, name="knowledge_document_status")
    )
    untrusted_content: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_content: Mapped[str] = mapped_column(Text)
    # A W3C parent is correlation metadata only; it contains no user content
    # and lets the independent worker continue the originating local trace.
    observability_traceparent: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[KnowledgeImportStatus] = mapped_column(
        Enum(KnowledgeImportStatus, name="knowledge_import_status"),
        default=KnowledgeImportStatus.queued,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_key",
            "product",
            "region",
            "language",
            "version",
            name="uq_knowledge_documents_version",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    import_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_imports.id"), unique=True
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_key: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="", server_default="")
    owner: Mapped[str] = mapped_column(String(255))
    audience: Mapped[str] = mapped_column(String(32))
    product: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        Enum(KnowledgeDocumentStatus, name="knowledge_document_status")
    )
    untrusted_content: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_knowledge_chunks_position"
        ),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_l2_ops"},
        ),
        Index(
            "ix_knowledge_chunks_acl_scope",
            "tenant_id",
            "status",
            "audience",
            "product",
            "region",
            "language",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_key: Mapped[str] = mapped_column(String(255))
    document_version: Mapped[str] = mapped_column(String(32))
    audience: Mapped[str] = mapped_column(String(32), index=True)
    product: Mapped[str] = mapped_column(String(255), index=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(32), index=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        Enum(KnowledgeDocumentStatus, name="knowledge_document_status"), index=True
    )
    untrusted_content: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(32))
    chunk_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
