"""Non-destructive first-run bootstrap for the local Compose environment."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .db import SessionLocal
from .seed import load_synthetic_world

_MIGRATION_TABLES = {"alembic_version"}


def populated_application_tables(db: Session) -> list[str]:
    """Return application tables that already contain at least one row."""

    bind = db.get_bind()
    inspector = inspect(bind)
    quote = bind.dialect.identifier_preparer.quote
    populated: list[str] = []
    for table_name in sorted(inspector.get_table_names()):
        if table_name in _MIGRATION_TABLES:
            continue
        row = db.execute(text(f"SELECT 1 FROM {quote(table_name)} LIMIT 1")).first()
        if row is not None:
            populated.append(table_name)
    return populated


def bootstrap_if_empty(db: Session) -> bool:
    """Load demo fixtures only when every migrated application table is empty."""

    if populated_application_tables(db):
        return False
    load_synthetic_world(db)
    return True


def main() -> int:
    with SessionLocal() as db:
        seeded = bootstrap_if_empty(db)
    result = "seeded synthetic world" if seeded else "preserved data"
    print(f"bootstrap: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
