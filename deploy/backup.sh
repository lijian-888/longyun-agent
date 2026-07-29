#!/usr/bin/env bash
# Back up PostgreSQL and irreplaceable uploaded source/research files. Model
# caches are excluded because they are reproducible from approved model media.
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/deploy/.env.production}"
COMPOSE_FILE="$ROOT_DIR/docker-compose.lan.yml"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT_DIR/deploy/backups/$STAMP"

compose() {
  bash "$ROOT_DIR/deploy/compose.sh" "$@"
}

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }
project_name="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE" | tail -n 1)"
project_name="${project_name:-longyun-agent}"
mkdir -p "$BACKUP_DIR"

compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$project_name" ps -q db | grep -q . \
  || { echo "Database container is not running." >&2; exit 1; }

compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$project_name" \
  exec -T db pg_dump -U rice -d rice_demo | gzip -9 > "$BACKUP_DIR/rice_demo.sql.gz"

for volume in raw_data research_data; do
  docker volume inspect "${project_name}_${volume}" >/dev/null
  docker run --rm \
    -v "${project_name}_${volume}:/source:ro" \
    -v "$BACKUP_DIR:/backup" \
    alpine:3.20 \
    tar -C /source -czf "/backup/${volume}.tar.gz" .
done

sha256sum "$BACKUP_DIR"/* > "$BACKUP_DIR/SHA256SUMS"
printf 'Backup created: %s\n' "$BACKUP_DIR"
printf 'Copy this directory to an encrypted, access-controlled backup target.\n'
