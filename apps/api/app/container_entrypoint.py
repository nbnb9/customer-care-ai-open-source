"""Resolve runtime-only container secrets before launching an application command."""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from urllib.parse import quote


def _secret_from_file(
    environ: MutableMapping[str, str], file_variable: str, *, required: bool = False
) -> str | None:
    path_value = environ.get(file_variable, "").strip()
    if not path_value:
        if required:
            raise RuntimeError(f"{file_variable} must point to a mounted secret")
        return None
    path = Path(path_value)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read mounted secret from {file_variable}") from exc
    if not value:
        raise RuntimeError(f"mounted secret for {file_variable} must not be empty")
    return value


def _required(environ: MutableMapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def configure_runtime_environment(environ: MutableMapping[str, str]) -> None:
    """Populate sensitive settings without exposing them in image or Compose config."""

    if not environ.get("DATABASE_URL", "").strip():
        password = _secret_from_file(environ, "DATABASE_PASSWORD_FILE", required=True)
        driver = environ.get("DATABASE_DRIVER", "postgresql+psycopg").strip()
        user = quote(_required(environ, "POSTGRES_USER"), safe="")
        host = _required(environ, "POSTGRES_HOST")
        port = _required(environ, "POSTGRES_PORT")
        database = quote(_required(environ, "POSTGRES_DB"), safe="")
        encoded_password = quote(password or "", safe="")
        environ["DATABASE_URL"] = (
            f"{driver}://{user}:{encoded_password}@{host}:{port}/{database}"
        )

    secret_mappings = (
        ("S3_SECRET_KEY_FILE", "S3_SECRET_KEY"),
        ("LLM_API_KEY_FILE", "LLM_API_KEY"),
    )
    for file_variable, target_variable in secret_mappings:
        value = _secret_from_file(environ, file_variable)
        if value is not None:
            environ[target_variable] = value


def main(argv: Sequence[str] | None = None) -> int:
    command = list(argv if argv is not None else sys.argv[1:])
    if not command:
        raise RuntimeError("container entrypoint requires a command")
    configure_runtime_environment(os.environ)
    os.execvpe(command[0], command, os.environ)
    return 0  # pragma: no cover - exec replaces the process


if __name__ == "__main__":
    raise SystemExit(main())
