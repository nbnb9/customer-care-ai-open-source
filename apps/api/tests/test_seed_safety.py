import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.seed import _assert_seed_safe
from app.shared.config import get_settings


def test_local_non_test_database_requires_explicit_destructive_opt_in():
    engine = create_engine(
        "postgresql+psycopg://app:password@127.0.0.1/customer_care_development"
    )
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="ALLOW_DESTRUCTIVE_SEED"):
            _assert_seed_safe(db, allow_destructive=False)
    engine.dispose()


def test_seed_is_always_refused_in_production(db):
    settings = get_settings()
    original_env = settings.app_env
    settings.app_env = "production"
    try:
        with pytest.raises(RuntimeError, match="outside development/test"):
            _assert_seed_safe(db, allow_destructive=True)
    finally:
        settings.app_env = original_env


def test_remote_test_database_cannot_bypass_host_guard():
    engine = create_engine(
        "postgresql+psycopg://app:password@db.example.test/customer_care_test"
    )
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="non-local database"):
            _assert_seed_safe(db, allow_destructive=True)
    engine.dispose()
