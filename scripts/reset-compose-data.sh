#!/bin/sh
set -eu

step41_project_name=${COMPOSE_PROJECT_NAME:-customer-care-ai-full}
step41_expected_confirmation="${step41_project_name}/delete-local-volumes"

if [ "${1:-}" != "--confirm" ] || [ "${2:-}" != "${step41_expected_confirmation}" ]; then
  echo "This permanently deletes local Compose data." >&2
  echo "Re-run with: $0 --confirm ${step41_expected_confirmation}" >&2
  exit 2
fi

docker compose --project-name "${step41_project_name}" down --volumes --remove-orphans
echo "deleted volumes for Compose project ${step41_project_name}"
