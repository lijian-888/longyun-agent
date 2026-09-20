#!/usr/bin/env bash
# Default is non-destructive. Full teardown requires verified backup and an
# exact confirmation token. Only the named Compose project is targeted.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT_DIR/deploy/.env.production"
mode=dry-run
confirmation=''
backup_dir=''
allow_production=false
while (($#)); do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --backup-dir) backup_dir="$2"; shift 2 ;;
    --confirm) confirmation="$2"; shift 2 ;;
    --execute) mode=execute; shift ;;
    --allow-production) allow_production=true; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }
project="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE" | tail -n 1)"
project="${project:-longyun-agent}"
[[ "$project" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || { echo 'Invalid project name' >&2; exit 1; }
printf 'Target project: %s; mode: %s\n' "$project" "$mode"
printf 'Would stop/remove only this Compose project and its named volumes. No host directories are deleted.\n'
if [[ "$mode" == dry-run ]]; then exit 0; fi
[[ "$confirmation" == "DESTROY:$project" ]] || { echo 'Exact confirmation token required' >&2; exit 1; }
if [[ "$project" == longyun-agent && "$allow_production" != true ]]; then
  echo 'Production teardown additionally requires --allow-production' >&2; exit 1
fi
[[ -n "$backup_dir" ]] || { echo 'A verified backup is required' >&2; exit 1; }
bash "$ROOT_DIR/deploy/r10/verify-backup.sh" "$backup_dir"
grep -Fxq "project=$project" "$backup_dir/MANIFEST.txt" || { echo 'Backup belongs to a different project' >&2; exit 1; }
audit_file="$ROOT_DIR/deploy/r10-audit.jsonl"
printf '{"time":"%s","action":"destroy","project":"%s","phase":"started"}\n' \
  "$(date -u +%FT%TZ)" "$project" >> "$audit_file"
bash "$ROOT_DIR/deploy/compose.sh" --env-file "$ENV_FILE" -f "$ROOT_DIR/docker-compose.lan.yml" \
  -p "$project" down --remove-orphans --volumes
for suffix in acps_state keycloak_data r10_audit; do
  volume="${project}_${suffix}"
  if docker volume inspect "$volume" >/dev/null 2>&1; then docker volume rm "$volume"; fi
done
printf '{"time":"%s","action":"destroy","project":"%s","phase":"completed","backup":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$project" "$(basename -- "$backup_dir")" >> "$audit_file"
chmod 600 "$audit_file"
printf 'Project containers/volumes removed. Audit: %s\n' "$audit_file"
