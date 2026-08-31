"""add retrieval display and trust metadata

Revision ID: f6a7b8c9d0e
Revises: e5f6a7b8c9d0
Create Date: 2026-08-24 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("knowledge_imports", "knowledge_documents"):
        op.add_column(
            table_name,
            sa.Column(
                "title",
                sa.String(length=255),
                nullable=False,
                server_default=sa.text("''"),
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "untrusted_content",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "untrusted_content",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_acl_scope",
        "knowledge_chunks",
        ["tenant_id", "status", "audience", "product", "region", "language"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_acl_scope", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "untrusted_content")
    for table_name in ("knowledge_documents", "knowledge_imports"):
        op.drop_column(table_name, "untrusted_content")
        op.drop_column(table_name, "title")
