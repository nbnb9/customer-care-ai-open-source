import pytest
from pydantic import ValidationError

from app.shared.config import Settings


def test_liveness_and_readiness(client):
    live = client.get("/health")
    ready = client.get("/ready")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "javascript://example.test",
        "https://user:secret@example.test",
        "https://example.test/path",
        "https://example.test?redirect=evil",
        "https://example.test:bad-port",
        "https://exa mple.test",
    ],
)
def test_cors_origin_validation_rejects_unsafe_values(origin):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="development",
            database_url="postgresql+psycopg://app:password@localhost/example",
            cors_allowed_origins=origin,
        )


def test_non_development_cors_requires_https():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+psycopg://app:password@localhost/example",
            cors_allowed_origins="http://support.example.test",
        )
