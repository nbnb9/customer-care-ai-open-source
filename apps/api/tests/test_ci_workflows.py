"""Offline contract checks for the Step 40 GitHub Actions gates."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
EXPECTED_WORKFLOWS = {
    "backend.yml",
    "frontend.yml",
    "e2e.yml",
    "eval-security.yml",
}
PINNED_ACTION_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


def _workflow_text(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def test_step40_workflows_are_present_and_run_for_pull_requests():
    names = {path.name for path in WORKFLOW_DIR.glob("*.yml")}
    assert EXPECTED_WORKFLOWS.issubset(names)
    for name in EXPECTED_WORKFLOWS:
        text = _workflow_text(name)
        assert "pull_request:" in text
        assert "workflow_dispatch:" in text
        assert "permissions:\n  contents: read" in text
        assert "runs-on: ubuntu-24.04" in text
        assert "timeout-minutes:" in text


def test_workflow_actions_are_immutable_and_no_repository_secrets_are_used():
    for name in EXPECTED_WORKFLOWS:
        text = _workflow_text(name)
        assert "${{ secrets." not in text
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("uses:"):
                continue
            action = line.removeprefix("uses:").split("#", 1)[0].strip()
            assert PINNED_ACTION_RE.fullmatch(action), (name, action)


def test_database_workflows_use_synthetic_test_databases_and_fake_gateway():
    for name in {"backend.yml", "e2e.yml", "eval-security.yml"}:
        text = _workflow_text(name)
        assert "LLM_PROVIDER: fake" in text
        assert "CUSTOMER_TOOLS_PROVIDER: mock" in text
        assert "synthetic-ci-only" in text
        assert re.search(r"POSTGRES_DB: customer_care(?:_e2e)?_test", text)
        assert not re.search(r"LLM_PROVIDER:\s*(deepseek|openai)", text)


def test_locked_install_and_required_gate_commands_are_declared():
    backend = _workflow_text("backend.yml")
    frontend = _workflow_text("frontend.yml")
    e2e = _workflow_text("e2e.yml")
    evaluation = _workflow_text("eval-security.yml")

    for text in (backend, e2e, evaluation):
        assert "uv sync --frozen --all-groups" in text
    for text in (frontend, e2e):
        assert "pnpm install --frozen-lockfile" in text

    for command in (
        "./scripts/init-compose-secrets.sh",
        "docker compose config --quiet",
        "docker compose --profile observability config --quiet",
        "ruff check",
        "ruff format --check",
        "mypy app",
        "alembic check",
        "pytest -q",
    ):
        assert command in backend
    for command in ("pnpm lint", "pnpm typecheck", "pnpm test:unit", "pnpm build"):
        assert command in frontend
    assert "playwright install --with-deps chromium" in e2e
    assert "pnpm test:e2e" in e2e
    for command in ("eval_triage", "eval_rag", "pytest -q tests/security"):
        assert command in evaluation


def test_frontend_declares_a_non_watch_unit_test_script():
    package = json.loads(
        (REPO_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8")
    )
    assert package["scripts"]["typecheck"] == "next typegen && tsc --noEmit"
    assert package["scripts"]["test:unit"] == "vitest run"


def test_shopify_sandbox_workflow_is_manual_read_only_and_secret_scoped():
    text = _workflow_text("shopify-sandbox.yml")
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "CUSTOMER_TOOLS_PROVIDER: shopify_sandbox" in text
    assert 'SHOPIFY_EXTERNAL_WRITES_ENABLED: "false"' in text
    assert "uv run shopify_sandbox_smoke" in text
    job_env = text.split("    env:\n", 1)[1].split("\n\n    steps:", 1)[0]
    assert "${{ runner." not in job_env
    for variable, filename in (
        ("SHOPIFY_CLIENT_ID_FILE", "shopify_client_id"),
        ("SHOPIFY_CLIENT_SECRET_FILE", "shopify_client_secret"),
        ("SHOPIFY_SANDBOX_BINDINGS_FILE", "shopify_bindings.json"),
    ):
        step_value = f"{variable}: ${{{{ runner.temp }}}}/{filename}"
        assert text.count(step_value) == 2
    for secret in (
        "SHOPIFY_SANDBOX_CLIENT_ID",
        "SHOPIFY_SANDBOX_CLIENT_SECRET",
        "SHOPIFY_SANDBOX_BINDINGS_JSON",
    ):
        assert f"secrets.{secret}" in text
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("uses:"):
            continue
        action = line.removeprefix("uses:").split("#", 1)[0].strip()
        assert PINNED_ACTION_RE.fullmatch(action), action
