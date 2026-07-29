#!/usr/bin/env bash
# Run Docker Compose through either the V2 plugin or the standalone V2 binary.
# This keeps deployment commands portable across managed CentOS 7 hosts.
set -Eeuo pipefail

if docker compose version >/dev/null 2>&1; then
  exec docker compose "$@"
fi

if command -v docker-compose >/dev/null 2>&1; then
  exec docker-compose "$@"
fi

echo "Docker Compose v2 plugin or docker-compose v2 binary is required." >&2
exit 1
