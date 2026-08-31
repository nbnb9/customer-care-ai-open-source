"""add internal_note to ticket_event_type

Revision ID: b58d3c758527
Revises: 6e9fa97ac022
Create Date: 2026-08-16 11:35:43.493122

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b58d3c758527"
down_revision: str | Sequence[str] | None = "6e9fa97ac022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE ticket_event_type ADD VALUE 'internal_note'")


def downgrade() -> None:
    # PostgreSQL cannot remove one enum value in place. Refuse a lossy rollback
    # when such rows exist, then rebuild the enum without internal_note.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM ticket_events WHERE event_type = 'internal_note'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: internal_note ticket events still exist';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE ticket_events "
        "ALTER COLUMN event_type TYPE VARCHAR USING event_type::text"
    )
    op.execute("DROP TYPE ticket_event_type")
    op.execute(
        "CREATE TYPE ticket_event_type AS ENUM "
        "('created', 'status_changed', 'comment_added', 'assigned')"
    )
    op.execute(
        "ALTER TABLE ticket_events ALTER COLUMN event_type "
        "TYPE ticket_event_type USING event_type::ticket_event_type"
    )
