#!/bin/sh
set -eu

step41_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
step41_repo_root=$(CDPATH= cd -- "${step41_script_dir}/.." && pwd)
step41_secret_dir="${step41_repo_root}/infra/compose/secrets"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to generate local Compose secrets" >&2
  exit 1
fi

umask 077
mkdir -p "${step41_secret_dir}"

generate_secret() {
  step41_secret_path="${step41_secret_dir}/$1"
  if [ -s "${step41_secret_path}" ]; then
    echo "preserved ${step41_secret_path}"
    return
  fi
  openssl rand -hex 32 >"${step41_secret_path}"
  chmod 600 "${step41_secret_path}"
  echo "created ${step41_secret_path}"
}

generate_secret postgres_password
generate_secret minio_root_password
generate_secret grafana_admin_password
