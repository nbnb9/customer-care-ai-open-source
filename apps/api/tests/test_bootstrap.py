from sqlalchemy import func, select

from app.bootstrap import bootstrap_if_empty
from app.models.identity import Tenant
from app.models.tickets import Ticket
from app.seed import clear


def test_bootstrap_preserves_existing_application_data(db):
    tenant_count = db.scalar(select(func.count()).select_from(Tenant))
    ticket_count = db.scalar(select(func.count()).select_from(Ticket))

    assert bootstrap_if_empty(db) is False
    assert db.scalar(select(func.count()).select_from(Tenant)) == tenant_count
    assert db.scalar(select(func.count()).select_from(Ticket)) == ticket_count


def test_bootstrap_loads_synthetic_world_only_into_an_empty_database(db):
    clear(db)
    db.commit()

    assert bootstrap_if_empty(db) is True
    assert db.scalar(select(func.count()).select_from(Tenant)) > 0
    assert db.scalar(select(func.count()).select_from(Ticket)) > 0
