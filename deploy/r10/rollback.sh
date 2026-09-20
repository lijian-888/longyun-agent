#!/usr/bin/env bash
# R10 rollback removes only its control-plane container, retaining audit and
# all business services/data. Default is a read-only rehearsal.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT_DIR/deploy/.env.production"
mode=dry-run
confirmation=''
while (($#)); do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --confirm) confirmation="$2"; shift 2 ;;
    --execute) mode=execute; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }
project="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE" | tail -n 1)"
project="${project:-longyun-agent}"
[[ "$project" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || exit 1
echo "R10 rollback target: ${project}-control-plane-1; mode: $mode"
if [[ "$mode" == dry-run ]]; then exit 0; fi
[[ "$confirmation" == "ROLLBACK:$project" ]] || { echo 'Exact confirmation token required' >&2; exit 1; }
compose=(bash "$ROOT_DIR/deploy/compose.sh" --env-file "$ENV_FILE" -f "$ROOT_DIR/docker-compose.lan.yml"
  -f "$ROOT_DIR/deploy/r10/docker-compose.r10.yml" -p "$project")
"${compose[@]}" stop control-plane
"${compose[@]}" rm -f control-plane
audit_file="$ROOT_DIR/deploy/r10-audit.jsonl"
printf '{"time":"%s","action":"r10_rollback","project":"%s","result":"control_plane_removed","data_preserved":true}\n' \
  "$(date -u +%FT%TZ)" "$project" >> "$audit_file"
chmod 600 "$audit_file"
echo "R10 control plane rolled back; business services and volumes preserved. Audit: $audit_file"
