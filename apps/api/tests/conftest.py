import os
from pathlib import Path

# Unit and integration tests must never consume a developer's real-model key.
# This runs before importing app.main/app.db, whose settings are initialized at
# module import time.
os.environ["LLM_PROVIDER"] = "fake"
os.environ["CUSTOMER_TOOLS_PROVIDER"] = "mock"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.seed import clear, seed
from app.shared.config import get_settings
from app.shared.observability import configure_observability_storage


def _test_database_url() -> str:
    url = get_settings().database_url
    base, _ = url.rsplit("/", 1)
    return f"{base}/customer_care_test"


def _ensure_test_database() -> None:
    """Create only the dedicated local test database when it is absent."""

    url = make_url(_test_database_url())
    database = url.database or ""
    if (
        get_settings().app_env not in {"development", "test"}
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not database.endswith("_test")
    ):
        raise RuntimeError("Refusing to prepare a non-local test database")

    maintenance = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        with maintenance.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :database"),
                {"database": database},
            )
            if not exists:
                quoted_name = connection.dialect.identifier_preparer.quote(database)
                connection.execute(text(f"CREATE DATABASE {quoted_name}"))
    except SQLAlchemyError:
        # Some DBAPI exception renderers include the full conninfo. Keep setup
        # failures useful without ever copying a local password into pytest logs.
        raise RuntimeError(
            "Unable to prepare the dedicated local test database"
        ) from None
    finally:
        maintenance.dispose()


test_engine = create_engine(_test_database_url(), pool_pre_ping=True)
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
configure_observability_storage(
    session_factory=TestingSessionLocal,
    engine=test_engine,
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
get_settings().allow_demo_identity = True


def _alembic_config() -> Config:
    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _test_database_url())
    return config


@pytest.fixture(scope="session", autouse=True)
def _prepare_schema(request):
    collected = request.session.items
    if collected and all(item.get_closest_marker("offline") for item in collected):
        yield
        return
    _ensure_test_database()
    config = _alembic_config()
    command.upgrade(config, "head")
    with TestingSessionLocal() as db:
        clear(db)
        db.commit()
    # Prove the complete empty-database downgrade/upgrade path before tests.
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    with TestingSessionLocal() as db:
        clear(db)
        db.commit()


@pytest.fixture()
def db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _seed(request, db):
    if request.node.get_closest_marker("offline"):
        return
    seed(db)  # 每个测试前清空并灌入 world.json


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def auth(user_id: str) -> dict:
    return {"X-Demo-User-ID": user_id}


def create_headers(user_id: str, key: str) -> dict[str, str]:
    return {**auth(user_id), "Idempotency-Key": key}
