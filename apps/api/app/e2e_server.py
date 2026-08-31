"""Start an isolated, repeatable local API for the Playwright suite."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .shared.config import get_settings

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_E2E_DATABASE_SUFFIX = "_e2e_test"


class E2EConfigurationError(ValueError):
    """The requested E2E database is outside the safe local boundary."""


def resolve_e2e_database_url(
    *,
    source_url: str,
    app_env: str,
    explicit_url: str | None,
) -> URL:
    """Return a safe local E2E URL without ever logging credentials."""
    if app_env not in {"development", "test"}:
        raise E2EConfigurationError("E2E bootstrap is allowed only in development/test")

    explicit = explicit_url.strip() if explicit_url else None
    try:
        source = make_url(explicit or source_url)
    except Exception as exc:
        raise E2EConfigurationError("invalid E2E database URL") from exc

    if explicit is None:
        database = source.database
        if not database:
            raise E2EConfigurationError("DATABASE_URL must include a database name")
        base = database.removesuffix(_E2E_DATABASE_SUFFIX).removesuffix("_test")
        source = source.set(database=f"{base}{_E2E_DATABASE_SUFFIX}")

    database = source.database or ""
    if not database.endswith(_E2E_DATABASE_SUFFIX):
        raise E2EConfigurationError(
            "E2E database name must end in '_e2e_test' to permit a reset"
        )
    if source.host not in _LOCAL_HOSTS:
        raise E2EConfigurationError("E2E database must use a loopback host")
    return source


def _ensure_database(url: URL) -> None:
    """Create the dedicated database once; never drop databases from this tool."""
    database = url.database
    if not database:
        raise E2EConfigurationError("E2E database URL must include a database name")

    maintenance_engine = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        with maintenance_engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :database"),
                {"database": database},
            )
            if exists:
                return
            quoted_name = connection.dialect.identifier_preparer.quote(database)
            connection.execute(text(f"CREATE DATABASE {quoted_name}"))
    finally:
        maintenance_engine.dispose()


def prepare_e2e_database(url: URL) -> None:
    """Migrate and reseed the dedicated E2E database from synthetic fixtures."""
    # Import after _activate_e2e_environment. The fixtures import SQLAlchemy models,
    # which in turn initialize app.db from the effective DATABASE_URL.
    from .seed import seed

    _ensure_database(url)
    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(config, "head")

    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with factory() as db:
            seed(db)
    finally:
        engine.dispose()


def _activate_e2e_environment(url: URL) -> None:
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    os.environ["APP_ENV"] = "test"
    os.environ["ALLOW_DEMO_IDENTITY"] = "true"
    get_settings.cache_clear()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2e_api",
        description="Prepare a local isolated database and start the E2E API.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in _LOCAL_HOSTS:
        print(
            "E2E configuration error: API host must be a loopback address",
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.port <= 65535:
        print(
            "E2E configuration error: API port must be between 1 and 65535",
            file=sys.stderr,
        )
        return 2
    source_settings = get_settings()
    try:
        database_url = resolve_e2e_database_url(
            source_url=source_settings.database_url,
            app_env=source_settings.app_env,
            explicit_url=os.environ.get("E2E_DATABASE_URL"),
        )
        _activate_e2e_environment(database_url)
        prepare_e2e_database(database_url)
    except E2EConfigurationError as exc:
        print(f"E2E configuration error: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(
            "E2E database error: "
            f"{type(exc).__name__}; verify the local dedicated *_e2e_test database",
            file=sys.stderr,
        )
        return 2

    print(f"E2E database prepared: {database_url.database}")
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
