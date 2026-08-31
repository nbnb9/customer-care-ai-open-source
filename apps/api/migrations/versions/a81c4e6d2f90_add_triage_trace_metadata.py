"""add triage trace metadata

Revision ID: a81c4e6d2f90
Revises: 9f3a21d4c8b7
Create Date: 2026-08-18 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a81c4e6d2f90"
down_revision: str | Sequence[str] | None = "9f3a21d4c8b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "triage_suggestions",
        sa.Column(
            "provider",
            sa.String(),
            server_default="legacy_unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "triage_suggestions",
        sa.Column(
            "schema_version",
            sa.String(),
            server_default="triage-result-v1",
            nullable=False,
        ),
    )
    op.add_column(
        "triage_suggestions",
        sa.Column(
            "generation_latency_ms",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("triage_suggestions", "generation_latency_ms")
    op.drop_column("triage_suggestions", "schema_version")
    op.execute("ALTER TABLE triage_suggestions DROP COLUMN IF EXISTS provider")
