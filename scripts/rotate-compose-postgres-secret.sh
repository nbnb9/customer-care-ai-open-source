#!/bin/sh
set -eu

rotate_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
rotate_repo_root=$(CDPATH= cd -- "${rotate_script_dir}/.." && pwd)
rotate_secret_path="${rotate_repo_root}/infra/compose/secrets/postgres_password"
rotate_api_env="${rotate_repo_root}/apps/api/.env"
rotate_db_user=${POSTGRES_USER:-customer_care_app}
rotate_db_name=${POSTGRES_DB:-customer_care}

case "${rotate_db_user}" in
  (""|*[!A-Za-z0-9_]*)
    echo "POSTGRES_USER must be a safe SQL identifier" >&2
    exit 1
    ;;
esac

case "${rotate_db_name}" in
  (""|*[!A-Za-z0-9_]*)
    echo "POSTGRES_DB must be a safe SQL identifier" >&2
    exit 1
    ;;
esac

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to rotate the local Compose secret" >&2
  exit 1
fi

if ! docker compose -f "${rotate_repo_root}/compose.yaml" ps --status running \
  --services | grep -qx postgres; then
  echo "The local Compose postgres service must be running" >&2
  exit 1
fi

umask 077
rotate_new_password=$(openssl rand -hex 32)
rotate_temp_path=$(mktemp "${rotate_secret_path}.rotate.XXXXXX")
rotate_cleanup() {
  rm -f "${rotate_temp_path}"
}
trap rotate_cleanup EXIT HUP INT TERM

# The generated password is hex-only and the role identifier is validated
# above. SQL is sent over stdin and both stdout/stderr are suppressed so a
# database driver cannot expand credentials into diagnostic output.
if ! printf "ALTER ROLE %s PASSWORD '%s';\n" \
  "${rotate_db_user}" "${rotate_new_password}" | \
  docker compose -f "${rotate_repo_root}/compose.yaml" exec -T postgres \
    psql --username "${rotate_db_user}" --dbname "${rotate_db_name}" \
      --set=ON_ERROR_STOP=1 >/dev/null 2>&1; then
  echo "PostgreSQL rejected the credential rotation" >&2
  exit 1
fi

printf '%s\n' "${rotate_new_password}" >"${rotate_temp_path}"
chmod 600 "${rotate_temp_path}"
mv "${rotate_temp_path}" "${rotate_secret_path}"

# Keep the optional host-side development URL in sync, but touch it only when
# it points at the expected loopback Compose database. The secret is never
# printed and the file replacement remains private and atomic.
if [ -f "${rotate_api_env}" ]; then
  ROTATE_API_ENV="${rotate_api_env}" \
  ROTATE_POSTGRES_PASSWORD="${rotate_new_password}" \
  ROTATE_POSTGRES_USER="${rotate_db_user}" \
  ROTATE_POSTGRES_DB="${rotate_db_name}" \
  python3 - <<'PY'
import os
import re
import tempfile
from pathlib import Path

path = Path(os.environ["ROTATE_API_ENV"])
password = os.environ["ROTATE_POSTGRES_PASSWORD"]
user = re.escape(os.environ["ROTATE_POSTGRES_USER"])
database = re.escape(os.environ["ROTATE_POSTGRES_DB"])
pattern = re.compile(
    rf"^(DATABASE_URL=postgresql\+psycopg://{user}:)[^@\r\n]*"
    rf"(@(?:127\.0\.0\.1|localhost):15432/{database})$",
    re.MULTILINE,
)
original = path.read_text(encoding="utf-8")
updated, count = pattern.subn(rf"\g<1>{password}\g<2>", original)
if count == 1:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(updated)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
PY
fi

unset rotate_new_password

docker compose -f "${rotate_repo_root}/compose.yaml" up -d --force-recreate \
  migrate bootstrap api knowledge-worker after-sales-worker

echo "Rotated the local Compose PostgreSQL credential and recreated dependents."
