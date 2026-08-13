#!/bin/sh
set -eu

require_value() {
  variable_name="$1"
  eval "variable_value=\${$variable_name:-}"
  if [ -z "$variable_value" ]; then
    echo "Required MinIO setting is empty: $variable_name" >&2
    exit 1
  fi
}

for required in \
  MINIO_ROOT_USER MINIO_ROOT_PASSWORD \
  ORG_A_MINIO_ACCESS_KEY ORG_A_MINIO_SECRET_KEY \
  ORG_A_MINIO_BACKUP_ACCESS_KEY ORG_A_MINIO_BACKUP_SECRET_KEY \
  ORG_B_MINIO_ACCESS_KEY ORG_B_MINIO_SECRET_KEY \
  ORG_B_MINIO_BACKUP_ACCESS_KEY ORG_B_MINIO_BACKUP_SECRET_KEY; do
  require_value "$required"
done

keys="$ORG_A_MINIO_ACCESS_KEY $ORG_A_MINIO_BACKUP_ACCESS_KEY $ORG_B_MINIO_ACCESS_KEY $ORG_B_MINIO_BACKUP_ACCESS_KEY"
if [ "$(printf '%s\n' $keys | sort -u | wc -l | tr -d ' ')" -ne 4 ]; then
  echo "Every institution data/backup MinIO access key must be unique." >&2
  exit 1
fi

until mc alias set longyun http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  sleep 2
done

configure_bucket() {
  bucket="$1"
  quota="$2"
  mc mb --ignore-existing "longyun/$bucket"
  # Files are never anonymously readable, even if an operator previously
  # experimented with a public policy on the same bucket.
  mc anonymous set none "longyun/$bucket" >/dev/null
  mc version enable "longyun/$bucket"
  if [ -n "$quota" ]; then
    mc quota set "longyun/$bucket" --size "$quota"
  fi
}

configure_bucket longyun-org-a-data "${ORG_A_MINIO_DATA_QUOTA:-200GiB}"
configure_bucket longyun-org-a-backup "${ORG_A_MINIO_BACKUP_QUOTA:-50GiB}"
configure_bucket longyun-org-b-data "${ORG_B_MINIO_DATA_QUOTA:-200GiB}"
configure_bucket longyun-org-b-backup "${ORG_B_MINIO_BACKUP_QUOTA:-50GiB}"

mc admin user add longyun "$ORG_A_MINIO_ACCESS_KEY" "$ORG_A_MINIO_SECRET_KEY"
mc admin user add longyun "$ORG_A_MINIO_BACKUP_ACCESS_KEY" "$ORG_A_MINIO_BACKUP_SECRET_KEY"
mc admin user add longyun "$ORG_B_MINIO_ACCESS_KEY" "$ORG_B_MINIO_SECRET_KEY"
mc admin user add longyun "$ORG_B_MINIO_BACKUP_ACCESS_KEY" "$ORG_B_MINIO_BACKUP_SECRET_KEY"
mc admin policy create longyun longyun-org-a /policies/org-a-policy.json
mc admin policy create longyun longyun-org-a-backup /policies/org-a-backup-policy.json
mc admin policy create longyun longyun-org-b /policies/org-b-policy.json
mc admin policy create longyun longyun-org-b-backup /policies/org-b-backup-policy.json
mc admin policy attach longyun longyun-org-a --user "$ORG_A_MINIO_ACCESS_KEY"
mc admin policy attach longyun longyun-org-a-backup --user "$ORG_A_MINIO_BACKUP_ACCESS_KEY"
mc admin policy attach longyun longyun-org-b --user "$ORG_B_MINIO_ACCESS_KEY"
mc admin policy attach longyun longyun-org-b-backup --user "$ORG_B_MINIO_BACKUP_ACCESS_KEY"

# Verify both positive access and the most important negative boundary. A
# configuration that can read another institution's bucket must fail startup.
mc alias set org-a http://minio:9000 "$ORG_A_MINIO_ACCESS_KEY" "$ORG_A_MINIO_SECRET_KEY" >/dev/null
mc alias set org-a-backup http://minio:9000 "$ORG_A_MINIO_BACKUP_ACCESS_KEY" "$ORG_A_MINIO_BACKUP_SECRET_KEY" >/dev/null
mc alias set org-b http://minio:9000 "$ORG_B_MINIO_ACCESS_KEY" "$ORG_B_MINIO_SECRET_KEY" >/dev/null
mc alias set org-b-backup http://minio:9000 "$ORG_B_MINIO_BACKUP_ACCESS_KEY" "$ORG_B_MINIO_BACKUP_SECRET_KEY" >/dev/null
mc ls org-a/longyun-org-a-data >/dev/null
mc ls org-a-backup/longyun-org-a-backup >/dev/null
mc ls org-b/longyun-org-b-data >/dev/null
mc ls org-b-backup/longyun-org-b-backup >/dev/null
if mc ls org-a/longyun-org-b-data >/dev/null 2>&1; then
  echo "MinIO isolation check failed: org-a can list org-b data." >&2
  exit 1
fi
if mc ls org-b/longyun-org-a-data >/dev/null 2>&1; then
  echo "MinIO isolation check failed: org-b can list org-a data." >&2
  exit 1
fi
if mc ls org-a/longyun-org-a-backup >/dev/null 2>&1; then
  echo "MinIO isolation check failed: org-a application credentials can list backups." >&2
  exit 1
fi
if mc ls org-b/longyun-org-b-backup >/dev/null 2>&1; then
  echo "MinIO isolation check failed: org-b application credentials can list backups." >&2
  exit 1
fi

echo "MinIO institution buckets and policies are ready."
