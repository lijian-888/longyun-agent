#!/usr/bin/env bash
# Logical PostgreSQL dumps plus irreplaceable object/file volumes.
# Run during a maintenance window or while imports/uploads are quiescent.
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/deploy/.env.production}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${2:-$ROOT_DIR/deploy/backups/$STAMP}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || fail "Missing environment file: $ENV_FILE"
[[ ! -e "$BACKUP_DIR" ]] || fail "Backup target already exists: $BACKUP_DIR"
project_name="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE" | tail -n 1)"
project_name="${project_name:-longyun-agent}"
[[ "$project_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || fail "Invalid Compose project name"
db_container="${project_name}-db-1"
docker inspect "$db_container" >/dev/null 2>&1 || fail "Database container is not running: $db_container"
docker exec "$db_container" pg_isready -U rice -d rice_demo >/dev/null || fail "Database is not ready"

mkdir -m 700 -p "$BACKUP_DIR/databases" "$BACKUP_DIR/volumes"
printf 'started_at=%s\nproject=%s\nsource=%s\n' "$(date -u +%FT%TZ)" "$project_name" "$ROOT_DIR" > "$BACKUP_DIR/MANIFEST.txt"
if command -v git >/dev/null && git -C "$ROOT_DIR" rev-parse HEAD >/dev/null 2>&1; then
  printf 'git_commit=%s\n' "$(git -C "$ROOT_DIR" rev-parse HEAD)" >> "$BACKUP_DIR/MANIFEST.txt"
fi
docker ps --filter "label=com.docker.compose.project=$project_name" --format '{{.Names}} {{.Image}} {{.ID}}' \
  > "$BACKUP_DIR/CONTAINERS.txt"

# rice_demo includes Keycloak schema. Also include institution business DBs
# and role/grant metadata. Dumps use the server-side database principal.
docker exec "$db_container" pg_dumpall -U rice --globals-only > "$BACKUP_DIR/databases/globals.sql"
mapfile -t database_names < <(docker exec "$db_container" psql -U rice -d postgres -Atc \
  "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres' ORDER BY datname")
((${#database_names[@]} > 0)) || fail "No business database found"
for database_name in "${database_names[@]}"; do
  [[ "$database_name" =~ ^[a-zA-Z0-9_]+$ ]] || fail "Unexpected database name: $database_name"
  docker exec "$db_container" pg_dump -U rice -d "$database_name" -Fc \
    > "$BACKUP_DIR/databases/${database_name}.dump"
  [[ -s "$BACKUP_DIR/databases/${database_name}.dump" ]] || fail "Empty dump for $database_name"
  printf 'database=%s\n' "$database_name" >> "$BACKUP_DIR/MANIFEST.txt"
done

# No image pull: use the gateway image already running on this host. Model
# caches and PostgreSQL physical files are intentionally not archived.
for volume_suffix in minio_data raw_data research_data acps_state keycloak_data r10_audit; do
  volume_name="${project_name}_${volume_suffix}"
  if docker volume inspect "$volume_name" >/dev/null 2>&1; then
    docker run --rm --network none --entrypoint tar \
      -v "${volume_name}:/source:ro" -v "${BACKUP_DIR}:/backup" \
      nginx:1.27-alpine -C /source -czf "/backup/volumes/${volume_suffix}.tar.gz" .
    printf 'volume=%s\n' "$volume_name" >> "$BACKUP_DIR/MANIFEST.txt"
  fi
done

# Configuration, TLS/ACPs material and realm import belong in the encrypted
# backup set. Never print their contents to terminal or ordinary logs.
tar -C "$ROOT_DIR" --exclude='deploy/backups' --exclude='deploy/r10-audit.jsonl' \
  -czf "$BACKUP_DIR/release-config.tar.gz" deploy docker-compose.lan.yml keycloak/themes
if [[ "$ENV_FILE" != "$ROOT_DIR/deploy/.env.production" ]]; then
  cp -- "$ENV_FILE" "$BACKUP_DIR/environment.production"
fi
printf 'completed_at=%s\n' "$(date -u +%FT%TZ)" >> "$BACKUP_DIR/MANIFEST.txt"
(
  cd "$BACKUP_DIR"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
# Docker writes the volume archives as root. The enclosing backup directory
# is mode 0700, while caller-owned files are narrowed to 0600. Avoid chmod on
# root-owned archives from an unprivileged SSH account.
find "$BACKUP_DIR" -type d -user "$(id -u)" -exec chmod 700 {} +
find "$BACKUP_DIR" -type f -user "$(id -u)" -exec chmod 600 {} +
bash "$ROOT_DIR/deploy/r10/verify-backup.sh" "$BACKUP_DIR"
printf 'Backup complete and verified: %s\n' "$BACKUP_DIR"
printf 'Contains secrets; move to encrypted, access-controlled off-host storage.\n'
