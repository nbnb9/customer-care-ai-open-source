import pytest
from sqlalchemy.engine import make_url

from app.e2e_server import E2EConfigurationError, main, resolve_e2e_database_url

pytestmark = pytest.mark.offline


def test_e2e_database_is_derived_from_the_local_application_database():
    resolved = resolve_e2e_database_url(
        source_url="postgresql+psycopg://app:password@127.0.0.1/customer_care",
        app_env="development",
        explicit_url=None,
    )

    assert resolved.database == "customer_care_e2e_test"
    assert resolved.host == "127.0.0.1"
    assert resolved.password == "password"


def test_empty_e2e_database_override_uses_the_safe_derived_target():
    resolved = resolve_e2e_database_url(
        source_url="postgresql+psycopg://app:password@127.0.0.1/customer_care",
        app_env="development",
        explicit_url="",
    )

    assert resolved.database == "customer_care_e2e_test"


@pytest.mark.parametrize(
    ("explicit_url", "message"),
    [
        (
            "postgresql+psycopg://app:password@127.0.0.1/customer_care_test",
            "_e2e_test",
        ),
        (
            "postgresql+psycopg://app:password@db.example.test/customer_care_e2e_test",
            "loopback",
        ),
    ],
)
def test_e2e_database_rejects_non_isolated_or_remote_targets(explicit_url, message):
    with pytest.raises(E2EConfigurationError, match=message):
        resolve_e2e_database_url(
            source_url="postgresql+psycopg://app:password@127.0.0.1/customer_care",
            app_env="development",
            explicit_url=explicit_url,
        )


def test_e2e_database_rejects_production_even_with_a_safe_url():
    with pytest.raises(E2EConfigurationError, match="development/test"):
        resolve_e2e_database_url(
            source_url="postgresql+psycopg://app:password@127.0.0.1/customer_care",
            app_env="production",
            explicit_url=None,
        )


def test_e2e_database_keeps_a_custom_isolated_url():
    explicit_url = "postgresql+psycopg://app:password@localhost/custom_e2e_test"
    resolved = resolve_e2e_database_url(
        source_url="postgresql+psycopg://app:password@127.0.0.1/customer_care",
        app_env="test",
        explicit_url=explicit_url,
    )

    assert make_url(explicit_url) == resolved


def test_e2e_server_rejects_non_loopback_api_host(capsys):
    assert main(["--host", "0.0.0.0"]) == 2
    assert "loopback" in capsys.readouterr().err
