from pathlib import Path

import pytest

from app.container_entrypoint import configure_runtime_environment

pytestmark = pytest.mark.offline


def test_container_entrypoint_builds_encoded_database_url_from_secret(tmp_path: Path):
    password = tmp_path / "postgres_password"
    password.write_text("local p@ss/word\n", encoding="utf-8")
    environ = {
        "DATABASE_PASSWORD_FILE": str(password),
        "POSTGRES_USER": "customer care",
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "customer/care",
    }

    configure_runtime_environment(environ)

    assert environ["DATABASE_URL"] == (
        "postgresql+psycopg://customer%20care:local%20p%40ss%2Fword@"
        "postgres:5432/customer%2Fcare"
    )


def test_container_entrypoint_resolves_optional_runtime_secrets(tmp_path: Path):
    s3_secret = tmp_path / "s3"
    llm_secret = tmp_path / "llm"
    s3_secret.write_text("s3-only\n", encoding="utf-8")
    llm_secret.write_text("llm-only\n", encoding="utf-8")
    environ = {
        "DATABASE_URL": "sqlite:///already-configured.db",
        "S3_SECRET_KEY_FILE": str(s3_secret),
        "LLM_API_KEY_FILE": str(llm_secret),
    }

    configure_runtime_environment(environ)

    assert environ["S3_SECRET_KEY"] == "s3-only"
    assert environ["LLM_API_KEY"] == "llm-only"


def test_container_entrypoint_fails_closed_for_missing_database_secret():
    with pytest.raises(RuntimeError, match="DATABASE_PASSWORD_FILE"):
        configure_runtime_environment({})
