#!/bin/sh
set -eu

step41_api_url=${COMPOSE_API_URL:-http://127.0.0.1:${API_PORT:-8000}}
step41_web_url=${COMPOSE_WEB_URL:-http://127.0.0.1:${WEB_PORT:-3000}}

step41_ready=$(curl --fail --silent --show-error "${step41_api_url}/ready")
case "${step41_ready}" in
  *'"status":"ready"'*) ;;
  *)
    echo "API readiness response is unexpected" >&2
    exit 1
    ;;
esac

step41_identity=$(
  curl --fail --silent --show-error \
    -H 'X-Demo-User-ID: user-nw-customer-01' \
    "${step41_api_url}/whoami"
)
case "${step41_identity}" in
  *'"tenant_id":"tenant-northwind-demo"'*) ;;
  *)
    echo "synthetic bootstrap identity is unavailable" >&2
    exit 1
    ;;
esac

step41_web=$(curl --fail --silent --show-error "${step41_web_url}/")
case "${step41_web}" in
  *'CareOps'*) ;;
  *)
    echo "Web response is unexpected" >&2
    exit 1
    ;;
esac

echo "compose smoke passed: API ready, synthetic identity present, Web reachable"
