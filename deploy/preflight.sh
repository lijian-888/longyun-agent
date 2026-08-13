#!/usr/bin/env bash
# Read-only production preflight. It does not create, remove, or restart any
# containers, images, databases, or volumes.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/deploy/.env.production}"
COMPOSE_FILE="$ROOT_DIR/docker-compose.lan.yml"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

compose() {
  bash "$ROOT_DIR/deploy/compose.sh" "$@"
}

value_from_env() {
  local key="$1"
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1
}

require_value() {
  local key="$1"
  local value
  value="$(value_from_env "$key")"
  [[ -n "$value" ]] || fail "$key is missing in $ENV_FILE"
  [[ "$value" != *"replace-with"* ]] || fail "$key still contains an example placeholder"
}

[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE. Copy deploy/.env.production.example first."
command -v docker >/dev/null || fail "Docker is not installed."
docker info >/dev/null || fail "The current user cannot access the Docker daemon."

for key in PUBLIC_PLATFORM_URL PUBLIC_PLATFORM_HOST HTTPS_PORT TLS_CERT_FILE TLS_KEY_FILE \
  POSTGRES_PASSWORD APP_DATABASE_PASSWORD KEYCLOAK_DATABASE_PASSWORD \
  KEYCLOAK_ADMIN_PASSWORD INITIAL_RESEARCHER_PASSWORD \
  INITIAL_PROCESSOR_PASSWORD INITIAL_FIELD_ADMIN_PASSWORD; do
  require_value "$key"
done

deployment_mode="$(value_from_env LONGYUN_LLM_DEPLOYMENT_MODE)"
data_environment="$(value_from_env LONGYUN_DATA_ENVIRONMENT)"
[[ "$deployment_mode" =~ ^(external_api|local|on_prem|private)$ ]] \
  || fail "LONGYUN_LLM_DEPLOYMENT_MODE must be external_api, local, on_prem, or private"
[[ "$data_environment" =~ ^(sandbox_desensitized|institution_private)$ ]] \
  || fail "LONGYUN_DATA_ENVIRONMENT must be sandbox_desensitized or institution_private"
for key in LONGYUN_LLM_BASE_URL LONGYUN_LLM_MODEL LONGYUN_LLM_ALIAS; do
  require_value "$key"
done
if [[ "$deployment_mode" == "external_api" ]]; then
  require_value LONGYUN_LLM_PROVIDER_NAME
  require_value LONGYUN_LLM_API_KEY
  [[ "$(value_from_env LONGYUN_LLM_BASE_URL)" == https://* ]] \
    || fail "External LONGYUN_LLM_BASE_URL must start with https://"
fi

platform_url="$(value_from_env PUBLIC_PLATFORM_URL)"
platform_host="$(value_from_env PUBLIC_PLATFORM_HOST)"
https_port="$(value_from_env HTTPS_PORT)"
cert_path="$(value_from_env TLS_CERT_FILE)"
key_path="$(value_from_env TLS_KEY_FILE)"

[[ "$platform_url" == https://* ]] || fail "PUBLIC_PLATFORM_URL must start with https://"
[[ "$platform_url" != *"localhost"* ]] || fail "PUBLIC_PLATFORM_URL cannot use localhost in production"
[[ "$platform_url" == *"$platform_host"* ]] || fail "PUBLIC_PLATFORM_HOST does not match PUBLIC_PLATFORM_URL"
[[ -r "$ROOT_DIR/${cert_path#./}" ]] || fail "TLS certificate cannot be read: $cert_path"
[[ -r "$ROOT_DIR/${key_path#./}" ]] || fail "TLS private key cannot be read: $key_path"

available_kb="$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')"
(( available_kb >= 83886080 )) || fail "Less than 80 GB is free on the deployment filesystem."

printf 'Docker: %s\n' "$(docker --version)"
printf 'Compose: %s\n' "$(compose version | head -n 1)"
printf 'Free disk: %s\n' "$(df -h "$ROOT_DIR" | awk 'NR==2 {print $4}')"

if command -v ss >/dev/null && ss -ltn | grep -Eq ":${https_port}\\b"; then
  printf 'Port %s is already listening. Refusing an initial deployment to avoid changing another project.\n' "$https_port" >&2
  ss -ltn | grep -E ":${https_port}\\b" || true
  exit 1
else
  printf 'HTTPS port %s is available.\n' "$https_port"
fi

compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config -q
printf 'Production deployment preflight passed.\n'
