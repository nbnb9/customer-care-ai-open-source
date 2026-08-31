"""store idempotent response

Revision ID: c27b5d8e1a43
Revises: a81c4e6d2f90
Create Date: 2026-08-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c27b5d8e1a43"
down_revision: str | Sequence[str] | None = "a81c4e6d2f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_records",
        sa.Column(
            "response_body",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("idempotency_records", "response_body")
