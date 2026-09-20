#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 1 ]] || { echo 'usage: verify-backup.sh BACKUP_DIR' >&2; exit 2; }
directory="$(realpath -- "$1")"
[[ -d "$directory" && -f "$directory/SHA256SUMS" && -f "$directory/MANIFEST.txt" ]] \
  || { echo 'Incomplete backup directory' >&2; exit 1; }
( cd "$directory" && sha256sum -c SHA256SUMS >/dev/null )
grep -q '^completed_at=' "$directory/MANIFEST.txt"
[[ -s "$directory/databases/globals.sql" && -s "$directory/release-config.tar.gz" ]]
find "$directory/databases" -maxdepth 1 -name '*.dump' -size +0c | grep -q .
echo "Backup checksums and required artifacts verified: $directory"
