#!/usr/bin/env bash
# Recreate application containers from a previously retained immutable image
# tag.  The default is a dry run; pass --apply only after the target tag and
# current backup have been verified.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/deploy/.env.production}"
PREVIOUS_TAG="${2:-}"
MODE="${3:-}"
COMPOSE_FILE="$ROOT_DIR/docker-compose.lan.yml"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
compose() { bash "$ROOT_DIR/deploy/compose.sh" "$@"; }

[[ -f "$ENV_FILE" ]] || fail "Missing environment file: $ENV_FILE"
[[ "$PREVIOUS_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?$ ]] \
  || fail "Previous tag must be an immutable release tag such as v1.10.2"

project_name="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE" | tail -n 1)"
project_name="${project_name:-longyun-agent}"

for image in "longyun-agent-api:$PREVIOUS_TAG" "longyun-agent-web:$PREVIOUS_TAG"; do
  docker image inspect "$image" >/dev/null 2>&1 || fail "Required rollback image is missing: $image"
done

printf 'Rollback target: %s\n' "$PREVIOUS_TAG"
printf 'Compose project: %s\n' "$project_name"
printf 'Database and file volumes will not be deleted or recreated.\n'

if [[ "$MODE" != "--apply" ]]; then
  printf 'Dry run only. Re-run with --apply after checking the backup and target images.\n'
  exit 0
fi

RELEASE_TAG="$PREVIOUS_TAG" compose \
  --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$project_name" \
  up -d --no-deps api web agent-workflow-worker genotype-worker

compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$project_name" ps
printf 'Application rollback completed. Validate health and login before reopening traffic.\n'
