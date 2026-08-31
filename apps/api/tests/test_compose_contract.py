"""Offline contract checks for the Step 41 complete local Compose stack."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.offline

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "compose.yaml"
EXPECTED_SERVICES = {
    "postgres",
    "redis",
    "minio",
    "migrate",
    "bootstrap",
    "api",
    "knowledge-worker",
    "after-sales-worker",
    "web",
    "prometheus",
    "grafana",
}
APP_DOCKERFILES = (
    REPO_ROOT / "apps" / "api" / "Dockerfile",
    REPO_ROOT / "apps" / "worker" / "Dockerfile",
    REPO_ROOT / "apps" / "web" / "Dockerfile",
)


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_step41_declares_complete_stack_and_loopback_only_ports():
    compose = _compose()
    services = compose["services"]
    assert EXPECTED_SERVICES == set(services)

    for service_name, service in services.items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (service_name, port)


def test_startup_dependencies_wait_for_health_and_completed_jobs():
    services = _compose()["services"]
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == (
        "service_healthy"
    )
    assert services["bootstrap"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["api"]["depends_on"]["bootstrap"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["web"]["depends_on"]["api"]["condition"] == "service_healthy"

    for service_name in ("postgres", "redis", "minio", "api", "web"):
        assert "healthcheck" in services[service_name]


def test_runtime_secrets_are_file_mounted_and_fake_llm_is_fail_safe_default():
    compose = _compose()
    services = compose["services"]
    secret_files = {item["file"] for item in compose["secrets"].values()}
    assert secret_files == {
        "./infra/compose/secrets/postgres_password",
        "./infra/compose/secrets/minio_root_password",
        "./infra/compose/secrets/grafana_admin_password",
    }

    forbidden_environment_keys = {
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "GF_SECURITY_ADMIN_PASSWORD",
        "LLM_API_KEY",
    }
    for service_name, service in services.items():
        environment = service.get("environment", {})
        assert forbidden_environment_keys.isdisjoint(environment), service_name

    assert services["api"]["environment"]["LLM_PROVIDER"] == "fake"
    assert services["api"]["environment"]["CUSTOMER_TOOLS_PROVIDER"] == "mock"
    assert "LLM_API_KEY" not in COMPOSE_PATH.read_text(encoding="utf-8")
    secret_ignore = (
        REPO_ROOT / "infra" / "compose" / "secrets" / ".gitignore"
    ).read_text(encoding="utf-8")
    assert secret_ignore.splitlines() == ["*", "!.gitignore"]


def test_stateful_services_use_named_volumes_and_reset_is_guarded():
    compose = _compose()
    services = compose["services"]
    volumes = set(compose["volumes"])
    assert {"postgres_data", "redis_data", "minio_data"}.issubset(volumes)
    assert "postgres_data:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert "redis_data:/data" in services["redis"]["volumes"]
    assert "minio_data:/data" in services["minio"]["volumes"]

    reset_script = (REPO_ROOT / "scripts" / "reset-compose-data.sh").read_text(
        encoding="utf-8"
    )
    assert "--confirm" in reset_script
    assert "delete-local-volumes" in reset_script
    assert "down --volumes --remove-orphans" in reset_script


def test_images_are_reproducible_non_root_and_observability_is_optional():
    for dockerfile in APP_DOCKERFILES:
        text = dockerfile.read_text(encoding="utf-8")
        assert "@sha256:" in text, dockerfile
        assert "--frozen" in text, dockerfile
        assert " AS " in text, dockerfile
        assert "USER " in text, dockerfile

    services = _compose()["services"]
    assert services["prometheus"]["profiles"] == ["observability"]
    assert services["grafana"]["profiles"] == ["observability"]
    assert services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == ("false")
    assert services["grafana"]["environment"]["GF_PLUGINS_PREINSTALL_DISABLED"] == (
        "true"
    )

    for script_name in (
        "compose-smoke.sh",
        "init-compose-secrets.sh",
        "reset-compose-data.sh",
        "rotate-compose-postgres-secret.sh",
    ):
        mode = (REPO_ROOT / "scripts" / script_name).stat().st_mode
        assert mode & os.X_OK, script_name


def test_api_image_packages_runtime_qa_regression_catalog():
    dockerfile = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "data/evals /workspace/data/evals" in dockerfile


def test_compose_smoke_uses_current_stable_product_marker():
    smoke_script = (REPO_ROOT / "scripts" / "compose-smoke.sh").read_text(
        encoding="utf-8"
    )
    assert "CareOps" in smoke_script
    assert "CustomerCare AI" not in smoke_script
