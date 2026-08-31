"""add asynchronous knowledge imports and pgvector chunks

Revision ID: d4e5f6a7b8c9
Revises: c27b5d8e1a43
Create Date: 2026-08-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c27b5d8e1a43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'knowledge_operator'")

    import_status = postgresql.ENUM(
        "queued",
        "processing",
        "published",
        "failed",
        name="knowledge_import_status",
    )
    document_status = postgresql.ENUM(
        "draft",
        "review",
        "published",
        "expired",
        "withdrawn",
        name="knowledge_document_status",
    )
    import_status.create(bind, checkfirst=True)
    document_status.create(bind, checkfirst=True)
    import_status_column = postgresql.ENUM(
        "queued",
        "processing",
        "published",
        "failed",
        name="knowledge_import_status",
        create_type=False,
    )
    document_status_column = postgresql.ENUM(
        "draft",
        "review",
        "published",
        "expired",
        "withdrawn",
        name="knowledge_document_status",
        create_type=False,
    )

    op.create_table(
        "knowledge_imports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("document_key", sa.String(length=255), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("audience", sa.String(length=32), nullable=False),
        sa.Column("product", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_status", document_status_column, nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_content", sa.Text(), nullable=False),
        sa.Column("status", import_status_column, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_key",
            "version",
            "source_sha256",
            name="uq_knowledge_imports_dedup",
        ),
    )
    op.create_index(
        "ix_knowledge_imports_tenant_id", "knowledge_imports", ["tenant_id"]
    )
    op.create_index(
        "ix_knowledge_imports_requested_by",
        "knowledge_imports",
        ["requested_by"],
    )
    op.create_index(
        "ix_knowledge_imports_source_sha256",
        "knowledge_imports",
        ["source_sha256"],
    )
    op.create_index("ix_knowledge_imports_status", "knowledge_imports", ["status"])

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("import_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("document_key", sa.String(length=255), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("audience", sa.String(length=32), nullable=False),
        sa.Column("product", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", document_status_column, nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["import_id"], ["knowledge_imports.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_key",
            "product",
            "region",
            "language",
            "version",
            name="uq_knowledge_documents_version",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"]
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("document_key", sa.String(length=255), nullable=False),
        sa.Column("document_version", sa.String(length=32), nullable=False),
        sa.Column("audience", sa.String(length=32), nullable=False),
        sa.Column("product", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", document_status_column, nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_knowledge_chunks_position"
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"]
    )
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
    op.create_index("ix_knowledge_chunks_audience", "knowledge_chunks", ["audience"])
    op.create_index("ix_knowledge_chunks_product", "knowledge_chunks", ["product"])
    op.create_index("ix_knowledge_chunks_region", "knowledge_chunks", ["region"])
    op.create_index("ix_knowledge_chunks_language", "knowledge_chunks", ["language"])
    op.create_index("ix_knowledge_chunks_status", "knowledge_chunks", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    for index_name in (
        "ix_knowledge_chunks_status",
        "ix_knowledge_chunks_language",
        "ix_knowledge_chunks_region",
        "ix_knowledge_chunks_product",
        "ix_knowledge_chunks_audience",
        "ix_knowledge_chunks_tenant_id",
        "ix_knowledge_chunks_document_id",
    ):
        op.drop_index(index_name, table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_documents_tenant_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    for index_name in (
        "ix_knowledge_imports_status",
        "ix_knowledge_imports_source_sha256",
        "ix_knowledge_imports_requested_by",
        "ix_knowledge_imports_tenant_id",
    ):
        op.drop_index(index_name, table_name="knowledge_imports")
    op.drop_table("knowledge_imports")
    postgresql.ENUM(
        "draft",
        "review",
        "published",
        "expired",
        "withdrawn",
        name="knowledge_document_status",
    ).drop(bind, checkfirst=True)
    postgresql.ENUM(
        "queued",
        "processing",
        "published",
        "failed",
        name="knowledge_import_status",
    ).drop(bind, checkfirst=True)
    op.execute("DROP EXTENSION IF EXISTS vector")
